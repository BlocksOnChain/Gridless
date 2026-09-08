"""Validate generated SQL before it is allowed anywhere near the database.

Two independent layers protect /api/ask, and both must hold:

1. **This guard.** Parses with `sqlglot` -- not regex -- and rejects anything
   that is not exactly one read-only SELECT over this organisation's views.
2. **The database.** Execution happens as `gridless_ro`, which has no USAGE on
   `public` (so `record` and `edge` are unreachable), SELECT only, a statement
   timeout, and a read-only transaction.

The guard exists so users get a clear message instead of a Postgres permission
error, and so a bug in one layer is not a breach. Neither layer is trusted to be
sufficient alone.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

DIALECT = "postgres"

# Statement types that must never run, whatever else the SQL says.
FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.TruncateTable, exp.Grant, exp.Command, exp.Merge,
)


class UnsafeSQL(ValueError):
    """The generated SQL is not a safe, single, read-only SELECT."""


@dataclass
class GuardedSQL:
    sql: str
    tables: list[str]
    limit: int


def guard(
    sql: str, *, allowed_tables: set[str], schema: str, max_rows: int
) -> GuardedSQL:
    """Return runnable SQL, or raise UnsafeSQL with the reason."""
    if not sql or not sql.strip():
        raise UnsafeSQL("No SQL was generated.")

    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except Exception as exc:
        raise UnsafeSQL(f"Could not parse the SQL: {exc}")

    if len(statements) != 1:
        raise UnsafeSQL(
            f"Expected exactly one statement, found {len(statements)}. "
            f"Multiple statements are never allowed."
        )

    tree = statements[0]

    # A CTE wrapper is fine; what it wraps is not necessarily.
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        raise UnsafeSQL(f"Only SELECT is allowed, got {type(tree).__name__.upper()}.")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN):
            raise UnsafeSQL(
                f"{type(node).__name__.upper()} is not allowed; this endpoint is read-only."
            )
        # SELECT ... INTO creates a table.
        if isinstance(node, exp.Select) and node.args.get("into"):
            raise UnsafeSQL("SELECT ... INTO is not allowed.")

    # Every table reference must be one of this org's views. CTE aliases are
    # names defined inside the query, so they are excluded from the check.
    cte_names = {
        cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE) if cte.alias_or_name
    }
    referenced: list[str] = []
    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if not name or name in cte_names:
            continue
        db = (table.db or "").lower()
        if db and db != schema.lower():
            raise UnsafeSQL(
                f"Table {db}.{name} is outside this organization's schema."
            )
        if name not in allowed_tables:
            raise UnsafeSQL(
                f"Unknown table {name!r}. Available: {', '.join(sorted(allowed_tables))}."
            )
        referenced.append(name)

    if not referenced:
        raise UnsafeSQL("The query does not read any of this organization's tables.")

    # Enforce a LIMIT. Rewriting is preferred over rejecting so a good query with
    # a missing LIMIT still answers the question -- but the user is shown the SQL
    # that actually ran, not the one the model wrote.
    limit = tree.args.get("limit")
    effective = max_rows
    if limit is None:
        tree.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))
    else:
        try:
            requested = int(limit.expression.name)
            effective = min(requested, max_rows)
        except (AttributeError, ValueError):
            effective = max_rows
        tree.set("limit", exp.Limit(expression=exp.Literal.number(effective)))

    return GuardedSQL(
        sql=tree.sql(dialect=DIALECT, pretty=True),
        tables=sorted(set(referenced)),
        limit=effective,
    )
