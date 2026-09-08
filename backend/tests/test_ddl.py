"""The identifier guard is the one place untrusted text could reach SQL."""

import pytest

from commitdata.ddl import UnsafeIdentifier, safe_ident, schema_name, slugify, unique_slug


@pytest.mark.parametrize(
    "hostile",
    [
        "select; DROP TABLE record",
        'name"; DROP TABLE record; --',
        "record--",
        "1abc",
        "a" * 64,
        "",
        "Name",          # uppercase
        "na me",         # space
        "café",          # non-ascii
        "id",            # shadows the view's own projected column
        "select",        # reserved
        "data",          # shadows the physical column
        None,
        123,
    ],
)
def test_safe_ident_rejects(hostile):
    with pytest.raises(UnsafeIdentifier):
        safe_ident(hostile)


@pytest.mark.parametrize("ok", ["customer", "policy_no", "a", "x1", "a" * 63])
def test_safe_ident_accepts(ok):
    assert safe_ident(ok) == ok


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Customer Name", "customer_name"),
        ("Poliçe No", "police_no"),
        ("Ürün Adı", "urun_adi"),
        ("  Total  ", "total"),
        ("2024 Sales", "field_2024_sales"),
        ("id", "id_col"),
        ("select", "select_col"),
        ("", "field"),
        ("!!!", "field"),
    ],
)
def test_slugify(raw, expected):
    assert slugify(raw) == expected


def test_slugify_output_always_passes_the_gate():
    for raw in ["Customer Name", "!!!", "id", "2024", "Ürün", "a" * 200, "DROP TABLE x"]:
        safe_ident(slugify(raw))


def test_unique_slug_disambiguates():
    taken: set[str] = set()
    assert [unique_slug(n, taken) for n in ("Name", "Name", "Name")] == [
        "name", "name_2", "name_3",
    ]


def test_schema_name_rejects_non_positive():
    assert schema_name(7) == "org_7"
    for bad in (0, -1, "1", None):
        with pytest.raises(UnsafeIdentifier):
            schema_name(bad)
