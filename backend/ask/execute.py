"""Execute guarded SQL on a powerless connection.

Separate from the Django connection on purpose: nothing about /api/ask should be
able to reach the ORM's session or its privileges. `gridless_ro` has no USAGE on
`public`, SELECT only on the org schemas it has been granted, and every
statement runs inside a read-only transaction with a timeout.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import psycopg
from django.conf import settings

from commitdata.ddl import safe_ident


class ReadOnlyUnavailable(RuntimeError):
    """The read-only role is not reachable, so no query may run."""


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict]
    truncated: bool


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def run_readonly(sql: str, *, schema: str, max_rows: int) -> QueryResult:
    """Run already-guarded SQL. Never call this with unguarded input."""
    try:
        conn = psycopg.connect(settings.READONLY_DSN, connect_timeout=5)
    except Exception as exc:
        raise ReadOnlyUnavailable(
            f"Cannot connect as the read-only role ({exc.__class__.__name__}). "
            f"Is the gridless_ro role present? See docker/postgres/init/."
        ) from exc

    try:
        conn.read_only = True
        with conn.cursor() as cur:
            # SET LOCAL does not accept bound parameters in Postgres, so both
            # values are inlined. Both are trusted by construction: the timeout
            # is an int from settings and the schema is derived from an integer
            # primary key -- neither is user input.
            timeout_ms = int(settings.ASK_STATEMENT_TIMEOUT_MS)
            cur.execute(f"SET LOCAL statement_timeout = {timeout_ms}")
            # search_path so unqualified view names resolve to this org only.
            cur.execute(f'SET LOCAL search_path TO "{safe_ident(schema)}"')
            cur.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(max_rows + 1) if columns else []
        conn.rollback()
    finally:
        conn.close()

    truncated = len(fetched) > max_rows
    rows = [
        {c: _jsonable(v) for c, v in zip(columns, row)} for row in fetched[:max_rows]
    ]
    return QueryResult(columns=columns, rows=rows, truncated=truncated)
