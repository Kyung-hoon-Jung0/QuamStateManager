"""Real N-of-M progress from long-running loops (docs/126 r3).

The brand-area loading indicator (NavProgress, app.js) shows elapsed time by
default because a synchronous request reports no progress — this module is how
a hot loop OPTS IN to reporting real counts. ``/api/progress`` exposes the
newest active operation; the client polls it only while the indicator is
visible (and the background-backfill poller feeds the same display directly),
so an idle app pays nothing.

Honesty rules: an operation reports a count it is actually incrementing over a
total it actually knows — nothing here estimates, extrapolates, or invents a
percentage. An operation that dies without ``finish()`` (a raised loop) is
swept by age so a stale counter can never outlive its work.
"""

from __future__ import annotations

import itertools
import threading
import time
from typing import Any

_lock = threading.Lock()
_ops: dict[int, dict[str, Any]] = {}
_ids = itertools.count(1)

# A crashed loop must not pin a live-looking counter: current() sweeps
# anything not stepped for this long. Generous — a single ingest step on a
# slow network mount can take tens of seconds.
_STALE_S = 300.0


class Progress:
    """Context manager a loop steps through.

    >>> with Progress("Rebuilding change index", total=len(merged)) as p:
    ...     for item in merged:
    ...         ...
    ...         p.step()
    """

    def __init__(self, label: str, total: int | None = None):
        self.id = next(_ids)
        with _lock:
            _ops[self.id] = {"label": label, "done": 0, "total": total,
                             "t": time.monotonic()}

    def step(self, inc: int = 1, *, done: int | None = None,
             total: int | None = None) -> None:
        with _lock:
            op = _ops.get(self.id)
            if op is None:
                return
            op["done"] = done if done is not None else op["done"] + inc
            if total is not None:
                op["total"] = total
            op["t"] = time.monotonic()

    def finish(self) -> None:
        with _lock:
            _ops.pop(self.id, None)

    def __enter__(self) -> "Progress":
        return self

    def __exit__(self, *exc: object) -> None:
        self.finish()


def current() -> dict[str, Any] | None:
    """The newest active operation (or None) — what /api/progress serves."""
    now = time.monotonic()
    with _lock:
        for op_id in sorted(_ops, reverse=True):
            op = _ops[op_id]
            if now - op["t"] > _STALE_S:
                del _ops[op_id]
                continue
            return {"label": op["label"], "done": op["done"],
                    "total": op["total"]}
    return None
