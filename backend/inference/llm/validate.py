"""Validate LLM output against the real workbook before it touches the database.

The agent's tools and skills make the common hallucinations hard to express, but
"hard" is not "impossible", and a schema proposal is not the place to find out.
Everything below assumes the model is wrong and checks it against the parsed
table, which is ground truth.

Every correction is recorded in `warnings`, which end up in `Sheet.notes` and are
shown on the review screen. A silent correction is almost as bad as an
uncorrected error -- the reviewer needs to know the model got something wrong
here, because it is a signal about the rest of the proposal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from commitdata.ddl import UnsafeIdentifier, safe_ident, unique_slug
from inference.keys import choose_primary_key
from inference.llm.schemas import EntityProposal, RelationshipJudgements
from inference.relationships import RelationshipCandidate
from ingest.analyse import AnalysedTable

# Types that are almost never a real primary key. A float or timestamp column
# can easily be unique across a few hundred sampled rows by coincidence.
IMPLAUSIBLE_PK_TYPES = {"numeric", "date", "datetime", "boolean", "json"}


@dataclass
class ValidatedField:
    source_header: str
    name: str
    column_slug: str
    is_primary_key: bool = False


@dataclass
class ValidatedEntity:
    name: str
    table_slug: str
    confidence: float
    notes: str
    fields: list[ValidatedField]
    #: The model got something wrong. These cap confidence.
    warnings: list[str] = field(default_factory=list)
    #: Environmental changes the model could not have avoided -- most often a
    #: slug already taken by a different workbook in the same organisation.
    #: Recorded for the reviewer but NOT held against the proposal.
    adjustments: list[str] = field(default_factory=list)


def validate_entity_proposal(
    proposal: EntityProposal, table: AnalysedTable, org_slugs: set[str]
) -> ValidatedEntity:
    """Reconcile a proposal with the columns that actually exist."""
    warnings: list[str] = []
    real_headers = [c.header for c in table.columns]
    by_header = {c.header: c for c in table.columns}

    # 1. Discard invented columns.
    proposed = []
    for f in proposal.fields:
        if f.source_header in by_header:
            proposed.append(f)
        else:
            warnings.append(
                f"Discarded invented field {f.source_header!r} "
                f"(no such column; the sheet has {', '.join(real_headers)})."
            )

    # 2. Collapse duplicates -- first mention wins.
    seen_headers: set[str] = set()
    deduped = []
    for f in proposed:
        if f.source_header in seen_headers:
            warnings.append(f"Dropped duplicate proposal for column {f.source_header!r}.")
            continue
        seen_headers.add(f.source_header)
        deduped.append(f)

    # 3. Restore columns the model omitted. Losing real data because the model
    #    found a header cryptic is not an acceptable outcome.
    adjustments: list[str] = []
    fields: list[ValidatedField] = []
    slugs: set[str] = set()
    by_proposed = {f.source_header: f for f in deduped}

    for header in real_headers:
        f = by_proposed.get(header)
        if f is None:
            warnings.append(f"Restored column {header!r}, which the model omitted.")
            fields.append(
                ValidatedField(
                    source_header=header,
                    name=header,
                    column_slug=unique_slug(header, slugs, fallback="field"),
                )
            )
            continue

        slug = _safe_slug(f.column_slug, header, slugs, warnings, adjustments)
        fields.append(
            ValidatedField(
                source_header=header,
                name=(f.name or header).strip() or header,
                column_slug=slug,
            )
        )

    # 4. The primary key must exist, and the data must actually support it.
    pk = proposal.primary_key
    if pk is not None:
        column = by_header.get(pk)
        if column is None:
            warnings.append(f"Rejected primary key {pk!r}: no such column.")
            pk = None
        elif not column.is_unique:
            warnings.append(
                f"Rejected primary key {pk!r}: {column.distinct_count} distinct "
                f"values across {len(column.values)} rows, so it is not unique."
            )
            pk = None
        elif column.nullable:
            warnings.append(f"Rejected primary key {pk!r}: the column contains blanks.")
            pk = None
        elif column.data_type in IMPLAUSIBLE_PK_TYPES:
            warnings.append(
                f"Rejected primary key {pk!r}: a {column.data_type} column that is "
                f"unique across {len(column.values)} rows is more likely a "
                f"coincidence than a key."
            )
            pk = None

    if pk is None:
        # Rejecting the model's key is not a reason to ship no key. Fall back to
        # the measured choice, which is right on every fixture we have.
        fallback = choose_primary_key(table.columns)
        if fallback is not None:
            pk = fallback.header
            if proposal.primary_key is not None:
                warnings.append(f"Fell back to the measured key {pk!r}.")

    if pk is not None:
        for f in fields:
            if f.source_header == pk:
                f.is_primary_key = True

    # 5. The entity slug is a Postgres view name; it gets the same treatment.
    entity_name = (proposal.entity_name or table.name).strip() or table.name
    table_slug = _safe_slug(
        proposal.table_slug, entity_name, org_slugs, warnings, adjustments, kind="table"
    )

    confidence = min(max(proposal.confidence, 0.0), 1.0)
    if warnings:
        # A proposal we had to *correct* is less trustworthy than one we did
        # not, and the review screen colours on this number. Adjustments are
        # deliberately excluded: penalising a proposal because an unrelated
        # workbook already used the slug would flag items the user cannot act on.
        confidence = min(confidence, 0.6)

    return ValidatedEntity(
        name=entity_name,
        table_slug=table_slug,
        confidence=confidence,
        notes=proposal.notes.strip(),
        fields=fields,
        warnings=warnings,
        adjustments=adjustments,
    )


def _safe_slug(
    candidate: str,
    fallback_source: str,
    taken: set[str],
    warnings: list[str],
    adjustments: list[str],
    *,
    kind: str = "column",
) -> str:
    """Accept the model's slug only if it is genuinely safe and free.

    An unsafe slug is the model's fault and counts against it. A slug that is
    merely *taken* -- usually by an entity in a different workbook of the same
    organisation, since slugs become view names in one shared schema -- is not
    something the model could have known, so it is recorded as an adjustment and
    does not lower confidence.
    """
    cleaned = (candidate or "").strip()
    if cleaned:
        try:
            safe_ident(cleaned)
        except UnsafeIdentifier as exc:
            warnings.append(f"Regenerated {kind} slug {cleaned!r}: {exc}.")
        else:
            if cleaned in taken:
                adjustments.append(
                    f"Renamed {kind} slug {cleaned!r}: already used elsewhere in this "
                    f"organisation. View names share one schema, so they must be unique."
                )
            else:
                taken.add(cleaned)
                return cleaned
    return unique_slug(fallback_source, taken, fallback="entity" if kind == "table" else "field")


@dataclass
class JudgedRelationship:
    candidate: RelationshipCandidate
    accepted: bool
    name: str
    confidence: float
    notes: str
    #: The ranking stage returned no verdict. Neither accepted nor rejected --
    #: the reviewer decides.
    needs_review: bool = False


def validate_relationship_judgements(
    judged: RelationshipJudgements, candidates: list[RelationshipCandidate]
) -> tuple[list[JudgedRelationship], list[str]]:
    """Apply verdicts to measured candidates. Nothing may be added.

    A judgement whose index is out of range is discarded rather than clamped: a
    model that returns index 7 for a list of 4 has lost track of the list, and
    guessing which candidate it meant would be inventing a relationship by
    another route.
    """
    warnings: list[str] = []
    verdicts: dict[int, object] = {}

    for j in judged.judgements:
        if not 0 <= j.index < len(candidates):
            warnings.append(
                f"Discarded judgement for candidate index {j.index}: "
                f"only {len(candidates)} candidate(s) were measured."
            )
            continue
        if j.index in verdicts:
            warnings.append(f"Ignored duplicate judgement for candidate {j.index}.")
            continue
        verdicts[j.index] = j

    results: list[JudgedRelationship] = []
    for i, candidate in enumerate(candidates):
        j = verdicts.get(i)
        if j is None:
            # Unjudged is not the same as rejected. Keep it, flag it low, and let
            # a human decide -- dropping a measured candidate because the model
            # forgot to mention it would silently cost recall.
            warnings.append(
                f"No judgement returned for {candidate.from_table}.{candidate.from_column} "
                f"-> {candidate.to_table}.{candidate.to_column}; kept for review."
            )
            results.append(
                JudgedRelationship(
                    candidate=candidate,
                    accepted=False,
                    name=_default_name(candidate),
                    confidence=0.3,
                    notes="The ranking stage returned no verdict for this candidate, "
                          "so it needs a human decision.",
                    needs_review=True,
                )
            )
            continue

        name = (j.name or "").strip() or _default_name(candidate)
        try:
            safe_ident(name)
        except UnsafeIdentifier:
            name = _default_name(candidate)

        results.append(
            JudgedRelationship(
                candidate=candidate,
                accepted=bool(j.accept),
                name=name,
                confidence=min(max(j.confidence, 0.0), 1.0),
                notes=j.notes.strip(),
            )
        )
    return results, warnings


def _default_name(candidate: RelationshipCandidate) -> str:
    from commitdata.ddl import slugify

    return f"{slugify(candidate.from_table)}_{slugify(candidate.to_table)}"[:63]
