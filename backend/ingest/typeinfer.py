"""Stage 2 — type inference. Deterministic, no LLM.

Per column: sample up to 500 non-empty values, score every candidate type by how
many of them parse, and take the most specific type that clears the agreement
threshold. Anything below the threshold falls back to `text`, because a wrong
strong type breaks the generated view at commit time whereas `text` merely
under-describes.

The winning parse rate becomes the field's confidence, which is what the review
screen colours on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from ingest.reader import is_blank

SAMPLE_LIMIT = 500
AGREEMENT_THRESHOLD = 0.90
SAMPLE_VALUE_COUNT = 5

TRUE_WORDS = {"true", "yes", "y", "t", "evet", "doğru"}
FALSE_WORDS = {"false", "no", "n", "f", "hayır", "yanlış"}
BOOL_WORDS = TRUE_WORDS | FALSE_WORDS

DATE_FORMATS = (
    "%Y-%m-%d",
    "%d/%m/%Y",
    "%m/%d/%Y",
    "%d.%m.%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d %b %Y",
    "%d %B %Y",
    "%b %d, %Y",
)
DATETIME_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
)

# Strip currency symbols, thousands separators and a trailing percent before
# attempting a numeric parse. "₺1.234,50" and "$1,234.50" are both numbers.
_CURRENCY_RE = re.compile(r"^[\s$€£₺¥]+|[\s%]+$")


def _clean_number(text: str) -> str | None:
    s = _CURRENCY_RE.sub("", text.strip())
    if not s or not any(ch.isdigit() for ch in s):
        return None
    negative = s.startswith("(") and s.endswith(")")
    if negative:
        s = s[1:-1]
    if "," in s and "." in s:
        # Whichever separator comes last is the decimal point.
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        # A single comma with exactly three trailing digits is a thousands
        # separator; anything else is a decimal comma.
        s = s.replace(",", "") if len(parts) > 1 and len(parts[-1]) == 3 else s.replace(",", ".")
    return f"-{s}" if negative else s


# --- individual predicates -------------------------------------------------


def parses_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, str):
        return value.strip().lower() in BOOL_WORDS
    return False


def parses_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return value.is_integer()
    if isinstance(value, Decimal):
        return value == value.to_integral_value()
    if isinstance(value, str):
        cleaned = _clean_number(value)
        if cleaned is None:
            return False
        try:
            return Decimal(cleaned) == Decimal(cleaned).to_integral_value()
        except InvalidOperation:
            return False
    return False


def parses_numeric(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float, Decimal)):
        return True
    if isinstance(value, str):
        cleaned = _clean_number(value)
        if cleaned is None:
            return False
        try:
            Decimal(cleaned)
            return True
        except InvalidOperation:
            return False
    return False


def _parse_temporal(value: Any) -> datetime | date | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        for fmt in DATETIME_FORMATS:
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        for fmt in DATE_FORMATS:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
    return None


def parses_temporal(value: Any) -> bool:
    return _parse_temporal(value) is not None


def _is_midnight(value: Any) -> bool:
    parsed = _parse_temporal(value)
    if parsed is None:
        return False
    if isinstance(parsed, datetime):
        return parsed.time() == time(0, 0)
    return True  # a bare date carries no time at all


def parses_json(value: Any) -> bool:
    if isinstance(value, (dict, list)):
        return True
    if isinstance(value, str):
        text = value.strip()
        if not text or text[0] not in "{[":
            return False
        try:
            json.loads(text)
            return True
        except ValueError:
            return False
    return False


# --- result ----------------------------------------------------------------


@dataclass
class TypeResult:
    data_type: str
    confidence: float
    nullable: bool
    sample_values: list[Any]
    note: str


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _date_only(value: Any) -> Any:
    """Render a date-typed sample as a date, not a midnight timestamp.

    openpyxl hands back datetime objects for date-formatted cells; showing
    '2024-10-02T00:00:00' on the review screen for a column we just called
    `date` undermines the very confidence the screen exists to build.
    """
    parsed = _parse_temporal(value)
    if isinstance(parsed, datetime):
        return parsed.date().isoformat()
    if isinstance(parsed, date):
        return parsed.isoformat()
    return _jsonable(value)


def _samples(values: list[Any], render=_jsonable) -> list[Any]:
    seen: list[Any] = []
    seen_keys: set[str] = set()
    for value in values:
        key = repr(value)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        seen.append(render(value))
        if len(seen) == SAMPLE_VALUE_COUNT:
            break
    return seen


def infer_column_type(values: list[Any]) -> TypeResult:
    """Score candidate types by parse rate; take the most specific that clears
    the threshold, else `text`."""
    non_empty = [v for v in values if not is_blank(v)]
    nullable = len(non_empty) < len(values)

    if not non_empty:
        return TypeResult("text", 0.0, True, [], "Column is entirely empty; defaulted to text.")

    sample = non_empty[:SAMPLE_LIMIT]
    n = len(sample)

    def rate(predicate) -> float:
        return sum(1 for v in sample if predicate(v)) / n

    bool_rate = rate(parses_bool)
    int_rate = rate(parses_integer)
    num_rate = rate(parses_numeric)
    temporal_rate = rate(parses_temporal)
    json_rate = rate(parses_json)

    scanned = f"{n} of {len(non_empty)} non-empty values sampled"

    # Most specific first. Booleans deliberately do not claim 0/1 integer
    # columns -- stealing a real integer column is worse than under-typing a
    # flag, and the review screen can retype it in one click.
    if bool_rate >= AGREEMENT_THRESHOLD:
        return TypeResult("boolean", bool_rate, nullable, _samples(sample),
                          f"boolean: {bool_rate:.0%} of values parsed as true/false ({scanned}).")

    if int_rate >= AGREEMENT_THRESHOLD:
        return TypeResult("integer", int_rate, nullable, _samples(sample),
                          f"integer: {int_rate:.0%} parsed as whole numbers ({scanned}).")

    if num_rate >= AGREEMENT_THRESHOLD:
        return TypeResult("numeric", num_rate, nullable, _samples(sample),
                          f"numeric: {num_rate:.0%} parsed as numbers, "
                          f"{int_rate:.0%} were whole ({scanned}).")

    if temporal_rate >= AGREEMENT_THRESHOLD:
        midnight_rate = sum(1 for v in sample if _is_midnight(v)) / n
        if midnight_rate >= AGREEMENT_THRESHOLD:
            return TypeResult("date", temporal_rate, nullable, _samples(sample, _date_only),
                              f"date: {temporal_rate:.0%} parsed as dates and "
                              f"{midnight_rate:.0%} carried no time ({scanned}).")
        return TypeResult("datetime", temporal_rate, nullable, _samples(sample),
                          f"datetime: {temporal_rate:.0%} parsed as timestamps ({scanned}).")

    if json_rate >= AGREEMENT_THRESHOLD:
        return TypeResult("json", json_rate, nullable, _samples(sample),
                          f"json: {json_rate:.0%} parsed as JSON objects/arrays ({scanned}).")

    best = max(
        [("boolean", bool_rate), ("integer", int_rate), ("numeric", num_rate),
         ("date", temporal_rate), ("json", json_rate)],
        key=lambda kv: kv[1],
    )
    string_rate = sum(1 for v in sample if isinstance(v, str)) / n
    return TypeResult(
        "text",
        string_rate,
        nullable,
        _samples(sample),
        f"text: no type reached {AGREEMENT_THRESHOLD:.0%} agreement "
        f"(closest was {best[0]} at {best[1]:.0%}, {scanned}).",
    )
