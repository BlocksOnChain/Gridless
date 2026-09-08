"""POST /api/ask — natural language over the committed data.

The agent gets the schema, a `run_sql` tool that is guarded on every call, and
the `text-to-sql` skill. It can iterate: write SQL, see the rows, correct itself.

Every number it reports is one it actually read, and the SQL is returned with
the answer so the user can audit it. That is the whole contract of this endpoint
-- never present a number the user cannot check.
"""

from __future__ import annotations

import json

from asgiref.sync import sync_to_async
import msgspec

from django.conf import settings
from django_bolt import BoltAPI
from django_bolt.exceptions import BadRequest, UnprocessableEntity
from langchain_core.tools import tool

from ask.execute import ReadOnlyUnavailable, run_readonly
from ask.guard import UnsafeSQL, guard
from ask.schema_prompt import allowed_tables, describe_schema
from commitdata.ddl import schema_name
from core.deps import OrgHeader, offload, resolve_org
from core.models import Organization

api = BoltAPI()

SYSTEM_PROMPT = """\
You answer questions about a company's data by querying it.

Use the `text-to-sql` skill. Read it before writing SQL -- it describes the view \
schema, exactly what the guard rejects, and how to phrase the answer.

Workflow:
1. Look at the schema you are given.
2. Call `run_sql` with a single read-only SELECT.
3. If it returns an error, read the error and fix the SQL. The guard's messages \
say precisely what was wrong.
4. When you have the rows, return the final SQL and a one-paragraph answer.

Never state a number that is not in the rows you were returned. If the result is \
empty, say so plainly and say what you looked for."""


class AskRequest(msgspec.Struct):
    question: str


class AskResponse(msgspec.Struct):
    question: str
    answer: str
    sql: str
    columns: list[str]
    rows: list[dict]
    row_count: int
    truncated: bool
    attempts: int


def _build_tool(org: Organization, state: dict):
    schema = schema_name(org.pk)
    tables = allowed_tables(org)
    max_rows = settings.ASK_MAX_ROWS

    @tool
    def run_sql(sql: str) -> str:
        """Run one read-only SELECT against this organization's views.

        Args:
            sql: A single SELECT statement. No other statement type is permitted.
        """
        state["attempts"] += 1
        try:
            guarded = guard(sql, allowed_tables=tables, schema=schema, max_rows=max_rows)
        except UnsafeSQL as exc:
            return f"REJECTED: {exc}"
        try:
            result = run_readonly(guarded.sql, schema=schema, max_rows=max_rows)
        except ReadOnlyUnavailable as exc:
            return f"UNAVAILABLE: {exc}"
        except Exception as exc:
            return f"ERROR: {exc.__class__.__name__}: {exc}"

        state["last"] = (guarded.sql, result)
        preview = result.rows[:20]
        return json.dumps(
            {
                "sql": guarded.sql,
                "row_count": len(result.rows),
                "truncated": result.truncated,
                "columns": result.columns,
                "rows": preview,
                "note": "Showing the first 20 rows." if len(result.rows) > 20 else "",
            },
            default=str,
        )

    return run_sql


@offload
def _ask(org: Organization, question: str) -> AskResponse:
    from inference.llm.agent import MissingAPIKey, build_agent, structured_result
    from inference.llm.schemas import SqlAnswer

    tables = allowed_tables(org)
    if not tables:
        raise UnprocessableEntity(
            detail="Nothing has been committed yet, so there is nothing to ask about."
        )

    state: dict = {"attempts": 0, "last": None}
    try:
        agent = build_agent(
            system_prompt=SYSTEM_PROMPT,
            response_schema=SqlAnswer,
            tools=[_build_tool(org, state)],
        )
    except MissingAPIKey as exc:
        raise UnprocessableEntity(detail=str(exc))

    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Question: {question}\n\n"
                        f"Schema:\n```sql\n{describe_schema(org)}\n```"
                    ),
                }
            ]
        }
    )
    answer = structured_result(result, SqlAnswer)

    if state["last"] is None:
        # The model answered without ever running a query, so there is nothing to
        # audit. Refusing is better than presenting an unverifiable number.
        raise UnprocessableEntity(
            detail="No query was executed, so the answer could not be verified. "
                   "Try rephrasing the question.",
            extra={"proposed_sql": answer.sql, "attempts": state["attempts"]},
        )

    sql, query = state["last"]
    return AskResponse(
        question=question, answer=answer.answer.strip(), sql=sql,
        columns=query.columns, rows=query.rows, row_count=len(query.rows),
        truncated=query.truncated, attempts=state["attempts"],
    )


@api.post("/api/ask")
async def ask(payload: AskRequest, x_org_id: OrgHeader = None) -> AskResponse:
    org = await resolve_org(x_org_id)
    question = payload.question.strip()
    if not question:
        raise BadRequest(detail="Ask a question.")
    return await _ask(org, question)
