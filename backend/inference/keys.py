"""Deterministic primary-key selection.

Runs with no model. Stage 3's agent usually agrees with it, but this exists for
three reasons: the pipeline must produce a usable proposal without an API key,
the validator needs somewhere to fall back to when it rejects a hallucinated
key, and a measured answer is a better default than no answer.

The rule is uniqueness first, then plausibility. A column that is not unique and
complete is never a candidate no matter what it is called -- a name is a hint,
uniqueness is the evidence.
"""

from __future__ import annotations

import re

from ingest.analyse import AnalysedColumn

# Types that can be a key. A numeric/date column unique across a few hundred
# sampled rows is far more likely a coincidence than an identifier.
KEYABLE_TYPES = {"text", "integer"}

# Name hints, strongest first. Matched on word boundaries so "video" does not
# match "id" and "encode" does not match "code".
NAME_HINTS = (
    (re.compile(r"\b(id|no|code|ref|key|number|num)\b", re.I), 3),
    (re.compile(r"^(id|no|code|ref|key)$", re.I), 4),
    (re.compile(r"\b(name|title|email|slug|sku|isbn|barcode|iban|vkn|tckn)\b", re.I), 1),
)


def score_key_candidate(column: AnalysedColumn, position: int) -> float | None:
    """Score a column's fitness as a primary key, or None if it cannot be one."""
    if not column.is_unique or column.nullable:
        return None
    if column.data_type not in KEYABLE_TYPES:
        return None

    score = 10.0
    for pattern, weight in NAME_HINTS:
        if pattern.search(column.header):
            score += weight
    # Earlier columns are more likely to be the identifier in a spreadsheet.
    score -= position * 0.1
    # A text key is marginally preferred over an integer one: a unique integer
    # column is more often a quantity that happens not to repeat.
    if column.data_type == "text":
        score += 0.5
    return score


def choose_primary_key(columns: list[AnalysedColumn]) -> AnalysedColumn | None:
    """Best key candidate, or None when no column qualifies.

    None is a correct and common answer. Plenty of real sheets are row logs with
    no identifier, and inventing one produces a view built around a lie.
    """
    scored = [
        (score, column)
        for position, column in enumerate(columns)
        if (score := score_key_candidate(column, position)) is not None
    ]
    if not scored:
        return None
    return max(scored, key=lambda pair: pair[0])[1]
