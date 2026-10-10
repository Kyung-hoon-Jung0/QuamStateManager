"""Finish a Datasets index off the request threads, in the interpreter's quiet
moments (v1.3.0 perf, A1).

A data folder's first index is built in a bounded cold scan (docs/142: 3 s,
newest dates first); the rest used to be finished by whichever request called
``rescan_if_stale`` next with no deadline -- on a cold chip open, the live
strip's poll on the landing page, which then parsed ~3,500 runs on a request
thread while the chip's history catch-up shared the interpreter with it (the
catch-up took ~22 s instead of ~9 s, and a click that landed in the scan
waited 6-16 s for its page).

This worker finishes it instead: one daemon thread, kicked whenever a store is
left still filling. Before each step it waits for a quiet interpreter -- no
history catch-up building, no read index being prepared, no user request in
flight (``should_yield``) -- and each step scans under
:func:`dataset.yielding_scan`, so a request or a catch-up that starts mid-step
stops it at its next small chunk (the scan's own truncation semantics: the next
step continues exactly where it stopped). The Datasets page itself is never
gated: its requests scan as they always did.

Correctness never depends on this module: a store it has not finished is the
same truncated store a render already handles ("still filling").
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from quam_state_manager.core import dataset

logger = logging.getLogger(__name__)

#: One step's budget (the walk is bounded by it; the parse also stops early on
#: ``should_yield``). Each step re-walks the days still being read, so short
#: steps cost throughput (2 s steps with 32-run chunks: ~25% over one pass).
STEP_S = 5.0
#: A step that parsed nothing (its walk used the whole budget) gets twice the
#: budget next time, up to this.
MAX_STEP_S = 30.0
#: How often a waiting worker looks again.
IDLE_POLL_S = 0.2
#: The longest one step waits for quiet: a catch-up that never ends (a ledger
#: that keeps failing) slows the index to one small chunk per wait, never stops it.
MAX_WAIT_S = 60.0


class IndexWarmer:
    """``kick()`` never blocks; the thread (``start()``) steps every store that
    is still filling until none is. Without the thread, ``run_once()`` does one
    pass in the caller's thread (tests)."""

    def __init__(self, stores: Callable[[], list[Any]], should_yield: Callable[[], bool], *,
                 step_s: float = STEP_S, idle_poll_s: float = IDLE_POLL_S,
                 max_wait_s: float = MAX_WAIT_S):
        self._stores = stores
        self._should_yield = should_yield
        self.step_s = float(step_s)
        self.idle_poll_s = float(idle_poll_s)
        self.max_wait_s = float(max_wait_s)
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._budget: dict[int, float] = {}
        self.steps = 0
        self.waited_s = 0.0
        self.errors = 0

    def wait_quiet(self) -> float:
        """Block until ``should_yield()`` is false (or ``max_wait_s``)."""
        t0 = time.monotonic()
        while not self._stop.is_set():
            try:
                busy = self._should_yield()
            except Exception:  # noqa: BLE001 -- a broken probe never wedges the worker
                busy = False
            if not busy or time.monotonic() - t0 >= self.max_wait_s:
                break
            self._stop.wait(self.idle_poll_s)
        waited = time.monotonic() - t0
        self.waited_s += waited
        return waited

    def run_once(self) -> bool:
        """One step on every store still filling (each after a quiet moment).
        Returns whether any store is still filling."""
        remaining = False
        self.wait_quiet()
        with dataset.yielding_scan(self._should_yield):
            try:
                stores = list(self._stores() or ())
            except Exception:  # noqa: BLE001
                self.errors += 1
                logger.exception("dataset index warmer: could not resolve the stores")
                return False
            for store in stores:
                if self._stop.is_set():
                    return False
                if not getattr(store, "scan_truncated", False):
                    continue
                self.wait_quiet()
                budget = self._budget.get(id(store), self.step_s)
                before = getattr(store, "generation", None)
                try:
                    store.rescan_if_stale(deadline=time.monotonic() + budget)
                    self.steps += 1
                except Exception:  # noqa: BLE001 -- one bad folder never stops the others
                    self.errors += 1
                    logger.exception("dataset index warmer: a step failed for %s",
                                     getattr(store, "folder_path", store))
                    continue
                still = bool(getattr(store, "scan_truncated", False))
                if still and getattr(store, "generation", None) == before:
                    # nothing parsed: the walk alone used the budget (a slow
                    # share re-listing a big day it had cut) -- give it more
                    self._budget[id(store)] = min(budget * 2.0, MAX_STEP_S)
                else:
                    self._budget.pop(id(store), None)
                remaining = remaining or still
        return remaining

    # -- the thread -------------------------------------------------------
    def kick(self) -> None:
        self._wake.set()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="sm-datasets-index",
                                                daemon=True)
                self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=5.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.wait()
            self._wake.clear()
            try:
                while not self._stop.is_set() and self.run_once():
                    pass
            except Exception:  # noqa: BLE001 -- run_once guards; belt and braces
                self.errors += 1
                logger.exception("dataset index warmer: pass failed")
