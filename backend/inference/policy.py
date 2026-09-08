"""Who decides whether a relationship is real.

The answer is: the ranking stage, against a fixed threshold — never the person
reviewing the workbook. That person came here with a spreadsheet and a problem
they describe in their own vocabulary; "is Claims.Policy No a foreign key to
Policies.Policy No?" is not a question they can answer, and putting it in front
of them made the review screen look like a database tool.

So the verdict is mechanical:

    accepted  <=>  the stage judged it a real reference AND its confidence in
                   that judgement is at least RELATIONSHIP_ACCEPT_THRESHOLD

Everything else is dropped. That direction is deliberate: a dropped relationship
costs one graph edge, while a wrongly accepted one silently joins unrelated rows
and poisons every answer built on top of it. When no judgement exists at all
(the deterministic-only pipeline, run without an LLM), the measured overlap
stands in for the judgement — it is the only evidence there is.
"""

from __future__ import annotations

from django.conf import settings


def threshold() -> float:
    return float(settings.RELATIONSHIP_ACCEPT_THRESHOLD)


def is_accepted(*, confidence: float, judged_real: bool = True) -> bool:
    """The whole policy, in one place so analysis and commit cannot disagree."""
    return bool(judged_real) and confidence >= threshold()


def resolve_workbook_relationships(workbook) -> list[str]:
    """Apply the policy to every stored relationship. Returns the dropped names.

    Runs again at commit time rather than trusting what analysis wrote: a
    workbook analysed before the threshold moved (or before this policy existed)
    still has rows marked `needs_review`, and a commit must never be blocked by
    a decision nobody is going to make.
    """
    dropped: list[str] = []
    for rel in workbook.relationships.all():
        # An explicit human override wins over the threshold. Nothing in the
        # review UI sets this today; the API that can is still there, and a
        # decision a person actually made should not be silently reversed.
        if rel.user_confirmed and not rel.rejected:
            if rel.needs_review:
                rel.needs_review = False
                rel.save(update_fields=["needs_review"])
            continue

        rejected = not is_accepted(confidence=rel.confidence, judged_real=not rel.rejected)
        if rejected != rel.rejected or rel.needs_review:
            rel.rejected = rejected
            rel.needs_review = False
            rel.save(update_fields=["rejected", "needs_review"])
        if rejected:
            dropped.append(rel.name or str(rel))
    return dropped
