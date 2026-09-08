"""Stage 2: type inference by parse rate."""

from datetime import date, datetime

import pytest

from ingest.typeinfer import infer_column_type


@pytest.mark.parametrize(
    "values,expected",
    [
        ([1, 2, 3, "4"], "integer"),
        ([1.5, 2.25, 3.0], "numeric"),
        (["$1,234.50", "$2,000.00"], "numeric"),
        (["₺3.450,75", "₺1.200,00"], "numeric"),          # European decimal comma
        (["(1,200.50)", "3,400.25"], "numeric"),          # accounting negative
        ([datetime(2024, 1, 5), date(2024, 2, 9), "2024-03-01"], "date"),
        ([datetime(2024, 1, 5, 13, 30), datetime(2024, 2, 9, 8, 15)], "datetime"),
        (["Yes", "No", True, "false"], "boolean"),
        (['{"a": 1}', "[1, 2]"], "json"),
        (["abc", "def"], "text"),
        (["abc", 1, "2024-01-01", "zz"], "text"),          # nothing reaches 90%
    ],
)
def test_types(values, expected):
    assert infer_column_type(values).data_type == expected


def test_integers_are_not_stolen_by_boolean():
    """0/1 columns stay integer: mis-typing a real key as a flag is worse than
    under-typing a flag, and the review screen can retype it in one click."""
    assert infer_column_type([0, 1, 1, 0, 1]).data_type == "integer"


def test_empty_column():
    result = infer_column_type([None, "", None])
    assert result.data_type == "text"
    assert result.confidence == 0.0
    assert result.nullable is True


def test_nullable_detection():
    assert infer_column_type([1, None, 3]).nullable is True
    assert infer_column_type([1, 2, 3]).nullable is False


def test_date_samples_render_without_a_time():
    result = infer_column_type([datetime(2024, 10, 2), datetime(2022, 5, 6)])
    assert result.data_type == "date"
    assert result.sample_values == ["2024-10-02", "2022-05-06"]


def test_accounting_negative_parses():
    from ingest.typeinfer import _clean_number

    assert _clean_number("(1,200.00)") == "-1200.00"


def test_whole_accounting_values_are_integer_not_numeric():
    """Not a bug: '(1,200.00)' and '3,400.00' are both whole, so integer is the
    most specific correct type."""
    assert infer_column_type(["(1,200.00)", "3,400.00"]).data_type == "integer"


def test_confidence_is_the_winning_parse_rate():
    # 9 integers and 1 string -> 90% integer, which clears the threshold.
    assert infer_column_type([*range(9), "x"]).data_type == "integer"
    # 8 of 10 does not.
    assert infer_column_type([*range(8), "x", "y"]).data_type == "text"
