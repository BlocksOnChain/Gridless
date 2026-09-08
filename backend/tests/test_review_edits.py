"""Hand edits and chat edits to a proposal.

The chat's *tools* are tested here, not the agent: the tools are what can
actually change a proposal, and they are the only place a hallucinated table or
column could take effect. They run without an API key, so this file is part of
the ordinary suite rather than something that needs the network.
"""

import pytest
from django.test import override_settings

from core.models import Organization, Workbook
from core.review import ReviewEditError, add_manual_field, remove_field, set_primary_key
from inference.assist import build_tools, describe_proposal
from inference.policy import resolve_workbook_relationships
from ingest.pipeline import run_structure_and_types

pytestmark = pytest.mark.django_db(transaction=True)

FIXTURE = "fixtures/synthetic/relational.xlsx"


@pytest.fixture
def proposal():
    from pathlib import Path

    from django.core.files import File

    source = Path(FIXTURE)
    if not source.exists():
        pytest.skip("run scripts/make_fixtures.py first")

    org = Organization.objects.create(name="Test Org")
    workbook = Workbook(organization=org, original_filename=source.name)
    with source.open("rb") as fh:
        workbook.file.save(source.name, File(fh), save=True)
    run_structure_and_types(workbook, use_llm=False)
    return workbook


def tools_by_name(workbook, changes):
    return {t.name: t for t in build_tools(workbook, changes)}


# --- hand edits -----------------------------------------------------------


def test_a_hand_added_column_is_marked_as_having_no_source_cell(proposal):
    entity = proposal.entities.get(table_slug="customer")
    field = add_manual_field(entity, "Renewal Owner", "text")

    # The empty source_header is what the commit path reads to know this column
    # has nothing behind it in the sheet.
    assert field.source_header == ""
    assert field.column_slug == "renewal_owner"
    assert field.user_confirmed and field.confidence == 1.0
    assert field.position > max(
        f.position for f in entity.fields.exclude(pk=field.pk)
    )


def test_a_duplicate_column_name_is_refused(proposal):
    entity = proposal.entities.get(table_slug="customer")
    with pytest.raises(ReviewEditError, match="already has a column"):
        add_manual_field(entity, entity.fields.first().name)


def test_an_unusable_type_is_refused(proposal):
    entity = proposal.entities.get(table_slug="customer")
    with pytest.raises(ReviewEditError, match="is not a type"):
        add_manual_field(entity, "Whatever", "money")


def test_the_last_column_cannot_be_removed(proposal):
    entity = proposal.entities.get(table_slug="customer")
    fields = list(entity.fields.all())
    for f in fields[1:]:
        remove_field(f)
    with pytest.raises(ReviewEditError, match="no columns left"):
        remove_field(entity.fields.get())


def test_setting_the_id_column_clears_the_previous_one(proposal):
    entity = proposal.entities.get(table_slug="customer")
    other = entity.fields.exclude(is_primary_key=True).first()
    set_primary_key(other)
    assert list(entity.fields.filter(is_primary_key=True)) == [other]


# --- chat tools -----------------------------------------------------------


def test_the_chat_can_rename_and_retype_a_column(proposal):
    changes: list[str] = []
    tools = tools_by_name(proposal, changes)

    tools["rename_column"].invoke(
        {"table": "Customer", "column": "Full Name", "new_name": "Customer Name"}
    )
    tools["set_column_type"].invoke(
        {"table": "Customer", "column": "Customer Name", "data_type": "text"}
    )

    field = proposal.entities.get(table_slug="customer").fields.get(
        column_slug="full_name"
    )
    assert field.name == "Customer Name"
    assert len(changes) == 2


def test_the_chat_resolves_a_table_named_in_the_plural(proposal):
    """People say "Customers"; the entity is called "Customer"."""
    changes: list[str] = []
    tools = tools_by_name(proposal, changes)

    result = tools["add_column"].invoke(
        {"table": "Customers", "name": "Renewal Owner", "data_type": "text"}
    )
    assert result.startswith("OK")
    assert proposal.entities.get(table_slug="customer").fields.filter(
        column_slug="renewal_owner"
    ).exists()


def test_the_chat_can_find_a_column_by_its_spreadsheet_header(proposal):
    entity = proposal.entities.get(table_slug="customer")
    field = entity.fields.get(column_slug="full_name")
    field.name = "Something Else Entirely"
    field.save(update_fields=["name"])

    changes: list[str] = []
    tools = tools_by_name(proposal, changes)
    result = tools["rename_column"].invoke(
        {"table": "Customer", "column": field.source_header, "new_name": "Customer Name"}
    )
    assert result.startswith("OK")


def test_an_invented_table_changes_nothing_and_lists_the_real_ones(proposal):
    changes: list[str] = []
    tools = tools_by_name(proposal, changes)

    result = tools["add_column"].invoke(
        {"table": "Invoices", "name": "Total", "data_type": "numeric"}
    )
    assert result.startswith("ERROR")
    assert "Customer" in result, "the agent needs the real names to correct itself"
    assert changes == []


def test_an_invented_column_changes_nothing(proposal):
    changes: list[str] = []
    tools = tools_by_name(proposal, changes)

    result = tools["rename_column"].invoke(
        {"table": "Customer", "column": "Nonexistent", "new_name": "Whatever"}
    )
    assert result.startswith("ERROR")
    assert changes == []


def test_the_chat_records_only_changes_it_actually_made(proposal):
    changes: list[str] = []
    tools = tools_by_name(proposal, changes)

    tools["rename_table"].invoke({"table": "Customer", "new_name": "Client"})
    tools["rename_table"].invoke({"table": "Nope", "new_name": "Whatever"})

    assert len(changes) == 1
    assert "Client" in changes[0]


def test_the_proposal_description_names_tables_columns_and_samples(proposal):
    text = describe_proposal(proposal)
    assert 'Table "Customer"' in text
    assert "Full Name" in text
    assert "e.g." in text, "sample values are what make a type request decidable"


# --- relationship policy --------------------------------------------------


@override_settings(RELATIONSHIP_ACCEPT_THRESHOLD=0.9)
def test_relationships_are_resolved_by_the_threshold_not_by_a_person(proposal):
    rels = list(proposal.relationships.all())
    assert len(rels) >= 2

    high, low = rels[0], rels[1]
    high.confidence, high.rejected, high.needs_review = 0.95, False, True
    high.save(update_fields=["confidence", "rejected", "needs_review"])
    low.confidence, low.rejected, low.needs_review = 0.5, False, True
    low.save(update_fields=["confidence", "rejected", "needs_review"])

    dropped = resolve_workbook_relationships(proposal)

    high.refresh_from_db()
    low.refresh_from_db()
    assert not high.rejected and not high.needs_review
    assert low.rejected and not low.needs_review
    assert low.name in dropped and high.name not in dropped


@override_settings(RELATIONSHIP_ACCEPT_THRESHOLD=0.9)
def test_a_decision_a_person_actually_made_survives_the_threshold(proposal):
    """Nothing in the UI sets this today, but the API can, and reversing a
    human's explicit accept would be worse than keeping a weak edge."""
    rel = proposal.relationships.first()
    rel.confidence, rel.rejected, rel.user_confirmed = 0.2, False, True
    rel.save(update_fields=["confidence", "rejected", "user_confirmed"])

    resolve_workbook_relationships(proposal)

    rel.refresh_from_db()
    assert not rel.rejected
