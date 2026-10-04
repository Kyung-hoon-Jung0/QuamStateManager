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
``incomplete``) into a complete one on the client.

Cost and reach, corrected in docs/141 4ac: the run mtimes come from
``os.stat`` per entry, not ``DirEntry.stat()`` (a Windows cache read that
never sees a write inside the directory), so a poll is two ``scandir`` calls
plus one ``stat`` per run in the NEWEST date directory -- a few ms at a few
hundred runs, every ``interval_s``, per root. The trigger is therefore "any
write inside the newest date directory", not only "the run being created": a
figure landing in an older run of the same day ticks too. That is a wake, not
a scan, and ``/datasets/wait``'s own bound is what keeps a burst of them from
costing anything (docs/141 4ac). A root whose date directories are absent --
runs sitting directly under it -- degrades to the root's own mtime and gets
the first tick only.

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
                        # docs/141 4ac: os.stat, NOT DirEntry.stat(). On Windows
                        # DirEntry.stat() is served from the FindFirstFile
                        # listing -- the PARENT directory's cached copy of the
                        # child's timestamps -- and NTFS does not refresh that
                        # when a file is written INSIDE the child. The run
                        # directory's mtime therefore never moved and the
                        # "second tick" this module's docstring describes never
                        # fired at all (measured: never within 20 s; 0.50 s
                        # with a real stat).
                        runs.append((e.name, os.stat(os.path.join(dpath, e.name)).st_mtime_ns))
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
        # docs/275: roots watched for another owner (the hub's run sync
        # watches every data folder registered to an open chip, whether or
        # not the Datasets page shows it). They wake only the listeners that
        # asked for every root; the tick and the dataset listeners are as before.
        self._extra: dict[str, tuple[str, ...]] = {}
        self._sigs: dict[str, Any] = {}
        # docs/275 review: the other owners' OWN last look per root -- one
        # baseline per audience, so the dataset roots behave exactly as they
        # did before the hub watched anything (a root the hub saw first and
        # the Datasets page adds later is baselined for the dataset view)
        self._xsigs: dict[str, Any] = {}
        self.tick = 0
        self.polls = 0
        self.last_change_at: float | None = None
        self._cond = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listeners: list = []
        self._all_listeners: list = []
        # docs/263: per root, when this poll and the one before it looked --
        # the bound that makes "SM saw this run arrive" evidence of its time
        self._polled_at: dict[str, float] = {}
        self._gap: dict[str, float] = {}

    def add_listener(self, fn, *, all_roots: bool = False) -> None:
        """``fn(changed_roots)`` is called on the watcher's thread after a
        tick that moved (never under the condition, never with an empty list).
        It must return fast -- hand work to another thread (``run_ingest``).
        By default it hears only the dataset roots (``set_roots``);
        ``all_roots=True`` also hears the roots watched for other owners
        (``watch``, docs/275)."""
        with self._cond:
            bucket = self._all_listeners if all_roots else self._listeners
            if fn not in bucket:
                bucket.append(fn)

    # ── roots ─────────────────────────────────────────────────────────
    @property
    def roots(self) -> tuple[str, ...]:
        return self._roots

    def _watched(self) -> tuple[str, ...]:
        extra = [r for rs in self._extra.values() for r in rs]
        return tuple(dict.fromkeys((*self._roots, *extra)))

    def watch(self, owner: str, roots: Iterable[str]) -> None:
        """Watch *roots* for *owner* as well as the dataset roots (docs/275).
        Replaces that owner's previous set; ``roots`` (the dataset set) and
        the tick are unaffected.

        No signature is taken here: the caller is a request (a chip open),
        and a stat per run of the newest day convoys behind any background
        parse holding the interpreter (measured 2.2 s for 651 runs during a
        first open, 20 ms alone). The watcher's own next look takes it and
        ANNOUNCES the new root to the all-roots listeners instead of
        swallowing it as a baseline, so a run landing in between is never
        lost."""
        new = tuple(dict.fromkeys(str(r) for r in roots if r))
        with self._cond:
            if new:
                self._extra[owner] = new
            else:
                self._extra.pop(owner, None)
            extra = {r for rs in self._extra.values() for r in rs}
            for k in list(self._xsigs):
                if k not in extra:
                    del self._xsigs[k]

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

    def poll_gap(self, root: str) -> float | None:
        """Seconds between the latest look at *root* and the look before it
        (docs/263), or None when *root* has not been looked at twice. A run
        folder first seen by the latest look appeared within this gap."""
        with self._cond:
            return self._gap.get(str(root))

    # ── the poll ──────────────────────────────────────────────────────
    def poll_once(self) -> bool:
        """Take every root's signature; bump the tick if any changed. Returns
        whether it did. Never raises."""
        import time
        with self._cond:
            roots = self._watched()
            dataset = set(self._roots)
            extra = {r for rs in self._extra.values() for r in rs}
        changed = False
        moved: list[str] = []
        moved_dataset: list[str] = []
        for root in roots:
            try:
                sig = self._signature(root)
            except Exception:            # a signature_fn that raises is a bug, not a run
                logger.exception("run watcher: signature failed for %s", root)
                sig = None
            now = time.time()
            with self._cond:
                if sig is None:
                    # an unreadable look is no look: the next good one is
                    # bounded by the last GOOD one, never by this failure
                    pass
                else:
                    prev_at = self._polled_at.get(root)
                    if prev_at is not None:
                        self._gap[root] = now - prev_at
                    self._polled_at[root] = now
                if root in dataset:
                    if root not in self._sigs:
                        self._sigs[root] = sig          # first sight: baseline only
                    elif self._sigs[root] != sig:
                        self._sigs[root] = sig
                        changed = True
                        moved_dataset.append(root)
                if root in extra:
                    if root not in self._xsigs:
                        self._xsigs[root] = sig
                        if sig is not None:
                            moved.append(root)          # docs/275: an owner's root is announced
                    elif self._xsigs[root] != sig:
                        self._xsigs[root] = sig
                        moved.append(root)
                elif root in moved_dataset:
                    moved.append(root)
        with self._cond:
            self.polls += 1
            if changed:
                import time
                self.tick += 1
                self.last_change_at = time.time()
                self._cond.notify_all()
            listeners = list(self._listeners) if changed else []
            all_listeners = list(self._all_listeners) if moved else []
        for fn in listeners:
            try:
                fn(moved_dataset)
            except Exception:
                logger.exception("run watcher: listener failed")
        for fn in all_listeners:
            try:
                fn(moved)
            except Exception:
                logger.exception("run watcher: listener failed")
        return changed

    def bump(self, reason: str = "") -> int:
        """Wake every waiter now (docs/173 S3): an agent event is a change
        the page must see, and the run watcher already owns the one wake."""
        with self._cond:
            import time
            self.tick += 1
            self.last_change_at = time.time()
            self.last_bump_reason = reason
            self._cond.notify_all()
            return self.tick

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
                    "extra_roots": {k: list(v) for k, v in self._extra.items()},
                    "running": self.running, "interval_s": self.interval_s,
                    "last_change_at": self.last_change_at}
