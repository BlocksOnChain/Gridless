"""The /api/ask SQL guard.

This is the layer that decides whether generated SQL is allowed to touch the
database at all. It is deliberately paranoid, and these tests pin that: every
hostile shape must be rejected, and every legitimate read must survive.
"""

import pytest

from ask.guard import UnsafeSQL, guard

ALLOWED = {"customer", "policy", "claim", "product"}
KW = dict(allowed_tables=ALLOWED, schema="org_1", max_rows=500)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT full_name FROM customer",
        "SELECT c.full_name, count(*) FROM customer c JOIN policy p ON p.customer_id = c.customer_id GROUP BY 1",
        "WITH recent AS (SELECT * FROM policy) SELECT count(*) FROM recent",
        "SELECT * FROM org_1.customer LIMIT 10",
        "SELECT * FROM customer UNION SELECT * FROM customer",
        "SELECT * FROM customer c WHERE NOT EXISTS (SELECT 1 FROM policy p WHERE p.customer_id = c.customer_id)",
        "SELECT product, sum(premium) FROM policy GROUP BY product ORDER BY 2 DESC",
    ],
)
def test_legitimate_reads_survive(sql):
    assert guard(sql, **KW).sql


@pytest.mark.parametrize(
    "sql,why",
    [
        ("DROP TABLE record", "ddl"),
        ("SELECT 1; DROP TABLE record", "stacked statements"),
        ("DELETE FROM customer", "delete"),
        ("UPDATE customer SET city = 'x'", "update"),
        ("INSERT INTO customer VALUES (1)", "insert"),
        ("TRUNCATE customer", "truncate"),
        ("SELECT * FROM record", "base table is not a view"),
        ("SELECT * FROM edge", "graph table is not directly readable"),
        ("SELECT * FROM pg_catalog.pg_tables", "catalog"),
        ("SELECT * FROM information_schema.columns", "catalog"),
        ("SELECT * FROM org_8.customer", "another organisation"),
        ("WITH x AS (DELETE FROM customer RETURNING *) SELECT * FROM x", "data-modifying cte"),
        ("SELECT 1", "reads no table of ours"),
        ("this is not sql", "unparseable"),
        ("", "empty"),
        ("   ", "whitespace"),
    ],
)
def test_hostile_shapes_are_blocked(sql, why):
    with pytest.raises(UnsafeSQL):
        guard(sql, **KW)


def test_missing_limit_is_added():
    assert guard("SELECT * FROM customer", **KW).limit == 500


def test_oversized_limit_is_capped():
    assert guard("SELECT * FROM customer LIMIT 99999", **KW).limit == 500


def test_reasonable_limit_is_preserved():
    assert guard("SELECT * FROM customer LIMIT 10", **KW).limit == 10


def test_returned_sql_is_what_would_actually_run():
    """The user is shown the guarded SQL, not the model's draft, so the LIMIT
    they read is the LIMIT that was applied."""
    guarded = guard("SELECT * FROM customer", **KW)
    assert "500" in guarded.sql


def test_referenced_tables_are_reported():
    guarded = guard(
        "SELECT * FROM customer c JOIN policy p ON p.customer_id = c.customer_id", **KW
    )
    assert guarded.tables == ["customer", "policy"]


def test_cte_alias_is_not_mistaken_for_a_table():
    guarded = guard("WITH recent AS (SELECT * FROM policy) SELECT * FROM recent", **KW)
    assert guarded.tables == ["policy"]
