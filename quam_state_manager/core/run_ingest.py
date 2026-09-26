"""Absorb a new run OFF the request path (design ram_design.md §3, the
"Run-watch tick" row -- the one slice of the later ``sm-warm`` worker that
P3's "after a new run <= 30 ms" target cannot do without).

Without it, the first Trends request after a run landed paid the store's
incremental rescan itself: one ``stat`` per run component in the newest date
directory -- about 150 ms on the KH archive's 680-run day, measured in
process, and 0.7-2 s in the browser where the page's own polls queued on the
same scan lock.

The run watcher (``core/run_watch``) already notices a new run within its
0.5 s poll. On that tick this worker, on its own daemon thread:

1. runs ``DatasetStore.rescan_if_stale`` for the store of every changed root
   (the SAME call every request makes; it is idempotent, and the request's
   own call then finds the gate closed);
2. brings every RAM Trends index already built for that store up to date
   (``trend_index.refresh_store``: the append path for a new newest run) and
   re-encodes the series answers recently asked for from it.

Correctness never depends on this module. Every cache it fills is validated
on read against the store's generations (design §1.1), so a tick that is
late, skipped or racing a request can only cost time, never serve a stale
answer; the pins in tests/test_run_ingest.py check both halves.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

#: One rescan is bounded like the request-path one (routes'
#: ``_RENDER_SCAN_BUDGET_S`` is the same order); a truncated walk leaves the
#: store's gate open and the next tick or request continues it.
RESCAN_BUDGET_S = 2.0


class RunIngest:
    """A single coalescing worker: ``kick(roots)`` never blocks; the thread
    (``start()``) handles every root kicked since its last pass, once.
    Without the thread, ``run_once()`` does a pass in the caller's thread."""

    def __init__(self, resolve_stores: Callable[[list[str]], list[Any]],
                 refresh: Callable[[Any], None] | None = None,
                 budget_s: float = RESCAN_BUDGET_S):
        self._resolve = resolve_stores
        if refresh is None:
            from quam_state_manager.core import trend_index
            refresh = trend_index.refresh_store
        self._refresh = refresh
        self.budget_s = float(budget_s)
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.passes = 0
        self.errors = 0
        self.last_ms = 0.0
        # RAM P7: more precompute riding the same tick, each ``fn(roots)``
        # run after the stores (the workspace sidebar rescan, the alignment
        # verdicts, the Datasets rows, the drawer's run index). Every one of
        # them fills a cache that is validated on read, like the stores'.
        self._after: list[tuple[Callable[[list[str]], None], str]] = []
        self.after_ms: dict[str, float] = {}

    def add_after(self, fn: Callable[[list[str]], None], name: str | None = None) -> None:
        """Register one more precompute step (idempotent per function)."""
        with self._lock:
            if all(f is not fn for f, _n in self._after):
                self._after.append((fn, name or getattr(fn, "__name__", "step")))

    def kick(self, roots: Iterable[str]) -> None:
        with self._lock:
            self._pending.update(str(r) for r in roots if r)
        self._wake.set()

    def run_once(self) -> int:
        """Handle everything pending now, in the caller's thread (tests drive
        this directly). Returns how many stores were handled."""
        with self._lock:
            roots = sorted(self._pending)
            self._pending.clear()
        if not roots:
            return 0
        t0 = time.perf_counter()
        n = 0
        try:
            stores = self._resolve(roots)
        except Exception:
            self.errors += 1
            logger.exception("run ingest: could not resolve stores for %s", roots)
            return 0
        for store in stores:
            try:
                store.rescan_if_stale(deadline=time.monotonic() + self.budget_s)
                self._refresh(store)
                n += 1
            except Exception:
                self.errors += 1
                logger.exception("run ingest failed for %s",
                                 getattr(store, "folder_path", store))
        with self._lock:
            after = list(self._after)
        for fn, name in after:
            t1 = time.perf_counter()
            try:
                fn(roots)
            except Exception:
                self.errors += 1
                logger.exception("run ingest: step %s failed", name)
            self.after_ms[name] = round((time.perf_counter() - t1) * 1000.0, 3)
        self.passes += 1
        self.last_ms = (time.perf_counter() - t0) * 1000.0
        return n

    # ── the thread ────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sm-run-ingest", daemon=True)
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
            if self._stop.is_set():
                break
            try:
                self.run_once()
            except Exception:           # run_once already guards; belt and braces
                logger.exception("run ingest: pass failed")

    def stats(self) -> dict[str, Any]:
        with self._lock:
            pending = len(self._pending)
        return {"running": self.running, "passes": self.passes, "errors": self.errors,
                "last_ms": round(self.last_ms, 3), "pending": pending,
                "after_ms": dict(self.after_ms)}
