"""The ledger keeps itself current (S5, docs/275).

S3 (``hub_build``) builds a chip's ledger offline from an archive; S4
(``hub``) records every SM write into it. This module ingests RUNS inside SM,
continuously, so the ledger needs no page visit and no offline build:

* **catch-up on chip open** -- every data folder registered to the chip
  (``roots_for_chip``) is listed and every run folder the ledger does not
  hold is ingested, in the background, budgeted per slice;
* **real-time ingestion** -- the run watcher (``core/run_watch``) wakes the
  chip's sync when a registered folder moves; one new run is one read + hash
  + diff against the ledger state just before it;
* **out-of-order insertion** -- a run whose instant precedes existing events
  (a second folder, a late copy, a run that finished while SM wrote) takes
  its place by instant: it is diffed against its predecessor and its
  successor is re-diffed against it; nothing else is rebuilt
  (``HubStore`` S5 section, invariants I1-I4);
* **in-flight and rewritten runs** -- the newest run of a folder with no
  readable state (or node) waits until it is complete or a later run proves
  it final; a run folder whose saved pair is rewritten after ingestion is
  found by a stat watermark (size + mtime of state, wiring and node) and
  re-diffed in place, its successor too;
* **status** -- ``status(chip_dir)``: building n/N | ready; ``require_ready``
  raises :class:`Building` (a ``ramcache.Warming``) so no surface presents a
  partial ledger as complete.

One writer per ledger: every slice runs on the hub's projector thread, under
the chip's writer lock, on the projector's one connection -- the same writer
that projects SM events (docs/271). Two SM windows on one chip serialise on
SQLite ``BEGIN IMMEDIATE``; every insertion re-reads its neighbours inside its
own transaction, so a run the other window already ingested is skipped.

Binding user decisions: run by run (a run's change = its saved quam_state vs
the ledger state just before it); SM writes are exact and never re-diffed; no
fit-vs-human split; no copied-state detection; several data folders of one
chip merge into one timeline by instant.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import threading
import time
from collections import Counter, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from quam_state_manager.core import hub_build, hub_rules as rules, timefmt
from quam_state_manager.core.hub_store import (
    _NEW,
    CHIP_UNCERTAIN,
    NODE_UNREADABLE,
    OPS,
    OVERLAPS_SM_WRITE,
    REVERTS_TO_EARLIER,
    REWRITTEN,
    SM_KINDS,
    SOURCE_GONE,
    TIME_ASSUMED,
    HubStore,
    json_bytes,
)
from quam_state_manager.core.ramcache import Warming

logger = logging.getLogger(__name__)

#: One background slice of work; then the projector takes queued SM lines and
#: requests get the interpreter back (docs/275 "budget per tick").
SLICE_S = 0.5
#: A kick in inline mode (tests, the CLI) works this long on the caller's thread.
INLINE_BUDGET_S = 120.0
#: Rewrites of a root's newest runs are stat-checked on every light tick.
RECENT_CHECK = 64
#: A full listing + stat sweep of every root, at least this often.
SWEEP_EVERY_S = 300.0
#: Every date directory of every root is stat'ed (listed when it moved) this
#: often: a late copy into an OLD day moves no directory the run watcher
#: looks at. One stat per day folder; no run file is touched.
LISTING_EVERY_S = 30.0
#: Date directories re-listed on a light tick (new ones always are).
NEWEST_DATES = 2
#: A candidate that raised is retried after this long.
RETRY_AFTER_S = 30.0

_DAY = hub_build._DAY
_RUN = hub_build._RUN


class Deferred(Exception):
    """The newest run of a folder is not readable yet (torn or mid-write):
    its insertion is rolled back and it waits (docs/275)."""


class Building(Warming):
    """The ledger is still catching up: a surface answers "building n/N",
    never a partial ledger presented as complete (the ramcache rule)."""

    def __init__(self, chip_dir, status: dict):
        super().__init__("hub", str(chip_dir), 0.0)
        self.status = status


# ----------------------------------------------------------------------
# which folders belong to a chip
# ----------------------------------------------------------------------

#: Sources, strongest first (the order ``roots_for_chip`` keeps).
ROOT_SOURCES = ("declared", "project_storage", "project_roots", "decided_same")


def roots_for_chip(*, declared: Iterable[str] = (), project_storage: str | None = None,
                   project_roots: Iterable[str] = (), workspace_roots: Iterable[str] = (),
                   decided_same: Callable[[str], bool] | None = None,
                   key: Callable[[str], str] | None = None) -> list[tuple[str, str]]:
    """THE rule for which data folders are synced into a chip's ledger, from
    what SM already knows about the chip -- ``[(folder, source)]``, deduped,
    existing directories only, strongest source first:

    1. ``declared`` -- the chip's own ``extras.data_folder`` (docs/20 v2), as
       resolved by the OS-dialect bridge;
    2. ``project_storage`` -- the qualibrate project storage location of the
       chip's project scope (docs/63): where qualibrate saves its runs;
    3. ``project_roots`` -- folders recorded for that project
       (``instance/project_dataset_roots.json``);
    4. ``decided_same`` -- a Datasets (workspace) root the user has already
       declared to be THIS chip's data (``chip_decisions.json`` "same").

    A workspace root SM has no such evidence for is NOT synced: a workspace
    often holds several chips' data, and syncing a foreign folder would put
    every one of its runs into this chip's timeline. A run inside a synced
    folder whose identity disagrees is still kept and flagged CHIP_UNCERTAIN.
    """
    key = key or (lambda p: os.path.normcase(os.path.abspath(p)))
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(path, source):
        if not path:
            return
        try:
            if not Path(path).is_dir():
                return
            k = key(str(path))
        except (OSError, ValueError):
            return
        if k in seen:
            return
        seen.add(k)
        out.append((str(path), source))

    for p in declared:
        add(p, "declared")
    add(project_storage, "project_storage")
    for p in project_roots:
        add(p, "project_roots")
    if decided_same is not None:
        for p in workspace_roots:
            try:
                if decided_same(str(p)):
                    add(p, "decided_same")
            except Exception:  # noqa: BLE001 -- an unreadable decision syncs nothing
                continue
    return out


# ----------------------------------------------------------------------
# RAM state per chip (metadata only)
# ----------------------------------------------------------------------

def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def file_sig(folder: Path) -> str:
    """The stat watermark of one run folder: size + mtime of its saved state,
    wiring and node.json (``-`` for an absent file). Three stats, no read."""
    state = folder / "quam_state" / "state.json"
    parts = []
    try:
        st = os.stat(state)
        parts.append(f"{st.st_size}:{st.st_mtime_ns}")
        files = (state.with_name("wiring.json"), folder / "node.json")
    except OSError:
        s, w = hub_build.state_paths(folder)     # an older layout (or none)
        files = (s, w, folder / "node.json")
    for p in files:
        try:
            st = os.stat(p)
            parts.append(f"{st.st_size}:{st.st_mtime_ns}")
        except OSError:
            parts.append("-")
    return "|".join(parts)


@dataclass
class Cand:
    """One run folder read for ingestion (metadata only until attached)."""
    root: "RootState"
    rel: str
    run: hub_build.Run
    sig: str
    rewrite: bool = False

    @property
    def key(self) -> tuple:
        return (self.run.instant, self.root.fkey, self.run.run_id, self.run.experiment, self.rel, _NEW)


@dataclass
class RootState:
    path: Path
    sources: list = field(default_factory=list)
    root_id: int | None = None
    fkey: str = ""
    listed: bool = False
    dates: dict = field(default_factory=dict)      # date dir -> mtime_ns when last listed
    known: dict = field(default_factory=dict)      # rel -> [eid, sig]
    deferred: dict = field(default_factory=dict)   # rel -> why
    newest: tuple | None = None                    # newest ingested (t, run_id, rel)
    votes: Counter = field(default_factory=Counter)
    stored_hint: str | None = None
    failed: dict = field(default_factory=dict)     # rel -> retry-after (monotonic)
    readable: bool = True

    @property
    def hint(self) -> str | None:
        ranked = self.votes.most_common(2)
        if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
            return self.stored_hint
        return ranked[0][0]


class ChipSync:
    """One chip's run ingestion: registered roots, RAM bookkeeping, status."""

    def __init__(self, chip_dir):
        self.dir = Path(chip_dir)
        self.lock = threading.RLock()
        self.roots: dict[str, RootState] = {}
        self.identity: dict | None = None
        self.ledger_id: str | None = None
        self.full_wanted = True
        self.listing_wanted = False
        self.last_listing = 0.0
        self.dirty: set[str] = set()
        self.unread: deque = deque()          # (root, rel, rewrite?) to read
        self.read: list[Cand] = []
        self.ready_cands: deque = deque()     # read + ordered, to ingest
        self.sweep: list[tuple[RootState, str]] = []
        self.sweep_initial = False
        self.pending_max: dict = {}
        self.ready = False
        self.phase = "idle"
        self.done = 0
        self.total = 0
        self.last_full = 0.0
        self.counts: Counter = Counter()
        self.errors: deque = deque(maxlen=20)
        self.last_slice_ms = 0.0
        self.slices = 0

    # -- registration ----------------------------------------------------

    def set_roots(self, roots: Iterable[tuple[str, str]]) -> bool:
        """Register the chip's roots (``roots_for_chip``). Returns whether the
        set changed (a new root needs a full listing)."""
        with self.lock:
            want: dict[str, RootState] = {}
            for path, source in roots:
                k = _norm(path)
                rs = self.roots.get(k) or want.get(k) or RootState(Path(path))
                if source not in rs.sources:
                    rs.sources.append(source)
                want[k] = rs
            changed = set(want) != set(self.roots)
            self.roots = want
            if changed:
                self.full_wanted = True
                self.ready = False
            return changed

    def request(self, *, full: bool = False, listing: bool = False, roots: Iterable[str] = ()) -> bool:
        """Mark work. Returns whether any registered root is affected."""
        with self.lock:
            if full:
                self.full_wanted = True
                return bool(self.roots)
            if listing:
                self.listing_wanted = True
                return bool(self.roots)
            hit = False
            for r in roots:
                k = _norm(r)
                if k in self.roots:
                    self.dirty.add(k)
                    hit = True
            return hit

    def has_work(self) -> bool:
        with self.lock:
            inbox = bool(self.full_wanted or self.listing_wanted or self.dirty)
        return bool(inbox or self.unread or self.read or self.ready_cands or self.sweep)

    def status(self) -> dict:
        # lock-free snapshot: the slice owns the work lists; a read that races
        # it can only lag by one item, never block a request
        pending = len(self.unread) + len(self.read) + len(self.ready_cands)
        # a root the watcher saw move is "building" until it is looked at:
        # the run that moved it may not be in the ledger yet
        inbox = bool(self.full_wanted or self.dirty)
        building = (not self.ready) or inbox or pending > 0 or (self.sweep_initial and bool(self.sweep))
        roots = list(self.roots.values())
        st = {
            "state": "building" if building and roots else "ready",
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "roots": [{"path": str(rs.path), "sources": list(rs.sources), "runs": len(rs.known),
                       "deferred": sorted(rs.deferred), "readable": rs.readable} for rs in roots],
            "deferred": sum(len(rs.deferred) for rs in roots),
            "counts": dict(self.counts),
            "errors": list(self.errors),
            "last_slice_ms": round(self.last_slice_ms, 1),
            "slices": self.slices,
        }
        if not roots:
            st["note"] = "no data folder is registered to this chip"
        return st

    # -- binding to one ledger file --------------------------------------

    def _bind(self, store: HubStore) -> bool:
        """Bind RAM bookkeeping to this ledger file; True when it was reset
        (a full listing is then due)."""
        reset = False
        lid = store.meta("ledger_id")
        if lid != self.ledger_id:
            reset = True
            # a different file (first use, or the ledger was rebuilt): RAM
            # bookkeeping is derived from the ledger, so it starts over
            self.ledger_id = lid
            for rs in self.roots.values():
                rs.root_id = None
                rs.known.clear()
                rs.dates.clear()
                rs.listed = False
                rs.newest = None
            self.unread.clear()
            self.read.clear()
            self.ready_cands.clear()
            self.sweep.clear()
            self.ready = False
        if self.identity and store.meta("chip_identity") is None:
            with store.conn:
                store.set_meta("chip_identity", json_bytes(self.identity).decode("utf-8"))
        for rs in list(self.roots.values()):
            if rs.root_id is None:
                self._register(store, rs)
                reset = True
        return reset

    def _register(self, store: HubStore, rs: RootState) -> None:
        normalized = os.path.normcase(str(rs.path.resolve()))
        with store.conn:
            store.conn.execute("INSERT OR IGNORE INTO roots(path,folder_key,offset_hint) VALUES(?,?,NULL)",
                               (normalized, hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]))
        row = store.conn.execute("SELECT root_id, folder_key, offset_hint FROM roots WHERE path=?",
                                 (normalized,)).fetchone()
        rs.root_id, rs.fkey, rs.stored_hint = row[0], row[1], row[2]
        votes = store.meta(f"offset_votes:{rs.root_id}")
        rs.votes = Counter(json.loads(votes)) if votes else Counter()
        rs.known = {r[0]: [r[1], r[2]] for r in store.conn.execute(
            "SELECT l.rel_path, l.eid, f.sig FROM locations l LEFT JOIN run_files f "
            "ON f.root_id=l.root_id AND f.rel_path=l.rel_path WHERE l.root_id=?", (rs.root_id,))}
        row = store.conn.execute(
            "SELECT e.t_utc_us, e.run_id, l.rel_path FROM locations l JOIN events e USING(eid) "
            "WHERE l.root_id=? ORDER BY e.ord DESC LIMIT 1", (rs.root_id,)).fetchone()
        rs.newest = (row[0], row[1] or 0, row[2]) if row else None

    # -- one slice -------------------------------------------------------

    def run_slice(self, store: HubStore, budget_s: float | None) -> bool:
        """Do up to *budget_s* of work (None: until done). Returns whether
        more work remains. Runs on the projector thread only; the RAM lock is
        taken just to read the inbox (``request`` / ``set_roots`` from other
        threads), so ``status()`` never waits for a slice."""
        t0 = time.perf_counter()
        deadline = None if budget_s is None else time.monotonic() + budget_s
        self.slices += 1
        with self.lock:
            full, dirty, listing = self.full_wanted, set(self.dirty), self.listing_wanted
            self.full_wanted = self.listing_wanted = False
            self.dirty.clear()
        if self.ready and (full or dirty):
            self.done = self.total = 0              # a new burst of work
        if self._bind(store):
            full = True
        if full or dirty or listing:
            self._list(full=full, dirty=dirty, listing=listing)
        # every slice makes progress (at least one item), whatever the budget
        steps = 0

        def late() -> bool:
            return steps > 0 and _late(deadline)

        while self.unread and not late():
            rs, rel, rewrite = self.unread.popleft()
            self._read(rs, rel, rewrite)
            steps += 1
        if not self.unread and self.read:
            self._resolve()
        while self.ready_cands and not late():
            cand = self.ready_cands.popleft()
            self._ingest(store, cand)
            steps += 1
        while self.sweep and not self.unread and not self.ready_cands and not late():
            rs, rel = self.sweep.pop()
            self._check(store, rs, rel)
            steps += 1
        if self.sweep and not self.unread and not self.ready_cands:
            self.phase = "sweeping"
        more = self.has_work()
        if not more:
            self.phase = "ready"
            self.ready = True
            self.sweep_initial = False
        self.last_slice_ms = (time.perf_counter() - t0) * 1000.0
        return more

    # -- listing ---------------------------------------------------------

    def _list(self, *, full: bool, dirty: set, listing: bool = False) -> None:
        self.phase = "listing"
        roots = dict(self.roots)
        if full or listing:
            targets = list(roots.values())
            self.last_listing = time.monotonic()
        else:
            targets = [roots[k] for k in dirty if k in roots]
        queued = {(id(rs), rel) for rs, rel, _ in self.unread}
        queued |= {(id(c.root), c.rel) for c in [*self.read, *self.ready_cands]}
        added = 0
        for rs in targets:
            for rel in [*self._list_root(rs, full or listing), *list(rs.deferred)]:
                if (id(rs), rel) not in queued:
                    self.unread.append((rs, rel, False))
                    queued.add((id(rs), rel))
                    added += 1
            if full:
                self.sweep.extend((rs, rel) for rel in rs.known)
            elif _norm(rs.path) in dirty:
                newest = sorted(rs.known)[-RECENT_CHECK:]
                self.sweep.extend((rs, rel) for rel in newest)
        self.total += added               # each queued item counts once in "n/N"
        if full:
            self.last_full = time.monotonic()
            if not self.ready:
                self.sweep_initial = True
        self.phase = "reading"

    def _list_root(self, rs: RootState, full: bool) -> list[str]:
        try:
            with os.scandir(rs.path) as it:
                days = sorted(e.name for e in it if _DAY.fullmatch(e.name) and e.is_dir())
            rs.readable = True
        except OSError as exc:
            rs.readable = False
            self.errors.append(f"{rs.path}: cannot list ({exc})")
            return []
        for gone in set(rs.dates) - set(days):
            rs.dates.pop(gone, None)
        look = days if (full or not rs.listed) else sorted(set(d for d in days if d not in rs.dates)
                                                            | set(days[-NEWEST_DATES:]))
        new: list[str] = []
        now = time.monotonic()
        for day in look:
            try:
                mtime = os.stat(rs.path / day).st_mtime_ns
            except OSError:
                continue
            if rs.dates.get(day) == mtime:
                continue
            try:
                with os.scandir(rs.path / day) as it:
                    names = [e.name for e in it if _RUN.fullmatch(e.name) and e.is_dir()]
            except OSError:
                continue
            rs.dates[day] = mtime
            for name in names:
                rel = f"{day}/{name}"
                if rel in rs.known or rs.failed.get(rel, 0) > now:
                    continue
                new.append(rel)
        rs.listed = True
        return new

    # -- reading + ordering ----------------------------------------------

    def _read(self, rs: RootState, rel: str, rewrite: bool) -> None:
        folder = rs.path / rel
        try:
            sig = file_sig(folder)
            run = hub_build.run_of(folder)
        except OSError as exc:
            run, sig = None, None
            self.errors.append(f"{rel}: {exc}")
        if run is None:
            rs.deferred.pop(rel, None)
            self.done += 1
            return
        if not rewrite:
            label = timefmt.archive_offset_hint([run.node])
            if label is not None:
                rs.votes[label] += 1
        self.read.append(Cand(rs, rel, run, sig, rewrite))

    def _resolve(self) -> None:
        for cand in self.read:
            hub_build.resolve_instant(cand.run, cand.root.hint)
        self.ready_cands = deque(sorted([*self.ready_cands, *self.read], key=lambda c: c.key))
        self.read.clear()
        # the newest pending run of each folder (one pass, not one per run)
        self.pending_max = {}
        for c in self.ready_cands:
            mine = (c.run.instant, c.run.run_id, c.rel)
            self.pending_max[id(c.root)] = max(self.pending_max.get(id(c.root), mine), mine)
        self.phase = "ingesting"

    def _is_newest(self, cand: Cand) -> bool:
        """No later run of the same folder is known (ingested or pending): an
        incomplete newest run may still be in flight."""
        rs = cand.root
        mine = (cand.run.instant, cand.run.run_id, cand.rel)
        if rs.newest is not None and rs.newest > mine:
            return False
        return self.pending_max.get(id(rs), mine) <= mine

    # -- ingestion -------------------------------------------------------

    def _ingest(self, store: HubStore, cand: Cand) -> None:
        rs = cand.root
        try:
            if not cand.run.folder.is_dir():
                # deleted between listing and ingestion: nothing happened here
                rs.deferred.pop(cand.rel, None)
                self.counts["vanished"] += 1
                self.done += 1
                return
            raw, digest, error = _read_pair(cand.run.folder)
            if (not cand.rewrite and (error is not None or cand.run.error is not None)
                    and self._is_newest(cand)):
                # in flight, or a crash we cannot tell from one yet: wait for
                # the run to complete or for a later run to prove it final
                rs.deferred[cand.rel] = error or cand.run.error
                self.counts["deferred"] += 1
                self.done += 1
                return
            if cand.rewrite:
                outcome = self._rewrite(store, cand, raw, digest, error)
            else:
                try:
                    outcome = attach_run(store, cand, raw, digest, error, src="sm_sync",
                                         newest=self._is_newest(cand))
                except Deferred as why:
                    rs.deferred[cand.rel] = str(why)
                    self.counts["deferred"] += 1
                    self.done += 1
                    return
            rs.deferred.pop(cand.rel, None)
            self.counts[outcome] += 1
            if outcome in ("added", "inserted", "location", "rewritten", "moved", "unchanged", "present", "gone"):
                eid = store.conn.execute("SELECT eid FROM locations WHERE root_id=? AND rel_path=?",
                                         (rs.root_id, cand.rel)).fetchone()
                if eid is not None:
                    rs.known[cand.rel] = [eid[0], cand.sig]
                    key = (cand.run.instant, cand.run.run_id, cand.rel)
                    if rs.newest is None or key > rs.newest:
                        rs.newest = key
            self.done += 1
        except Exception as exc:  # noqa: BLE001 -- one bad folder never stops the sync
            logger.warning("hub sync: %s failed", cand.run.folder, exc_info=True)
            self.errors.append(f"{cand.rel}: {type(exc).__name__}: {exc}")
            self.counts["failed"] += 1
            rs.failed[cand.rel] = time.monotonic() + RETRY_AFTER_S
            rs.dates.pop(cand.rel.split("/", 1)[0], None)     # re-list that day later
            self.done += 1

    def _check(self, store: HubStore, rs: RootState, rel: str) -> None:
        """One stat watermark: a changed one is re-read and re-diffed in place;
        a vanished folder is flagged SOURCE_GONE (its rows stay)."""
        entry = rs.known.get(rel)
        if entry is None:
            return
        folder = rs.path / rel
        sig = file_sig(folder)
        if sig == entry[1]:
            return
        if not folder.is_dir() or sig.split("|")[0] == "-":
            if entry[1] != "gone":
                self._flag_gone(store, rs, rel, entry)
            return
        if entry[1] is None:
            # ingested by the offline builder (S3), which kept no watermark:
            # adopt the current one without re-reading (docs/275 residual)
            with txn(store):
                _put_sig(store, rs.root_id, rel, sig, None)
            entry[1] = sig
            return
        self.unread.append((rs, rel, True))
        self.total += 1
        self.phase = "reading"

    def _flag_gone(self, store: HubStore, rs: RootState, rel: str, entry: list) -> None:
        with txn(store):
            others = store.conn.execute("SELECT COUNT(*) FROM locations WHERE eid=? AND NOT "
                                        "(root_id=? AND rel_path=?)", (entry[0], rs.root_id, rel)).fetchone()[0]
            if not others:
                store.conn.execute("UPDATE events SET flags = flags | ? WHERE eid=?", (SOURCE_GONE, entry[0]))
            store.conn.execute("INSERT OR REPLACE INTO run_files(root_id,rel_path,sig,rewritten_us) "
                               "VALUES(?,?,?,(SELECT rewritten_us FROM run_files WHERE root_id=? AND rel_path=?))",
                               (rs.root_id, rel, "gone", rs.root_id, rel))
        entry[1] = "gone"
        self.counts["gone"] += 1

    def _rewrite(self, store: HubStore, cand: Cand, raw, digest, error) -> str:
        rs = cand.root
        with txn(store):
            loc = store.conn.execute("SELECT eid FROM locations WHERE root_id=? AND rel_path=?",
                                     (rs.root_id, cand.rel)).fetchone()
            if loc is None:
                return "vanished"
            ev = store.event(loc[0])
            if error is not None:
                state_file = hub_build.state_paths(cand.run.folder)[0]
                if ev["error"] is not None:
                    # it never had a state and still has none: its node only
                    _update_run_meta(store, ev, cand, rs.hint)
                    _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
                    return "unchanged"
                if not state_file.is_file():
                    # the saved state is gone: the ledger keeps what it ingested
                    store.conn.execute("UPDATE events SET flags = flags | ? WHERE eid=?", (SOURCE_GONE, ev["eid"]))
                    _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
                    return "gone"
                # being rewritten right now (torn or changing): no new
                # watermark, so the next sweep looks again
                return "retry"
            same_place = (ev["t_utc_us"], ev["run_id"], ev["experiment"]) == \
                (cand.run.instant, cand.run.run_id, cand.run.experiment)
            if same_place and ev["error"] is None and digest == ev["state_hash"]:
                # same bytes (a re-save of identical content), or node.json
                # alone moved: metadata only, nothing to re-diff
                _update_run_meta(store, ev, cand, rs.hint)
                flags = (ev["flags"] & ~(SOURCE_GONE | NODE_UNREADABLE)) | (
                    NODE_UNREADABLE if cand.run.error is not None else 0)
                store.conn.execute("UPDATE events SET flags=? WHERE eid=?", (flags, ev["eid"]))
                _reprove(store, ev, cand.run.node)
                _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
                return "unchanged"
            if not same_place:
                # node.json now names another instant: the run moves (its
                # neighbours on both sides are repaired)
                remove_event(store, ev)
                attach_run(store, cand, raw, digest, error, src="sm_sync", in_txn=True, extra_flags=REWRITTEN)
                return "moved"
            rediff_in_place(store, ev, cand, raw, digest, hint=rs.hint)
            return "rewritten"


def _late(deadline) -> bool:
    return deadline is not None and time.monotonic() >= deadline


# ----------------------------------------------------------------------
# ledger mutations (one transaction each)
# ----------------------------------------------------------------------

@contextmanager
def txn(store: HubStore):
    """``BEGIN IMMEDIATE`` .. ``COMMIT``: one writer at a time across
    processes; on any error everything rolls back, the path-id cache and the
    flat cache too (a crash mid-insert leaves the ledger as it was)."""
    if store.conn.in_transaction:
        store.conn.commit()
    old_pids = store._pids.copy()
    store.conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        store.conn.execute("COMMIT")
    except BaseException:
        store.conn.execute("ROLLBACK")
        store._pids = old_pids
        store.__dict__.pop("_flats", None)
        store.__dict__.pop("_fkeys", None)
        raise


def _read_pair(folder: Path):
    try:
        raw = hub_build.read_pair(folder)
        return raw, rules.state_hash(*raw), None
    except (OSError, ValueError) as exc:
        return None, None, str(exc) or type(exc).__name__


def _put_sig(store, root_id, rel, sig, rewritten_us) -> None:
    store.conn.execute(
        "INSERT INTO run_files(root_id,rel_path,sig,rewritten_us) VALUES(?,?,?,?) "
        "ON CONFLICT(root_id,rel_path) DO UPDATE SET sig=excluded.sig, "
        "rewritten_us=COALESCE(excluded.rewritten_us, run_files.rewritten_us)",
        (root_id, rel, sig, rewritten_us))


def _chip(store) -> dict | None:
    raw = store.meta("chip_identity")
    return json.loads(raw) if raw else None


def _prover(row) -> Callable | None:
    """``prove(rows) -> proven paths`` for a run whose rows are re-diffed:
    its node.json patches, read again from its folder; None (carry the old
    proofs) when the folder is not readable."""
    folder = Path(row["state_ref"] or "")
    if not row["state_ref"] or not folder.is_dir():
        return None
    node, err = hub_build.read_node(folder)
    if err is not None:
        return None

    def prove(rows, flat):
        return hub_build._proven(node, rows, flat)
    return prove


def _rediff_successor(store, succ, base_flat, base_hash, succ_flat) -> None:
    prove = _prover(succ)
    rows = rules.diff(base_flat, succ_flat)
    store.rediff_run(succ, base_flat, base_hash, succ_flat,
                     proven=prove(rows, succ_flat) if prove is not None else None)


def _prepare_successor(store, succ):
    """Before the event in front of *succ* changes: anchor an SM successor
    (I3) or take a run successor's own state (it does not change)."""
    if succ is None:
        return None
    if succ["kind"] in SM_KINDS:
        store.ensure_anchor(succ)
        return None
    return store.flat_of(succ)


def attach_run(store: HubStore, cand: Cand, raw, digest, error, *, src: str,
               in_txn: bool = False, extra_flags: int = 0, newest: bool = False) -> str:
    """Insert one run at its place (I1-I4). Returns ``added`` (at the head),
    ``inserted`` (before existing events), ``location`` (a copy of a run the
    ledger holds) or ``present`` (another writer ingested this folder)."""
    if not in_txn:
        with txn(store):
            return attach_run(store, cand, raw, digest, error, src=src, in_txn=True, extra_flags=extra_flags,
                              newest=newest)
    rs, run = cand.root, cand.run
    c = store.conn
    if c.execute("SELECT 1 FROM locations WHERE root_id=? AND rel_path=?", (rs.root_id, cand.rel)).fetchone():
        _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
        return "present"
    known = c.execute("SELECT eid FROM events WHERE kind='run' AND t_utc_us=? AND run_id=? AND experiment=? "
                      "AND state_hash IS ?", (run.instant, run.run_id, run.experiment, digest)).fetchone()
    if known:
        c.execute("INSERT INTO locations VALUES(?,?,?)", (known[0], rs.root_id, cand.rel))
        _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
        return "location"
    lo, hi = store.neighbors(cand.key)
    pred = store.good_at_or_before(lo)
    succ = store.good_at_or_after(hi)
    pred_hash = pred["state_hash"] if pred is not None else None
    flags = extra_flags
    if run.quality in ("assumed_local", "mtime"):
        flags |= TIME_ASSUMED
    if run.error is not None:
        flags |= NODE_UNREADABLE
    rows: list = []
    doc = flat = None
    shape_hash = None
    chip = _chip(store)
    if error is None:
        pred_flat = store.flat_of(pred) if pred is not None else {}
        if digest == pred_hash and pred is not None and pred["shape_hash"] is not None:
            # byte-identical to the state before it: a zero-change event, no parse
            flat, shape_hash = pred_flat, pred["shape_hash"]
            flags |= pred["flags"] & CHIP_UNCERTAIN
        else:
            try:
                doc, flat, id_flags, chip = hub_build.parse_state(raw, run.folder, chip)
                flags |= id_flags
                rows = rules.diff(pred_flat, flat)
            except (OSError, ValueError, TypeError) as exc:
                if newest:
                    # a torn save of the newest run is a write in flight, not
                    # a final error (the transaction rolls back)
                    raise Deferred(f"saved state not readable yet: {exc}") from exc
                error, doc, flat, rows = str(exc), None, None, []
    if error is not None:
        flags |= CHIP_UNCERTAIN
    succ_flat = _prepare_successor(store, succ) if error is None else None
    if error is None and store.overlaps_sm_write(_run_start(run, cand.root.hint), run.instant):
        flags |= OVERLAPS_SM_WRITE
    ord_ = store.alloc_ord(lo, hi)
    event = hub_build.event_fields(run, root_id=rs.root_id, rel=cand.rel, hint=cand.root.hint, digest=digest,
                                   base_hash=pred_hash, flags=flags,
                                   error=error, src=src)
    if error is None and shape_hash is None:
        shape_hash = store.shape_and_arrays(doc, flat)
    event.update(ord=ord_, shape_hash=shape_hash, n_changes=len(rows))
    columns = ",".join(event)
    eid = c.execute(f"INSERT INTO events({columns}) VALUES({','.join('?' for _ in event)})",
                    tuple(event.values())).lastrowid
    c.execute("INSERT INTO locations VALUES(?,?,?)", (eid, rs.root_id, cand.rel))
    _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
    if chip is not None and store.meta("chip_identity") is None:
        store.set_meta("chip_identity", json_bytes(chip).decode("utf-8"))
    label = timefmt.archive_offset_hint([run.node])
    if label is not None and not cand.rewrite:
        store.set_meta(f"offset_votes:{rs.root_id}", json_bytes(dict(rs.votes)).decode("utf-8"))
    if error is None:
        store.write_rows(eid, rows, hub_build._proven(run.node, rows, flat))
        row = store.event(eid)
        store.remember_flat(row, flat)

        def doc_fn():
            return doc if doc is not None else store.state_at(eid)
        if succ is not None:
            succ = store.event(succ["eid"])          # alloc_ord may have renumbered
            if succ_flat is not None:
                _rediff_successor(store, succ, flat, digest, succ_flat)
        store.refresh_error_checkpoints(ord_, succ["ord"] if succ is not None else None, doc_fn)
        store.maybe_checkpoint(eid, ord_, doc_fn)
        store.refresh_reverts([eid, succ["eid"] if succ is not None else None,
                               *store.same_hash_after(digest, ord_)])
    return "added" if hi is None else "inserted"


def remove_event(store: HubStore, ev) -> None:
    """Take a run out of the chain (inside a transaction): its successor is
    re-diffed against its predecessor, an SM successor anchored first, error
    checkpoints between them hold the predecessor's state."""
    pred = store.conn.execute("SELECT * FROM events WHERE ord<? AND error IS NULL ORDER BY ord DESC LIMIT 1",
                              (ev["ord"],)).fetchone()
    succ = store.conn.execute("SELECT * FROM events WHERE ord>? AND error IS NULL ORDER BY ord LIMIT 1",
                              (ev["ord"],)).fetchone()
    succ_flat = _prepare_successor(store, succ) if ev["error"] is None else None
    pred_flat = store.flat_of(pred) if pred is not None else {}
    eid = ev["eid"]
    for table in ("changes", "checkpoints", "locations"):
        store.conn.execute(f"DELETE FROM {table} WHERE eid=?", (eid,))
    store.conn.execute("DELETE FROM events WHERE eid=?", (eid,))
    if succ is not None and succ_flat is not None:
        _rediff_successor(store, succ, pred_flat, pred["state_hash"] if pred is not None else None, succ_flat)
    if ev["error"] is None:
        store.refresh_error_checkpoints(pred["ord"] if pred is not None else 0.0,
                                        succ["ord"] if succ is not None else None,
                                        lambda: store.state_at(pred["eid"]) if pred is not None else {})
    store.refresh_reverts([succ["eid"] if succ is not None else None,
                           *store.same_hash_after(ev["state_hash"], ev["ord"])])


def rediff_in_place(store: HubStore, ev, cand: Cand, raw, digest, *, hint) -> None:
    """A rewritten run folder (inside a transaction): the event keeps its
    place and eid; its rows become the diff of its CURRENT saved pair against
    its predecessor, its successor is re-diffed against it, and the
    checkpoints that held its state are refreshed. SM events after it are
    facts: an un-anchored one is anchored first, none is re-diffed."""
    run = cand.run
    pred = store.conn.execute("SELECT * FROM events WHERE ord<? AND error IS NULL ORDER BY ord DESC LIMIT 1",
                              (ev["ord"],)).fetchone()
    succ = store.conn.execute("SELECT * FROM events WHERE ord>? AND error IS NULL ORDER BY ord LIMIT 1",
                              (ev["ord"],)).fetchone()
    succ_flat = _prepare_successor(store, succ)
    pred_flat = store.flat_of(pred) if pred is not None else {}
    chip = _chip(store)
    doc, flat, id_flags, chip = hub_build.parse_state(raw, run.folder, chip)
    rows = rules.diff(pred_flat, flat)
    store.conn.execute("DELETE FROM changes WHERE eid=?", (ev["eid"],))
    store.write_rows(ev["eid"], rows, hub_build._proven(run.node, rows, flat))
    shape_hash = store.shape_and_arrays(doc, flat)
    keep = ev["flags"] & ~(CHIP_UNCERTAIN | NODE_UNREADABLE | SOURCE_GONE | TIME_ASSUMED)
    flags = keep | id_flags | REWRITTEN
    if run.error is not None:
        flags |= NODE_UNREADABLE
    if run.quality in ("assumed_local", "mtime"):
        flags |= TIME_ASSUMED
    fields = hub_build.event_fields(run, root_id=ev["root_id"], rel=cand.rel, hint=hint, digest=digest,
                                    base_hash=pred["state_hash"] if pred is not None else None,
                                    flags=flags, error=None, src=ev["src"])
    for k in ("kind", "t_utc_us", "root_id", "rel_path", "run_id", "experiment", "src"):
        fields.pop(k)
    fields.update(shape_hash=shape_hash, n_changes=len(rows))
    sets = ",".join(f"{k}=?" for k in fields)
    store.conn.execute(f"UPDATE events SET {sets} WHERE eid=?", (*fields.values(), ev["eid"]))
    old_hash = ev["state_hash"]
    row = store.event(ev["eid"])
    store.remember_flat(row, flat)
    if store.conn.execute("SELECT 1 FROM checkpoints WHERE eid=?", (ev["eid"],)).fetchone():
        store.conn.execute("UPDATE checkpoints SET hash=? WHERE eid=?", (store.put_blob(json_bytes(doc)), ev["eid"]))
    if succ is not None and succ_flat is not None:
        _rediff_successor(store, succ, flat, digest, succ_flat)
    store.refresh_error_checkpoints(ev["ord"], succ["ord"] if succ is not None else None, lambda: doc)
    if chip is not None and store.meta("chip_identity") is None:
        store.set_meta("chip_identity", json_bytes(chip).decode("utf-8"))
    store.refresh_reverts([ev["eid"], succ["eid"] if succ is not None else None,
                           *store.same_hash_after(old_hash, ev["ord"]),
                           *store.same_hash_after(digest, ev["ord"])])
    _put_sig(store, ev["root_id"], cand.rel, cand.sig, time.time_ns() // 1000)


def _reprove(store, ev, node: dict) -> None:
    """``proven`` of an event's rows from (possibly rewritten) node patches."""
    ops = {v: k for k, v in OPS.items()}
    rows = [rules.Change(r["path"], ops[r["op"]]) for r in store.conn.execute(
        "SELECT p.path, c.op FROM changes c JOIN paths p USING(pid) WHERE c.eid=?", (ev["eid"],))]
    if not rows:
        return
    proven = hub_build._proven(node, rows, store.flat_of(ev))
    store.conn.executemany("UPDATE changes SET proven=? WHERE eid=? AND pid=(SELECT pid FROM paths WHERE path=?)",
                           [(int(r.path in proven), ev["eid"], r.path) for r in rows])


def _update_run_meta(store, ev, cand: Cand, hint) -> None:
    run = cand.run
    fields = hub_build.event_fields(run, root_id=ev["root_id"], rel=cand.rel, hint=hint,
                                    digest=ev["state_hash"], base_hash=ev["base_hash"], flags=ev["flags"],
                                    error=ev["error"], src=ev["src"])
    upd = {k: fields[k] for k in ("t_src", "t_quality", "status", "run_start_us", "run_end_us", "parents",
                                  "targets", "patches_n")}
    sets = ",".join(f"{k}=?" for k in upd)
    store.conn.execute(f"UPDATE events SET {sets} WHERE eid=?", (*upd.values(), ev["eid"]))


def _run_start(run: hub_build.Run, hint) -> int | None:
    from quam_state_manager.core import run_time
    return run_time.resolve(run.node["metadata"].get("run_start"), offset_hint=hint, read_node=False)[0]


# ----------------------------------------------------------------------
# registry + scheduling (the projector thread runs every slice)
# ----------------------------------------------------------------------

_SYNCS: dict[str, ChipSync] = {}
_SYNCS_LOCK = threading.Lock()


def sync_for(chip_dir) -> ChipSync:
    key = _norm(chip_dir)
    with _SYNCS_LOCK:
        cs = _SYNCS.get(key)
        if cs is None:
            cs = _SYNCS[key] = ChipSync(chip_dir)
        return cs


def registered() -> list[ChipSync]:
    with _SYNCS_LOCK:
        return list(_SYNCS.values())


def open_chip(chip_dir, roots: Iterable[tuple[str, str]], *, identity: dict | None = None,
              kick: bool = True) -> ChipSync:
    """A chip was activated: register its roots and catch every one of them
    up in the background (no page visit needed)."""
    cs = sync_for(chip_dir)
    cs.set_roots(roots)
    if identity:
        cs.identity = identity
    cs.request(full=True)
    if kick and cs.roots:
        _kick(cs)
    return cs


def on_roots_moved(moved: Iterable[str]) -> int:
    """The run watcher's listener: wake every chip whose registered folder
    moved. Never blocks (the work runs on the projector thread)."""
    moved = list(moved)
    n = 0
    for cs in registered():
        if cs.request(roots=moved):
            _kick(cs)
            n += 1
    return n


def periodic(now: float | None = None) -> int:
    """A full listing + stat sweep for every chip whose last one is older
    than ``SWEEP_EVERY_S`` (the projector calls this when idle)."""
    now = time.monotonic() if now is None else now
    n = 0
    for cs in registered():
        if not cs.roots or cs.has_work():
            continue
        if now - cs.last_full >= SWEEP_EVERY_S:
            cs.request(full=True)
        elif now - cs.last_listing >= LISTING_EVERY_S:
            cs.request(listing=True)
        else:
            # an in-flight run whose state lands by rewriting a file inside
            # an existing quam_state folder moves no directory the watcher
            # looks at: look at the deferred ones again (a few reads)
            waiting = [str(rs.path) for rs in list(cs.roots.values()) if rs.deferred]
            if not waiting:
                continue
            cs.request(roots=waiting)
        _kick(cs)
        n += 1
    return n


def _kick(cs: ChipSync) -> None:
    from quam_state_manager.core import hub
    hub.kick_sync(hub.Hub.for_chip(cs.dir))


def kick(cs: ChipSync) -> None:
    """Queue a slice for a registered chip (when it has roots)."""
    if cs.roots:
        _kick(cs)


def run(chip_dir, store: HubStore, budget_s: float | None) -> bool:
    """One slice for *chip_dir* on *store* (the projector's connection, under
    the chip's writer lock). Returns whether more work remains."""
    return sync_for(chip_dir).run_slice(store, budget_s)


def status(chip_dir) -> dict:
    """``{"state": "building" | "ready", "done", "total", ...}`` from RAM."""
    with _SYNCS_LOCK:
        cs = _SYNCS.get(_norm(chip_dir))
    if cs is None:
        return {"state": "idle", "note": "no run sync is registered for this chip in this window",
                "done": 0, "total": 0}
    return cs.status()


def require_ready(chip_dir) -> dict:
    st = status(chip_dir)
    if st["state"] == "building":
        raise Building(chip_dir, st)
    return st


# ----------------------------------------------------------------------
# invariants, for tests, the rig and a crash check
# ----------------------------------------------------------------------

def verify(store: HubStore, *, sample: int | None = None, read_runs: bool = True) -> dict:
    """Check I1-I4 on a ledger: canonical order, every run's base hash, every
    run's ``state_at`` against its own saved pair (when its folder holds the
    ingested bytes), every checkpoint, every run's location. Returns counts
    and a list of problems (empty = consistent)."""
    problems: list[str] = []
    rows = store.conn.execute("SELECT * FROM events ORDER BY ord").fetchall()
    keys = [store.order_key(r) for r in rows]
    for a, b in zip(keys, keys[1:]):
        if not a < b:
            problems.append(f"order: {a} !< {b}")
    prev_good = None
    runs_checked = 0
    import random
    good_runs = [r for r in rows if r["kind"] == "run" and r["error"] is None]
    pick = set(r["eid"] for r in (random.Random(275).sample(good_runs, sample)
                                  if sample is not None and sample < len(good_runs) else good_runs))
    for r in rows:
        if r["kind"] == "run":
            if not store.conn.execute("SELECT 1 FROM locations WHERE eid=?", (r["eid"],)).fetchone():
                problems.append(f"eid {r['eid']}: run without a location")
            if r["error"] is None:
                want = prev_good["state_hash"] if prev_good is not None else None
                if r["base_hash"] != want:
                    problems.append(f"eid {r['eid']}: base_hash {r['base_hash']} != predecessor {want}")
                if read_runs and r["eid"] in pick and not r["flags"] & SOURCE_GONE:
                    loc = store.conn.execute(
                        "SELECT r.path, l.rel_path, f.sig FROM locations l JOIN roots r USING(root_id) "
                        "LEFT JOIN run_files f ON f.root_id=l.root_id AND f.rel_path=l.rel_path "
                        "WHERE l.eid=? LIMIT 1", (r["eid"],)).fetchone()
                    folder = Path(loc[0]) / loc[1] if loc is not None else Path(r["state_ref"] or "")
                    if folder.is_dir() and (loc is None or loc[2] is None or file_sig(folder) == loc[2]):
                        try:
                            own = rules.flatten(hub_build.read_doc(folder))
                        except (OSError, ValueError, TypeError):
                            own = None
                        if own is not None:
                            got = rules.flatten(store.state_at(r["eid"]))
                            if rules.diff(got, own):
                                problems.append(f"eid {r['eid']}: state_at differs from its saved pair")
                            runs_checked += 1
        if r["error"] is None:
            prev_good = r
    for cp in store.conn.execute("SELECT c.eid, c.hash, e.error, e.ord FROM checkpoints c JOIN events e USING(eid)"):
        if cp["error"] is None:
            want = store.state_at(cp["eid"])
        else:
            p = store.conn.execute("SELECT eid FROM events WHERE ord<? AND error IS NULL ORDER BY ord DESC LIMIT 1",
                                   (cp["ord"],)).fetchone()
            want = store.state_at(p[0]) if p else {}
        if rules.diff(rules.flatten(store.blob(cp["hash"])), rules.flatten(want)):
            problems.append(f"checkpoint {cp['eid']} does not hold its state")
    return {"events": len(rows), "runs_checked": runs_checked, "problems": problems}


# ----------------------------------------------------------------------
# CLI: catch a ledger up from folders, synchronously (perf + rigs)
# ----------------------------------------------------------------------

def catch_up(chip_dir, roots: Iterable[str], *, budget_s: float | None = None) -> dict:
    """Register *roots* and run slices until done, on this thread."""
    cs = sync_for(chip_dir)
    cs.set_roots((r, "declared") for r in roots)
    cs.request(full=True)
    t0 = time.perf_counter()
    with HubStore(chip_dir) as store:
        while run(chip_dir, store, budget_s if budget_s is not None else None):
            pass
        store.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    out = cs.status()
    out["seconds"] = round(time.perf_counter() - t0, 3)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Catch a chip ledger up from run folders (docs/275).")
    ap.add_argument("roots", nargs="+", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--verify", action="store_true")
    ap.add_argument("--sample", type=int)
    args = ap.parse_args(argv)
    result = catch_up(args.out, [str(r) for r in args.roots])
    if args.verify:
        with HubStore(args.out) as store:
            result["verify"] = verify(store, sample=args.sample)
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
