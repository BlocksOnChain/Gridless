"""The validators are what stand between a confident model and the database.

These tests use hand-built proposals rather than live model output: the point is
to prove the validator catches each failure mode, and a real model would not
reliably produce them on demand.
"""

from dataclasses import dataclass

import pytest

from inference.llm.schemas import (
    EntityProposal,
    FieldProposal,
    RelationshipJudgement,
    RelationshipJudgements,
)
from inference.llm.validate import (
    validate_entity_proposal,
    validate_relationship_judgements,
)
from inference.relationships import RelationshipCandidate
from ingest.analyse import AnalysedColumn, AnalysedTable


def column(header, data_type="text", *, unique=False, nullable=False, n=10):
    values = [f"{header}-{i}" for i in range(n)] if unique else [header] * n
    return AnalysedColumn(
        header=header, letter="A", index=0, values=values, data_type=data_type,
        confidence=1.0, nullable=nullable, sample_values=values[:3], note="",
        distinct_count=n if unique else 1, non_null_count=n,
    )


def table(*columns):
    return AnalysedTable(
        name="Sheet1", sheet_index=0, header_row=0, first_data_row=1,
        last_data_row=10, range_a1="A1:B11", header_score=1.0, raw_row_count=11,
        columns=list(columns),
    )


def proposal(fields, **kw):
    return EntityProposal(
        entity_name=kw.get("entity_name", "Thing"),
        table_slug=kw.get("table_slug", "thing"),
        primary_key=kw.get("primary_key"),
        confidence=kw.get("confidence", 0.95),
        notes=kw.get("notes", ""),
        fields=[FieldProposal(source_header=h, name=n, column_slug=s) for h, n, s in fields],
    )


def test_invented_column_is_discarded():
    t = table(column("Real"))
    v = validate_entity_proposal(
        proposal([("Real", "Real", "real"), ("Ghost", "Ghost", "ghost")]), t, set()
    )
    assert [f.source_header for f in v.fields] == ["Real"]
    assert any("invented" in w and "Ghost" in w for w in v.warnings)


def test_omitted_column_is_restored():
    t = table(column("Kept"), column("Forgotten"))
    v = validate_entity_proposal(proposal([("Kept", "Kept", "kept")]), t, set())
    assert [f.source_header for f in v.fields] == ["Kept", "Forgotten"]
    assert any("Restored" in w for w in v.warnings)


def test_unsafe_slug_is_regenerated():
    t = table(column("Name"))
    v = validate_entity_proposal(
        proposal([("Name", "Name", "select; DROP TABLE record")]), t, set()
    )
    assert v.fields[0].column_slug == "name"
    assert any("Regenerated" in w for w in v.warnings)


def test_reserved_slug_is_regenerated():
    t = table(column("Identifier"))
    v = validate_entity_proposal(proposal([("Identifier", "Identifier", "id")]), t, set()), 
    v = v[0]
    assert v.fields[0].column_slug != "id"


def test_duplicate_slugs_are_disambiguated():
    t = table(column("A"), column("B"))
    v = validate_entity_proposal(
        proposal([("A", "A", "same"), ("B", "B", "same")]), t, set()
    )
    assert len({f.column_slug for f in v.fields}) == 2


def test_invented_primary_key_is_rejected_then_measured_one_is_used():
    """Rejecting the model's key is not a reason to ship no key: the validator
    falls back to the deterministic choice, which is right on every fixture."""
    t = table(column("Real", unique=True))
    v = validate_entity_proposal(
        proposal([("Real", "Real", "real")], primary_key="Nonexistent"), t, set()
    )
    assert any("no such column" in w for w in v.warnings)
    assert any("Fell back to the measured key" in w for w in v.warnings)
    assert [f.source_header for f in v.fields if f.is_primary_key] == ["Real"]


def test_no_fallback_when_no_column_qualifies():
    """A sheet with no unique column still gets no primary key."""
    t = table(column("Status"))  # 1 distinct value over 10 rows
    v = validate_entity_proposal(
        proposal([("Status", "Status", "status")], primary_key="Status"), t, set()
    )
    assert not any(f.is_primary_key for f in v.fields)


def test_primary_key_must_actually_be_unique():
    t = table(column("Status"))  # 1 distinct value over 10 rows
    v = validate_entity_proposal(
        proposal([("Status", "Status", "status")], primary_key="Status"), t, set()
    )
    assert not any(f.is_primary_key for f in v.fields)
    assert any("not unique" in w for w in v.warnings)


def test_implausible_type_is_rejected_as_primary_key():
    """A numeric column unique across a few hundred sampled rows is far more
    likely a coincidence than a key."""
    t = table(column("Amount", "numeric", unique=True))
    v = validate_entity_proposal(
        proposal([("Amount", "Amount", "amount")], primary_key="Amount"), t, set()
    )
    assert not any(f.is_primary_key for f in v.fields)
    assert any("coincidence" in w for w in v.warnings)


def test_valid_primary_key_survives():
    t = table(column("Policy No", unique=True))
    v = validate_entity_proposal(
        proposal([("Policy No", "Policy No", "policy_no")], primary_key="Policy No"), t, set()
    )
    assert [f.is_primary_key for f in v.fields] == [True]
    assert v.warnings == []


def test_corrections_cap_confidence():
    t = table(column("Real"))
    v = validate_entity_proposal(
        proposal([("Real", "Real", "real"), ("Ghost", "G", "g")], confidence=0.99), t, set()
    )
    assert v.confidence <= 0.6


# --- relationship judgements ----------------------------------------------


def candidate(i=0):
    return RelationshipCandidate(
        from_table=f"A{i}", from_column="x", to_table=f"B{i}", to_column="y",
        match_rate=1.0, cardinality="many_to_one", distinct_source=10,
        distinct_target=10, rationale="",
    )


def test_out_of_range_judgement_is_discarded():
    cands = [candidate(0)]
    judged, warnings = validate_relationship_judgements(
        RelationshipJudgements(judgements=[
            RelationshipJudgement(index=7, accept=True, name="a_b", confidence=0.9),
        ]),
        cands,
    )
    assert any("only 1 candidate" in w for w in warnings)
    # The real candidate survives, unjudged and flagged for a human.
    assert len(judged) == 1
    assert judged[0].needs_review and judged[0].confidence == 0.3


def test_unjudged_candidate_needs_review_rather_than_being_accepted():
    """Silence is neither acceptance nor rejection.

    Dropping a measured candidate the model forgot would cost recall invisibly;
    accepting it would let an unjudged join through on a skim. It becomes its
    own state and a human decides.
    """
    judged, warnings = validate_relationship_judgements(
        RelationshipJudgements(judgements=[]), [candidate(0), candidate(1)]
    )
    assert len(judged) == 2
    assert all(j.needs_review for j in judged)
    assert not any(j.accepted for j in judged)
    assert all(j.confidence == 0.3 for j in judged)
    assert len(warnings) == 2


def test_judged_candidates_do_not_need_review():
    judged, _ = validate_relationship_judgements(
        RelationshipJudgements(judgements=[
            RelationshipJudgement(index=0, accept=True, name="a_b", confidence=0.9),
        ]),
        [candidate(0)],
    )
    assert judged[0].accepted and not judged[0].needs_review


def test_verdicts_are_applied():
    cands = [candidate(0), candidate(1)]
    judged, _ = validate_relationship_judgements(
        RelationshipJudgements(judgements=[
            RelationshipJudgement(index=0, accept=True, name="a_b", confidence=0.95),
            RelationshipJudgement(index=1, accept=False, name=None, confidence=0.1,
                                  notes="coincidence"),
        ]),
        cands,
    )
    assert [j.accepted for j in judged] == [True, False]
    assert judged[0].name == "a_b"
    assert judged[1].notes == "coincidence"


def test_no_relationship_can_be_added():
    """There is no code path by which output contains more relationships than
    were measured."""
    cands = [candidate(0)]
    judged, _ = validate_relationship_judgements(
        RelationshipJudgements(judgements=[
            RelationshipJudgement(index=0, accept=True, name="a_b", confidence=0.9),
            RelationshipJudgement(index=1, accept=True, name="ghost", confidence=0.9),
            RelationshipJudgement(index=99, accept=True, name="ghost2", confidence=0.9),
        ]),
        cands,
    )
    assert len(judged) == 1


def test_taken_slug_is_an_adjustment_not_a_correction():
    """A slug already used by another workbook is not the model's mistake, so it
    must not drag the proposal's confidence down."""
    t = table(column("Name"))
    v = validate_entity_proposal(
        proposal([("Name", "Name", "name")], table_slug="customer", confidence=0.97),
        t,
        {"customer"},  # already taken elsewhere in this organisation
    )
    assert v.table_slug != "customer"
    assert v.warnings == []
    assert any("already used elsewhere" in a for a in v.adjustments)
    assert v.confidence == pytest.approx(0.97)


def test_unsafe_slug_still_counts_against_confidence():
    t = table(column("Name"))
    v = validate_entity_proposal(
        proposal([("Name", "Name", "DROP TABLE")], confidence=0.99), t, set()
    )
    assert v.warnings
    assert v.confidence <= 0.6
