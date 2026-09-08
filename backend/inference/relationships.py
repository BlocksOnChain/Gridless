"""Stage 4, first half — deterministic relationship detection.

For every pair of columns across different tables, measure what fraction of the
source column's distinct values actually exist in the target column. Propose a
relationship only when that overlap is high *and* the target side is a candidate
key.

This runs before any LLM touches the problem, and the LLM is never allowed to
add to the result -- only to name and rank what was measured here. Letting a
model propose joins from column names alone is precisely how hallucinated joins
get into a schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from ingest.analyse import AnalysedColumn, AnalysedTable
from ingest.reader import is_blank

# A source column must clear this fraction of its distinct values being present
# in the target before we will even consider it.
MIN_OVERLAP = 0.80
# Below this many distinct values a column is an enum, not a key: a three-value
# status column overlaps almost anything by coincidence.
MIN_DISTINCT_SOURCE = 3
# Types that never form a foreign key in practice. Excluding them removes the
# largest class of coincidental matches.
EXCLUDED_TYPES = {"boolean", "json"}
# Dates overlap across unrelated tables constantly (two sheets covering the same
# year share every date), so they are excluded as key candidates too.
TEMPORAL_TYPES = {"date", "datetime"}


def _norm(value: Any) -> str | None:
    """Canonical comparison key.

    1000 (int), 1000.0 (float) and "1000" (text) are the same identifier as far
    as a spreadsheet user is concerned, and cross-sheet keys routinely differ in
    exactly that way.
    """
    if is_blank(value):
        return None
    if isinstance(value, bool):
        return f"b:{value}"
    if isinstance(value, (datetime, date)):
        return f"d:{value.isoformat()}"
    if isinstance(value, (int, float, Decimal)):
        try:
            d = Decimal(str(value)).normalize()
            return f"n:{d:f}"
        except InvalidOperation:
            return f"s:{value}"
    text = str(value).strip()
    if not text:
        return None
    try:
        d = Decimal(text).normalize()
        return f"n:{d:f}"
    except InvalidOperation:
        return f"s:{text.casefold()}"


@dataclass
class ColumnStats:
    table: AnalysedTable
    column: AnalysedColumn
    distinct: set[str]
    non_null: int

    @property
    def is_candidate_key(self) -> bool:
        """Unique and complete: every row has a value and no value repeats."""
        return (
            self.non_null > 0
            and len(self.distinct) == self.non_null
            and self.non_null == len(self.column.values)
        )


@dataclass
class RelationshipCandidate:
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    match_rate: float
    cardinality: str
    distinct_source: int
    distinct_target: int
    rationale: str

    @property
    def key(self) -> tuple[str, str, str, str]:
        return (self.from_table, self.from_column, self.to_table, self.to_column)


def _stats(tables: list[AnalysedTable]) -> list[ColumnStats]:
    out: list[ColumnStats] = []
    for table in tables:
        for column in table.columns:
            if column.data_type in EXCLUDED_TYPES:
                continue
            keys = [k for k in (_norm(v) for v in column.values) if k is not None]
            out.append(
                ColumnStats(
                    table=table,
                    column=column,
                    distinct=set(keys),
                    non_null=len(keys),
                )
            )
    return out


def _compatible(a: AnalysedColumn, b: AnalysedColumn) -> bool:
    """Numeric-ish and text-ish mix freely (a key written as text on one sheet
    and as a number on another is the normal case); temporal never mixes."""
    a_temporal = a.data_type in TEMPORAL_TYPES
    b_temporal = b.data_type in TEMPORAL_TYPES
    return a_temporal == b_temporal


def find_candidates(tables: list[AnalysedTable]) -> list[RelationshipCandidate]:
    """Every measured relationship candidate, best match rate first."""
    stats = _stats(tables)
    candidates: dict[tuple[str, str, str, str], RelationshipCandidate] = {}

    for source in stats:
        if len(source.distinct) < MIN_DISTINCT_SOURCE:
            continue
        if source.column.data_type in TEMPORAL_TYPES:
            continue

        for target in stats:
            if target.table is source.table:
                continue
            if not target.is_candidate_key:
                continue
            if target.column.data_type in TEMPORAL_TYPES:
                continue
            if not _compatible(source.column, target.column):
                continue

            overlap = len(source.distinct & target.distinct) / len(source.distinct)
            if overlap < MIN_OVERLAP:
                continue

            source_unique = source.is_candidate_key
            cardinality = "one_to_one" if source_unique else "many_to_one"

            candidate = RelationshipCandidate(
                from_table=source.table.name,
                from_column=source.column.header,
                to_table=target.table.name,
                to_column=target.column.header,
                match_rate=round(overlap, 4),
                cardinality=cardinality,
                distinct_source=len(source.distinct),
                distinct_target=len(target.distinct),
                rationale=(
                    f"{overlap:.0%} of the {len(source.distinct)} distinct values in "
                    f"{source.table.name}.{source.column.header} exist in "
                    f"{target.table.name}.{target.column.header}, which is unique "
                    f"across all {target.non_null} of its rows."
                ),
            )

            # A pair that qualifies in both directions is one relationship, not
            # two. Keep the direction whose source is non-unique (the many side);
            # if both are unique it is genuinely 1:1 and either direction is
            # equivalent, so keep the first seen deterministically.
            reverse = (
                candidate.to_table,
                candidate.to_column,
                candidate.from_table,
                candidate.from_column,
            )
            if reverse in candidates:
                if source_unique:
                    continue
                del candidates[reverse]
            candidates[candidate.key] = candidate

    return sorted(
        candidates.values(),
        key=lambda c: (-c.match_rate, -c.distinct_source, c.from_table, c.from_column),
    )
