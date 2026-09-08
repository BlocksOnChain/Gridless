"""A header merged across columns.

Excel draws one column wide; `ingest.reader` forward-fills merged ranges, so it
arrives as two columns with the same header and the same values. Left alone,
everything downstream works to tell the halves apart -- the naming stage
produces "Machine Capacity" and "Machine Capacity 1", the slug generator
produces two slugs, and the committed table has two columns holding the same
data that a person has to edit twice.

These grids are built in memory rather than from a fixture, so the file is about
the rule and nothing else.
"""

from ingest.reader import RawSheet
from ingest.structure import detect_tables


def _sheet(grid) -> RawSheet:
    width = max(len(r) for r in grid)
    return RawSheet(
        name="Sheet1", index=0, grid=[list(r) + [None] * (width - len(r)) for r in grid]
    )


def test_a_header_merged_across_two_columns_becomes_one_column():
    """Excel draws one column wide; the reader fills both halves.

    Left alone, everything downstream works to tell the halves apart -- the
    naming stage produces "Machine Capacity" and "Machine Capacity 1", and the
    committed table has two columns holding the same data that a person has to
    edit twice.
    """
    grid = [
        ["Machine", "Product", "Product", "Total"],
        ["HAT-1", "3666-2", "3666-2", 6900],
        ["HAT-2", "349-50G", "349-50G", 7700],
    ]
    table = detect_tables(_sheet(grid))[0]
    assert [c.header for c in table.columns] == ["Machine", "Product", "Total"]
    assert any("Folded 1 duplicate" in n for n in table.notes)


def test_a_broken_merge_still_folds_and_keeps_the_value():
    """Merit's sheet is merged on 44 rows and broken on one, where someone
    typed into the left half alone. Cell-for-cell equality would keep two
    columns because of that single cell, and folding must not lose it."""
    grid = [
        ["Machine", "Product", "Product", "Total"],
        ["HAT-1", "3666-2", "3666-2", 6900],
        ["HAT-2", "349-50G", "349-50G", 7700],
        ["HAT-3", "249-50G", "249-50G", 5250],
        # The merge is broken here: someone typed into the left half alone.
        ["HAT-4", "504", None, 1290],
    ]
    table = detect_tables(_sheet(grid))[0]
    assert [c.header for c in table.columns] == ["Machine", "Product", "Total"]
    assert [str(v) for v in table.columns[1].values] == [
        "3666-2",
        "349-50G",
        "249-50G",
        "504",
    ]


def test_an_empty_merged_pair_folds_to_one_column():
    grid = [
        ["Machine", "Capacity", "Capacity"],
        ["HAT-1", None, None],
        ["HAT-2", None, None],
    ]
    table = detect_tables(_sheet(grid))[0]
    assert [c.header for c in table.columns] == ["Machine", "Capacity"]


def test_two_columns_that_disagree_are_kept_apart():
    """Same lazy header, different data: two real columns."""
    grid = [
        ["Machine", "Shift", "Shift"],
        ["HAT-1", 700, 3000],
        ["HAT-2", 1700, 3100],
    ]
    table = detect_tables(_sheet(grid))[0]
    assert len(table.columns) == 3


def test_two_sparse_columns_that_never_overlap_are_kept_apart():
    """Nothing here is evidence that they are the same column."""
    grid = [
        ["Machine", "Note", "Note"],
        ["HAT-1", "left", None],
        ["HAT-2", None, "right"],
    ]
    table = detect_tables(_sheet(grid))[0]
    assert len(table.columns) == 3


def test_identical_values_under_different_headers_are_not_folded():
    grid = [
        ["Machine", "Active", "Billable"],
        ["HAT-1", "Yes", "Yes"],
        ["HAT-2", "Yes", "Yes"],
    ]
    table = detect_tables(_sheet(grid))[0]
    assert len(table.columns) == 3


def test_non_adjacent_columns_are_not_folded():
    grid = [
        ["Product", "Machine", "Product"],
        ["3666-2", "HAT-1", "3666-2"],
        ["349-50G", "HAT-2", "349-50G"],
    ]
    table = detect_tables(_sheet(grid))[0]
    assert len(table.columns) == 3
