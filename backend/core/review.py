"""Edits the review screen can make to a proposal.

These operations have two callers with different error conventions -- the HTTP
endpoints in `core.api`, which raise `BadRequest`, and the chat tools in
`inference.assist`, which return an error *string* for the agent to read and
correct itself against. Both go through here, so "a column added by hand" means
exactly one thing whichever way the user asked for it.

The rules themselves are the reason this is shared, not the code volume: a
hand-added column is identified by an empty `source_header` (the commit path
reads that to decide whether to load values), and a table must keep at least one
column. Getting either wrong in one caller only would be a silent divergence
between clicking and asking.
"""

from __future__ import annotations

from commitdata.ddl import UnsafeIdentifier, safe_ident, unique_slug
from core.models import (
    DataType,
    EntitySection,
    InferredEntity,
    InferredField,
    SectionKind,
)

VALID_TYPES = {c for c, _ in DataType.choices}
VALID_SECTION_KINDS = {c for c, _ in SectionKind.choices}


class ReviewEditError(ValueError):
    """The edit cannot be made, with a message written for a non-technical user."""


def check_type(data_type: str) -> str:
    wanted = (data_type or "").strip().lower()
    if wanted not in VALID_TYPES:
        raise ReviewEditError(
            f"{data_type!r} is not a type. Use one of: {', '.join(sorted(VALID_TYPES))}."
        )
    return wanted


def add_manual_field(
    entity: InferredEntity, name: str, data_type: str = "text"
) -> InferredField:
    """Add a column the spreadsheet did not have. It commits empty."""
    label = (name or "").strip()
    if not label:
        raise ReviewEditError("Give the column a name.")
    wanted = check_type(data_type or "text")

    existing = list(entity.fields.all())
    if any(f.name.lower() == label.lower() for f in existing):
        raise ReviewEditError(f'"{entity.name}" already has a column called "{label}".')

    taken = {f.column_slug for f in existing}
    try:
        slug = safe_ident(unique_slug(label, taken, fallback="field"))
    except UnsafeIdentifier as exc:
        raise ReviewEditError(f"{label!r} cannot be used as a column name ({exc}).")

    # `source_header` stays empty on purpose: it is the marker that this column
    # has no cell behind it in the spreadsheet. `commitdata.loader` reads it to
    # decide whether to load values or leave the column blank.
    return InferredField.objects.create(
        entity=entity,
        name=label,
        source_header="",
        column_slug=slug,
        source_column_letter="",
        data_type=wanted,
        nullable=True,
        is_primary_key=False,
        # A human typed it, so there is nothing to be unsure about.
        confidence=1.0,
        user_confirmed=True,
        sample_values=[],
        position=max((f.position for f in existing), default=-1) + 1,
    )


def remove_field(field: InferredField) -> str:
    """Drop a column from the proposal. Returns its name, for the change log.

    The uploaded file is never touched; the column simply is not imported. Any
    relationship built on it goes with it via the FK cascade, which is correct:
    a reference through a column that no longer exists is not a reference.
    """
    entity = field.entity
    if entity.fields.count() <= 1:
        raise ReviewEditError(
            f'"{entity.name}" would have no columns left. Leave at least one.'
        )
    label = field.name
    field.delete()
    return label


def set_primary_key(field: InferredField) -> None:
    """Make this the entity's ID column, clearing any other."""
    InferredField.objects.filter(entity=field.entity).exclude(pk=field.pk).update(
        is_primary_key=False
    )
    field.is_primary_key = True
    field.user_confirmed = True
    field.save(update_fields=["is_primary_key", "user_confirmed"])


def check_section_kind(kind: str) -> str:
    wanted = (kind or "note").strip().lower()
    if wanted not in VALID_SECTION_KINDS:
        raise ReviewEditError(
            f"{kind!r} is not a section kind. Use one of: "
            f"{', '.join(sorted(VALID_SECTION_KINDS))}."
        )
    return wanted


def add_section(
    entity: InferredEntity, title: str, body: str, kind: str = "note"
) -> EntitySection:
    """Add a section under a table.

    `created_by_user` is what protects it from re-analysis: the pipeline
    replaces everything it inferred from the sheet, and nothing in the sheet
    implies a section a person or the assistant wrote.
    """
    label = (title or "").strip()
    text = (body or "").strip()
    if not label and not text:
        raise ReviewEditError("A section needs a title or some text.")
    return EntitySection.objects.create(
        entity=entity,
        kind=check_section_kind(kind),
        title=label,
        body=text,
        source_range="",
        position=(entity.sections.count() or 0),
        created_by_user=True,
    )
