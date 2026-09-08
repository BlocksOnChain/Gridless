"""The table page's chat: change many rows by asking.

The review chat edits the *shape* of a table before it exists. This one edits
the rows of one that does -- committed business data, the thing the company
actually runs on. So the two chats are deliberately not the same agent:

* Its tools cannot write. They compile a plan, count what it matches and show a
  sample, and hand that back. Applying is a separate HTTP call the person makes
  by pressing a button, having read how many of how many rows are involved.
* It can only see and touch one entity. The plan carries no table name; the
  endpoint supplies the entity, so "and while you're there, empty the customer
  table" is not expressible.

Anything not covered by a plan -- aggregates, joins, "which machine produced
most" -- belongs to /api/ask, which is read-only by construction. This agent
says so rather than guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import InferredEntity, Organization
from records import bulk

SYSTEM_PROMPT = """\
You edit one table of a company's data on their behalf. They are not technical: \
talk about rows, records and columns, never about SQL, JSONB or views.

You have three tools. `count_rows` answers questions about how many rows look a \
certain way. `plan_delete` and `plan_update` PREPARE a change -- they do not \
make it. Nothing you do changes any data: the person reads what your plan would \
affect and presses a button to apply it. Never claim you deleted or changed \
anything; say what you have prepared, and how many rows it matches.

Rules:
1. Use the column names you were given. If a tool answers with an error listing \
the real columns, read it and try again with one of those.
2. A request that matches nothing is worth saying plainly: prepare it anyway if \
it is what they asked, and tell them it matches 0 rows.
3. Prepare ONE plan per turn. If a request needs two different changes, prepare \
the first and say what the second would be.
4. Only set match_all when the person clearly means every row in the table. It \
is never the way to satisfy a request that mentions a condition.
5. For a question you cannot answer with a row count -- totals, averages, \
comparisons across tables -- say it belongs on the Ask page, in one sentence.

Answer in at most three short sentences, in the user's own language."""


@dataclass
class TableAssistResult:
    reply: str
    #: The plan the person is being asked to confirm, if the agent prepared one.
    plan: bulk.BulkPlan | None = None
    preview: bulk.BulkPreview | None = None
    #: Read-only counts the agent asked for along the way, for transparency.
    lookups: list[str] = field(default_factory=list)


def describe_table(entity: InferredEntity, total: int) -> str:
    lines = [f'Table "{entity.name}" -- {total} rows. Columns:']
    for f in entity.fields.all():
        samples = [str(v) for v in (f.sample_values or [])[:3] if v is not None]
        line = f'  {f.column_slug} -- "{f.name}" ({f.data_type})'
        if samples:
            line += f", e.g. {', '.join(samples)}"
        lines.append(line)
    lines.append(f"Comparisons available: {', '.join(bulk.OPERATORS)}.")
    return "\n".join(lines)


def _conditions(raw) -> list[bulk.Condition]:
    """Turn the model's loose condition list into typed conditions.

    Accepts the two shapes a model actually produces -- a list of dicts, or a
    single dict -- and rejects everything else with a message it can act on.
    This is also why the tools annotate `conditions` as `list | dict`: LangChain
    validates a tool's arguments against that annotation *before* the function
    runs, so a stricter one would reject the single-dict form as a schema error
    the agent cannot learn from, instead of the correction it can.
    """
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise ValueError(
            "conditions must be a list of {column, operator, value} objects."
        )
    out = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(
                "each condition must be an object with column, operator and value."
            )
        out.append(
            bulk.Condition(
                column=str(item.get("column", "")).strip(),
                operator=str(item.get("operator", "eq")).strip().lower(),
                value=item.get("value"),
            )
        )
    return out


def build_tools(org: Organization, entity: InferredEntity, state: dict):
    """Tools bound to one entity. Nothing else is reachable, and none of them
    write."""
    from langchain_core.tools import tool

    def prepare(op: str, conditions, changes: dict | None, match_all: bool) -> str:
        try:
            plan = bulk.BulkPlan(
                op=op,
                where=_conditions(conditions),
                changes=changes or {},
                match_all=bool(match_all),
            )
        except ValueError as exc:
            return f"ERROR: {exc}"
        try:
            preview = bulk.preview(org, entity, plan)
        except bulk.PlanError as exc:
            return f"ERROR: {exc.detail}"

        if op != bulk.COUNT:
            # One pending plan per turn: the UI confirms one thing at a time,
            # and a second proposal would silently replace the first.
            state["plan"] = plan
            state["preview"] = preview
        else:
            state.setdefault("lookups", []).append(preview.summary)
        return (
            f"PREPARED (nothing has changed yet): {preview.summary} "
            f"Matched {preview.matched} of {preview.total} rows."
        )

    @tool
    def count_rows(conditions: list | dict | None = None) -> str:
        """Count the rows matching some conditions. Changes nothing.

        Args:
            conditions: List of {column, operator, value}. Omit to count all rows.
        """
        return prepare(bulk.COUNT, conditions, None, match_all=not conditions)

    @tool
    def plan_delete(
        conditions: list | dict | None = None, match_all: bool = False
    ) -> str:
        """Prepare a deletion for the person to confirm. Deletes nothing itself.

        Args:
            conditions: List of {column, operator, value} selecting the rows.
            match_all: Only when every row in the table should go.
        """
        return prepare(bulk.DELETE, conditions, None, match_all)

    @tool
    def plan_update(
        changes: dict,
        conditions: list | dict | None = None,
        match_all: bool = False,
    ) -> str:
        """Prepare a change to matching rows, for the person to confirm.

        Args:
            changes: Column name -> new value, e.g. {"city": "Ankara"}.
            conditions: List of {column, operator, value} selecting the rows.
            match_all: Only when every row in the table should change.
        """
        return prepare(bulk.UPDATE, conditions, changes, match_all)

    return [count_rows, plan_delete, plan_update]


def run_table_assist(
    org: Organization,
    entity: InferredEntity,
    total: int,
    message: str,
    history: list[dict] | None = None,
) -> TableAssistResult:
    """One turn. Raises MissingAPIKey when no key is set."""
    from inference.llm.agent import build_agent, final_text, structured_result
    from inference.llm.schemas import AssistAnswer

    state: dict = {}
    agent = build_agent(
        system_prompt=SYSTEM_PROMPT,
        response_schema=AssistAnswer,
        tools=build_tools(org, entity, state),
    )

    messages: list[dict] = []
    for turn in history or []:
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": f"{message}\n\n--- The table ---\n{describe_table(entity, total)}",
        }
    )

    try:
        result = agent.invoke({"messages": messages})
        try:
            reply = structured_result(result, AssistAnswer).reply.strip()
        except Exception:
            # It answered in prose rather than through the structured-output
            # tool. The plan came from the tools either way, so the sentence is
            # worth keeping.
            reply = final_text(result)
    except Exception as exc:
        if state.get("plan") is not None:
            # The plan is intact and harmless -- nothing has been applied -- so
            # it is better offered than thrown away.
            return TableAssistResult(
                reply=(
                    "I prepared the change below but lost the thread after that "
                    f"({exc.__class__.__name__}). Check it before applying it."
                ),
                plan=state.get("plan"),
                preview=state.get("preview"),
                lookups=state.get("lookups", []),
            )
        raise

    if not reply:
        reply = (
            "Here is what I prepared."
            if state.get("plan")
            else "I could not work out which rows you mean. Try naming a column and a value."
        )
    return TableAssistResult(
        reply=reply,
        plan=state.get("plan"),
        preview=state.get("preview"),
        lookups=state.get("lookups", []),
    )
