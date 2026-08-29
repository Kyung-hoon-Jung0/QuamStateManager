"""A cheap, dependency-free watcher for new experiment runs (docs/141 §4p).

The user's ask: when qualibrate saves a new run folder, SM should react
almost at once — not on the next 5 s Datasets delta poll or the next 60 s
new-run popup poll. ``watchdog`` is not part of the customer envs, so this
is stat-based: every ``interval_s`` a daemon thread takes a *signature* of
each watched root — the root directory's mtime, the newest date directory's
name and mtime, and the newest run directory's name, mtime and count — and
bumps a monotonically increasing ``tick`` when any signature changes. A
client long-polls ``GET /datasets/wait?since=<tick>`` (routes.py), which
blocks on the watcher's condition until the tick moves or a timeout passes,
and then runs the polls it already has. The existing polls stay as the
safety net; this only makes them fire NOW.

Why those five stats: NTFS/ext4 update a directory's mtime when an entry is
created, renamed or removed, so a new run directory moves the date
directory's mtime; qualibrate then writes node.json / data files INTO the
run directory over some hundreds of ms, which moves the run directory's
mtime — the second tick is what turns a half-written run (docs/80's
``incomplete``) into a complete one on the client. Two ``scandir`` calls and
three ``stat`` calls per root per interval: ~1 ms on a lab's workspace.

Pure and testable: ``signature`` is a function of a path; ``RunWatcher``
runs without a Flask app (``poll_once`` can be driven by hand).
"""
from __future__ import annotations

import logging
import os
import re
import threading
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
DEFAULT_INTERVAL_S = 0.5
MAX_WAIT_S = 25.0


def signature(root: str) -> tuple | None:
    """What can change when a run lands under *root*; ``None`` when the root
    cannot be read (never an exception)."""
    try:
        st = os.stat(root)
        with os.scandir(root) as it:
            dates = [e.name for e in it
                     if e.is_dir(follow_symlinks=False) and _DATE_RE.match(e.name)]
    except OSError:
        return None
    if not dates:
        return (st.st_mtime_ns, None, 0, None, 0, 0)
    newest = max(dates)                      # ISO date names sort chronologically
    dpath = os.path.join(root, newest)
    try:
        dst = os.stat(dpath)
        with os.scandir(dpath) as it:
            runs = []
            for e in it:
                try:
                    if e.is_dir(follow_symlinks=False):
                        runs.append((e.name, e.stat(follow_symlinks=False).st_mtime_ns))
                except OSError:
                    continue
    except OSError:
        return (st.st_mtime_ns, newest, 0, None, 0, 0)
    if not runs:
        return (st.st_mtime_ns, newest, dst.st_mtime_ns, None, 0, 0)
    rname, rmt = max(runs, key=lambda x: (x[1], x[0]))   # the run being written moves last
    return (st.st_mtime_ns, newest, dst.st_mtime_ns, rname, rmt, len(runs))


class RunWatcher:
    """Owns the roots, the signatures, the tick and the condition."""

    def __init__(self, interval_s: float = DEFAULT_INTERVAL_S, signature_fn=signature):
        self.interval_s = max(0.02, float(interval_s))
        self._signature = signature_fn
        self._roots: tuple[str, ...] = ()
        self._sigs: dict[str, Any] = {}
        self.tick = 0
        self.polls = 0
        self.last_change_at: float | None = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── roots ─────────────────────────────────────────────────────────
    @property
    def roots(self) -> tuple[str, ...]:
        return self._roots

    def set_roots(self, roots: Iterable[str]) -> None:
        """The folders to watch (the active dataset folders); a root seen for
        the first time is recorded, not announced — its content is what the
        client already has."""
        new = tuple(dict.fromkeys(str(r) for r in roots if r))
        with self._cond:
            if new == self._roots:
                return
            self._roots = new
            for k in list(self._sigs):
                if k not in new:
                    del self._sigs[k]
            fresh = [r for r in new if r not in self._sigs]
        # baseline a new root NOW, in the caller's thread (~1 ms): a run that
        # lands between the client's handshake and the thread's first look
        # would otherwise be folded into the baseline and never announced
        for r in fresh:
            try:
                sig = self._signature(r)
            except Exception:
                sig = None
            with self._cond:
                if r in self._roots and r not in self._sigs:
                    self._sigs[r] = sig

    # ── the poll ──────────────────────────────────────────────────────
    def poll_once(self) -> bool:
        """Take every root's signature; bump the tick if any changed. Returns
        whether it did. Never raises."""
        with self._cond:
            roots = self._roots
        changed = False
        for root in roots:
            try:
                sig = self._signature(root)
            except Exception:            # a signature_fn that raises is a bug, not a run
                logger.exception("run watcher: signature failed for %s", root)
                sig = None
            with self._cond:
                if root not in self._sigs:
                    self._sigs[root] = sig          # first sight: baseline only
                elif self._sigs[root] != sig:
                    self._sigs[root] = sig
                    changed = True
        with self._cond:
            self.polls += 1
            if changed:
                import time
                self.tick += 1
                self.last_change_at = time.time()
                self._cond.notify_all()
        return changed

    def wait(self, since: int, timeout_s: float) -> int:
        """Block until the tick differs from *since* (or the watcher stops),
        at most *timeout_s*; return the current tick."""
        timeout_s = max(0.0, min(float(timeout_s), MAX_WAIT_S))
        with self._cond:
            self._cond.wait_for(lambda: self.tick != since or self._stop.is_set(),
                                timeout=timeout_s)
            return self.tick

    # ── the thread ────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sm-run-watch", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout=2.0)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                logger.exception("run watcher: poll failed")
            self._stop.wait(self.interval_s)

    def stats(self) -> dict[str, Any]:
        with self._cond:
            return {"tick": self.tick, "polls": self.polls, "roots": list(self._roots),
                    "running": self.running, "interval_s": self.interval_s,
                    "last_change_at": self.last_change_at}
