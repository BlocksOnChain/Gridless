"""Stages 1 + 2 as one pure, DB-free call.

Both the persisting pipeline (`ingest.pipeline`) and the eval harness
(`scripts/eval.py`) go through here, so the number eval prints is produced by
exactly the code the API runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ingest.reader import is_blank, read_workbook
from ingest.structure import detect_tables
from ingest.typeinfer import infer_column_type


def _key(value: Any) -> str:
    """Comparison key for counting distinct values.

    Deliberately the same normalisation relationship detection uses, so a column
    reported unique here is the same column that qualifies as a foreign-key
    target there.
    """
    from inference.relationships import _norm

    return _norm(value) or ""


@dataclass
class AnalysedColumn:
    header: str
    letter: str
    index: int
    values: list[Any]
    data_type: str
    confidence: float
    nullable: bool
    sample_values: list[Any]
    note: str
    # Measured here rather than re-derived downstream: primary-key validation
    # and relationship detection both need them, and both must agree.
    distinct_count: int = 0
    non_null_count: int = 0

    @property
    def is_unique(self) -> bool:
        """Unique and complete -- every row has a value and no value repeats."""
        return (
            self.non_null_count > 0
            and self.distinct_count == self.non_null_count
            and self.non_null_count == len(self.values)
        )


@dataclass
class AnalysedTable:
    name: str
    sheet_index: int
    header_row: int          # 0-based
    first_data_row: int
    last_data_row: int
    range_a1: str
    header_score: float
    raw_row_count: int
    columns: list[AnalysedColumn]
    notes: list[str] = field(default_factory=list)

    @property
    def row_count(self) -> int:
        return max(0, self.last_data_row - self.first_data_row + 1)

    def column(self, header: str) -> AnalysedColumn | None:
        return next((c for c in self.columns if c.header == header), None)


def analyse_workbook(path: str | Path) -> list[AnalysedTable]:
    """One .xlsx in, one AnalysedTable per detected table out."""
    tables: list[AnalysedTable] = []
    for raw in read_workbook(path):
        for detected in detect_tables(raw):
            columns: list[AnalysedColumn] = []
            type_notes: list[str] = []
            for column in detected.columns:
                result = infer_column_type(column.values)
                keys = [_key(v) for v in column.values if not is_blank(v)]
                columns.append(
                    AnalysedColumn(
                        header=column.header,
                        letter=column.letter,
                        index=column.index,
                        values=column.values,
                        data_type=result.data_type,
                        confidence=result.confidence,
                        nullable=result.nullable,
                        sample_values=result.sample_values,
                        note=result.note,
                        distinct_count=len(set(keys)),
                        non_null_count=len(keys),
                    )
                )
                type_notes.append(f"  {column.letter} {column.header!r} -> {result.note}")

            tables.append(
                AnalysedTable(
                    name=detected.name,
                    sheet_index=detected.sheet_index,
                    header_row=detected.header_row,
                    first_data_row=detected.first_data_row,
                    last_data_row=detected.last_data_row,
                    range_a1=detected.range_a1,
                    header_score=detected.header_score,
                    raw_row_count=raw.n_rows,
                    columns=columns,
                    notes=detected.notes
                    + [
                        f"Typed {len(columns)} column(s) over "
                        f"{detected.row_count} data row(s):\n" + "\n".join(type_notes)
                    ],
                )
            )
    return tables
