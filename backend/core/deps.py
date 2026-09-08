"""Organization resolution -- the single seam between "who is asking" and the
rest of the system.

In v1 there is no auth, so the org comes from the `X-Org-Id` header. That header
is spoofable: org scoping is a correctness boundary, not a security one. Turning
on real auth means rewriting `resolve_org` to read `request.user` and setting
BOLT_AUTHENTICATION_CLASSES / BOLT_DEFAULT_PERMISSION_CLASSES in settings.
Nothing else in the codebase should need to change.

Two framework details this file exists to encapsulate:

1. The header is declared as a handler *parameter*, not read off `request`.
   Django-Bolt only materialises the parts of a request the handler's signature
   asks for, so a helper that reaches into `request.headers` from another module
   sees nothing.
2. The annotation is `str | None`, not `int | None`. Bolt treats an aliased
   header typed as `int` as required and raises before the handler runs, which
   turns a missing header into a 500. Taking it as a string and parsing it here
   keeps a client error a 400.
"""

from __future__ import annotations

import functools
from typing import Annotated

from asgiref.sync import sync_to_async
from django.db import close_old_connections
from django_bolt.exceptions import BadRequest, NotFound
from django_bolt.param_functions import Header, Query

from core.models import Organization

#: Run a slow sync handler on its own thread instead of the shared one.
#:
#: `sync_to_async` defaults to `thread_sensitive=True`, which runs *every* sync
#: call on one shared executor thread. That default is right for short queries
#: and catastrophic for the long ones: while `analyse` spends two minutes in the
#: LLM stages, every other database-touching endpoint queues behind it on that
#: single thread. The event loop stays responsive -- `/docs` answers in
#: milliseconds -- so the symptom is not "the server is down" but "every page
#: that reads data hangs, and the container's own health check fails", which is
#: a much harder thing to recognise.
#:
#: Use this for handlers measured in seconds or minutes (LLM calls, commit, row
#: loading). Do NOT use it as the default: `thread_sensitive=True` is what keeps
#: short sync ORM work predictable, and each offloaded call costs a thread and a
#: database connection of its own.
def offload(func):
    @functools.wraps(func)
    def run(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        finally:
            # Nothing else will: a connection opened in one of these threads is
            # outside the request/response cycle Django closes connections on,
            # so without this the pool accumulates idle connections -- one per
            # thread, held for the life of the process.
            close_old_connections()

    return sync_to_async(run, thread_sensitive=False)

#: Declare on any handler that needs the org: `x_org_id: OrgHeader = None`.
OrgHeader = Annotated[str | None, Header(alias="X-Org-Id")]

#: For SSE endpoints only: the browser's EventSource cannot set headers, so the
#: progress stream takes the org in the query string instead.
OrgQuery = Annotated[str | None, Query(alias="org_id")]


async def resolve_org(raw: str | None, *, source: str = "X-Org-Id header") -> Organization:
    if raw is None or str(raw).strip() == "":
        raise BadRequest(
            detail=f"Missing organization: send the {source}.",
            extra={"header": "X-Org-Id", "query_param": "org_id"},
        )
    try:
        org_id = int(str(raw).strip())
    except ValueError:
        raise BadRequest(detail=f"Organization id must be an integer, got {raw!r}")

    org = await Organization.objects.filter(pk=org_id).afirst()
    if org is None:
        raise NotFound(detail=f"Organization {org_id} not found")
    return org
