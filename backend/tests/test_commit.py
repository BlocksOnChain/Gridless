"""Stage 5 commit against a real Postgres.

These are the tests that matter most for safety: the commit path is the only
place identifiers reach SQL by interpolation, and the only place a partial write
could corrupt an org's data.
"""

import pytest
from django.db import connection

from commitdata.ddl import DATA_TYPE_TO_PG
from commitdata.loader import CommitError, commit_workbook, preflight
from core.models import Organization, Workbook, WorkbookStatus
from ingest.pipeline import run_structure_and_types

pytestmark = pytest.mark.django_db(transaction=True)

FIXTURE = "fixtures/synthetic/relational.xlsx"


@pytest.fixture
def committed_workbook(tmp_path):
    from pathlib import Path

    from django.core.files import File

    source = Path(FIXTURE)
    if not source.exists():
        pytest.skip("run scripts/make_fixtures.py first")

    org = Organization.objects.create(name="Test Org")
    workbook = Workbook(organization=org, original_filename=source.name)
    with source.open("rb") as fh:
        workbook.file.save(source.name, File(fh), save=True)
    # No LLM: these tests must not depend on a network call or an API key.
    run_structure_and_types(workbook, use_llm=False)
    return workbook


def test_preflight_passes_for_a_clean_proposal(committed_workbook):
    assert preflight(committed_workbook) == []


def test_an_undecided_relationship_does_not_block_the_commit(committed_workbook):
    """Relationships are decided by the threshold, never by the reviewer.

    The review screen shows no relationships at all, so a row still marked
    `needs_review` (analysed under an older build) must not be able to block a
    commit nobody can unblock.
    """
    rel = committed_workbook.relationships.first()
    assert rel is not None
    rel.needs_review = True
    rel.save(update_fields=["needs_review"])

    assert preflight(committed_workbook) == []

    commit_workbook(committed_workbook)
    rel.refresh_from_db()
    assert not rel.needs_review


def test_commit_drops_relationships_below_the_threshold(committed_workbook):
    from django.test import override_settings

    rel = committed_workbook.relationships.filter(rejected=False).first()
    assert rel is not None
    rel.confidence = 0.6  # judged, but not confidently enough
    rel.save(update_fields=["confidence"])

    with override_settings(RELATIONSHIP_ACCEPT_THRESHOLD=0.9):
        result = commit_workbook(committed_workbook)

    rel.refresh_from_db()
    assert rel.rejected, "a relationship under the threshold must not reach the graph"
    assert rel.name in result.skipped_relationships
    assert not rel.edges.exists()


def test_a_lookup_relationship_at_the_default_threshold_still_builds_edges(committed_workbook):
    """0.85 exists so a real lookup reference survives.

    The ranking stage judges Policy.Status -> Status Code.Status in the high
    eighties. Keeping the relationship is only worth anything if it reaches the
    graph, so this asserts the edges, not merely the `rejected` flag.
    """
    rel = committed_workbook.relationships.filter(rejected=False).first()
    assert rel is not None
    rel.confidence = 0.88
    rel.save(update_fields=["confidence"])

    result = commit_workbook(committed_workbook)

    rel.refresh_from_db()
    assert not rel.rejected
    assert rel.name not in result.skipped_relationships
    assert rel.edges.exists(), "a kept relationship must produce edges"


def test_a_hand_added_column_commits_as_an_empty_column(committed_workbook):
    """A column added on the review screen has no cell behind it in the sheet.

    It has to survive the commit as a real, empty, typed column -- that is the
    whole point of adding one -- rather than failing the "column is no longer in
    the sheet" check that guards against a changed file.
    """
    entity = committed_workbook.entities.get(table_slug="customer")
    entity.fields.create(
        name="Renewal Owner",
        source_header="",
        column_slug="renewal_owner",
        data_type="text",
        position=99,
        sample_values=[],
    )

    result = commit_workbook(committed_workbook)

    with connection.cursor() as cur:
        cur.execute(
            f'SELECT renewal_owner FROM "{result.schema}"."customer" LIMIT 5'
        )
        values = [r[0] for r in cur.fetchall()]
    assert values and all(v is None for v in values)


def test_commit_creates_typed_views(committed_workbook):
    result = commit_workbook(committed_workbook)
    committed_workbook.refresh_from_db()

    assert committed_workbook.status == WorkbookStatus.COMMITTED
    assert result.records > 0
    assert len(result.views) == committed_workbook.entities.count()

    entity = committed_workbook.entities.get(table_slug="customer")
    with connection.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = %s AND table_name = %s",
            [result.schema, "customer"],
        )
        types = dict(cur.fetchall())

    # Every field is projected with the Postgres type its data_type maps to,
    # not as text.
    for f in entity.fields.all():
        assert f.column_slug in types, f.column_slug
    assert types["customer_id"] in ("bigint",)
    assert types["joined"] == "date"
    assert types["active"] == "boolean"
    assert types["lifetime_value"] == "numeric"


def test_committed_rows_are_queryable_through_the_view(committed_workbook):
    result = commit_workbook(committed_workbook)
    with connection.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{result.schema}"."customer"')
        assert cur.fetchone()[0] == 50


def test_edges_agree_with_the_typed_join(committed_workbook):
    result = commit_workbook(committed_workbook)
    if result.edges == 0:
        pytest.skip("no confirmed relationships in this proposal")
    with connection.cursor() as cur:
        cur.execute(
            f'SELECT count(*) FROM "{result.schema}"."policy" p '
            f'JOIN "{result.schema}"."customer" c ON c.customer_id = p.customer_id'
        )
        via_join = cur.fetchone()[0]
        # Count only the policy -> customer relationship. A policy row also has
        # edges for its other confirmed relationships, so an unfiltered count
        # would be a multiple of this.
        cur.execute(
            "SELECT count(*) FROM edge e "
            "JOIN core_inferredrelationship r ON r.id = e.relationship_id "
            "JOIN core_inferredentity fe ON fe.id = r.from_entity_id "
            "JOIN core_inferredentity te ON te.id = r.to_entity_id "
            "WHERE r.workbook_id = %s AND fe.table_slug = 'policy' "
            "AND te.table_slug = 'customer'",
            [committed_workbook.pk],
        )
        via_graph = cur.fetchone()[0]
    assert via_graph == via_join


def test_recommit_replaces_rather_than_duplicates(committed_workbook):
    first = commit_workbook(committed_workbook)
    second = commit_workbook(committed_workbook)
    assert second.records == first.records
    with connection.cursor() as cur:
        cur.execute(f'SELECT count(*) FROM "{second.schema}"."customer"')
        assert cur.fetchone()[0] == 50


def test_unsafe_slug_blocks_the_commit(committed_workbook):
    entity = committed_workbook.entities.first()
    # Bypass the API's validation to simulate a corrupted row.
    Workbook.objects.filter(pk=committed_workbook.pk).update()
    type(entity).objects.filter(pk=entity.pk).update(table_slug="drop table record")
    problems = preflight(committed_workbook)
    assert any("unsafe table slug" in p for p in problems)
    with pytest.raises(CommitError):
        commit_workbook(committed_workbook)


def test_every_data_type_has_a_postgres_mapping():
    from core.models import DataType

    for value, _ in DataType.choices:
        assert value in DATA_TYPE_TO_PG
