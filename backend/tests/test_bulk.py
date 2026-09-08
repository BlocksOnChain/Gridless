"""Bulk edits over committed records.

This is the code that deletes a company's rows, so the tests are about what it
*refuses* as much as what it does: an unknown column, a plan with no conditions,
a value of the wrong type, and a confirmation that arrives with the plan rewritten
on the way back from the browser.

The chat's tools are tested here too (no API key needed): they are the only path
from a sentence to a plan, and they must never write.
"""

import pytest

from core.models import Organization, Workbook
from commitdata.loader import commit_workbook
from inference.table_assist import build_tools, describe_table
from ingest.pipeline import run_structure_and_types
from records import bulk

pytestmark = pytest.mark.django_db(transaction=True)

FIXTURE = "fixtures/synthetic/relational.xlsx"


@pytest.fixture
def committed():
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
    commit_workbook(workbook)
    return org, workbook.entities.get(table_slug="customer")


def count(org, entity) -> int:
    plan = bulk.BulkPlan(op=bulk.COUNT, match_all=True)
    return bulk.preview(org, entity, plan).total


# --- what it refuses ------------------------------------------------------


def test_a_plan_with_no_conditions_is_refused(committed):
    org, entity = committed
    with pytest.raises(bulk.PlanError, match="every row"):
        bulk.preview(org, entity, bulk.BulkPlan(op=bulk.DELETE))


def test_an_invented_column_is_refused_and_the_real_ones_are_named(committed):
    org, entity = committed
    plan = bulk.BulkPlan(
        op=bulk.DELETE,
        where=[bulk.Condition(column="turnover", operator="eq", value=0)],
    )
    with pytest.raises(bulk.PlanError) as caught:
        bulk.preview(org, entity, plan)
    # The message is what the agent reads to correct itself.
    assert "city" in str(caught.value.detail)


def test_a_value_of_the_wrong_type_is_refused(committed):
    org, entity = committed
    plan = bulk.BulkPlan(
        op=bulk.DELETE,
        where=[bulk.Condition(column="lifetime_value", operator="eq", value="lots")],
    )
    with pytest.raises(bulk.PlanError, match="numeric"):
        bulk.preview(org, entity, plan)


def test_an_update_with_nothing_to_set_is_refused(committed):
    org, entity = committed
    plan = bulk.BulkPlan(
        op=bulk.UPDATE,
        where=[bulk.Condition(column="city", operator="eq", value="Istanbul")],
    )
    with pytest.raises(bulk.PlanError, match="at least one column"):
        bulk.preview(org, entity, plan)


def test_comparing_with_nothing_points_at_the_right_operator(committed):
    org, entity = committed
    plan = bulk.BulkPlan(
        op=bulk.DELETE, where=[bulk.Condition(column="city", operator="eq", value=None)]
    )
    with pytest.raises(bulk.PlanError, match="is_empty"):
        bulk.preview(org, entity, plan)


def test_a_tampered_plan_is_re_validated_on_apply(committed):
    """The browser confirms; it does not authorise. A plan that was previewed as
    one thing and posted back as another must be checked again."""
    org, entity = committed
    tampered = bulk.BulkPlan(
        op=bulk.DELETE, where=[bulk.Condition(column="../etc", operator="eq", value=1)]
    )
    with pytest.raises(bulk.PlanError):
        bulk.apply(org, entity, tampered)
    assert count(org, entity) > 0


# --- what it does ---------------------------------------------------------


def test_preview_counts_and_samples_without_changing_anything(committed):
    org, entity = committed
    before = count(org, entity)
    plan = bulk.BulkPlan(
        op=bulk.DELETE,
        where=[bulk.Condition(column="city", operator="eq", value="Istanbul")],
    )

    preview = bulk.preview(org, entity, plan)

    assert 0 < preview.matched < preview.total
    assert len(preview.sample) <= bulk.SAMPLE_ROWS
    assert "cannot be undone" in preview.summary
    assert count(org, entity) == before, "a preview must not delete anything"


def test_delete_removes_exactly_the_matching_rows(committed):
    org, entity = committed
    plan = bulk.BulkPlan(
        op=bulk.DELETE,
        where=[bulk.Condition(column="city", operator="eq", value="Istanbul")],
    )
    preview = bulk.preview(org, entity, plan)
    before = preview.total

    result = bulk.apply(org, entity, plan)

    assert result.affected == preview.matched
    assert count(org, entity) == before - preview.matched
    # And nothing matching the condition is left.
    assert bulk.preview(org, entity, plan).matched == 0


def test_a_numeric_condition_compares_as_a_number_not_as_text(committed):
    """The whole reason conditions run against the typed view.

    Against raw JSONB, `lifetime_value > 1000` would compare strings and put
    "999.5" above "1000".
    """
    org, entity = committed
    plan = bulk.BulkPlan(
        op=bulk.COUNT,
        where=[bulk.Condition(column="lifetime_value", operator="gt", value=1000)],
    )
    matched = bulk.preview(org, entity, plan).matched

    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM "org_{org.pk}"."customer" WHERE lifetime_value > 1000'
        )
        assert matched == cur.fetchone()[0]


def test_update_sets_only_the_named_column_on_matching_rows(committed):
    org, entity = committed
    plan = bulk.BulkPlan(
        op=bulk.UPDATE,
        where=[bulk.Condition(column="city", operator="eq", value="Istanbul")],
        changes={"city": "Ankara"},
    )
    preview = bulk.preview(org, entity, plan)

    from django.db import connection

    with connection.cursor() as cur:
        cur.execute(
            f'SELECT count(*), count(full_name) FROM "org_{org.pk}"."customer"'
        )
        rows_before, names_before = cur.fetchone()

    result = bulk.apply(org, entity, plan)

    assert result.affected == preview.matched
    with connection.cursor() as cur:
        cur.execute(
            f'SELECT count(*), count(full_name) FROM "org_{org.pk}"."customer"'
        )
        assert cur.fetchone() == (rows_before, names_before), "other columns untouched"
    assert bulk.preview(org, entity, plan).matched == 0


def test_match_all_is_honoured_when_it_is_explicit(committed):
    org, entity = committed
    plan = bulk.BulkPlan(op=bulk.DELETE, match_all=True)

    preview = bulk.preview(org, entity, plan)
    assert preview.affects_everything

    bulk.apply(org, entity, plan)
    assert count(org, entity) == 0


def test_a_deletion_that_matches_nothing_is_not_an_error(committed):
    org, entity = committed
    before = count(org, entity)
    plan = bulk.BulkPlan(
        op=bulk.DELETE,
        where=[bulk.Condition(column="city", operator="eq", value="Atlantis")],
    )
    assert bulk.preview(org, entity, plan).matched == 0
    assert bulk.apply(org, entity, plan).affected == 0
    assert count(org, entity) == before


def test_one_entity_cannot_be_used_to_edit_another(committed):
    """The plan carries no table name: the endpoint supplies the entity."""
    org, customer = committed
    policy = customer.workbook.entities.get(table_slug="policy")
    before_policies = count(org, policy)

    bulk.apply(org, customer, bulk.BulkPlan(op=bulk.DELETE, match_all=True))

    assert count(org, policy) == before_policies


def test_deleting_a_row_that_other_rows_reference_removes_its_edges(committed):
    """Nothing cascades in raw SQL.

    `Edge` declares `on_delete=CASCADE`, but Django applies that in Python
    during an ORM delete; the committed data is written and deleted with raw
    SQL, so the DELETE succeeds and the *transaction* then fails at COMMIT with
    a foreign key violation naming `edge`. Deleting a customer that has policies
    is the ordinary case, so this is the ordinary path, not an edge case.
    """
    from django.db import connection

    org, customer = committed
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM edge e JOIN record r ON r.id = e.to_record_id "
            "WHERE r.entity_id = %s",
            [customer.pk],
        )
        edges_before = cur.fetchone()[0]
    assert edges_before > 0, "the fixture is supposed to have relationships"

    result = bulk.apply(org, customer, bulk.BulkPlan(op=bulk.DELETE, match_all=True))

    assert result.affected > 0
    with connection.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM edge e JOIN record r ON r.id = e.to_record_id "
            "WHERE r.entity_id = %s",
            [customer.pk],
        )
        assert cur.fetchone()[0] == 0


# --- the chat's tools -----------------------------------------------------


def tools(org, entity, state):
    return {t.name: t for t in build_tools(org, entity, state)}


def test_the_chat_prepares_a_deletion_without_performing_it(committed):
    org, entity = committed
    before = count(org, entity)
    state: dict = {}

    result = tools(org, entity, state)["plan_delete"].invoke(
        {"conditions": [{"column": "city", "operator": "eq", "value": "Istanbul"}]}
    )

    assert "PREPARED" in result and "nothing has changed yet" in result
    assert state["plan"].op == bulk.DELETE
    assert state["preview"].matched > 0
    assert count(org, entity) == before, "preparing must never delete"


def test_the_chat_reports_an_invented_column_instead_of_preparing(committed):
    org, entity = committed
    state: dict = {}

    result = tools(org, entity, state)["plan_delete"].invoke(
        {"conditions": [{"column": "nonsense", "operator": "eq", "value": 1}]}
    )

    assert result.startswith("ERROR")
    assert "plan" not in state


def test_the_chat_accepts_a_single_condition_object(committed):
    """Models produce one dict as readily as a list of one."""
    org, entity = committed
    state: dict = {}

    result = tools(org, entity, state)["count_rows"].invoke(
        {"conditions": {"column": "city", "operator": "eq", "value": "Istanbul"}}
    )
    assert result.startswith("PREPARED")
    assert "plan" not in state, "a count is not a change to confirm"
    assert state["lookups"]


def test_counting_is_not_applicable(committed):
    org, entity = committed
    with pytest.raises(bulk.PlanError, match="nothing to apply"):
        bulk.apply(org, entity, bulk.BulkPlan(op=bulk.COUNT, match_all=True))


def test_the_table_description_gives_the_agent_slugs_and_samples(committed):
    org, entity = committed
    text = describe_table(entity, count(org, entity))
    assert "city" in text and "Full Name" in text
    assert "e.g." in text
    assert "is_empty" in text, "the operator list is part of what it needs"


# --- the reply, when the model answers in prose ---------------------------


def test_a_prose_reply_is_used_when_the_structured_one_is_missing():
    """Observed with glm-5.3-flash: having just used a real tool, the model
    answers in plain text instead of calling the structured-output tool. The
    plan came from the tools either way, so the sentence is kept rather than the
    turn failed."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    from inference.llm.agent import final_text

    result = {
        "structured_response": None,
        "messages": [
            HumanMessage(content="remove all records with 0 Total (KG)"),
            AIMessage(content=""),
            ToolMessage(content="PREPARED ...", tool_call_id="1"),
            AIMessage(content="I prepared a deletion of the 31 rows."),
        ],
    }
    assert final_text(result) == "I prepared a deletion of the 31 rows."


def test_prose_reply_reads_block_style_content():
    from langchain_core.messages import AIMessage

    from inference.llm.agent import final_text

    result = {
        "messages": [
            AIMessage(content=[{"type": "text", "text": "Prepared the deletion."}])
        ]
    }
    assert final_text(result) == "Prepared the deletion."


def test_no_messages_at_all_yields_no_text():
    from inference.llm.agent import final_text

    assert final_text({}) == ""
