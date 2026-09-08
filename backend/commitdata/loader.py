"""Stage 5 — commit.

Turns a reviewed proposal into queryable data:

1. records   -- every row as JSONB in the shared `record` table
2. views     -- one typed Postgres view per entity, in the org's own schema
3. edges     -- one row per confirmed relationship instance

All of it in a single transaction. A failed commit leaves no partial state: no
half-populated table, no view pointing at rows that are not there.

Everything here is raw parameterised SQL. The committed data has no Django
models by design (the schema is not known at build time), and the generated
views are the relational surface the CRUD endpoints and the text-to-SQL agent
both read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.db import connection, transaction

from commitdata.ddl import (
    DATA_TYPE_TO_PG,
    UnsafeIdentifier,
    safe_ident,
    schema_name,
)
from core.models import (
    InferredEntity,
    InferredRelationship,
    Workbook,
    WorkbookStatus,
)
from ingest.analyse import analyse_workbook
from inference.policy import resolve_workbook_relationships
from inference.relationships import _norm

INSERT_BATCH = 500


class CommitError(Exception):
    """The proposal cannot be committed as it stands."""


@dataclass
class CommitResult:
    schema: str
    entities: int
    records: int
    views: list[str]
    edges: int
    skipped_relationships: list[str] = field(default_factory=list)


def _quote(schema: str, name: str) -> str:
    """Both halves are gate-checked before they reach a query string."""
    return f'"{safe_ident(schema)}"."{safe_ident(name)}"'


def preflight(workbook: Workbook) -> list[str]:
    """Reasons this workbook cannot be committed. Empty list means it can.

    Checked before anything is written, so the user gets every problem at once
    rather than discovering them one failed commit at a time.
    """
    problems: list[str] = []
    entities = list(workbook.entities.prefetch_related("fields"))
    if not entities:
        problems.append("This workbook has no entities to commit.")

    org_slugs: dict[str, str] = {}
    for entity in entities:
        try:
            safe_ident(entity.table_slug)
        except UnsafeIdentifier as exc:
            problems.append(f"{entity.name}: unsafe table slug ({exc}).")
        if entity.table_slug in org_slugs:
            problems.append(
                f"{entity.name}: table slug {entity.table_slug!r} collides with "
                f"{org_slugs[entity.table_slug]}."
            )
        org_slugs[entity.table_slug] = entity.name

        fields = list(entity.fields.all())
        if not fields:
            problems.append(f"{entity.name}: has no fields.")
        seen: set[str] = set()
        for f in fields:
            try:
                safe_ident(f.column_slug)
            except UnsafeIdentifier as exc:
                problems.append(f"{entity.name}.{f.name}: unsafe column slug ({exc}).")
            if f.column_slug in seen:
                problems.append(
                    f"{entity.name}: duplicate column slug {f.column_slug!r}."
                )
            seen.add(f.column_slug)
            if f.data_type not in DATA_TYPE_TO_PG:
                problems.append(f"{entity.name}.{f.name}: unknown type {f.data_type!r}.")

    # Relationships are deliberately absent from this list. They are decided by
    # the confidence threshold in `inference.policy`, not by the reviewer, so an
    # undecided one is not a blocker -- `commit_workbook` resolves it and
    # reports whatever got dropped.
    return problems


@transaction.atomic
def commit_workbook(workbook: Workbook) -> CommitResult:
    """Load records, build typed views, populate the graph. All or nothing."""
    problems = preflight(workbook)
    if problems:
        raise CommitError("; ".join(problems))

    org = workbook.organization
    schema = schema_name(org.pk)
    entities = list(workbook.entities.select_related("source_sheet").prefetch_related("fields"))

    # Settle every relationship against the threshold first, so nothing reaches
    # the graph on the strength of a verdict nobody gave.
    auto_dropped = resolve_workbook_relationships(workbook)

    # Re-read the source workbook: `record` stores the real values, and only the
    # parsed grid has them. The proposal carries names and types, not data.
    tables = {t.name: t for t in analyse_workbook(workbook.file.path)}

    with connection.cursor() as cur:
        cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{safe_ident(schema)}"')
        _grant_readonly(cur, schema)

        # Re-committing replaces this workbook's data rather than duplicating it.
        for entity in entities:
            cur.execute(f'DROP VIEW IF EXISTS {_quote(schema, entity.table_slug)}')
        cur.execute(
            "DELETE FROM edge WHERE relationship_id IN "
            "(SELECT id FROM core_inferredrelationship WHERE workbook_id = %s)",
            [workbook.pk],
        )
        cur.execute(
            "DELETE FROM record WHERE entity_id IN "
            "(SELECT id FROM core_inferredentity WHERE workbook_id = %s)",
            [workbook.pk],
        )

        total_records = 0
        views: list[str] = []
        # (entity_id, normalised value of column_slug) -> [record_id, ...]
        value_index: dict[tuple[int, str, str], list[int]] = {}

        for entity in entities:
            table = tables.get(entity.source_sheet.name)
            if table is None:
                raise CommitError(
                    f"Sheet {entity.source_sheet.name!r} is no longer present in the "
                    f"uploaded file; re-analyse the workbook before committing."
                )

            fields = list(entity.fields.all())
            by_header = {c.header: c for c in table.columns}
            # A field added by hand on the review screen has no `source_header`
            # and therefore no column in the sheet. It commits as an empty
            # column people fill in afterwards, which is the point of adding it.
            columns = [
                (f, by_header.get(f.source_header) if f.source_header else None)
                for f in fields
            ]
            missing = [f.name for f, c in columns if c is None and f.source_header]
            if missing:
                raise CommitError(
                    f"{entity.name}: columns {missing} are no longer in the sheet; "
                    f"re-analyse the workbook before committing."
                )

            rows = table.row_count
            payloads: list[tuple[int, int, str, int]] = []
            import json

            for i in range(rows):
                data = {}
                for f, column in columns:
                    data[f.column_slug] = (
                        _jsonable(column.values[i]) if column is not None else None
                    )
                payloads.append((entity.pk, org.pk, json.dumps(data), i))

            for start in range(0, len(payloads), INSERT_BATCH):
                chunk = payloads[start : start + INSERT_BATCH]
                placeholders = ",".join(["(%s, %s, %s::jsonb, %s, now(), now())"] * len(chunk))
                flat: list[Any] = [v for row in chunk for v in row]
                cur.execute(
                    "INSERT INTO record "
                    "(entity_id, organization_id, data, source_row_index, created_at, updated_at) "
                    f"VALUES {placeholders} RETURNING id",
                    flat,
                )
                ids = [r[0] for r in cur.fetchall()]
                for (_, _, _, row_index), record_id in zip(chunk, ids):
                    for f, column in columns:
                        if column is None:
                            continue
                        key = _norm(column.values[row_index])
                        if key is not None:
                            value_index.setdefault(
                                (entity.pk, f.column_slug, key), []
                            ).append(record_id)
            total_records += len(payloads)

            views.append(_create_view(cur, schema, entity, fields))

        # Views created above did not exist when the first grant ran.
        _grant_readonly(cur, schema)

        edges, skipped = _build_edges(cur, workbook, org.pk, value_index)
        # Below the threshold and matched-but-empty are different reasons for a
        # relationship to be absent; the caller sees both, deduplicated.
        skipped = sorted(set(skipped) | set(auto_dropped))

    workbook.status = WorkbookStatus.COMMITTED
    workbook.error = ""
    workbook.save(update_fields=["status", "error"])

    return CommitResult(
        schema=schema,
        entities=len(entities),
        records=total_records,
        views=views,
        edges=edges,
        skipped_relationships=skipped,
    )


READONLY_ROLE = "gridless_ro"


def _grant_readonly(cur, schema: str) -> None:
    """Let the powerless /api/ask role read THIS org's views and nothing else.

    The role has no USAGE on `public`, so it cannot reach `record` or `edge`
    directly, and it gets USAGE on one org schema at a time. That is what makes
    the text-to-SQL allowlist enforceable at the database rather than only in
    the guard.
    """
    quoted = f'"{safe_ident(schema)}"'
    cur.execute(f"SELECT 1 FROM pg_roles WHERE rolname = %s", [READONLY_ROLE])
    if cur.fetchone() is None:
        # Compose sets up the role; a database created another way may not have
        # it. Skip rather than fail the commit -- /api/ask reports it instead.
        return
    cur.execute(f'GRANT USAGE ON SCHEMA {quoted} TO {READONLY_ROLE}')
    cur.execute(f'GRANT SELECT ON ALL TABLES IN SCHEMA {quoted} TO {READONLY_ROLE}')
    cur.execute(
        f'ALTER DEFAULT PRIVILEGES IN SCHEMA {quoted} GRANT SELECT ON TABLES TO {READONLY_ROLE}'
    )


def _jsonable(value: Any) -> Any:
    from datetime import date, datetime
    from decimal import Decimal

    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _create_view(cur, schema: str, entity: InferredEntity, fields) -> str:
    """Project the JSONB into typed columns.

    This is the only place identifiers are interpolated into SQL. Every one has
    already passed `safe_ident` in preflight and passes it again here; the casts
    come from a fixed map, never from user text.

    Casts are NULLIF-guarded so one unparseable cell cannot make the entire view
    raise on select -- a view that errors is worse than a view with a null.
    """
    projections = ["id", "created_at", "updated_at"]
    for f in fields:
        slug = safe_ident(f.column_slug)
        pg_type = DATA_TYPE_TO_PG[f.data_type]
        if pg_type == "text":
            expr = f"data->>'{slug}'"
        elif pg_type == "jsonb":
            expr = f"data->'{slug}'"
        else:
            expr = f"NULLIF(data->>'{slug}', '')::{pg_type}"
        projections.append(f'{expr} AS "{slug}"')

    view = _quote(schema, entity.table_slug)
    cur.execute(
        f"CREATE VIEW {view} AS SELECT {', '.join(projections)} "
        f"FROM record WHERE entity_id = %s",
        [entity.pk],
    )
    return f"{schema}.{entity.table_slug}"


def _build_edges(cur, workbook: Workbook, org_id: int, value_index) -> tuple[int, list[str]]:
    """One edge per (source row, matching target row) for confirmed relationships."""
    relationships = (
        workbook.relationships.filter(rejected=False, needs_review=False)
        .select_related("from_entity", "from_field", "to_entity", "to_field")
    )
    total = 0
    skipped: list[str] = []

    for rel in relationships:
        pairs: list[tuple[int, int, int, int]] = []
        prefix = (rel.from_entity_id, rel.from_field.column_slug)
        for (entity_id, slug, key), source_ids in value_index.items():
            if (entity_id, slug) != prefix:
                continue
            targets = value_index.get((rel.to_entity_id, rel.to_field.column_slug, key))
            if not targets:
                continue
            for source_id in source_ids:
                for target_id in targets:
                    pairs.append((org_id, source_id, target_id, rel.pk))

        if not pairs:
            skipped.append(rel.name)
            continue

        for start in range(0, len(pairs), INSERT_BATCH):
            chunk = pairs[start : start + INSERT_BATCH]
            placeholders = ",".join(["(%s, %s, %s, %s)"] * len(chunk))
            flat = [v for row in chunk for v in row]
            cur.execute(
                "INSERT INTO edge (organization_id, from_record_id, to_record_id, relationship_id) "
                f"VALUES {placeholders}",
                flat,
            )
        total += len(pairs)
    return total, skipped
