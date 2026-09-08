# Gridless

Turn a company's folder of messy `.xlsx` files into a working internal web app:
ingest → infer a normalised relational schema → human review → commit to Postgres →
generated CRUD → natural-language querying.

**The migration is the product.** The defensible part is schema inference on real
messy workbooks (merged cells, headers not in row 1, several tables per sheet, junk
columns). Success is measured, not asserted:

- **>80%** of columns typed correctly
- **>70%** of true relationships detected

`backend/scripts/eval.py` prints both numbers against `backend/fixtures/`.

## Stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 16 (App Router), TypeScript, Tailwind 4 |
| Backend | Python 3.14 + Django 6 + **Django-Bolt** (Rust HTTP server) |
| Database | PostgreSQL 16 |
| Spreadsheet parsing | `openpyxl` |
| Schema inference | Deterministic parsing + **deepagents** harness on OpenRouter (`z-ai/glm-5.3-flash`), with skills and a hard validation layer |
| Packages | `uv` (backend), `pnpm` (frontend) |

## Running it

Everything -- database, API, web -- comes up from one compose file:

```bash
docker compose up --watch
```

Then open <http://localhost:3000>. That is the whole setup: no `uv sync`, no
`pnpm install`, no migrate step. The API container runs its own migrations and
seeds the demo organization on start, both idempotent, so restarting changes
nothing.

`--watch` is what makes it a development environment rather than a demo: edits
to `backend/` sync into the API container, which runs `runbolt --dev` and
reloads; edits to `frontend/` sync into the web container, which runs
`next dev` and hot-reloads. Changing `pyproject.toml` or `package.json`
rebuilds that image, since a dependency change is not something a file sync can
apply. Drop `--watch` for a plain run.

Start order is by health, not by delay: the API waits for `pg_isready` and the
web server waits for the API's own `/api/health`, so a cold start cannot serve
a page that fails on its first fetch.

Ports: Postgres is published on **5433** and the API on **8010** to avoid colliding
with anything already running on 5432/8000. Those are the same ports the local
tooling expects, so `pytest` and the e2e scripts run against these containers
unchanged.

<details>
<summary>Running the services directly on the host instead</summary>

```bash
docker compose up -d db                   # just Postgres

cd backend
uv sync
uv run python manage.py migrate
uv run python manage.py seed_org          # prints the org id to use
uv run python manage.py runbolt --dev --port 8010

cd frontend
pnpm install
pnpm dev
```

`backend/.env` already points at `127.0.0.1:5433`; the compose file overrides
those two values for the container, which reaches Postgres as `db:5432`.

</details>

Then open <http://localhost:3000>: upload a workbook, analyse it, review the proposal,
commit, use the generated screens, and ask a question.

- API docs: <http://127.0.0.1:8010/docs>
- Django Admin (internal inspection for the meta-schema): <http://127.0.0.1:8010/admin/>
  (`uv run python manage.py createsuperuser` first)
- Health: <http://127.0.0.1:8010/api/health>

### Configuration

Copy `backend/.env.example` to `backend/.env`. Without `OPENROUTER_API_KEY` the
pipeline still runs — stages 3 and 4b are skipped and you get the deterministic
proposal, which is complete and usable. `.env` is gitignored.

```
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_MODEL=z-ai/glm-5.3-flash
OPENROUTER_TIMEOUT_MS=180000        # milliseconds, not seconds
OPENROUTER_REASONING_EFFORT=low
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5433
```

`frontend/.env.local` sets `NEXT_PUBLIC_API_BASE` and `NEXT_PUBLIC_ORG_ID`.

The API has **two** base URLs, and the difference matters under Docker.
`NEXT_PUBLIC_API_BASE` is inlined into the client bundle, so it has to be an
address that resolves in the *browser* (`127.0.0.1:8010`). Server components
render inside the container, where `127.0.0.1` is the container itself, so they
use `API_INTERNAL_BASE` (`http://api:8010`) which is read at run time and falls
back to the public URL when unset. See `frontend/lib/api.ts`.

## Reading real workbooks

Two failure modes that come up on actual customer files, both handled in
`ingest/`:

**openpyxl refuses files Excel opens.** A daily production report arrived with
its print-title definition set to `#N/A` -- page-layout scar tissue, nothing to
do with the cells -- and openpyxl raised `ValueError` while parsing reserved
defined names, so upload answered "this file is not a readable .xlsx workbook"
about a perfectly readable file. `ingest/repair.py` retries such a read against
an in-memory copy with those definitions stripped. The uploaded bytes are never
modified, the repair is attempted only after a genuine failure, and it is
narrow on purpose: one element type, only when its value is an Excel error
literal. Anything else still fails loudly, because a file we cannot read for a
reason we do not understand must not be quietly half-imported.

**Long analyses must not freeze the app.** With the LLM stages on, `analyse`
runs for minutes. `sync_to_async` defaults to `thread_sensitive=True`, which
funnels every sync call through one shared thread -- so one analysis put every
other database-touching endpoint in a queue behind it, including
`/api/health`, while `/docs` kept answering in milliseconds. The long handlers
use `core.deps.offload` instead, which gives each its own thread (and closes
its database connection afterwards). `tests/test_concurrency.py` pins that,
since the symptom is easy to misread as "the server is down".

## Sections: what a table cannot hold

A daily production report ends like this, merged across all nine columns and
twenty rows:

```
AÇIKLAMALAR :
*HAT 5 SABAH VARDİYASINDA BIÇAK ARIZASINDAN DOLAYI 2 KERE KAFAYA MAL SARDI...
```

That is the most human information on the sheet, and a table cannot hold it.
The old continuation rule asked only "do these rows put something in the
table's columns?" -- which a paragraph merged across the sheet does trivially --
so twenty rows of prose were imported as data and a "Machine Capacity" column
ended up with the same Turkish sentence in every cell. Information is not
preserved by being stored in the wrong shape.

`ingest/structure.py:classify_block` now recognises two kinds of non-table
block before the table logic sees them: **prose** (a merged paragraph, or a lone
long text cell, optionally introduced by AÇIKLAMALAR / NOTLAR / NOTES / REMARKS)
and **totals** (an explicit TOPLAM / TOTAL label with every row too sparse to be
data). They are stored as `EntitySection` rows and rendered under their table in
the generated app. On the Merit report that is the difference between 75
"records" and the 45 production rows that actually exist.

A merged header gets the same treatment for the same reason.
`MAKİNA KAPASİTESİ` merged across H:I is one column Excel draws wide, but the
reader forward-fills merged ranges, so it arrived as two columns with the same
header and the same values -- and everything downstream then worked to tell the
halves apart: "Machine Capacity" and "Machine Capacity 1", two slugs, two
columns of identical data to edit twice. `_collapse_merged_duplicates` folds
adjacent same-header columns back together when their values are compatible,
keeping the union: Merit's B:C merge is broken on exactly one row, and that
value survives. Seven columns instead of nine.

Sections are also the first thing the assistant can *create*: `add_section`,
`edit_section` and `remove_section` are review-chat tools, so the page can grow
a piece of UI that nobody wrote a component for. Re-analysis rebuilds sections
from the file, discarding hand-written ones -- the same contract as a renamed
field.

## How /api/ask stays safe

Two independent layers, neither trusted alone:

1. **The guard** (`ask/guard.py`) parses with `sqlglot`, not regex. It rejects
   anything that is not exactly one read-only `SELECT`, rejects any table outside
   this org's views (including `record`, `edge`, `pg_catalog`), and enforces a
   `LIMIT`. The SQL shown to the user is the guarded SQL that actually ran.
2. **The database.** Execution happens as `gridless_ro`, which has no `USAGE` on
   `public` (so the base tables are unreachable), `SELECT` only on granted org
   schemas, a statement timeout, and a read-only transaction.

29 tests cover the guard, including stacked statements, data-modifying CTEs,
catalog access and cross-org reads. The role's own refusal is verified too: a
`DELETE` through the read-only path fails with `permission denied` even if the
guard were bypassed.

## Auth: there is none in v1

By explicit decision, v1 runs with `AllowAny` and resolves the organization from an
`X-Org-Id` header (`backend/core/deps.py`). **That header is spoofable** — org scoping
is a correctness boundary here, not a security one. Do not expose this build to an
untrusted network.

The `/api/ask` SQL guard is built as though auth existed regardless: single-`SELECT`
parsing with `sqlglot`, a per-org view allowlist, an enforced `LIMIT`, and execution on
a separate powerless `gridless_ro` role with a statement timeout.

Turning auth on later is two settings plus one function — see `core/deps.py`.

## Layout

```
docker/postgres/init/   read-only role bootstrap
backend/
  gridless/             Django project settings
  core/                 meta-schema models, admin, deps, API
  ingest/               stages 1-2 (structure, types) + the persisting pipeline
  inference/            stage 4a (measured candidates), stages 3 & 4b (deep agents)
    llm/                agent factory, Pydantic schemas, validators
  skills/               SKILL.md domain knowledge loaded by the agents
  commitdata/           stage 5: records, typed views, edges
  records/              runtime CRUD over the generated views
  ask/                  text-to-SQL: schema prompt, guard, read-only execution
  scripts/              make_fixtures.py, eval.py
  tests/                129 tests, incl. commit against real Postgres
frontend/
  app/                  App Router pages
    workbooks/[id]/review/   the review screen
    app/[entitySlug]/        the generated CRUD screens
    ask/                     natural-language querying
  components/           ReviewScreen, RecordTable, RecordForm, …
  lib/api.ts            typed fetch client
  e2e/                  Playwright tests against the running stack
```

## How inference works

Five stages. **The first three run with no model at all**, and they are the ones
carrying the accuracy numbers.

| Stage | What it does | LLM? |
|---|---|---|
| 1 Structure | Unmerge and forward-fill, find the header row, trim totals rows, split multi-table sheets | no |
| 2 Types | Score candidate types by parse rate over ≤500 values, assign at >90% agreement | no |
| 3 Naming | Propose entity name, field names, slugs, primary key | **deep agent** |
| 4a Candidates | Measure value overlap across every column pair; keep overlap >0.8 with a unique target | no |
| 4b Ranking | Decide which measured candidates *mean* something | **deep agent** |
| 5 Commit | Records, typed views, edges, one transaction | no |

### Why a deep agent, and why only there

Stages 3 and 4b run on the [`deepagents`](https://docs.langchain.com/oss/python/deepagents/overview)
harness rather than a hand-wired graph, because the two things it provides map
exactly onto what these stages need:

- **Skills.** The domain rules live in `backend/skills/*/SKILL.md` as versioned
  prose, not concatenated into a Python f-string. They load with progressive
  disclosure, so the agent pays for a skill's full text only when it decides the
  skill applies.
- **Tools as the source of truth.** Stage 4b gets a `column_samples` tool that
  can only return values from columns that exist, so it can inspect a suspected
  lookup table before judging it.

Stages 1, 2 and 4a stay pure Python on purpose. They already score 1.00 on type
accuracy and 1.00 on relationship recall with no model involved; handing that to
an agent could only make it worse. The agent is used where judgement is needed —
naming, and telling a real foreign key from a coincidence.

**Nothing the model returns is trusted.** `inference/llm/validate.py` re-checks
every proposal against the parsed workbook: invented columns are discarded,
omitted columns restored, unsafe or reserved slugs regenerated, and a proposed
primary key is rejected unless the data proves it unique and non-null. A
relationship that was not *measured* cannot be emitted at all. Every correction
is recorded and surfaced on the review screen — a silent correction hides a
signal the reviewer needs.

### Skills

```
backend/skills/
  schema-inference/        SKILL.md + references/slug-rules.md
  relationship-ranking/    SKILL.md
  text-to-sql/             SKILL.md   (used by /api/ask, build step 7)
```

Each is a directory whose name matches the `name:` in its frontmatter. Add a new
one by creating another directory here; it is discovered automatically.

## Measuring it

```bash
cd backend
uv run python scripts/make_fixtures.py   # writes fixtures/synthetic/
uv run python scripts/eval.py            # deterministic only; no API calls
uv run python scripts/eval.py --llm      # + stage 4b deep-agent ranking
uv run python scripts/eval.py --verbose  # plus every individual miss
uv run pytest                            # 129 tests, incl. commit against real Postgres
```

Browser tests drive the real UI against the real backend (`cd frontend`):

```bash
pnpm e2e             # smoke + journey + ask
pnpm e2e:commit      # preflight gate then commit (mutates state, so run alone)
```

Screenshots land in `frontend/e2e/shots/` on every run, including failures.

|  | deterministic only | + stage 4b (deep agent) |
|---|---|---|
| column type accuracy (target >0.80) | **1.00** (70/70) | 1.00 (70/70) |
| primary key accuracy | **1.00** (15/15) | 1.00 (15/15) |
| relationship recall (target >0.70) | **1.00** (6/6) | 1.00 (6/6) |
| relationship precision | 0.75 (6/8) | **1.00** (6/6) |

Both targets pass without any model. What the deep agent adds is **precision**:
the two remaining false positives are hazards planted on purpose — a status
lookup whose `Meaning` column is case-insensitively identical to its key, and a
customer `City` that overlaps a branch `City`. Neither is separable by value
overlap alone, and stage 4b rejects both with a written reason.

It also improves naming in a way the numbers do not capture. On `nightmare.xlsx`
the deterministic pass names the two tables sharing a sheet `Reference` and
`Reference (2)`; stage 3 names them **Product** and **Branch** and picks the
right primary key for each. Where it cannot tell, it says so: the keyless
`By Region` table comes back at confidence 0.60 with `primary_key: null`.

Read all of this as a floor-check on the logic, **not** proof of generalisation —
the fixtures are synthetic and were written alongside the code.

**Real workbooks are what will move these numbers.** Drop an `.xlsx` into
`backend/fixtures/` with a hand-written `<name>.expected.json` in the format
documented at the top of `scripts/make_fixtures.py` — no code change needed.

## Build status

- [x] **1. Skeleton** — compose + Postgres, meta-schema models & migrations, admin, `runbolt` serving `/api/health`, Next.js shell reading it
- [x] **2. Upload + stages 1 & 2** — `POST /api/workbooks`, `POST /api/workbooks/{id}/analyse`, structural + type inference, drag-and-drop upload page and workbook list
- [x] **3. Fixtures + eval** — 7 messy fixtures, `eval.py`, baseline above
- [x] **4. Stages 3 & 4** — deterministic candidate detection, plus deep-agent entity/key proposal and relationship ranking, with validators and skills
- [x] **5. Review screen** — tables and columns only: rename, retype, choose the ID column, add or remove columns by hand, or ask the assistant to do it in words. Confidence scores, relationships and stage notes are deliberately not shown — relationships are settled by the `RELATIONSHIP_ACCEPT_THRESHOLD` (0.85) in `inference/policy.py`, never by the reviewer
- [x] **6. Commit + generated CRUD** — typed views, graph edges, and list/detail/create screens generated from the committed schema
- [x] **7. `/api/ask`** — text-to-SQL with a `sqlglot` guard, read-only execution, and the SQL always shown

The demo works end to end: drag in a messy workbook, watch the schema get
inferred, correct a field by hand, commit, use the generated app, ask a
cross-entity question, get a correct answer with visible SQL.

**Known rough edge.** With the LLM stages on, `POST /api/workbooks/{id}/analyse`
runs synchronously: roughly 15s per table plus 25s for ranking, so ~70s for a
five-table workbook. Fine from a script, too slow for a browser request. The plan
specifies replacing it with the SSE progress stream (`EventSourceResponse`
driving the run, each stage committing as it completes) — that is the main piece
of the plan still outstanding. Without an API key, or with
`run_structure_and_types(wb, use_llm=False)`, the deterministic path returns in
under a second.

## Notes for whoever picks this up

- **Django-Bolt only materialises the parts of a request the handler signature
  asks for.** A helper that reaches into `request.headers` from another module
  sees nothing. Declare what you need as a parameter — see `core/deps.py`.
- **An aliased header typed `int | None` is treated as required** and raises
  before the handler runs, turning a missing header into a 500. Take it as
  `str | None` and parse it yourself.
- **`BoltAPI(prefix=...)` also prefixes ASGI mounts**, including the Django
  admin that Bolt auto-mounts at the prefix it finds in `ROOT_URLCONF`. With
  `prefix="/api"` the admin mounts at `/api/admin` while Django's own patterns
  still say `admin/`, so it 404s and the two can never agree. `core/api.py`
  therefore sets no prefix and spells `/api` into each route path.
- `openpyxl`'s `iter_rows(max_row=N)` materialises N rows even for a 60-row
  sheet. Always pass `min(ws.max_row, CEILING)`. This one cost 20s per workbook.
- **`ChatOpenRouter.request_timeout` is in MILLISECONDS**, not seconds. Setting
  it to `180` gives every request a 0.18s budget; the OpenRouter SDK then retries
  with backoff forever, so the symptom is a hang, not an error. This cost the
  most time of anything in the build.
- **GLM 5.3 Flash is a reasoning model and reasoning cannot be turned off**
  (`"Reasoning is mandatory for this endpoint"`). Left unbounded it spends the
  whole token budget thinking and returns `content: null` with
  `finish_reason: "length"`. Pass `reasoning={"effort": "low"}`.
- `deepagents` hard-depends on `langchain-anthropic` and `langchain-google-genai`
  even when you only use OpenRouter. They install; they are not used.
- **`SET LOCAL` cannot take a bound parameter** in Postgres. `SET LOCAL
  statement_timeout = %s` fails with `syntax error at or near "$1"`; the value
  has to be inlined (safely, from a validated int).
- **Bolt reads an unmarked `dict` handler parameter as a query parameter**, not a
  body. The dynamic record payloads need `Annotated[dict, Body()]`.
- **`safe_ident` rejects `id`** on purpose — a data column must never shadow the
  view's own projected `id`. The system columns (`id`, `created_at`,
  `updated_at`) are therefore a separate fixed allowlist, checked by membership
  rather than by the slug gate.
- `frontend/` contains its own git repository from `create-next-app`. If you
  initialise a repo at the project root, deal with that nested `.git` first.
