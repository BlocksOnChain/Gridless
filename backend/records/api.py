"""Runtime CRUD over the generated views.

No ORM abstraction over committed data, by design: the schema is not known at
build time. Every query here is raw parameterised SQL.

Identifiers cannot be parameterised, so the only identifiers that ever reach a
query string are (a) the org's own schema name, derived from an integer primary
key, and (b) column and table slugs looked up from `InferredEntity` /
`InferredField` rows and re-checked with `safe_ident`. A slug that arrives in a
query string or request body is matched against that set -- it is never
interpolated directly.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from asgiref.sync import sync_to_async
from django.db import connection, transaction
import msgspec

from django_bolt import BoltAPI
from django_bolt.exceptions import BadRequest, NotFound, UnprocessableEntity
from django_bolt.param_functions import Body, Query

from commitdata.ddl import DATA_TYPE_TO_PG, safe_ident, schema_name
from core.deps import OrgHeader, offload, resolve_org
from core.models import InferredEntity, Organization, WorkbookStatus
from records import bulk

api = BoltAPI()

MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50

# Columns every generated view projects in addition to the entity's own. They
# are filterable and sortable like any other, but they are not user data and so
# do not appear in `columns` metadata (which drives form generation).
SYSTEM_COLUMNS = {
    "id": "integer",
    "created_at": "datetime",
    "updated_at": "datetime",
}


class ColumnMeta(msgspec.Struct):
    name: str
    column_slug: str
    data_type: str
    nullable: bool
    is_primary_key: bool


class EntityMeta(msgspec.Struct):
    id: int
    name: str
    table_slug: str
    workbook: str
    record_count: int
    columns: list[ColumnMeta]


class RecordPage(msgspec.Struct):
    entity: str
    table_slug: str
    columns: list[ColumnMeta]
    total: int
    page: int
    page_size: int
    rows: list[dict]


class RecordOut(msgspec.Struct):
    id: int
    entity_slug: str
    data: dict


# --------------------------------------------------------------------------
# Entity resolution
# --------------------------------------------------------------------------


def _quote_column(key: str) -> str:
    """Quote a column that is already known to be queryable.

    System columns are literals from a fixed allowlist -- `safe_ident` rejects
    `id` precisely because a *data* slug must never shadow it, so they are
    checked by membership instead. Everything else is a stored slug and goes
    through the gate.
    """
    if key in SYSTEM_COLUMNS:
        return f'"{key}"'
    return f'"{safe_ident(key)}"'


def _committed_entities(org: Organization):
    return InferredEntity.objects.filter(
        workbook__organization=org, workbook__status=WorkbookStatus.COMMITTED
    ).select_related("workbook").prefetch_related("fields")


def _resolve(org: Organization, slug: str) -> InferredEntity:
    """A slug is only usable if it names a committed entity of THIS org."""
    entity = _committed_entities(org).filter(table_slug=slug).first()
    if entity is None:
        known = sorted(_committed_entities(org).values_list("table_slug", flat=True))
        raise NotFound(
            detail=f"No committed entity {slug!r} for this organization.",
            extra={"available": known},
        )
    safe_ident(entity.table_slug)  # belt and braces: it was safe at commit time too
    return entity


def _columns(entity: InferredEntity) -> list[ColumnMeta]:
    return [
        ColumnMeta(
            name=f.name, column_slug=f.column_slug, data_type=f.data_type,
            nullable=f.nullable, is_primary_key=f.is_primary_key,
        )
        for f in entity.fields.all()
    ]


def _coerce(value: Any, data_type: str) -> Any:
    """Convert an inbound JSON value to what the typed view expects.

    Rejecting a bad value here gives the user a field-level message; letting it
    through gives them a Postgres cast error from a view they never wrote.
    """
    if value is None or value == "":
        return None
    try:
        if data_type == "integer":
            return int(value)
        if data_type == "numeric":
            return float(Decimal(str(value)))
        if data_type == "boolean":
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in {"true", "yes", "y", "1", "t"}
        if data_type in ("date", "datetime"):
            text = str(value).strip()
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return parsed.date().isoformat() if data_type == "date" else parsed.isoformat()
        if data_type == "json":
            return json.loads(value) if isinstance(value, str) else value
        return str(value)
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise BadRequest(detail=f"Value {value!r} is not a valid {data_type} ({exc}).")


# --------------------------------------------------------------------------
# Endpoints
# --------------------------------------------------------------------------


@sync_to_async
def _list_entities(org: Organization) -> list[EntityMeta]:
    from django.db.models import Count

    rows = _committed_entities(org).annotate(n=Count("records")).order_by("name")
    return [
        EntityMeta(
            id=e.pk, name=e.name, table_slug=e.table_slug,
            workbook=e.workbook.original_filename, record_count=e.n, columns=_columns(e),
        )
        for e in rows
    ]


@api.get("/api/entities")
async def list_entities(x_org_id: OrgHeader = None) -> list[EntityMeta]:
    """Committed entities for the org. The CRUD UI builds itself from this."""
    org = await resolve_org(x_org_id)
    return await _list_entities(org)


@sync_to_async
def _list_records(
    org: Organization, slug: str, page: int, page_size: int, order_by: str | None,
    descending: bool, filters: dict[str, str],
) -> RecordPage:
    entity = _resolve(org, slug)
    columns = _columns(entity)
    by_slug = {c.column_slug: c.data_type for c in columns}
    queryable = {**by_slug, **SYSTEM_COLUMNS}
    view = f'"{safe_ident(schema_name(org.pk))}"."{safe_ident(entity.table_slug)}"'

    where, params = ["TRUE"], []
    for key, raw in filters.items():
        data_type = queryable.get(key)
        if data_type is None:
            raise BadRequest(
                detail=f"Unknown filter column {key!r}.",
                extra={"available": sorted(queryable)},
            )
        col = _quote_column(key)
        if data_type == "text":
            where.append(f"{col} ILIKE %s")
            params.append(f"%{raw}%")
        else:
            where.append(f"{col} = %s")
            params.append(_coerce(raw, data_type))

    order_col = "id"
    if order_by:
        if order_by not in queryable:
            raise BadRequest(
                detail=f"Cannot order by {order_by!r}.", extra={"available": sorted(queryable)}
            )
        order_col = order_by

    clause = " AND ".join(where)
    with connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {view} WHERE {clause}", params)
        total = cur.fetchone()[0]
        cur.execute(
            f"SELECT * FROM {view} WHERE {clause} "
            f'ORDER BY {_quote_column(order_col)} {"DESC" if descending else "ASC"} '
            f"LIMIT %s OFFSET %s",
            [*params, page_size, (page - 1) * page_size],
        )
        names = [d[0] for d in cur.description]
        rows = [dict(zip(names, _row_jsonable(r))) for r in cur.fetchall()]

    return RecordPage(
        entity=entity.name, table_slug=entity.table_slug, columns=columns,
        total=total, page=page, page_size=page_size, rows=rows,
    )


def _row_jsonable(row) -> list:
    out = []
    for v in row:
        if isinstance(v, (datetime, date)):
            out.append(v.isoformat())
        elif isinstance(v, Decimal):
            out.append(float(v))
        else:
            out.append(v)
    return out


@api.get("/api/entities/{slug}/records")
async def list_records(
    slug: str,
    request,
    page: Annotated[int, Query()] = 1,
    page_size: Annotated[int, Query()] = DEFAULT_PAGE_SIZE,
    order_by: Annotated[str | None, Query()] = None,
    desc: Annotated[bool, Query()] = False,
    x_org_id: OrgHeader = None,
) -> RecordPage:
    """List with filtering and pagination.

    Any query parameter that is not a known control parameter is treated as a
    column filter, so `?city=Istanbul&page=2` reads naturally.
    """
    org = await resolve_org(x_org_id)
    reserved = {"page", "page_size", "order_by", "desc", "org_id"}
    filters = {k: v for k, v in request.query.items() if k not in reserved}
    page = max(1, page)
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    return await _list_records(org, slug, page, page_size, order_by, desc, filters)


@sync_to_async
@transaction.atomic
def _create_record(org: Organization, slug: str, payload: dict) -> RecordOut:
    entity = _resolve(org, slug)
    by_slug = {f.column_slug: f for f in entity.fields.all()}
    unknown = set(payload) - set(by_slug)
    if unknown:
        raise BadRequest(
            detail=f"Unknown column(s): {sorted(unknown)}.",
            extra={"available": sorted(by_slug)},
        )
    data = {
        slug_: _coerce(payload.get(slug_), f.data_type)
        for slug_, f in by_slug.items()
    }
    with connection.cursor() as cur:
        cur.execute(
            "INSERT INTO record (entity_id, organization_id, data, created_at, updated_at) "
            "VALUES (%s, %s, %s::jsonb, now(), now()) RETURNING id",
            [entity.pk, org.pk, json.dumps(data)],
        )
        record_id = cur.fetchone()[0]
    return RecordOut(id=record_id, entity_slug=entity.table_slug, data=data)


@api.post("/api/entities/{slug}/records", status_code=201)
async def create_record(
    slug: str,
    # Explicitly Body(): the record shape is dynamic, so this cannot be a
    # msgspec.Struct, and Bolt reads an unmarked `dict` as a query parameter.
    payload: Annotated[dict, Body()],
    x_org_id: OrgHeader = None,
) -> RecordOut:
    org = await resolve_org(x_org_id)
    return await _create_record(org, slug, payload)


@sync_to_async
@transaction.atomic
def _update_record(org: Organization, record_id: int, payload: dict) -> RecordOut:
    with connection.cursor() as cur:
        cur.execute(
            "SELECT entity_id, data FROM record WHERE id = %s AND organization_id = %s",
            [record_id, org.pk],
        )
        row = cur.fetchone()
    if row is None:
        raise NotFound(detail=f"Record {record_id} not found")
    entity_id, existing = row
    # psycopg may hand jsonb back as a parsed dict or as raw text depending on
    # the adapter in play; normalise rather than assume.
    if isinstance(existing, (str, bytes)):
        existing = json.loads(existing)

    entity = _committed_entities(org).filter(pk=entity_id).first()
    if entity is None:
        raise NotFound(detail=f"Record {record_id} belongs to an uncommitted entity")

    by_slug = {f.column_slug: f for f in entity.fields.all()}
    unknown = set(payload) - set(by_slug)
    if unknown:
        raise BadRequest(
            detail=f"Unknown column(s): {sorted(unknown)}.",
            extra={"available": sorted(by_slug)},
        )

    merged = dict(existing or {})
    for slug_, value in payload.items():
        merged[slug_] = _coerce(value, by_slug[slug_].data_type)

    with connection.cursor() as cur:
        cur.execute(
            "UPDATE record SET data = %s::jsonb, updated_at = now() WHERE id = %s",
            [json.dumps(merged), record_id],
        )
    return RecordOut(id=record_id, entity_slug=entity.table_slug, data=merged)


@api.patch("/api/records/{record_id}")
async def update_record(
    record_id: int,
    payload: Annotated[dict, Body()],
    x_org_id: OrgHeader = None,
) -> RecordOut:
    org = await resolve_org(x_org_id)
    return await _update_record(org, record_id, payload)


@sync_to_async
@transaction.atomic
def _delete_record(org: Organization, record_id: int) -> None:
    with connection.cursor() as cur:
        # Graph edges first: nothing cascades in raw SQL, and a row that another
        # row references cannot be deleted while the edge stands. See
        # records.bulk.delete_edges_referencing.
        cur.execute(
            "DELETE FROM edge WHERE from_record_id = %s OR to_record_id = %s",
            [record_id, record_id],
        )
        cur.execute(
            "DELETE FROM record WHERE id = %s AND organization_id = %s RETURNING id",
            [record_id, org.pk],
        )
        if cur.fetchone() is None:
            raise NotFound(detail=f"Record {record_id} not found")


@api.delete("/api/records/{record_id}", status_code=204)
async def delete_record(record_id: int, x_org_id: OrgHeader = None) -> None:
    org = await resolve_org(x_org_id)
    await _delete_record(org, record_id)


# --------------------------------------------------------------------------
# Bulk edits and the table chat
# --------------------------------------------------------------------------


class AssistTurn(msgspec.Struct):
    role: str
    content: str


class TableAssistRequest(msgspec.Struct):
    message: str
    history: list[AssistTurn] = []


class TableAssistResponse(msgspec.Struct):
    reply: str
    #: Present when the agent prepared something. NOTHING has been applied:
    #: the person confirms it, and the client posts it back to /bulk.
    plan: bulk.BulkPlan | None
    preview: bulk.BulkPreview | None
    #: Read-only counts the agent looked up on the way to its answer.
    lookups: list[str]


@offload
def _table_assist(
    org: Organization, slug: str, payload: TableAssistRequest
) -> TableAssistResponse:
    from inference.llm.agent import MissingAPIKey
    from inference.table_assist import run_table_assist

    entity = _resolve(org, slug)
    view = f'"{safe_ident(schema_name(org.pk))}"."{safe_ident(entity.table_slug)}"'
    with connection.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {view}")
        total = cur.fetchone()[0]

    history = [{"role": t.role, "content": t.content} for t in payload.history]
    try:
        result = run_table_assist(org, entity, total, payload.message, history)
    except MissingAPIKey as exc:
        raise UnprocessableEntity(detail=str(exc))
    except bulk.PlanError:
        raise
    except Exception as exc:
        raise UnprocessableEntity(
            detail=f"The assistant failed: {exc.__class__.__name__}: {exc}"
        )

    return TableAssistResponse(
        reply=result.reply,
        plan=result.plan,
        preview=result.preview,
        lookups=result.lookups,
    )


@api.post("/api/entities/{slug}/assist")
async def table_assist(
    slug: str, payload: TableAssistRequest, x_org_id: OrgHeader = None
) -> TableAssistResponse:
    """Ask for a change to many rows at once.

    Returns a *plan*, never a change: see records/bulk.py for why proposing and
    applying are two calls.
    """
    org = await resolve_org(x_org_id)
    message = payload.message.strip()
    if not message:
        raise BadRequest(detail="Type what you would like to change.")
    return await _table_assist(org, slug, payload)


@sync_to_async
def _preview_bulk(org: Organization, slug: str, plan: bulk.BulkPlan) -> bulk.BulkPreview:
    return bulk.preview(org, _resolve(org, slug), plan)


@api.post("/api/entities/{slug}/bulk/preview")
async def preview_bulk(
    slug: str, plan: bulk.BulkPlan, x_org_id: OrgHeader = None
) -> bulk.BulkPreview:
    """What this plan would affect, without affecting it."""
    org = await resolve_org(x_org_id)
    return await _preview_bulk(org, slug, plan)


@offload
def _apply_bulk(org: Organization, slug: str, plan: bulk.BulkPlan) -> bulk.BulkResult:
    return bulk.apply(org, _resolve(org, slug), plan)


@api.post("/api/entities/{slug}/bulk")
async def apply_bulk(
    slug: str, plan: bulk.BulkPlan, x_org_id: OrgHeader = None
) -> bulk.BulkResult:
    """Run a confirmed plan. Re-validated here: the browser is not a gatekeeper."""
    org = await resolve_org(x_org_id)
    return await _apply_bulk(org, slug, plan)
