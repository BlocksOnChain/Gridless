"""Deterministic primary-key selection."""

import pytest

from inference.keys import choose_primary_key, score_key_candidate
from ingest.analyse import AnalysedColumn


def col(header, data_type="text", *, unique=True, nullable=False, n=10):
    values = [f"{header}{i}" for i in range(n)] if unique else [header] * n
    return AnalysedColumn(
        header=header, letter="A", index=0, values=values, data_type=data_type,
        confidence=1.0, nullable=nullable, sample_values=values[:3], note="",
        distinct_count=n if unique else 1, non_null_count=n,
    )


def test_uniqueness_is_required_not_the_name():
    """A column called 'Customer ID' that repeats is not a key."""
    assert score_key_candidate(col("Customer ID", "integer", unique=False), 0) is None


def test_nullable_column_is_never_a_key():
    assert score_key_candidate(col("Ref", nullable=True), 0) is None


@pytest.mark.parametrize("bad_type", ["numeric", "date", "datetime", "boolean", "json"])
def test_implausible_types_are_excluded(bad_type):
    """A float or timestamp unique across the sample is a coincidence, not a key."""
    assert score_key_candidate(col("Amount", bad_type), 0) is None


def test_name_hint_breaks_a_tie():
    columns = [col("Full Name"), col("Policy No")]
    assert choose_primary_key(columns).header == "Policy No"


def test_leftmost_wins_when_hints_are_equal():
    columns = [col("Order Code"), col("Item Code")]
    assert choose_primary_key(columns).header == "Order Code"


def test_no_candidate_returns_none():
    """A row log with no identifier is a normal, correct None."""
    columns = [col("Region", unique=False), col("Amount", "numeric")]
    assert choose_primary_key(columns) is None


def test_word_boundaries_avoid_false_hints():
    """'video' must not match the 'id' hint, 'encode' must not match 'code'."""
    plain = col("Video")
    hinted = col("Order Id")
    assert score_key_candidate(hinted, 0) > score_key_candidate(plain, 0)


def test_matches_ground_truth_on_every_fixture():
    """The eval measures this too; this pins it so a regression fails fast."""
    import json
    from pathlib import Path

    from ingest.analyse import analyse_workbook

    fixtures = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic"
    if not (fixtures / "clean.xlsx").exists():
        pytest.skip("run scripts/make_fixtures.py first")

    checked = 0
    for expected_path in sorted(fixtures.glob("*.expected.json")):
        truth = json.loads(expected_path.read_text())
        xlsx = expected_path.with_name(expected_path.name.replace(".expected.json", ".xlsx"))
        by_name = {t.name: t for t in analyse_workbook(xlsx)}
        for entity in truth["entities"]:
            table = by_name.get(entity["sheet"])
            assert table is not None, entity["sheet"]
            got = choose_primary_key(table.columns)
            assert (got.header if got else None) == entity["primary_key"], entity["sheet"]
            checked += 1
    assert checked == 15
