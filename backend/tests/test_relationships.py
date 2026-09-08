"""Stage 4, deterministic half: value-overlap relationship detection."""

from pathlib import Path

import pytest

from inference.relationships import find_candidates
from ingest.analyse import analyse_workbook

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic"

pytestmark = pytest.mark.skipif(
    not (FIXTURES / "relational.xlsx").exists(),
    reason="run `uv run python scripts/make_fixtures.py` first",
)


def pairs(name: str):
    return {
        (c.from_table, c.from_column, c.to_table, c.to_column)
        for c in find_candidates(analyse_workbook(FIXTURES / f"{name}.xlsx"))
    }


def test_finds_the_real_foreign_keys():
    found = pairs("relational")
    assert ("Policies", "Customer ID", "Customers", "Customer ID") in found
    assert ("Claims", "Policy No", "Policies", "Policy No") in found


def test_direction_points_at_the_unique_side():
    """The many side is the source. A key that is unique on both sides would be
    one_to_one; here Customers.Customer ID is unique and Policies' is not."""
    found = pairs("relational")
    assert ("Customers", "Customer ID", "Policies", "Customer ID") not in found


def test_non_unique_target_is_rejected():
    """Customers.City is not unique, so nothing may point at it -- this is the
    rule that kills most coincidental overlaps."""
    assert not any(t == ("Customers", "City") for (_, _, *t) in
                   [(a, b, c, d) for (a, b, c, d) in pairs("nightmare")])


def test_no_relationships_between_unrelated_tables():
    assert pairs("two_tables") == set()


def test_low_cardinality_columns_do_not_become_keys():
    """A boolean or near-constant column overlaps almost anything. Nothing in
    `clean` should relate to anything, and it has no second table anyway."""
    assert pairs("clean") == set()


def test_cardinality_is_reported():
    candidates = find_candidates(analyse_workbook(FIXTURES / "relational.xlsx"))
    by_pair = {(c.from_table, c.from_column): c for c in candidates}
    assert by_pair[("Policies", "Customer ID")].cardinality == "many_to_one"
    assert by_pair[("Policies", "Customer ID")].match_rate == 1.0
