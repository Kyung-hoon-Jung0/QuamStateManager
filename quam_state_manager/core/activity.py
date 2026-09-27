"""Foreground request activity, for background work that should yield (RAM P10).

A background build (the search-index prewarm) that runs while a page renders
competes with it for the GIL: measured on a 30-qubit chip, a prewarm started
right after ``POST /load`` pushed the cold ``/bulk`` TTFB from 13.6 s to 23.7 s.
This module answers one question -- "is a foreground request running, or did
one just run?" -- so such work can wait for a quiet server and pause while a
request is in flight.

Long polls (a request that blocks by design, waiting for something to change)
are not foreground work: counting them would make the server look busy
forever. They are named in :data:`LONG_POLL_PATHS`.
"""

from __future__ import annotations

import threading
import time

#: Requests that block by design (docs/141 §4p ``/datasets/wait``) or stream,
#: plus the background polls and the agent's held node waits (RAM P7: ``/api/
#: agent/run/<key>?wait_s=`` and ``POST /api/agent/run-node`` hold the request
#: open up to 3,600 s while an agent waits on a node -- exactly when runs land
#: -- so counting them would stall every background step for its full bound).
#: ONE rule for every yield-to-foreground mechanism (the search-index prewarm
#: here, ``run_ingest.FOREGROUND`` for the run-watch tick): ``is_foreground``.
LONG_POLL_PATHS = frozenset({"/datasets/wait", "/datasets/poll", "/workspace/tree/poll",
                             "/workbench/watch", "/api/agent/run-node"})
#: Requests that are not a user waiting on a page.
EXEMPT_PREFIXES = ("/static/", "/debug/", "/api/agent/run/")


def is_foreground(path: str | None) -> bool:
    """Does a request to *path* count as a user waiting on a page?"""
    p = path or ""
    return not (p in LONG_POLL_PATHS or p.startswith(EXEMPT_PREFIXES))

#: How long the server must have been quiet before :func:`busy` says no.
QUIET_S = 0.5

_LOCK = threading.Lock()
_STATE = {"inflight": 0, "last": 0.0}
_LOCAL = threading.local()


def begin(path: str | None) -> None:
    """A request started (``before_request``). Idempotent per thread: a
    missed ``end`` on a reused worker thread is closed first."""
    if getattr(_LOCAL, "open", False):
        end()
    if not is_foreground(path):
        return
    with _LOCK:
        _STATE["inflight"] += 1
        _STATE["last"] = time.monotonic()
    _LOCAL.open = True


def end() -> None:
    """The request on this thread finished (``teardown_request``)."""
    if not getattr(_LOCAL, "open", False):
        return
    _LOCAL.open = False
    with _LOCK:
        _STATE["inflight"] = max(0, _STATE["inflight"] - 1)
        _STATE["last"] = time.monotonic()


def busy(quiet_s: float = QUIET_S) -> bool:
    """True while a foreground request is in flight or one began/ended in the
    last *quiet_s* seconds."""
    with _LOCK:
        return (_STATE["inflight"] > 0
                or time.monotonic() - _STATE["last"] < quiet_s)


def wait_quiet(poll_s: float = 0.05, stop=None) -> None:
    """Block until :func:`busy` is false (or ``stop()`` is true)."""
    while busy():
        if stop is not None and stop():
            return
        time.sleep(poll_s)
