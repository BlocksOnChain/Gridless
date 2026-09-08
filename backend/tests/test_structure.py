"""Stage 1 against the generated fixtures.

These assert the behaviours the fixtures exist to prove: the header is found
under a title block, merged region labels are filled down, two tables on one
sheet are split, and junk columns and totals rows are dropped.
"""

from pathlib import Path

import pytest

from ingest.analyse import analyse_workbook

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "clean.xlsx").exists(),
    reason="run `uv run python scripts/make_fixtures.py` first",
)


def tables(name: str):
    return analyse_workbook(FIXTURES / f"{name}.xlsx")


def test_clean_header_on_row_one():
    (t,) = tables("clean")
    assert t.header_row == 0
    assert t.row_count == 60
    assert [c.header for c in t.columns][:2] == ["Customer ID", "Full Name"]


def test_offset_header_skips_the_title_block():
    (t,) = tables("offset_header")
    assert t.header_row == 4          # 0-based; spreadsheet row 5
    assert t.row_count == 60
    assert t.column("Customer ID") is not None


def test_merged_region_labels_are_filled_down():
    (t,) = tables("merged_cells")
    region = t.column("Region")
    assert region is not None
    # Every data row carries a region, not just the first of each merged block.
    assert all(v not in (None, "") for v in region.values)
    assert set(region.values) == {"West", "Central", "East"}


def test_two_tables_on_one_sheet_are_split():
    result = tables("two_tables")
    assert [t.name for t in result] == ["Reference", "Reference (2)"]
    assert [c.header for c in result[0].columns] == [
        "Product Code", "Product Name", "Base Rate",
    ]
    assert [c.header for c in result[1].columns] == [
        "Branch Code", "Branch City", "Head Count", "Opened",
    ]


def test_junk_columns_and_totals_row_are_dropped():
    (t,) = tables("junk_columns")
    assert [c.header for c in t.columns] == [
        "Policy No", "Customer ID", "Premium", "Notes",
    ]
    assert t.row_count == 40           # the TOTAL row is trimmed
    assert "TOTAL" not in t.column("Policy No").values


def test_nightmare_finds_every_table():
    names = [t.name for t in tables("nightmare")]
    assert names == ["Customers", "Policies", "Reference", "Reference (2)", "By Region"]


def test_leading_blank_column_is_dropped_in_nightmare():
    policies = next(t for t in tables("nightmare") if t.name == "Policies")
    assert policies.columns[0].header == "Policy No"
    assert policies.row_count == 80    # Grand Total row trimmed
