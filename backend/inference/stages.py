"""Stages 3 and 4b -- the LLM half of the pipeline, run as deep agents.

Stage 3 names one table's entity and fields and picks a primary key.
Stage 4b judges the *measured* relationship candidates from `relationships.py`.

Both are pure functions over `AnalysedTable` objects and return validated
dataclasses. They touch no database, so `scripts/eval.py` runs exactly the code
the API runs.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from inference.llm.agent import build_agent, structured_result
from inference.llm.schemas import EntityProposal, RelationshipJudgements
from inference.llm.validate import (
    JudgedRelationship,
    ValidatedEntity,
    validate_entity_proposal,
    validate_relationship_judgements,
)
from inference.relationships import RelationshipCandidate
from ingest.analyse import AnalysedTable

STAGE3_PROMPT = """\
You propose a normalised relational schema for one table extracted from a \
spreadsheet.

Use the `schema-inference` skill. Read it before answering -- it holds the slug \
rules, the primary-key criteria, and the naming conventions this system depends \
on.

You are given the column profile. It is complete and it is ground truth. Do not \
add columns, do not omit columns, and do not change any data type."""

STAGE4_PROMPT = """\
You judge candidate foreign keys between tables from one spreadsheet workbook.

Use the `relationship-ranking` skill. Read it before answering -- it explains \
how to tell a real lookup table from coincidental value overlap, which is the \
whole job.

Every candidate has already been measured: high value overlap, unique target. \
You decide which ones MEAN something. You may only judge candidates in the list; \
you may not add one. Use `column_samples` when you need to see actual values to \
decide.

Return exactly one judgement per candidate."""


# --------------------------------------------------------------------------
# Prompt payloads -- what the agent actually sees
# --------------------------------------------------------------------------


def profile_for(table: AnalysedTable) -> dict[str, Any]:
    """The column profile. Names, measured types, uniqueness, five samples.

    Never the whole sheet: the model does not need the data to name a column,
    and sending it would be both expensive and an invitation to over-fit to
    whatever happened to be in the first rows.
    """
    return {
        "sheet": table.name,
        "detected_header_row": table.header_row + 1,
        "row_count": table.row_count,
        "columns": [
            {
                "source_header": c.header,
                "column_letter": c.letter,
                "data_type": c.data_type,
                "type_confidence": round(c.confidence, 3),
                "nullable": c.nullable,
                "unique": c.is_unique,
                "distinct_values": c.distinct_count,
                "samples": c.sample_values,
            }
            for c in table.columns
        ],
    }


def candidates_payload(candidates: list[RelationshipCandidate]) -> list[dict[str, Any]]:
    return [
        {
            "index": i,
            "from": f"{c.from_table}.{c.from_column}",
            "to": f"{c.to_table}.{c.to_column}",
            "match_rate": c.match_rate,
            "cardinality": c.cardinality,
            "distinct_source_values": c.distinct_source,
            "distinct_target_values": c.distinct_target,
        }
        for i, c in enumerate(candidates)
    ]


# --------------------------------------------------------------------------
# Stage 3 -- entity and key proposal
# --------------------------------------------------------------------------


def propose_entity(table: AnalysedTable, org_slugs: set[str]) -> ValidatedEntity:
    agent = build_agent(system_prompt=STAGE3_PROMPT, response_schema=EntityProposal)
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Propose the entity for this table.\n\n"
                        f"```json\n{json.dumps(profile_for(table), indent=2, default=str)}\n```"
                    ),
                }
            ]
        }
    )
    proposal = structured_result(result, EntityProposal)
    return validate_entity_proposal(proposal, table, org_slugs)


# --------------------------------------------------------------------------
# Stage 4b -- relationship ranking
# --------------------------------------------------------------------------


def _samples_tool(tables: list[AnalysedTable]):
    """A tool that can only return values from columns that exist.

    This is the point of running stage 4b as an agent rather than a single call:
    deciding whether `Status Codes` is a lookup table or a coincidence is much
    easier with the actual values in front of you, and the tool makes those
    reachable without putting every column of every sheet in the prompt.
    """
    index = {(t.name, c.header): c for t in tables for c in t.columns}

    @tool
    def column_samples(table: str, column: str) -> str:
        """Return up to 12 distinct sample values for one column.

        Args:
            table: The sheet/table name exactly as shown in the candidate list.
            column: The column header exactly as shown in the candidate list.
        """
        col = index.get((table, column))
        if col is None:
            known = sorted({t for (t, _) in index})
            return (
                f"No column {column!r} on table {table!r}. "
                f"Known tables: {', '.join(known)}."
            )
        seen: list[str] = []
        for v in col.values:
            s = str(v).strip()
            if s and s not in seen:
                seen.append(s)
            if len(seen) == 12:
                break
        return json.dumps(
            {
                "table": table,
                "column": column,
                "data_type": col.data_type,
                "distinct_values": col.distinct_count,
                "rows": len(col.values),
                "unique": col.is_unique,
                "samples": seen,
            }
        )

    return column_samples


def judge_relationships(
    tables: list[AnalysedTable], candidates: list[RelationshipCandidate]
) -> tuple[list[JudgedRelationship], list[str]]:
    if not candidates:
        return [], []

    agent = build_agent(
        system_prompt=STAGE4_PROMPT,
        response_schema=RelationshipJudgements,
        tools=[_samples_tool(tables)],
    )
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Judge these measured candidates.\n\n"
                        f"```json\n{json.dumps(candidates_payload(candidates), indent=2)}\n```"
                    ),
                }
            ]
        }
    )
    judged = structured_result(result, RelationshipJudgements)
    return validate_relationship_judgements(judged, candidates)
