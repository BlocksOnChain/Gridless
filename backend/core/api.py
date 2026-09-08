"""Core API: health, workbook upload, and the deterministic analysis trigger.

The SSE progress stream and the LangGraph-driven stages 3/4 land here in the
next build step. Nothing on this module is stubbed: every endpoint below does
real work against the real database.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from asgiref.sync import sync_to_async
from django.db import connection
import msgspec

from django_bolt import BoltAPI, FileSize, OpenAPIConfig, UploadFile
from django_bolt.exceptions import BadRequest, NotFound, UnprocessableEntity
from django_bolt.param_functions import File

from core.deps import OrgHeader, offload, resolve_org
from core.models import Organization, Workbook, WorkbookStatus
from ingest.pipeline import EmptyWorkbook, run_structure_and_types
from ingest.reader import UnreadableWorkbook, probe_workbook

# No `prefix=` on purpose. Django-Bolt prepends the API prefix to ASGI mounts,
# including the Django admin it auto-mounts at the prefix found in ROOT_URLCONF.
# With prefix="/api" the admin mounts at /api/admin while Django's own patterns
# still say admin/, and the two can never agree. Spelling /api into each route
# keeps the admin reachable at the conventional /admin/.
api = BoltAPI(
    openapi_config=OpenAPIConfig(title="Gridless API", version="0.1.0", path="/docs"),
)

ALLOWED_SUFFIXES = {".xlsx"}


# --------------------------------------------------------------------------
# Schemas (msgspec, per Django-Bolt -- not DRF serializers, not Pydantic)
# --------------------------------------------------------------------------


class Health(msgspec.Struct):
    status: str
    database: str


class WorkbookOut(msgspec.Struct):
    id: int
    original_filename: str
    status: str
    uploaded_at: str
    sheet_count: int
    entity_count: int
    error: str


class AnalyseOut(msgspec.Struct):
    workbook_id: int
    status: str
    sheets: int
    entities: int
    fields: int
    relationships: int
    low_confidence: list[str]


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


@sync_to_async
def _check_database() -> str:
    """Django forbids sync DB access from an async context and the connection
    handle is sync-only, so the probe runs in a thread."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return "ok"
    except Exception as exc:  # a health check that hides failure is worthless
        return f"error: {exc.__class__.__name__}: {exc}"


@api.get("/api/health")
async def health() -> Health:
    database = await _check_database()
    return Health(status="ok" if database == "ok" else "degraded", database=database)


# --------------------------------------------------------------------------
# Workbooks
# --------------------------------------------------------------------------


def _serialise(workbook: Workbook, sheets: int, entities: int) -> WorkbookOut:
    return WorkbookOut(
        id=workbook.pk,
        original_filename=workbook.original_filename,
        status=workbook.status,
        uploaded_at=workbook.uploaded_at.isoformat(),
        sheet_count=sheets,
        entity_count=entities,
        error=workbook.error,
    )


@sync_to_async
def _list_workbooks(org: Organization) -> list[WorkbookOut]:
    from django.db.models import Count

    rows = (
        Workbook.objects.filter(organization=org)
        .annotate(n_sheets=Count("sheets", distinct=True),
                  n_entities=Count("entities", distinct=True))
        .order_by("-uploaded_at")
    )
    return [_serialise(w, w.n_sheets, w.n_entities) for w in rows]


@api.get("/api/workbooks")
async def list_workbooks(x_org_id: OrgHeader = None) -> list[WorkbookOut]:
    org = await resolve_org(x_org_id)
    return await _list_workbooks(org)


@sync_to_async
def _store(org: Organization, filename: str, upload: UploadFile) -> Workbook:
    workbook = Workbook(
        organization=org,
        original_filename=filename,
        status=WorkbookStatus.UPLOADED,
    )
    # UploadFile.file bridges the Rust-parsed upload to a Django File without
    # an extra copy, so it can be assigned straight to the FileField.
    workbook.file = upload.file
    workbook.save()
    return workbook


@api.post("/api/workbooks", status_code=201)
async def upload_workbook(
    file: Annotated[UploadFile, File(max_size=FileSize.MB_50)],
    x_org_id: OrgHeader = None,
) -> WorkbookOut:
    """Accept one .xlsx. Multi-file upload is the client calling this per file,
    which keeps per-file failure isolated."""
    org = await resolve_org(x_org_id)
    filename = file.filename or "workbook.xlsx"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise BadRequest(
            detail=f"Only .xlsx is supported in v1 (got {suffix or 'no extension'}).",
            extra={"filename": filename, "allowed": sorted(ALLOWED_SUFFIXES)},
        )

    # Reject an unreadable file here rather than storing it and failing later.
    # A .xlsx extension is not evidence that a file is a workbook.
    data = await file.read()
    try:
        await sync_to_async(probe_workbook)(data)
    except UnreadableWorkbook as exc:
        raise BadRequest(detail=str(exc), extra={"filename": filename})
    # read() leaves the cursor at EOF; rewind so the FileField saves the whole file.
    await file.seek(0)

    workbook = await _store(org, filename, file)
    return _serialise(workbook, sheets=0, entities=0)


@sync_to_async
def _get_workbook(org: Organization, workbook_id: int) -> Workbook:
    workbook = Workbook.objects.filter(organization=org, pk=workbook_id).first()
    if workbook is None:
        raise NotFound(detail=f"Workbook {workbook_id} not found")
    return workbook


@offload
def _run_deterministic(workbook: Workbook) -> AnalyseOut:
    workbook.status = WorkbookStatus.ANALYSING
    workbook.error = ""
    workbook.save(update_fields=["status", "error"])
    try:
        result = run_structure_and_types(workbook)
    except Exception as exc:
        workbook.status = WorkbookStatus.FAILED
        workbook.error = f"{exc.__class__.__name__}: {exc}"
        workbook.save(update_fields=["status", "error"])
        raise
    return AnalyseOut(
        workbook_id=workbook.pk,
        status=workbook.status,
        sheets=result.sheets,
        entities=result.entities,
        fields=result.fields,
        relationships=result.relationships,
        low_confidence=result.low_confidence,
    )


@api.post("/api/workbooks/{workbook_id}/analyse")
async def analyse_workbook_endpoint(
    workbook_id: int, x_org_id: OrgHeader = None
) -> AnalyseOut:
    """Run stages 1, 2 and the deterministic half of 4, synchronously.

    This is the honest shape while the LLM stages are not wired: the work is
    deterministic and fast enough to do inline. Once stages 3 and 4 join the
    graph, this endpoint marks the workbook `analysing` and the SSE progress
    stream drives the run -- see the plan's execution-model section.
    """
    org = await resolve_org(x_org_id)
    workbook = await _get_workbook(org, workbook_id)
    try:
        return await _run_deterministic(workbook)
    except EmptyWorkbook as exc:
        raise UnprocessableEntity(detail=str(exc), extra={"workbook_id": workbook_id})
    except UnreadableWorkbook as exc:
        raise UnprocessableEntity(detail=str(exc), extra={"workbook_id": workbook_id})
    except Exception as exc:
        # The pipeline already recorded the failure on the workbook row; surface
        # it as JSON rather than letting Django render an HTML debug page at a
        # JSON endpoint.
        raise UnprocessableEntity(
            detail=f"Analysis failed: {exc.__class__.__name__}: {exc}",
            extra={"workbook_id": workbook_id},
        )


# --------------------------------------------------------------------------
# Proposal (the review screen's data) and the review edits
# --------------------------------------------------------------------------


class SheetOut(msgspec.Struct):
    id: int
    name: str
    detected_header_row: int | None
    detected_range: str
    raw_row_count: int
    notes: str


class FieldOut(msgspec.Struct):
    id: int
    name: str
    source_header: str
    column_slug: str
    source_column_letter: str
    data_type: str
    nullable: bool
    is_primary_key: bool
    confidence: float
    user_confirmed: bool
    sample_values: list
    position: int


class SectionOut(msgspec.Struct):
    id: int
    kind: str
    title: str
    body: str
    source_range: str
    position: int
    created_by_user: bool


class EntityOut(msgspec.Struct):
    id: int
    name: str
    table_slug: str
    confidence: float
    user_confirmed: bool
    sheet: SheetOut
    fields: list[FieldOut]
    #: Blocks of the sheet that are not rows: notes, totals. Rendered under the
    #: table in the generated app.
    sections: list[SectionOut]


class RelationshipOut(msgspec.Struct):
    id: int
    name: str
    from_entity_id: int
    from_entity: str
    from_field_id: int
    from_field: str
    to_entity_id: int
    to_entity: str
    to_field_id: int
    to_field: str
    cardinality: str
    match_rate: float
    confidence: float
    user_confirmed: bool
    rejected: bool
    needs_review: bool
    rationale: str


class ProposalOut(msgspec.Struct):
    workbook: WorkbookOut
    entities: list[EntityOut]
    relationships: list[RelationshipOut]


def _field_out(f) -> FieldOut:
    return FieldOut(
        id=f.pk, name=f.name, source_header=f.source_header, column_slug=f.column_slug,
        source_column_letter=f.source_column_letter, data_type=f.data_type,
        nullable=f.nullable, is_primary_key=f.is_primary_key,
        confidence=f.confidence, user_confirmed=f.user_confirmed,
        sample_values=list(f.sample_values or []), position=f.position,
    )


def _section_out(s) -> SectionOut:
    return SectionOut(
        id=s.pk, kind=s.kind, title=s.title, body=s.body,
        source_range=s.source_range, position=s.position,
        created_by_user=s.created_by_user,
    )


def _entity_out(e) -> EntityOut:
    s = e.source_sheet
    return EntityOut(
        id=e.pk, name=e.name, table_slug=e.table_slug, confidence=e.confidence,
        user_confirmed=e.user_confirmed,
        sheet=SheetOut(
            id=s.pk, name=s.name, detected_header_row=s.detected_header_row,
            detected_range=s.detected_range, raw_row_count=s.raw_row_count, notes=s.notes,
        ),
        fields=[_field_out(f) for f in e.fields.all()],
        sections=[_section_out(x) for x in e.sections.all()],
    )


def _relationship_out(r) -> RelationshipOut:
    return RelationshipOut(
        id=r.pk, name=r.name,
        from_entity_id=r.from_entity_id, from_entity=r.from_entity.name,
        from_field_id=r.from_field_id, from_field=r.from_field.name,
        to_entity_id=r.to_entity_id, to_entity=r.to_entity.name,
        to_field_id=r.to_field_id, to_field=r.to_field.name,
        cardinality=r.cardinality, match_rate=r.match_rate, confidence=r.confidence,
        user_confirmed=r.user_confirmed, rejected=r.rejected,
        needs_review=r.needs_review, rationale=r.rationale,
    )


def _proposal_out(org: Organization, workbook_id: int) -> ProposalOut:
    """Sync body, shared with the review chat, which is already in a thread."""
    from django.db.models import Count, Prefetch

    from core.models import InferredEntity, InferredField

    workbook = Workbook.objects.filter(organization=org, pk=workbook_id).first()
    if workbook is None:
        raise NotFound(detail=f"Workbook {workbook_id} not found")

    entities = (
        InferredEntity.objects.filter(workbook=workbook)
        .select_related("source_sheet")
        .prefetch_related(
            Prefetch("fields", queryset=InferredField.objects.order_by("position", "id")),
            "sections",
        )
        .order_by("id")
    )
    relationships = (
        workbook.relationships.select_related(
            "from_entity", "from_field", "to_entity", "to_field"
        ).order_by("rejected", "-confidence", "id")
    )
    counts = Workbook.objects.filter(pk=workbook.pk).annotate(
        n_sheets=Count("sheets", distinct=True), n_entities=Count("entities", distinct=True)
    ).first()

    return ProposalOut(
        workbook=_serialise(workbook, counts.n_sheets, counts.n_entities),
        entities=[_entity_out(e) for e in entities],
        relationships=[_relationship_out(r) for r in relationships],
    )


_load_proposal = sync_to_async(_proposal_out)


@api.get("/api/workbooks/{workbook_id}/proposal")
async def get_proposal(workbook_id: int, x_org_id: OrgHeader = None) -> ProposalOut:
    """Everything the review screen needs, in one request."""
    org = await resolve_org(x_org_id)
    return await _load_proposal(org, workbook_id)


class EntityPatch(msgspec.Struct):
    """Absent fields are left alone; this is a partial update."""

    name: str | None = None
    table_slug: str | None = None
    user_confirmed: bool | None = None


class FieldPatch(msgspec.Struct):
    name: str | None = None
    column_slug: str | None = None
    data_type: str | None = None
    is_primary_key: bool | None = None
    user_confirmed: bool | None = None


class RelationshipPatch(msgspec.Struct):
    user_confirmed: bool | None = None
    rejected: bool | None = None


def _check_slug(value: str, *, taken: set[str], label: str) -> str:
    """A user-supplied slug becomes a Postgres identifier at commit time, so it
    passes the same gate a generated one does."""
    from commitdata.ddl import UnsafeIdentifier, safe_ident

    cleaned = (value or "").strip()
    try:
        safe_ident(cleaned)
    except UnsafeIdentifier as exc:
        raise BadRequest(detail=f"Invalid {label}: {exc}")
    if cleaned in taken:
        raise BadRequest(detail=f"{label} {cleaned!r} is already used in this workbook.")
    return cleaned


@sync_to_async
def _patch_entity(org: Organization, entity_id: int, patch: EntityPatch) -> EntityOut:
    from core.models import InferredEntity

    entity = (
        InferredEntity.objects.filter(workbook__organization=org, pk=entity_id)
        .select_related("source_sheet")
        .first()
    )
    if entity is None:
        raise NotFound(detail=f"Entity {entity_id} not found")

    changed = []
    if patch.name is not None:
        name = patch.name.strip()
        if not name:
            raise BadRequest(detail="Entity name cannot be empty.")
        entity.name = name
        changed.append("name")
    if patch.table_slug is not None:
        taken = set(
            InferredEntity.objects.filter(workbook=entity.workbook)
            .exclude(pk=entity.pk)
            .values_list("table_slug", flat=True)
        )
        entity.table_slug = _check_slug(patch.table_slug, taken=taken, label="table_slug")
        changed.append("table_slug")
    if patch.user_confirmed is not None:
        entity.user_confirmed = patch.user_confirmed
        changed.append("user_confirmed")

    if changed:
        entity.save(update_fields=changed)
    return _entity_out(entity)


@api.patch("/api/entities/{entity_id}")
async def patch_entity(entity_id: int, patch: EntityPatch, x_org_id: OrgHeader = None) -> EntityOut:
    org = await resolve_org(x_org_id)
    return await _patch_entity(org, entity_id, patch)


@sync_to_async
def _patch_field(org: Organization, field_id: int, patch: FieldPatch) -> FieldOut:
    from core.models import DataType, InferredField

    field = (
        InferredField.objects.filter(entity__workbook__organization=org, pk=field_id)
        .select_related("entity")
        .first()
    )
    if field is None:
        raise NotFound(detail=f"Field {field_id} not found")

    changed = []
    if patch.name is not None:
        name = patch.name.strip()
        if not name:
            raise BadRequest(detail="Field name cannot be empty.")
        field.name = name
        changed.append("name")
    if patch.column_slug is not None:
        taken = set(
            InferredField.objects.filter(entity=field.entity)
            .exclude(pk=field.pk)
            .values_list("column_slug", flat=True)
        )
        field.column_slug = _check_slug(patch.column_slug, taken=taken, label="column_slug")
        changed.append("column_slug")
    if patch.data_type is not None:
        valid = {c for c, _ in DataType.choices}
        if patch.data_type not in valid:
            raise BadRequest(
                detail=f"Unknown data_type {patch.data_type!r}.",
                extra={"allowed": sorted(valid)},
            )
        field.data_type = patch.data_type
        changed.append("data_type")
    if patch.is_primary_key is not None:
        if patch.is_primary_key:
            # One primary key per entity: setting this one clears the others,
            # rather than letting the view be built around two.
            InferredField.objects.filter(entity=field.entity).exclude(pk=field.pk).update(
                is_primary_key=False
            )
        field.is_primary_key = patch.is_primary_key
        changed.append("is_primary_key")
        field.user_confirmed = True
        if "user_confirmed" not in changed:
            changed.append("user_confirmed")
    if patch.user_confirmed is not None:
        field.user_confirmed = patch.user_confirmed
        changed.append("user_confirmed")

    if changed:
        field.save(update_fields=changed)
    return _field_out(field)


@api.patch("/api/fields/{field_id}")
async def patch_field(field_id: int, patch: FieldPatch, x_org_id: OrgHeader = None) -> FieldOut:
    org = await resolve_org(x_org_id)
    return await _patch_field(org, field_id, patch)


class FieldCreate(msgspec.Struct):
    """A column the reviewer added by hand rather than one read from a sheet."""

    name: str
    data_type: str = "text"


@sync_to_async
def _add_field(org: Organization, entity_id: int, body: FieldCreate) -> FieldOut:
    from core.models import InferredEntity
    from core.review import ReviewEditError, add_manual_field

    entity = InferredEntity.objects.filter(
        workbook__organization=org, pk=entity_id
    ).first()
    if entity is None:
        raise NotFound(detail=f"Entity {entity_id} not found")
    try:
        field = add_manual_field(entity, body.name, body.data_type)
    except ReviewEditError as exc:
        raise BadRequest(detail=str(exc))
    return _field_out(field)


@api.post("/api/entities/{entity_id}/fields", status_code=201)
async def add_field(
    entity_id: int, body: FieldCreate, x_org_id: OrgHeader = None
) -> FieldOut:
    """Add a column that the spreadsheet did not have. It commits empty."""
    org = await resolve_org(x_org_id)
    return await _add_field(org, entity_id, body)


@sync_to_async
def _delete_field(org: Organization, field_id: int) -> None:
    from core.models import InferredField
    from core.review import ReviewEditError, remove_field

    field = (
        InferredField.objects.filter(entity__workbook__organization=org, pk=field_id)
        .select_related("entity")
        .first()
    )
    if field is None:
        raise NotFound(detail=f"Field {field_id} not found")
    try:
        remove_field(field)
    except ReviewEditError as exc:
        raise BadRequest(detail=str(exc))


@api.delete("/api/fields/{field_id}", status_code=204)
async def delete_field(field_id: int, x_org_id: OrgHeader = None) -> None:
    """Drop a column from the proposal. The spreadsheet file is untouched."""
    org = await resolve_org(x_org_id)
    await _delete_field(org, field_id)


class SectionCreate(msgspec.Struct):
    title: str
    body: str
    kind: str = "note"


class SectionPatch(msgspec.Struct):
    title: str | None = None
    body: str | None = None
    kind: str | None = None


@sync_to_async
def _add_section(org: Organization, entity_id: int, body: SectionCreate) -> SectionOut:
    from core.models import InferredEntity
    from core.review import ReviewEditError, add_section

    entity = InferredEntity.objects.filter(
        workbook__organization=org, pk=entity_id
    ).first()
    if entity is None:
        raise NotFound(detail=f"Entity {entity_id} not found")
    try:
        section = add_section(entity, body.title, body.body, body.kind)
    except ReviewEditError as exc:
        raise BadRequest(detail=str(exc))
    return _section_out(section)


@api.post("/api/entities/{entity_id}/sections", status_code=201)
async def add_section_endpoint(
    entity_id: int, body: SectionCreate, x_org_id: OrgHeader = None
) -> SectionOut:
    """Add a section under a table -- a note, or anything the sheet could not hold."""
    org = await resolve_org(x_org_id)
    return await _add_section(org, entity_id, body)


@sync_to_async
def _patch_section(org: Organization, section_id: int, patch: SectionPatch) -> SectionOut:
    from core.models import EntitySection
    from core.review import ReviewEditError, check_section_kind

    section = EntitySection.objects.filter(
        entity__workbook__organization=org, pk=section_id
    ).first()
    if section is None:
        raise NotFound(detail=f"Section {section_id} not found")

    changed = []
    if patch.title is not None:
        section.title = patch.title.strip()
        changed.append("title")
    if patch.body is not None:
        section.body = patch.body
        changed.append("body")
    if patch.kind is not None:
        try:
            section.kind = check_section_kind(patch.kind)
        except ReviewEditError as exc:
            raise BadRequest(detail=str(exc))
        changed.append("kind")
    if changed:
        section.save(update_fields=changed)
    return _section_out(section)


@api.patch("/api/sections/{section_id}")
async def patch_section(
    section_id: int, patch: SectionPatch, x_org_id: OrgHeader = None
) -> SectionOut:
    org = await resolve_org(x_org_id)
    return await _patch_section(org, section_id, patch)


@sync_to_async
def _delete_section(org: Organization, section_id: int) -> None:
    from core.models import EntitySection

    deleted, _ = EntitySection.objects.filter(
        entity__workbook__organization=org, pk=section_id
    ).delete()
    if not deleted:
        raise NotFound(detail=f"Section {section_id} not found")


@api.delete("/api/sections/{section_id}", status_code=204)
async def delete_section(section_id: int, x_org_id: OrgHeader = None) -> None:
    org = await resolve_org(x_org_id)
    await _delete_section(org, section_id)


@sync_to_async
def _patch_relationship(
    org: Organization, relationship_id: int, patch: RelationshipPatch
) -> RelationshipOut:
    from core.models import InferredRelationship

    rel = (
        InferredRelationship.objects.filter(workbook__organization=org, pk=relationship_id)
        .select_related("from_entity", "from_field", "to_entity", "to_field")
        .first()
    )
    if rel is None:
        raise NotFound(detail=f"Relationship {relationship_id} not found")

    changed = []
    if patch.rejected is not None:
        rel.rejected = patch.rejected
        changed.append("rejected")
        # Any explicit human decision resolves the "no verdict" state.
        rel.needs_review = False
        changed.append("needs_review")
        if patch.rejected and patch.user_confirmed is None:
            # Rejecting is itself a decision, so it counts as reviewed.
            rel.user_confirmed = True
            changed.append("user_confirmed")
    if patch.user_confirmed is not None:
        rel.user_confirmed = patch.user_confirmed
        if "user_confirmed" not in changed:
            changed.append("user_confirmed")

    if changed:
        rel.save(update_fields=changed)
    return _relationship_out(rel)


@api.patch("/api/relationships/{relationship_id}")
async def patch_relationship(
    relationship_id: int, patch: RelationshipPatch, x_org_id: OrgHeader = None
) -> RelationshipOut:
    org = await resolve_org(x_org_id)
    return await _patch_relationship(org, relationship_id, patch)


# --------------------------------------------------------------------------
# Review chat
# --------------------------------------------------------------------------


class AssistTurn(msgspec.Struct):
    role: str
    content: str


class AssistRequest(msgspec.Struct):
    message: str
    history: list[AssistTurn] = []


class AssistResponse(msgspec.Struct):
    reply: str
    #: One line per change a tool actually applied. Empty means nothing moved,
    #: whatever the reply says.
    changes: list[str]
    #: The proposal re-read after the changes, so the client replaces its state
    #: instead of trying to guess what the chat did to it.
    proposal: ProposalOut


@offload
def _assist(org: Organization, workbook_id: int, payload: AssistRequest) -> AssistResponse:
    from inference.assist import run_assist
    from inference.llm.agent import MissingAPIKey

    workbook = Workbook.objects.filter(organization=org, pk=workbook_id).first()
    if workbook is None:
        raise NotFound(detail=f"Workbook {workbook_id} not found")

    history = [{"role": t.role, "content": t.content} for t in payload.history]
    try:
        result = run_assist(workbook, payload.message, history)
    except MissingAPIKey as exc:
        raise UnprocessableEntity(detail=str(exc))
    except Exception as exc:
        raise UnprocessableEntity(
            detail=f"The assistant failed: {exc.__class__.__name__}: {exc}"
        )

    return AssistResponse(
        reply=result.reply,
        changes=result.changes,
        proposal=_proposal_out(org, workbook_id),
    )


@api.post("/api/workbooks/{workbook_id}/assist")
async def assist(
    workbook_id: int, payload: AssistRequest, x_org_id: OrgHeader = None
) -> AssistResponse:
    """Change the proposal by asking for it, in words.

    The reply is the model's; the `changes` list is the tools' record of what
    actually happened, and `proposal` is read back from the database afterwards.
    A reply that claims more than `changes` shows is therefore visibly wrong,
    rather than quietly believed.
    """
    org = await resolve_org(x_org_id)
    message = payload.message.strip()
    if not message:
        raise BadRequest(detail="Type what you would like to change.")
    return await _assist(org, workbook_id, payload)


# --------------------------------------------------------------------------
# Commit (stage 5)
# --------------------------------------------------------------------------


class CommitOut(msgspec.Struct):
    workbook_id: int
    status: str
    schema: str
    entities: int
    records: int
    views: list[str]
    edges: int
    skipped_relationships: list[str]


class PreflightOut(msgspec.Struct):
    can_commit: bool
    problems: list[str]


@sync_to_async
def _preflight(org: Organization, workbook_id: int) -> PreflightOut:
    from commitdata.loader import preflight

    workbook = Workbook.objects.filter(organization=org, pk=workbook_id).first()
    if workbook is None:
        raise NotFound(detail=f"Workbook {workbook_id} not found")
    problems = preflight(workbook)
    return PreflightOut(can_commit=not problems, problems=problems)


@api.get("/api/workbooks/{workbook_id}/preflight")
async def commit_preflight(workbook_id: int, x_org_id: OrgHeader = None) -> PreflightOut:
    """Everything blocking a commit, so the button can explain itself."""
    org = await resolve_org(x_org_id)
    return await _preflight(org, workbook_id)


@offload
def _commit(org: Organization, workbook_id: int) -> CommitOut:
    from commitdata.loader import CommitError, commit_workbook

    workbook = Workbook.objects.filter(organization=org, pk=workbook_id).first()
    if workbook is None:
        raise NotFound(detail=f"Workbook {workbook_id} not found")
    try:
        result = commit_workbook(workbook)
    except CommitError as exc:
        raise UnprocessableEntity(detail=str(exc), extra={"workbook_id": workbook_id})

    return CommitOut(
        workbook_id=workbook.pk, status=workbook.status, schema=result.schema,
        entities=result.entities, records=result.records, views=result.views,
        edges=result.edges, skipped_relationships=result.skipped_relationships,
    )


@api.post("/api/workbooks/{workbook_id}/commit")
async def commit_endpoint(workbook_id: int, x_org_id: OrgHeader = None) -> CommitOut:
    """Load records, build typed views, populate the graph. One transaction."""
    org = await resolve_org(x_org_id)
    return await _commit(org, workbook_id)
