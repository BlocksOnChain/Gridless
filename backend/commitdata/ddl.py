"""Identifier safety and view DDL construction.

Postgres cannot parameterise identifiers, so the generated views are built by
string assembly. That makes this the one place in the codebase where untrusted
text could reach SQL, and it is therefore the one place that gets a hard
allowlist rather than escaping.

Rules:
  * `safe_ident` raises on anything outside [a-z][a-z0-9_]{0,62} or on a
    reserved word. It never "cleans up" a bad identifier silently.
  * `slugify` is the *generator*; `safe_ident` is the *gate*. Every slug passes
    through the gate again at commit time even though the generator produced it,
    because between generation and commit the slug passes through an LLM
    proposal and a user-editable review screen.
"""

from __future__ import annotations

import re
import unicodedata

MAX_IDENT_LEN = 63  # Postgres NAMEDATALEN - 1

_IDENT_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")

# Reserved words that would break the generated DDL, plus the columns the view
# projects itself (`id`) and the physical table's own columns.
RESERVED = {
    "all", "analyse", "analyze", "and", "any", "array", "as", "asc", "authorization",
    "between", "binary", "both", "case", "cast", "check", "collate", "column",
    "constraint", "create", "cross", "current_date", "current_time",
    "current_timestamp", "current_user", "default", "deferrable", "desc",
    "distinct", "do", "else", "end", "except", "false", "for", "foreign", "freeze",
    "from", "full", "grant", "group", "having", "ilike", "in", "initially", "inner",
    "intersect", "into", "is", "isnull", "join", "leading", "left", "like", "limit",
    "localtime", "localtimestamp", "natural", "not", "notnull", "null", "offset",
    "on", "only", "or", "order", "outer", "overlaps", "placing", "primary",
    "references", "returning", "right", "select", "session_user", "similar", "some",
    "symmetric", "table", "then", "to", "trailing", "true", "union", "unique",
    "user", "using", "verbose", "when", "where", "window", "with",
    # projected/physical columns that a data column must not shadow
    "id", "record", "edge", "data", "entity_id", "organization_id",
}

# JSONB text is cast to the real Postgres type here. The cast never comes from
# user input -- only from this fixed map, keyed by our own DataType choices.
DATA_TYPE_TO_PG = {
    "text": "text",
    "integer": "bigint",
    "numeric": "numeric",
    "date": "date",
    "datetime": "timestamptz",
    "boolean": "boolean",
    "json": "jsonb",
}


class UnsafeIdentifier(ValueError):
    """Raised when an identifier would be unsafe to interpolate into SQL."""


def safe_ident(value: str) -> str:
    """Gate. Return `value` unchanged if it is a safe identifier, else raise."""
    if not isinstance(value, str) or not _IDENT_RE.match(value):
        raise UnsafeIdentifier(
            f"{value!r} is not a valid identifier "
            f"(need ^[a-z][a-z0-9_]{{0,62}}$)"
        )
    if value in RESERVED:
        raise UnsafeIdentifier(f"{value!r} is a reserved identifier")
    return value


def slugify(name: str, *, fallback: str = "field") -> str:
    """Generator. Turn arbitrary header text into a candidate identifier.

    Always produces something `safe_ident` accepts. Non-ASCII is transliterated
    rather than dropped so 'Poliçe No' becomes 'police_no', not 'no'.
    """
    text = str(name).strip().lower()
    # Turkish characters do not decompose under NFKD, so map them explicitly.
    for src, dst in (("ı", "i"), ("ğ", "g"), ("ü", "u"), ("ş", "s"), ("ö", "o"), ("ç", "c")):
        text = text.replace(src, dst)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    text = re.sub(r"_+", "_", text)

    if not text:
        text = fallback
    if not text[0].isalpha():
        text = f"{fallback}_{text}"
    text = text[:MAX_IDENT_LEN]
    if text in RESERVED:
        text = f"{text}_col"[:MAX_IDENT_LEN]
    return text


def unique_slug(name: str, taken: set[str], *, fallback: str = "field") -> str:
    """slugify, then disambiguate against slugs already used in this scope."""
    base = slugify(name, fallback=fallback)
    if base not in taken:
        taken.add(base)
        return base
    for n in range(2, 1000):
        suffix = f"_{n}"
        candidate = f"{base[: MAX_IDENT_LEN - len(suffix)]}{suffix}"
        if candidate not in taken:
            taken.add(candidate)
            return candidate
    raise UnsafeIdentifier(f"could not build a unique slug from {name!r}")


def schema_name(organization_id: int) -> str:
    """Postgres schema holding one org's generated views."""
    if not isinstance(organization_id, int) or organization_id <= 0:
        raise UnsafeIdentifier(f"bad organization id {organization_id!r}")
    return f"org_{organization_id}"
