---
name: text-to-sql
description: Answer a natural-language question about committed spreadsheet data by writing a single read-only Postgres SELECT against generated typed views, then explaining the result. Use for /api/ask. Covers the view/graph schema, the guard that will reject unsafe SQL, multi-hop questions via the edge table, and the requirement that every number be auditable.
---

# Answering questions over committed data

You write **one** Postgres `SELECT`. It is validated before it runs and executed
as a powerless read-only role. Then you explain the rows in a paragraph.

The user always sees your SQL next to your answer. Write it to be read.

## What you are querying

Committed rows live in a single JSONB table, but you never touch it. Every
committed entity has a **typed view** in the organisation's own schema, which
projects the JSONB into real Postgres columns:

```sql
CREATE VIEW org_7.customer AS
SELECT id,
       data->>'name'                AS name,
       (data->>'premium')::numeric  AS premium,
       (data->>'start_date')::date  AS start_date
FROM record WHERE entity_id = 42;
```

So `SELECT name, premium FROM customer WHERE premium > 1000` behaves exactly as
it would against an ordinary table. Types are real: you can compare dates with
`date`, sum a `numeric`, and use `interval` arithmetic.

Use the tools to discover the schema before writing SQL. Never guess a view or
column name — a name that does not exist fails the guard and wastes a turn.

## Rules the guard enforces

Your SQL is parsed with `sqlglot`, not pattern-matched. It is rejected unless:

1. It is **exactly one statement**, and that statement is a `SELECT`.
   No `INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `ALTER`, `GRANT`, `COPY`,
   `CALL`, or `SELECT ... INTO`. No semicolon-separated second statement.
2. Every table it references is a view in **this organisation's** schema.
   A reference to `record`, `edge`, `pg_catalog`, `information_schema`, or
   another org's schema is rejected. (`edge` is reachable through the graph
   helper described below, not by naming it directly.)
3. It has a `LIMIT`. If you omit one the guard adds it; if yours is too large it
   is lowered. Prefer to write your own so the SQL the user reads is the SQL
   that ran.
4. No data-modifying CTE (`WITH x AS (INSERT ...)`).

Read-only CTEs, joins, window functions, aggregates, `UNION`, and subqueries are
all fine. So is `WITH RECURSIVE`.

## Writing good SQL for this

- **Alias every table** and qualify every column. Views share column names
  (`id`, `name`) constantly, and an unqualified `id` in a join is ambiguous.
- **Round money**: `ROUND(SUM(p.premium), 2)`.
- **Name your output columns.** `AS total_premium`, not `AS sum`. The column
  headers appear in the UI.
- **Order deliberately.** A `LIMIT` without an `ORDER BY` returns arbitrary rows,
  and the user cannot tell.
- **Relative dates** come from `CURRENT_DATE`, not a literal:
  `WHERE p.expires BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '60 days'`.
- **"has no X"** is an anti-join, and `NOT IN` is a trap when the subquery can
  return NULL. Use `NOT EXISTS` or a `LEFT JOIN ... WHERE x.id IS NULL`.

## Multi-hop questions

Confirmed relationships are also materialised as rows in a graph edge table, and
a helper view exposes it safely. Reach for the graph when the question is about
*connection* rather than a specific join path — "customers connected to any
policy that lapsed", "everything touching this claim" — especially when the
number of hops is not fixed. A recursive CTE over edges expresses that; a chain
of joins cannot.

For a known, fixed path, a plain join is clearer and faster. Prefer it.

## Answering

After the rows come back, write **one paragraph**. Lead with the number that was
asked for. State the filter you applied in words, so the user can check that
your interpretation matched their question. If the result set is empty, say so
plainly and say what you looked for — an empty result is information, not a
failure.

Never state a number that is not in the rows you were returned. Never round a
count. Never soften an empty result into "there may be some".

## Worked example

Question: *"Which customers have a policy expiring in the next 60 days and no
renewal record?"*

```sql
SELECT c.id,
       c.full_name,
       p.policy_no,
       p.expires
FROM   policy   AS p
JOIN   customer AS c ON c.customer_id = p.customer_id
WHERE  p.expires BETWEEN CURRENT_DATE AND CURRENT_DATE + INTERVAL '60 days'
AND    NOT EXISTS (
           SELECT 1
           FROM   renewal AS r
           WHERE  r.policy_no = p.policy_no
       )
ORDER  BY p.expires
LIMIT  200;
```

Answer:

> 14 customers have a policy expiring within the next 60 days with no matching
> renewal record. The earliest is Deniz Hoca's POL-5043, which expires on
> 2026-09-19. "No renewal" means no row in `renewal` referencing that policy
> number; policies already renewed under a new number would not be counted.

Note the last sentence: it states the assumption the SQL encodes, so the user can
tell you whether you understood the question.
