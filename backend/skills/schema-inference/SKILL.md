---
name: schema-inference
description: Name an entity and its fields, and pick a primary key, from a spreadsheet table that has already been structurally parsed and type-inferred. Use when proposing a normalised relational schema for one table of an uploaded workbook. Covers snake_case slug rules, singular entity naming, primary-key selection, and the hard rule that no column may be invented.
---

# Proposing an entity from a parsed table

You are naming things, not discovering them. Stage 1 already found the header
row and the data range; stage 2 already typed every column by parse rate. The
columns you are given are the columns that exist. Your job is to turn
spreadsheet vocabulary into a schema a developer would be happy to inherit.

## Non-negotiable rules

1. **Never invent a column.** Every field you return must correspond to a column
   in the profile you were given, matched by its `source_header` exactly. A
   field whose `source_header` is not in the profile is discarded by the
   validator and counts as a failure.
2. **Never drop a column.** If you cannot think of a better name, reuse the
   header. Losing a column silently is worse than an ugly name.
3. **Never change a `data_type`.** Types were measured against up to 500 real
   values. You are not looking at the data; the parse rate is better evidence
   than your intuition. If a type looks wrong, say so in `notes` and leave it.

## Naming

**Entity name** — singular, title case, the thing one row *is*.
A sheet called `Customers` holds a `Customer`. A sheet called `2024 Policy
Register` holds a `Policy`. Strip years, export dates, department names, and
words like "master", "register", "list", "data", "extract", "final", "v2".

**`table_slug`** — snake_case, singular, `^[a-z][a-z0-9_]{0,62}$`.
This becomes a Postgres view name, so it cannot be a reserved word
(`user`, `order`, `select`, `table`, `data`, `id`, …). If the natural slug is
reserved, suffix it: `order` becomes `customer_order`, not `order_col` — prefer
a meaningful qualifier over a mechanical suffix.

**Field names** — expand abbreviations that are unambiguous in context and leave
the ones that are not. `Cust Nm` becomes `Customer Name`. `DOB` becomes `Date of
Birth`. `Prem` in an insurance workbook becomes `Premium`. But `Ref 2` stays
`Ref 2` — inventing meaning is worse than preserving ambiguity.

**`column_slug`** — snake_case, `^[a-z][a-z0-9_]{0,62}$`, unique within the
entity. Drop units from the slug but keep them in the name: `Premium (TRY)`
becomes name `Premium (TRY)`, slug `premium`. Transliterate non-ASCII:
`Poliçe No` becomes `police_no`, `Ürün Adı` becomes `urun_adi`.

## Choosing a primary key

Pick the column that identifies the row to a human, and only if the profile
proves it can:

- `unique` must be `true` and `nullable` must be `false`. The profile tells you
  both. A column that fails either is not a candidate, no matter how much its
  name looks like an identifier.
- Prefer an explicit business key (`Policy No`, `Customer ID`, `SKU`) over a
  positional one (`Row`, `No`, `#`, `Index`).
- Prefer `text` or `integer`. A `date`, `numeric` or `boolean` primary key is
  almost always a coincidence of the sample.
- A name containing `id`, `no`, `code`, `ref`, `key`, `number` is a hint, not
  evidence. Uniqueness is the evidence.

**If no column qualifies, return `primary_key: null`.** That is a correct,
common answer — plenty of real sheets are just row logs. A hallucinated primary
key is worse than none, because commit builds a view around it and the reviewer
trusts the badge.

## Confidence

Report `confidence` between 0 and 1 for the proposal as a whole:

| Range | Means |
|---|---|
| 0.9–1.0 | Headers are clear business terms; the entity is obvious; the key is unambiguous |
| 0.6–0.9 | Names required interpretation, or the key was one of several candidates |
| 0.3–0.6 | Headers are cryptic or generic (`Column1`, `Field 3`); you guessed |
| < 0.3 | You do not believe this is a coherent entity at all |

Be honest and use the low end when it applies. The review screen colours on this
number, and a confident wrong answer costs the user more than a hedged one. Put
the reason in `notes` whenever confidence is below 0.9 — the reviewer sees it.

## Worked example

Profile given:

```
sheet: "2024 Policy Register"
columns:
  A "Policy No"   text     unique=true  nullable=false  samples: POL-5001, POL-5002
  B "Cust ID"     integer  unique=false nullable=false  samples: 1052, 1020
  C "Prem"        numeric  unique=false nullable=false  samples: 3750.55, 5006.70
  D "Sts"         text     unique=false nullable=false  samples: active, lapsed
```

Correct response:

```json
{
  "entity_name": "Policy",
  "table_slug": "policy",
  "primary_key": "Policy No",
  "confidence": 0.93,
  "notes": "Year stripped from the sheet name. 'Prem' read as Premium and 'Sts' as Status from the sample values.",
  "fields": [
    {"source_header": "Policy No", "name": "Policy No",    "column_slug": "policy_no"},
    {"source_header": "Cust ID",   "name": "Customer ID",  "column_slug": "customer_id"},
    {"source_header": "Prem",      "name": "Premium",      "column_slug": "premium"},
    {"source_header": "Sts",       "name": "Status",       "column_slug": "status"}
  ]
}
```

Note what did *not* happen: no `id` column was added, no `created_at` was
invented, `Sts` was not dropped for being cryptic, and the primary key is the
column the profile proved unique.

See `references/slug-rules.md` for the full reserved-word list and
transliteration table.
