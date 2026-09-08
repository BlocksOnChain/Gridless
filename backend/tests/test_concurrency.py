"""The long endpoints must not monopolise the shared sync thread.

`sync_to_async` defaults to `thread_sensitive=True`, which funnels every sync
call through one shared executor thread. With that default on `analyse`, a
two-minute run of the LLM stages queues every other database-touching request
behind it: `/api/health` stops answering, `/` stops rendering, and the
container's own health check goes red -- while `/docs`, which touches no
database, keeps answering in milliseconds. That combination is genuinely hard
to read as "one thread is busy", which is why it is pinned here rather than
left to a comment.
"""

import pytest

from ask.api import _ask
from core.api import _assist, _commit, _run_deterministic

LONG_RUNNING = {
    "core.api._run_deterministic": _run_deterministic,
    "core.api._commit": _commit,
    "core.api._assist": _assist,
    "ask.api._ask": _ask,
}


@pytest.mark.parametrize("name", sorted(LONG_RUNNING))
def test_long_running_handlers_get_their_own_thread(name):
    handler = LONG_RUNNING[name]
    assert handler._thread_sensitive is False, (
        f"{name} runs on the shared sync thread. Decorate it with "
        f"core.deps.offload, not sync_to_async, or every other endpoint "
        f"queues behind it."
    )


def test_short_handlers_stay_on_the_shared_thread():
    """The counterpart: `offload` is not a free upgrade.

    Each offloaded call takes a thread and a database connection of its own, so
    the short reads deliberately keep the default.
    """
    from core.api import _list_workbooks, _load_proposal

    assert _list_workbooks._thread_sensitive is True
    assert _load_proposal._thread_sensitive is True
