"""Pydantic models for LLM structured output.

**This is the only module in the project that imports Pydantic.**

Django-Bolt validates HTTP bodies with msgspec, and everything at the API edge
is a `msgspec.Struct`. LangChain's structured output needs Pydantic. Rather than
let the two leak into each other, Pydantic is confined here and the validator
converts these models into plain dataclasses before anything else sees them.

Nothing in these schemas is trusted. `validate.py` re-checks every field against
the real parsed workbook, because the shapes below only guarantee that the model
returned well-formed JSON -- not that it returned *true* JSON.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class FieldProposal(BaseModel):
    """One column, renamed. `source_header` is the join back to reality."""

    source_header: str = Field(
        description=(
            "The column's header EXACTLY as it appeared in the profile you were "
            "given. This is how the field is matched back to the real column. "
            "A value not present in the profile is discarded."
        )
    )
    name: str = Field(description="Human-readable field name, e.g. 'Customer ID'.")
    column_slug: str = Field(
        description="snake_case identifier matching ^[a-z][a-z0-9_]{0,62}$, unique within the entity."
    )


class EntityProposal(BaseModel):
    """Stage 3 output for one table."""

    entity_name: str = Field(description="Singular, title case, the thing one row is. e.g. 'Policy'.")
    table_slug: str = Field(
        description="snake_case singular identifier matching ^[a-z][a-z0-9_]{0,62}$, not a reserved word."
    )
    primary_key: str | None = Field(
        default=None,
        description=(
            "The `source_header` of the primary key column, or null if no column "
            "qualifies. Must be a column the profile reports as unique and "
            "non-nullable. Null is a correct and common answer."
        ),
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="How confident you are in this proposal overall."
    )
    notes: str = Field(
        default="",
        description="Why you named things this way; required when confidence is below 0.9.",
    )
    fields: list[FieldProposal] = Field(
        description="One entry per column in the profile. Do not add or omit columns."
    )


class RelationshipJudgement(BaseModel):
    """A verdict on one *measured* candidate. Candidates cannot be added."""

    index: int = Field(description="The index of the candidate in the list you were given.")
    accept: bool = Field(description="True if this is a real foreign key, false if coincidence.")
    name: str | None = Field(
        default=None,
        description="snake_case <from_entity>_<to_entity> name when accepted, null when rejected.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Your judgement of MEANING, deliberately independent of the measured "
            "match_rate. A perfect match_rate with low confidence is a normal answer."
        ),
    )
    notes: str = Field(default="", description="Why. Required for every rejection.")


class RelationshipJudgements(BaseModel):
    """Stage 4b output for one workbook."""

    judgements: list[RelationshipJudgement] = Field(
        description="One judgement per candidate you were shown, in any order."
    )


class AssistAnswer(BaseModel):
    """The review chat's reply. The *changes* are carried by the tool calls, not
    by this text -- so a reply that claims a change no tool made is visibly a
    claim, and the UI shows the tool log next to it."""

    reply: str = Field(
        description=(
            "At most three short sentences, in the user's own language, saying "
            "what you changed. Plain business words: tables, columns, ID column."
        )
    )


class SqlAnswer(BaseModel):
    """/api/ask output."""

    sql: str = Field(description="A single read-only SELECT against the org's typed views.")
    answer: str = Field(description="One paragraph explaining the rows, stating the filter applied.")
