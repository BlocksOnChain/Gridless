"""Describe the committed schema to the agent.

This is the payoff for normalising: the agent sees the *model* -- typed columns
and named relationships -- not raw spreadsheet cells.
"""

from __future__ import annotations

from commitdata.ddl import DATA_TYPE_TO_PG, schema_name
from core.models import InferredEntity, Organization, WorkbookStatus


def committed_entities(org: Organization):
    return (
        InferredEntity.objects.filter(
            workbook__organization=org, workbook__status=WorkbookStatus.COMMITTED
        )
        .select_related("workbook")
        .prefetch_related("fields")
        .order_by("table_slug")
    )


def allowed_tables(org: Organization) -> set[str]:
    return set(committed_entities(org).values_list("table_slug", flat=True))


def describe_schema(org: Organization) -> str:
    """A compact DDL-shaped description, which models read more reliably than prose."""
    entities = list(committed_entities(org))
    if not entities:
        return "No entities have been committed yet."

    lines = [f"-- schema: {schema_name(org.pk)} (all views, read-only)"]
    for entity in entities:
        # Two workbooks can each contribute a "Customer"; the slugs differ but
        # the names do not, so the source file is what disambiguates them.
        lines.append(f"\n-- {entity.name} (from {entity.workbook.original_filename})")
        lines.append(f"CREATE VIEW {entity.table_slug} (")
        lines.append("  id bigint,")
        for f in entity.fields.all():
            pg = DATA_TYPE_TO_PG[f.data_type]
            note = "  -- primary key" if f.is_primary_key else ""
            lines.append(f"  {f.column_slug} {pg},{note}")
        lines.append(");")

    rels = []
    for entity in entities:
        for rel in entity.outgoing_relationships.filter(
            rejected=False, needs_review=False, workbook__status=WorkbookStatus.COMMITTED
        ).select_related("to_entity", "from_field", "to_field"):
            rels.append(
                f"-- {rel.from_entity.table_slug}.{rel.from_field.column_slug} "
                f"-> {rel.to_entity.table_slug}.{rel.to_field.column_slug} "
                f"({rel.cardinality})"
            )
    if rels:
        lines.append("\n-- confirmed relationships")
        lines.extend(sorted(set(rels)))
    return "\n".join(lines)
