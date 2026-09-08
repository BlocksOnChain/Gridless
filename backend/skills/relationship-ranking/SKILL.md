---
name: relationship-ranking
description: Judge measured foreign-key candidates between spreadsheet tables, deciding which are real relationships and which are coincidental value overlap. Use after deterministic value-overlap detection has produced a candidate list. Covers lookup tables vs coincidence, direction and cardinality, and the rule that no relationship may be added that was not measured.
---

# Ranking measured relationship candidates

Every candidate you are shown has already been **measured**: at least 80% of the
distinct values in the source column exist in the target column, and the target
column is a candidate key (unique and complete across every row). Columns that
are boolean, JSON, temporal, or have fewer than three distinct values were
excluded before you saw anything.

So the question is never "do these values overlap" — they demonstrably do. The
question is **"does this overlap mean anything?"**

## The one rule that matters

**You may only judge candidates in the list. You may not add one.**

If you think `Orders.customer_email` should point at `Customers.email` but it is
not in the candidate list, then the measurement said otherwise and the
measurement wins. Say it in `notes` if you like; do not emit it. The validator
discards any relationship whose `(from, to)` pair was not measured, and
inventing joins from column names alone is the single most damaging failure this
system can produce — it silently corrupts every downstream query.

## How to decide

For each candidate, ask what the source column *is about*, then what the target
column *is about*, and whether the first is a reference to the second.

### Accept

- **Explicit key match.** `Policies.Customer ID → Customers.Customer ID`. Same
  concept, same name, one side unique. This is the easy majority.
- **Renamed key.** `Claims.Policy Ref → Policies.Policy No`. Names differ but
  both clearly denote the same identifier.
- **Genuine lookup table.** `Policies.Status → Status Codes.Status`, where the
  target table exists to enumerate the values. A small target table whose key
  column *is* the vocabulary is a real foreign key, not a coincidence. Low
  cardinality is not disqualifying when the target is a lookup.
- **Code-to-name reference.** `Policies.Product → Products.Product Name`, where
  the target row describes the thing the source names.

### Reject

- **Shared vocabulary, different subject.** `Customers.City → Branches.Branch
  City` overlaps completely, because customers and branches are in the same six
  cities. A customer's city is a property of the customer, not a reference to a
  branch. This is the classic false positive and the main reason you exist.
- **The wrong column of the right table.** `Policies.Status → Status Codes.Meaning`
  where `Meaning` holds `Active`/`Lapsed` and `Status` holds `active`/`lapsed`.
  The comparison is case-insensitive, so both columns match. Accept the one that
  is the *key* of the lookup; reject the one that is its *label*.
- **Coincidental numeric range.** Two unrelated integer columns that happen to
  occupy the same small range (`Head Count → Floor Number`).
- **Both sides are the same denormalised export.** Two sheets that are slices of
  one table, not two entities in a relationship.

### Direction

The source is the **many** side; the target is the **one** side. The measurement
already enforces that the target is unique. If the source is *also* unique the
candidate is marked `one_to_one` — check that before agreeing: a true 1:1 is
usually either two halves of one entity (accept, and note they might be merged)
or a coincidence of a small fixture (reject).

If a candidate's direction is backwards — the target is the thing that should
point at the source — reject it and say so in `notes`. Do not emit the reverse;
it was not measured.

## Naming

Give each accepted relationship a name a developer would write on a diagram:
`policy_customer`, `claim_policy`, `policy_product`. snake_case,
`<from_entity>_<to_entity>`, singular on both sides. This becomes the label in
the review screen's relationship panel.

## Confidence

`confidence` is *your* judgement of meaning, and it is deliberately separate from
`match_rate`, which is the measured overlap. Do not copy one into the other.

| Range | Means |
|---|---|
| 0.9–1.0 | Names and concepts align unmistakably; this is the FK a developer would write |
| 0.7–0.9 | Clearly a reference, but the naming required interpretation |
| 0.4–0.7 | Plausible; a reviewer should look |
| < 0.4 | You are rejecting it |

A candidate with `match_rate: 1.00` and `confidence: 0.2` is a perfectly normal
output. That combination is exactly what the deterministic pass cannot express
and what you were added to provide.

## Worked example

Candidates given:

```
[0] Policies.Customer ID -> Customers.Customer ID   match=1.00  many_to_one  n=43
[1] Customers.City       -> Branches.Branch City    match=1.00  many_to_one  n=6
[2] Policies.Status      -> Status Codes.Status     match=1.00  many_to_one  n=3
[3] Policies.Status      -> Status Codes.Meaning    match=1.00  many_to_one  n=3
```

Correct response:

```json
{
  "judgements": [
    {"index": 0, "accept": true,  "name": "policy_customer", "confidence": 0.98,
     "notes": "Same identifier on both sides; Customers.Customer ID is the key."},
    {"index": 1, "accept": false, "name": null, "confidence": 0.15,
     "notes": "Customers and branches share the same six cities. A customer's city describes the customer; it is not a reference to a branch."},
    {"index": 2, "accept": true,  "name": "policy_status", "confidence": 0.88,
     "notes": "Status Codes is a lookup table and Status is its key column."},
    {"index": 3, "accept": false, "name": null, "confidence": 0.2,
     "notes": "Meaning is the human label of the same lookup row, not its key. Candidate 2 is the correct target column."}
  ]
}
```

Two accepted, two rejected, every rejection explained. Note that 1 and 3 both had
a perfect match rate — value overlap alone could not have separated them.
