"""Bulk edits over one entity's committed records.

"Remove all the rows where Total (KG) is 0" is a reasonable thing to ask for and
an unreasonable thing to do by hand eighty times. It is also irreversible, so
this module is built around two rules:

**The model never writes SQL.** It fills in a *plan* -- an operation, a list of
conditions, and for an update the new values -- and this module turns that into
parameterised SQL. Every column name in a plan is matched against the entity's real
fields and every value is coerced by that field's declared type, so a
hallucinated column cannot reach the database: it is rejected with the list of
columns that do exist, which the agent can then read and correct itself against.

**Nothing destructive happens without a human confirming it.** Proposing and
applying are separate calls. A proposal reports how many of how many rows match
and shows a sample of them, which is the only honest way to present "delete 12
records" to someone whose data it is. An agent that could delete rows in the
same turn it decided to would be a worse product than one that cannot.

Conditions are evaluated against the *typed view*, not against the JSONB, so
`total_kg = 0` is a numeric comparison rather than a string one. Rows are then
addressed by id: the view is the only thing that knows how to read the column,
and `record` is the only thing that can be written.
"""

from __future__ import annotations

from typing import Any

import msgspec
from django.db import connection, transaction
from django_bolt.exceptions import BadRequest

from commitdata.ddl import safe_ident, schema_name
from core.models import InferredEntity, Organization

#: How many matched rows a proposal shows. Enough to recognise them, few enough
#: to read before clicking a button that cannot be undone.
SAMPLE_ROWS = 5

DELETE = "delete"
UPDATE = "update"
COUNT = "count"
OPERATIONS = (DELETE, UPDATE, COUNT)

#: SQL fragment per operator. `%s` is bound, never interpolated.
COMPARISONS = {
    "eq": "{col} = %s",
    "ne": "{col} IS DISTINCT FROM %s",
    "lt": "{col} < %s",
    "lte": "{col} <= %s",
    "gt": "{col} > %s",
    "gte": "{col} >= %s",
    "contains": "{col}::text ILIKE %s",
    "starts_with": "{col}::text ILIKE %s",
}
#: Operators that take no value.
UNARY = {
    "is_empty": "({col} IS NULL OR {col}::text = '')",
    "is_not_empty": "({col} IS NOT NULL AND {col}::text <> '')",
}
OPERATORS = tuple(COMPARISONS) + tuple(UNARY)


class Condition(msgspec.Struct):
    column: str
    operator: str
    value: object | None = None


class BulkPlan(msgspec.Struct):
    """What to do, to which rows. Travels server -> browser -> server.

    It is re-validated on the way back in: the confirmation step must not be
    the only thing standing between a plan and the database.
    """

    op: str
    where: list[Condition] = msgspec.field(default_factory=list)
    #: For `update` only: column slug -> new value.
    changes: dict = msgspec.field(default_factory=dict)
    #: Required to act on every row. An empty `where` is otherwise refused,
    #: because "delete everything" is too easy a thing to arrive at by mistake.
    match_all: bool = False


class BulkPreview(msgspec.Struct):
    summary: str
    matched: int
    total: int
    columns: list[str]
    sample: list[dict]
    #: True when the plan touches every row in the table.
    affects_everything: bool


class BulkResult(msgspec.Struct):
    op: str
    affected: int
    summary: str


class PlanError(BadRequest):
    """The plan cannot be run. The message is written to be read by the agent."""


def _fields(entity: InferredEntity) -> dict[str, Any]:
    return {f.column_slug: f for f in entity.fields.all()}


def _column_help(entity: InferredEntity) -> str:
    fields = entity.fields.all()
    return ", ".join(f'{f.column_slug} ("{f.name}", {f.data_type})' for f in fields)


def _view(org: Organization, entity: InferredEntity) -> str:
    return f'"{safe_ident(schema_name(org.pk))}"."{safe_ident(entity.table_slug)}"'


def _coerce(value: Any, data_type: str, *, column: str) -> Any:
    """Reuse the CRUD coercion so a bulk edit cannot write a value a single-row
    edit would have rejected."""
    from records.api import _coerce as coerce_one

    try:
        return coerce_one(value, data_type)
    except BadRequest as exc:
        raise PlanError(
            detail=f"{column}: {exc.detail}",
            extra={"column": column, "expected_type": data_type},
        )


def build_where(
    entity: InferredEntity, conditions: list[Condition]
) -> tuple[str, list[Any]]:
    """Compile conditions into one parameterised WHERE clause."""
    fields = _fields(entity)
    clauses: list[str] = ["TRUE"]
    params: list[Any] = []

    for condition in conditions:
        field = fields.get(condition.column)
        if field is None:
            raise PlanError(
                detail=(
                    f"There is no column {condition.column!r} in "
                    f"{entity.name!r}. The columns are: {_column_help(entity)}."
                ),
                extra={"available": sorted(fields)},
            )
        if condition.operator not in OPERATORS:
            raise PlanError(
                detail=(
                    f"{condition.operator!r} is not a comparison I can make. "
                    f"Use one of: {', '.join(OPERATORS)}."
                )
            )

        col = f'"{safe_ident(field.column_slug)}"'
        if condition.operator in UNARY:
            clauses.append(UNARY[condition.operator].format(col=col))
            continue

        if condition.operator in ("contains", "starts_with"):
            text = str(condition.value if condition.value is not None else "")
            pattern = f"%{text}%" if condition.operator == "contains" else f"{text}%"
            clauses.append(COMPARISONS[condition.operator].format(col=col))
            params.append(pattern)
            continue

        value = _coerce(condition.value, field.data_type, column=field.column_slug)
        if value is None:
            # `= NULL` is never true; the caller meant "has no value".
            raise PlanError(
                detail=(
                    f"To match empty values in {field.column_slug!r}, use the "
                    f"is_empty operator rather than comparing with nothing."
                )
            )
        clauses.append(COMPARISONS[condition.operator].format(col=col))
        params.append(value)

    return " AND ".join(clauses), params


def validate(entity: InferredEntity, plan: BulkPlan) -> tuple[str, list[Any], dict]:
    """Everything a plan needs checked before it is allowed near the database."""
    if plan.op not in OPERATIONS:
        raise PlanError(
            detail=f"{plan.op!r} is not an operation. Use one of: {', '.join(OPERATIONS)}."
        )
    if not plan.where and not plan.match_all:
        raise PlanError(
            detail=(
                "This plan has no conditions, so it would affect every row. If "
                "that is really the intent, set match_all."
            )
        )

    where, params = build_where(entity, plan.where)

    changes: dict = {}
    if plan.op == UPDATE:
        if not plan.changes:
            raise PlanError(detail="An update needs at least one column to change.")
        fields = _fields(entity)
        for column, value in plan.changes.items():
            field = fields.get(column)
            if field is None:
                raise PlanError(
                    detail=(
                        f"There is no column {column!r} in {entity.name!r}. "
                        f"The columns are: {_column_help(entity)}."
                    ),
                    extra={"available": sorted(fields)},
                )
            changes[column] = _coerce(value, field.data_type, column=column)
    elif plan.changes:
        raise PlanError(detail=f"A {plan.op} does not take column changes.")

    return where, params, changes


def describe(entity: InferredEntity, plan: BulkPlan, matched: int, total: int) -> str:
    """One sentence, in the words of someone who owns the data."""
    fields = _fields(entity)

    def label(slug: str) -> str:
        field = fields.get(slug)
        return f'"{field.name}"' if field else slug

    if plan.where:
        parts = []
        for c in plan.where:
            if c.operator in UNARY:
                parts.append(f"{label(c.column)} {c.operator.replace('_', ' ')}")
            else:
                parts.append(f"{label(c.column)} {c.operator} {c.value!r}")
        scope = " and ".join(parts)
    else:
        scope = "every row"

    noun = "record" if matched == 1 else "records"
    if plan.op == DELETE:
        return f"Delete {matched} of {total} {noun} where {scope}. This cannot be undone."
    if plan.op == UPDATE:
        changes = ", ".join(f"{label(k)} → {v!r}" for k, v in plan.changes.items())
        return f"Set {changes} on {matched} of {total} {noun} where {scope}."
    return f"{matched} of {total} {noun} match {scope}."


def preview(org: Organization, entity: InferredEntity, plan: BulkPlan) -> BulkPreview:
    """Count and show what the plan would touch. Reads only."""
    where, params, _ = validate(entity, plan)
    view = _view(org, entity)

    with connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {view}")
        total = cur.fetchone()[0]
        cur.execute(f"SELECT count(*) FROM {view} WHERE {where}", params)
        matched = cur.fetchone()[0]
        cur.execute(
            f"SELECT * FROM {view} WHERE {where} ORDER BY id LIMIT %s",
            [*params, SAMPLE_ROWS],
        )
        names = [d[0] for d in cur.description]
        from records.api import _row_jsonable

        sample = [dict(zip(names, _row_jsonable(r))) for r in cur.fetchall()]

    return BulkPreview(
        summary=describe(entity, plan, matched, total),
        matched=matched,
        total=total,
        columns=names,
        sample=sample,
        affects_everything=bool(total) and matched == total,
    )


def delete_edges_referencing(cur, subquery: str, params: list[Any]) -> int:
    """Remove graph edges pointing at the rows a delete is about to remove.

    `Edge.from_record` / `to_record` are declared `on_delete=CASCADE`, but Django
    implements cascade in Python during an ORM delete -- the database constraint
    itself is a plain deferred foreign key. Committed data is written and deleted
    with raw SQL by design (the schema is not known at build time), so nothing
    cascades on its own: the DELETE succeeds, and then the transaction fails at
    COMMIT with a foreign key violation naming a table the user has never heard
    of.

    Deleting the edges first is what the ORM would have done. An edge exists only
    to say "this row references that one", so it cannot outlive either end.
    """
    cur.execute(
        f"DELETE FROM edge WHERE from_record_id IN ({subquery}) "
        f"OR to_record_id IN ({subquery})",
        [*params, *params],
    )
    return cur.rowcount


@transaction.atomic
def apply(org: Organization, entity: InferredEntity, plan: BulkPlan) -> BulkResult:
    """Run the plan. One transaction: it happens completely or not at all."""
    where, params, changes = validate(entity, plan)
    if plan.op == COUNT:
        raise PlanError(detail="Counting changes nothing; there is nothing to apply.")

    view = _view(org, entity)
    # Rows are selected through the view (which knows the column types) and
    # written by id in `record` (the only writable table). The organization and
    # entity guards are redundant given the view, and kept anyway: this is a
    # statement that deletes rows.
    subquery = f"SELECT id FROM {view} WHERE {where}"

    with connection.cursor() as cur:
        if plan.op == DELETE:
            delete_edges_referencing(cur, subquery, params)
            cur.execute(
                f"DELETE FROM record WHERE organization_id = %s AND entity_id = %s "
                f"AND id IN ({subquery})",
                [org.pk, entity.pk, *params],
            )
        else:
            import json

            cur.execute(
                f"UPDATE record SET data = data || %s::jsonb, updated_at = now() "
                f"WHERE organization_id = %s AND entity_id = %s AND id IN ({subquery})",
                [json.dumps(changes), org.pk, entity.pk, *params],
            )
        affected = cur.rowcount

    verb = "Deleted" if plan.op == DELETE else "Updated"
    noun = "record" if affected == 1 else "records"
    return BulkResult(
        op=plan.op, affected=affected, summary=f"{verb} {affected} {noun}."
    )
