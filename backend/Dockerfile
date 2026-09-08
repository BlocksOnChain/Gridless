# Gridless API: Django 6 + Django-Bolt (a Rust HTTP server behind a Python API).
#
# Django-Bolt ships abi3 wheels for CPython 3.12+ on manylinux, so this needs no
# Rust toolchain -- but it does need a glibc image: the wheel is manylinux, not
# musl, so Alpine would fall back to building from source.
FROM python:3.13-slim

# uv resolves and installs; it is the same tool the README uses locally, so the
# container and a laptop cannot drift onto different resolvers.
COPY --from=ghcr.io/astral-sh/uv:0.9.9 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Outside the source tree, so a bind mount over /app cannot shadow it.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Dependencies first: this layer is rebuilt only when pyproject.toml changes,
# not on every source edit. `package = false` in pyproject means uv installs
# the dependencies and not the project itself, so there is nothing else to do.
COPY pyproject.toml ./
RUN uv sync --no-dev

COPY . .

EXPOSE 8010

# A health check that hides failure is worthless (see the endpoint's own
# comment), so this reads the real endpoint, which reads the real database.
HEALTHCHECK --interval=5s --timeout=5s --start-period=40s --retries=12 \
    CMD python -c "import urllib.request,sys,json; \
r=json.load(urllib.request.urlopen('http://127.0.0.1:8010/api/health', timeout=4)); \
sys.exit(0 if r['status']=='ok' else 1)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
