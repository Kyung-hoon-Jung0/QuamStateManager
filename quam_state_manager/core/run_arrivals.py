"""Which runs SM saw ARRIVE (docs/263) -- the only runs whose first sight is
evidence of when they were saved.

docs/256's review note: SM's first sight of a run is a witness of the run's
time only when SM was watching while the run was written. An archive opened
days later, or a folder scanned for the first time, gives the scan time --
passing that would turn every old run into a clock question.

So a first sight counts only when it is BOUNDED: the run watcher
(``core/run_watch``) looked at the root at most ``project_time.LIVE_GAP_S``
earlier and the run folder was not there. The watcher polls every 0.5 s, so
the bound is normally half a second; after a sleep, a hang or SM being
closed it is not, and nothing is recorded.

Two phases, because a run folder appears before its ``node.json`` is
written: ``on_tick`` notes the arrival (folder path, first-seen instant,
gap) the moment the watcher sees it; ``complete`` -- on the run-ingest
thread, off the request path -- reads ``node.json`` once it is there and
returns the witness. A pending arrival whose ``node.json`` never becomes
readable is dropped after ``PENDING_TTL_S``.

Pure bookkeeping: one ``scandir`` of the newest date folder per tick of a
moved root, one ``node.json`` read per arrival. Never raises.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from typing import Any, Callable, Iterable

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PENDING_TTL_S = 600.0
#: a burst of copied folders is no live run: more than this many new
#: folders in one tick of one root are not arrivals (a paste, a sync)
MAX_PER_TICK = 3


def listing(root: str) -> tuple[str | None, frozenset[str]] | None:
    """``(newest date folder, run folder names in it)``; runs sitting directly
    under *root* (no date folders) give ``(None, names)``. None when the root
    cannot be read."""
    try:
        with os.scandir(root) as it:
            dirs = [e.name for e in it if e.is_dir(follow_symlinks=False)]
    except OSError:
        return None
    dates = [d for d in dirs if _DATE_RE.match(d)]
    if not dates:
        return (None, frozenset(dirs))
    newest = max(dates)
    try:
        with os.scandir(os.path.join(root, newest)) as it:
            names = frozenset(e.name for e in it if e.is_dir(follow_symlinks=False))
    except OSError:
        return None
    return (newest, names)


class Arrivals:
    """The per-app arrival log. ``clock`` is injectable for the pins."""

    def __init__(self, clock: Callable[[], float] = time.time,
                 list_fn: Callable[[str], Any] = listing):
        self._clock = clock
        self._list = list_fn
        self._known: dict[str, tuple] = {}
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.arrivals = 0
        self.rejected = 0

    def baseline(self, roots: Iterable[str]) -> None:
        """Record what is there now for every root not yet known (no
        arrival is reported for a root's first look)."""
        for r in roots:
            r = str(r)
            with self._lock:
                if r in self._known:
                    continue
            snap = self._list(r)
            if snap is not None:
                with self._lock:
                    self._known.setdefault(r, snap)

    def forget(self, keep: Iterable[str]) -> None:
        keep = {str(r) for r in keep}
        with self._lock:
            for r in list(self._known):
                if r not in keep:
                    del self._known[r]

    def on_tick(self, roots: Iterable[str], gap_of: Callable[[str], float | None]) -> int:
        """The watcher saw *roots* move. Each run folder that is new since
        the last look becomes a pending arrival -- if the watcher's previous
        look at that root was at most ``LIVE_GAP_S`` ago. Returns how many
        arrivals were noted."""
        from quam_state_manager.core.project_time import LIVE_GAP_S
        now = self._clock()
        noted = 0
        for r in roots:
            r = str(r)
            snap = self._list(r)
            if snap is None:
                continue
            with self._lock:
                prev = self._known.get(r)
                self._known[r] = snap
            if prev is None:
                continue                       # first look: a baseline, not an arrival
            date, names = snap
            pdate, pnames = prev
            fresh = sorted(names - pnames) if date == pdate else sorted(names)
            if date is not None and pdate is not None and date < pdate:
                fresh = []                     # the newest date went BACK: a deletion
            if not fresh:
                continue
            try:
                gap = gap_of(r)
            except Exception:  # noqa: BLE001
                gap = None
            if gap is None or gap > LIVE_GAP_S or len(fresh) > MAX_PER_TICK:
                self.rejected += len(fresh)
                continue
            for name in fresh:
                path = os.path.join(r, date, name) if date else os.path.join(r, name)
                with self._lock:
                    if path not in self._pending:
                        self._pending[path] = {"root": r, "date": date, "name": name,
                                               "seen_s": now, "gap_s": float(gap)}
                        noted += 1
        self.arrivals += noted
        return noted

    def pending(self) -> list[dict]:
        with self._lock:
            return [dict(v, path=k) for k, v in self._pending.items()]

    def complete(self, read_json: Callable[[str], Any] | None = None) -> list[dict]:
        """Turn every pending arrival whose ``node.json`` is readable into a
        witness (``project_time.make_witness``, ``src="live"``) and return
        them with their root: ``[{"root", "witness"}]``. An arrival whose
        node carries no usable instant is dropped (no evidence); one not yet
        readable stays pending until ``PENDING_TTL_S``."""
        from quam_state_manager.core import project_time
        if read_json is None:
            read_json = _read_node
        now = self._clock()
        out = []
        for item in self.pending():
            path = item["path"]
            try:
                node = read_json(os.path.join(path, "node.json"))
            except Exception:  # noqa: BLE001
                node = None
            if not isinstance(node, dict):
                if now - item["seen_s"] > PENDING_TTL_S:
                    with self._lock:
                        self._pending.pop(path, None)
                continue
            try:
                mtime_us = int(os.stat(path).st_mtime_ns // 1000)
            except OSError:
                mtime_us = None
            key = _run_key(item)
            w = project_time.make_witness(
                node, key, src="live",
                first_seen_utc_us=int(round(item["seen_s"] * 1_000_000)),
                gap_s=item["gap_s"], folder_mtime_utc_us=mtime_us)
            with self._lock:
                self._pending.pop(path, None)
            if w is not None:
                out.append({"root": item["root"], "witness": w})
        return out

    def stats(self) -> dict:
        with self._lock:
            return {"roots": len(self._known), "pending": len(self._pending),
                    "arrivals": self.arrivals, "rejected": self.rejected}


def _run_key(item: dict) -> str:
    """Run identity for a witness: root + date folder + run folder (a bare
    run id is not unique -- DESIGN §2 item 3)."""
    rel = f"{item['date']}/{item['name']}" if item.get("date") else item["name"]
    return f"{item['root']}::{rel}"


def run_key(root: str, folder: str) -> str:
    """The same key for a run folder found any other way (an archive)."""
    root_s, folder_s = os.path.normpath(str(root)), os.path.normpath(str(folder))
    try:
        rel = os.path.relpath(folder_s, root_s).replace("\\", "/")
    except ValueError:
        rel = os.path.basename(folder_s)
    return f"{root}::{rel}"


def _read_node(path: str) -> Any:
    # one attempt, no sleep: a node.json still being written is "come back
    # later" (docs/80), never an error
    from quam_state_manager.core import safe_io
    return safe_io.scan_json(path)
