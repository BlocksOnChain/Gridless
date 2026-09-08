-- The role /api/ask executes generated SELECTs as. It is deliberately powerless:
-- no CREATE, no write, and no USAGE on any schema by default. commitdata/ddl.py
-- grants USAGE + SELECT on org_<id> at commit time, one org schema at a time.
CREATE ROLE gridless_ro LOGIN PASSWORD 'gridless_ro' NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;

ALTER ROLE gridless_ro SET default_transaction_read_only = on;
ALTER ROLE gridless_ro SET statement_timeout = '10s';

REVOKE ALL ON DATABASE gridless FROM PUBLIC;
GRANT CONNECT ON DATABASE gridless TO gridless_ro;

-- No USAGE on public: gridless_ro must never read `record` or `edge` directly,
-- only through the per-org views, which are owned by the app role.
REVOKE ALL ON SCHEMA public FROM gridless_ro;
