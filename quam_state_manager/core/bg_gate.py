"""Keep background CPU work off a live write's critical path (w7/livewrite).

SM starts helper threads after a content change -- the generated-config
decision (a full pulse-index build), the deferred Param-History index insert,
the throttled post-apply snapshot. Each is correct to run in the background,
but Python runs one thread at a time: on a 19 MB chip the pulse-index build
alone took ~0.7 s of the GIL *inside* a Pull & apply that was still on its way
to the live write (measured: ``_replay_updates`` 576-785 ms with the thread
running, not listed at all without it).

The contract is small:

* a request that writes the chip runs inside :func:`critical`;
* a background job calls :func:`wait_quiet` before its CPU-heavy part, and
  waits until no critical section is open -- bounded by ``max_wait`` so it is
  delayed, never starved, and never deadlocked on a request that waits for it;
* a job that runs synchronously on a thread that is itself inside a critical
  section (the non-deferred fallbacks) never waits on itself.

Only WHEN background work starts changes; what it computes does not.
"""
from __future__ import annotations

import contextlib
import threading
import time

_cv = threading.Condition()
_active = 0
_local = threading.local()

DEFAULT_MAX_WAIT_S = 5.0


@contextlib.contextmanager
def critical():
    """Mark the calling thread's work as a live-write critical section."""
    global _active
    with _cv:
        _active += 1
    _local.depth = getattr(_local, "depth", 0) + 1
    try:
        yield
    finally:
        _local.depth -= 1
        with _cv:
            _active -= 1
            if _active == 0:
                _cv.notify_all()


def busy() -> bool:
    with _cv:
        return _active > 0


def wait_quiet(max_wait: float = DEFAULT_MAX_WAIT_S) -> float:
    """Block until no critical section is open (or *max_wait* passed).
    Returns the seconds waited. Never waits on the caller's own section."""
    if getattr(_local, "depth", 0) > 0:
        return 0.0
    t0 = time.monotonic()
    with _cv:
        _cv.wait_for(lambda: _active == 0, timeout=max_wait)
    return time.monotonic() - t0
