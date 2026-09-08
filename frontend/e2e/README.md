# Browser tests

These drive the real UI against the real backend and a real Postgres. Nothing is
mocked, so they need the whole stack running:

```bash
docker compose up -d
cd backend  && uv run python manage.py runbolt --dev --port 8010
cd frontend && pnpm dev
```

Then:

```bash
pnpm e2e            # smoke + journey + ask
pnpm e2e:smoke      # landing, review screen, edits persist, upload errors
pnpm e2e:journey    # generated CRUD: list, sort, filter, create, edit, delete
pnpm e2e:commit     # preflight gate, then commit (mutates state)
pnpm e2e:ask        # text-to-SQL, and that every number shown is auditable
```

Screenshots land in `e2e/shots/` on every run, including on failure, so a broken
assertion can be inspected rather than guessed at.

`e2e:commit` is kept out of `pnpm e2e` because it commits a workbook and so
changes what the other tests see. `smoke`, `journey` and `ask` are idempotent:
they clean up records they create and target data by content rather than by
position.
