"""Persist the deterministic stages for a workbook.

This runs with no LLM at all and produces a complete, usable proposal on its
own: entities named after their sheets, fields named after their headers, types
inferred by parse rate, and relationships measured by value overlap. Stages 3
and 4 improve the naming and prune the candidates, but the pipeline is
deliberately useful without them -- that is what makes the baseline eval number
meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from commitdata.ddl import unique_slug
from core.models import (
    Cardinality,
    InferredEntity,
    InferredField,
    InferredRelationship,
    Sheet,
    Workbook,
    WorkbookStatus,
)
from inference.keys import choose_primary_key
from inference.policy import is_accepted
from inference.relationships import RelationshipCandidate, find_candidates
from ingest.analyse import AnalysedTable, analyse_workbook

# Below this header score the structure is probably wrong, not just the naming,
# so the entity is surfaced to the reviewer rather than trusted.
LOW_CONFIDENCE_HEADER = 0.35


class EmptyWorkbook(Exception):
    """The workbook opened fine but contains no table we could detect."""


@dataclass
class StageResult:
    sheets: int
    entities: int
    fields: int
    relationships: int
    low_confidence: list[str]
    llm_used: bool = False
    llm_error: str = ""


def _singularise(name: str) -> str:
    """Cheap English singularisation for sheet names like 'Customers'.

    Deliberately conservative -- stage 3 replaces this with a real entity name.
    """
    text = name.strip()
    for suffix, replacement in (("ies", "y"), ("ses", "s"), ("s", "")):
        if text.lower().endswith(suffix) and len(text) > len(suffix) + 1:
            return text[: -len(suffix)] + replacement
    return text


@transaction.atomic
def run_structure_and_types(workbook: Workbook, *, use_llm: bool | None = None) -> StageResult:
    """The full inference pipeline, persisted.

    Stages 1, 2 and the deterministic half of 4 always run. Stages 3 (entity and
    key naming) and 4b (relationship ranking) run as deep agents when an
    OpenRouter key is configured, and are skipped otherwise -- the deterministic
    proposal is complete and usable on its own, so a missing key degrades the
    naming rather than failing the upload.

    Idempotent: re-running replaces the previous proposal rather than
    duplicating it.
    """
    if use_llm is None:
        # Imported lazily throughout this module: pulling in deepagents and the
        # LangChain stack costs ~10s, which every dev-server reload would pay
        # even for a request that never touches an LLM.
        from inference.llm.agent import api_key_configured

        use_llm = api_key_configured()
    workbook.relationships.all().delete()
    workbook.entities.all().delete()
    workbook.sheets.all().delete()

    tables = analyse_workbook(workbook.file.path)
    if not tables:
        # "proposed with nothing" is a dead end in the UI: the user gets a
        # success badge and an empty review screen. Failing loudly with the
        # reason is more useful.
        workbook.status = WorkbookStatus.FAILED
        workbook.error = (
            "No table was detected in this workbook. Every sheet was empty, or "
            "no row looked like a header row."
        )
        workbook.save(update_fields=["status", "error"])
        raise EmptyWorkbook(workbook.error)

    org_slugs: set[str] = set(
        InferredEntity.objects.filter(workbook__organization=workbook.organization)
        .exclude(workbook=workbook)
        .values_list("table_slug", flat=True)
    )

    entities: dict[str, InferredEntity] = {}
    fields: dict[tuple[str, str], InferredField] = {}
    low_confidence: list[str] = []
    llm_error = ""

    for table in tables:
        proposal = None
        if use_llm:
            try:
                from inference.stages import propose_entity

                proposal = propose_entity(table, org_slugs)
            except Exception as exc:  # a failed naming pass must not lose the table
                llm_error = f"{exc.__class__.__name__}: {exc}"
                proposal = None

        entity, table_fields = _persist_table(workbook, table, org_slugs, proposal)
        entities[table.name] = entity
        for header, field in table_fields.items():
            fields[(table.name, header)] = field
        if table.header_score < LOW_CONFIDENCE_HEADER:
            low_confidence.append(table.name)

    candidates = find_candidates(tables)
    judged = None
    if use_llm and candidates:
        try:
            from inference.stages import judge_relationships

            judged, judge_warnings = judge_relationships(tables, candidates)
            for w in judge_warnings:
                for sheet in workbook.sheets.all()[:1]:
                    sheet.note(f"Stage 4b: {w}")
        except Exception as exc:
            llm_error = f"{exc.__class__.__name__}: {exc}"
            judged = None

    relationships = _persist_relationships(
        workbook, candidates, judged, entities, fields
    )

    workbook.status = WorkbookStatus.PROPOSED
    workbook.save(update_fields=["status"])

    return StageResult(
        sheets=len(tables),
        entities=len(entities),
        fields=len(fields),
        relationships=relationships,
        low_confidence=low_confidence,
        llm_used=use_llm and not llm_error,
        llm_error=llm_error,
    )


def _persist_table(
    workbook: Workbook,
    table: AnalysedTable,
    org_slugs: set[str],
    proposal=None,
) -> tuple[InferredEntity, dict[str, InferredField]]:
    """Persist one table's entity and fields.

    `proposal` is a ValidatedEntity from stage 3 when the LLM ran, else None.
    Note it has already been reconciled against the real columns by
    inference.llm.validate -- this function trusts it because that validator
    does not.
    """
    notes = list(table.notes)
    if proposal is not None and proposal.warnings:
        notes.append(
            "Stage 3 corrections:\n" + "\n".join(f"  {w}" for w in proposal.warnings)
        )
    if proposal is not None and proposal.adjustments:
        notes.append(
            "Stage 3 adjustments (not the model's fault, confidence unaffected):\n"
            + "\n".join(f"  {a}" for a in proposal.adjustments)
        )
    if proposal is not None and proposal.notes:
        notes.append(f"Stage 3 reasoning: {proposal.notes}")

    sheet = Sheet.objects.create(
        workbook=workbook,
        name=table.name,
        sheet_index=table.sheet_index,
        detected_header_row=table.header_row + 1,  # stored 1-based, as a human reads it
        detected_range=table.range_a1,
        raw_row_count=table.raw_row_count,
        notes="\n".join(notes) + "\n",
    )

    if proposal is not None:
        entity = InferredEntity.objects.create(
            workbook=workbook,
            source_sheet=sheet,
            name=proposal.name,
            table_slug=proposal.table_slug,
            confidence=round(proposal.confidence, 4),
        )
        by_header = {c.header: c for c in table.columns}
        created: dict[str, InferredField] = {}
        for position, vf in enumerate(proposal.fields):
            column = by_header[vf.source_header]
            created[vf.source_header] = InferredField.objects.create(
                entity=entity,
                name=vf.name,
                source_header=vf.source_header,
                column_slug=vf.column_slug,
                source_column_letter=column.letter,
                data_type=column.data_type,   # never from the model
                nullable=column.nullable,
                is_primary_key=vf.is_primary_key,
                confidence=round(column.confidence, 4),
                sample_values=column.sample_values,
                position=position,
            )
        return entity, created

    # Deterministic fallback: entity named after the sheet, fields after headers.
    entity_name = _singularise(table.name)
    entity = InferredEntity.objects.create(
        workbook=workbook,
        source_sheet=sheet,
        name=entity_name,
        table_slug=unique_slug(entity_name, org_slugs, fallback="entity"),
        confidence=round(table.header_score, 4),
    )

    field_slugs: set[str] = set()
    created = {}
    pk_column = choose_primary_key(table.columns)
    for position, column in enumerate(table.columns):
        created[column.header] = InferredField.objects.create(
            entity=entity,
            name=column.header,
            source_header=column.header,
            column_slug=unique_slug(column.header, field_slugs, fallback="field"),
            source_column_letter=column.letter,
            data_type=column.data_type,
            nullable=column.nullable,
            is_primary_key=(pk_column is not None and column is pk_column),
            confidence=round(column.confidence, 4),
            sample_values=column.sample_values,
            position=position,
        )
    return entity, created


def _persist_relationships(
    workbook: Workbook,
    candidates: list[RelationshipCandidate],
    judged,
    entities: dict[str, InferredEntity],
    fields: dict[tuple[str, str], InferredField],
) -> int:
    """Persist measured candidates, annotated with stage 4b verdicts if we have them.

    Rejected candidates are stored with `rejected=True` rather than dropped:
    the row, its measured match rate and the model's reasoning stay on record,
    so a bad rejection is still diagnosable through the API and the admin even
    though the review screen no longer shows relationships at all.

    Acceptance is decided here by `inference.policy`, not by a reviewer -- see
    that module for why.
    """
    verdicts = {}
    if judged is not None:
        verdicts = {
            (j.candidate.from_table, j.candidate.from_column,
             j.candidate.to_table, j.candidate.to_column): j
            for j in judged
        }

    count = 0
    for candidate in candidates:
        from_field = fields.get((candidate.from_table, candidate.from_column))
        to_field = fields.get((candidate.to_table, candidate.to_column))
        from_entity = entities.get(candidate.from_table)
        to_entity = entities.get(candidate.to_table)
        if not all((from_field, to_field, from_entity, to_entity)):
            continue

        verdict = verdicts.get(
            (candidate.from_table, candidate.from_column,
             candidate.to_table, candidate.to_column)
        )
        rationale = candidate.rationale
        if verdict is not None and verdict.notes:
            rationale = f"{rationale}\n\nStage 4b: {verdict.notes}"

        confidence = verdict.confidence if verdict else candidate.match_rate
        accepted = is_accepted(
            confidence=confidence,
            # With no verdict the measured overlap is the only evidence there
            # is, so it is treated as the judgement rather than discarded.
            judged_real=verdict.accepted if verdict else True,
        )

        InferredRelationship.objects.create(
            workbook=workbook,
            from_entity=from_entity,
            from_field=from_field,
            to_entity=to_entity,
            to_field=to_field,
            name=verdict.name if verdict else
                 f"{candidate.from_table}.{candidate.from_column} -> "
                 f"{candidate.to_table}.{candidate.to_column}",
            cardinality=(
                Cardinality.ONE_TO_ONE
                if candidate.cardinality == "one_to_one"
                else Cardinality.MANY_TO_ONE
            ),
            match_rate=candidate.match_rate,
            # Measured overlap when nothing judged it; the agent's judgement of
            # MEANING when it did. These are deliberately different quantities.
            confidence=confidence,
            rejected=not accepted,
            # Nothing waits for a human verdict any more: the threshold is the
            # verdict. An unjudged candidate arrives with a low confidence from
            # stage 4b and is therefore dropped, which is the safe direction.
            needs_review=False,
            rationale=rationale,
        )
        count += 1
    return count
