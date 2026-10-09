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
* **in-flight and rewritten runs** -- a run with no readable state (or node)
  waits while it is the newest of its folder or its files are still being
  written, and lands once complete or proven final; a run folder whose saved
  pair is rewritten after ingestion is found by a stat watermark (size +
  mtime of state, wiring and node) and re-diffed in place, its successor too;
* **status** -- ``status(chip_dir)``: building n/N | ready | degraded;
  ``require_ready`` raises :class:`Building` (a ``ramcache.Warming``) so no
  surface presents a partial ledger as complete.

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
#: requests get the interpreter back (docs/275 "budget per tick"). A slice also
#: ends early, after any item, as soon as a user's request is in flight.
SLICE_S = 0.25
#: A kick in inline mode (tests, the CLI) works this long on the caller's thread.
INLINE_BUDGET_S = 120.0
#: Rewrites of a root's newest runs (by run id) are stat-checked on every light tick.
RECENT_CHECK = 64
#: Every ingested run of a root is stat-checked at least this often (round
#: robin, ``SWEEP_CHUNK`` runs per periodic look); chip open sweeps them all.
SWEEP_EVERY_S = 300.0
SWEEP_CHUNK = 2000
#: Day folders are stat'ed (listed when they moved) this often -- a late copy
#: into an OLD day moves no directory the run watcher looks at. At most
#: ``LISTING_CHUNK`` days per look (round robin; the newest days always), so a
#: deep archive costs a bounded look, not one stat per day every 30 s.
LISTING_EVERY_S = 30.0
LISTING_CHUNK = 200
#: Date directories re-listed on a light tick (new ones always are).
NEWEST_DATES = 2
#: A candidate that raised is retried after this long, at most MAX_RETRIES times.
RETRY_AFTER_S = 30.0
MAX_RETRIES = 5
#: A run folder whose files changed less than this long ago is still being
#: written (a copy, a save): a missing or torn saved pair waits instead of
#: becoming a final error event (docs/275 review).
FRESH_S = 120.0

_DAY = hub_build._DAY
_RUN = hub_build._RUN


class Deferred(Exception):
    """A run is not readable yet (torn or mid-write): its insertion is rolled
    back and it waits (docs/275)."""


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


def _holds_runs(folder: Path) -> bool:
    """A folder qualibrate writes runs into: it has a day folder (YYYY-MM-DD)."""
    try:
        with os.scandir(folder) as it:
            for e in it:
                n = e.name
                if (len(n) == 10 and n[4] == "-" and n[7] == "-"
                        and n[:4].isdigit() and n[5:7].isdigit() and n[8:].isdigit()
                        and e.is_dir()):
                    return True
    except OSError:
        return False
    return False


def project_run_root(location: str | None, project: str | None) -> str | None:
    """Where qualibrate writes a project's runs (docs/275 review, P1-1): the
    storage location when it already names the project (the lazy
    ``${#/qualibrate/project}`` template), else ``<location>/<project>`` -- a
    storage location shared by several projects holds one subfolder each.

    docs/301 F38: the evidence on disk decides before that naming rule. The
    config tool creates an EMPTY ``<location>/<project>`` whenever it writes a
    config whose location does not name the project, while the runs sit in
    the location's own day folders (observed on this machine's lab setup). A
    project subfolder holding no runs, beside a location that does, is not
    where the runs are: the ledger of a chip with no ``extras.data_folder``
    found none and every history surface stayed empty."""
    if not location:
        return None
    if not project:
        return location
    if os.path.normcase(project) in {os.path.normcase(p) for p in Path(location).parts}:
        return location
    sub = Path(location) / project
    if not _holds_runs(sub) and _holds_runs(Path(location)):
        return location
    return str(sub)


def _inside(child: str, parent: str, key: Callable[[str], str]) -> bool:
    try:
        c, p = key(child), key(parent)
    except (OSError, ValueError):
        return False
    return c == p or c.startswith(p.rstrip("\\/") + os.sep) or c.startswith(p.rstrip("\\/") + "/")


def roots_for_chip(*, declared: Iterable[str] = (), project_storage: str | None = None,
                   project_roots: Iterable[str] = (), workspace_roots: Iterable[str] = (),
                   decided_same: Callable[[str], bool] | None = None,
                   key: Callable[[str], str] | None = None,
                   shared_location: str | None = None) -> list[tuple[str, str]]:
    """THE rule for which data folders are synced into a chip's ledger, from
    what SM already knows about the chip -- ``[(folder, source)]``, deduped,
    existing directories only, strongest source first:

    1. ``declared`` -- the chip's own ``extras.data_folder`` (docs/20 v2), as
       resolved by the OS-dialect bridge;
    2. ``project_storage`` -- where qualibrate writes the runs of the chip's
       project (``project_run_root``: the project's own folder of a shared
       storage location);
    3. ``project_roots`` -- folders recorded for that project
       (``instance/project_dataset_roots.json``), EXCEPT another project's
       folder of the same shared storage location (``shared_location``);
    4. ``decided_same`` -- a Datasets (workspace) root the user has declared
       to be THIS chip's data.

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
        if shared_location and _inside(str(p), shared_location, key) and not (
                project_storage and _inside(str(p), project_storage, key)):
            continue        # a sibling project's folder: another chip's runs
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


def _fresh(folder: Path, now: float | None = None) -> bool:
    """A file of this run folder changed less than FRESH_S ago: something is
    still writing it (a copy, a save)."""
    now = time.time() if now is None else now
    newest = 0.0
    for p in (folder, folder / "node.json", folder / "quam_state", folder / "quam_state" / "state.json",
              folder / "quam_state" / "wiring.json"):
        try:
            newest = max(newest, os.stat(p).st_mtime)
        except OSError:
            continue
    return newest > 0 and now - newest < FRESH_S


def _rel_order(rel: str) -> tuple:
    day, _, name = rel.partition("/")
    m = _RUN.fullmatch(name)
    return (day, int(m[1]) if m else -1, name)


@dataclass
class Cand:
    """One run folder read for ingestion (metadata only until attached)."""
    root: "RootState"
    rel: str
    run: hub_build.Run
    sig: str
    rewrite: bool = False
    vote: str | None = None

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
    votes: Counter = field(default_factory=Counter)   # committed: one per ingested run
    stored_hint: str | None = None
    failed: dict = field(default_factory=dict)     # rel -> [retry_at (monotonic), attempts, error]
    readable: bool = True
    list_cursor: int = 0
    sweep_cursor: int = 0

    def hint(self, extra: Counter | None = None) -> str | None:
        votes = self.votes + extra if extra else self.votes
        ranked = votes.most_common(2)
        if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
            return self.stored_hint
        return ranked[0][0]

    def failed_day_pending(self, day: str, now: float) -> bool:
        return any(rel.startswith(day + "/") and f[0] > now and f[1] < MAX_RETRIES
                   for rel, f in self.failed.items())


class ChipSync:
    """One chip's run ingestion: registered roots, RAM bookkeeping, status."""

    def __init__(self, chip_dir):
        self.dir = Path(chip_dir)
        self.lock = threading.RLock()
        self.roots: dict[str, RootState] = {}
        self.identity: dict | None = None
        self.ledger_id: str | None = None
        self.active = True
        self.full_wanted = True
        self.listing_wanted = False
        self.last_listing = 0.0
        self.last_full = 0.0
        self.dirty: set[str] = set()
        self.unread: deque = deque()          # (root, rel, rewrite?) to read
        self.read: list[Cand] = []
        self.ready_cands: deque = deque()     # read + ordered, to ingest
        self.sweep: list[tuple[RootState, str]] = []
        self.sweep_initial = False
        self.pending_max: dict = {}
        self.in_hand = 0
        self.ready = False
        self.phase = "idle"
        self.done = 0
        self.total = 0
        self.counts: Counter = Counter()
        self.errors: deque = deque(maxlen=20)
        self.last_slice_ms = 0.0
        self.slices = 0
        # docs/282 review P1-2: the chip's Param History snapshots that are
        # not runs -- states SM itself observed -- imported once the runs are in
        self.observed_source: Callable[[], list[dict]] | None = None
        self.observe_wanted = True
        self.observe_queue: deque = deque()
        # S10 C1: why the last slice could not run (the ledger could not be
        # opened or bound); cleared by the next slice that completes
        self.slice_error: str | None = None
        # a busy/locked ledger: the projector's retry will very likely get through
        self.slice_error_transient = False
        # S10 C1.5: the live folder that opened the chip last (its comparison
        # key): every registered data root is linked to it in the ledger
        self.folder: str | None = None
        self.links_due = True

    @property
    def syncable(self) -> bool:
        """S10 C1: whether a slice has anything to read -- a registered data
        folder, or the chip's own observed states (its Param History
        snapshots). A chip with no data folder still gets its ledger and the
        states SM saw; it is kicked, re-listed and reported like any other."""
        return bool(self.roots) or self.observed_source is not None

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
            self.links_due = True
            return changed

    def request(self, *, full: bool = False, listing: bool = False, roots: Iterable[str] = ()) -> bool:
        """Mark work. Returns whether any registered root is affected."""
        with self.lock:
            if full:
                self.full_wanted = True
                self.observe_wanted = True
                return bool(self.roots)
            if listing:
                self.listing_wanted = True
                self.observe_wanted = True
                return bool(self.roots)
            hit = False
            for r in roots:
                k = _norm(r)
                if k in self.roots:
                    self.dirty.add(k)
                    hit = True
            if hit:
                # a run landed: SM may have captured a state around it too
                self.observe_wanted = True
            return hit

    def has_work(self) -> bool:
        with self.lock:
            inbox = bool(self.full_wanted or self.listing_wanted or self.dirty)
        observing = bool(self.observe_queue) or (self.observe_wanted and self.observed_source is not None)
        return bool(inbox or self.unread or self.read or self.ready_cands or self.sweep or observing)

    def _failed_waiting(self) -> tuple[int, int]:
        """(retries still due, given up)"""
        due = gave_up = 0
        for rs in list(self.roots.values()):
            for f in list(rs.failed.values()):
                if f[1] >= MAX_RETRIES:
                    gave_up += 1
                else:
                    due += 1
        return due, gave_up

    def status(self) -> dict:
        # lock-free snapshot: the slice owns the work lists; a read that races
        # it can only lag by one item, never block a request. The item a
        # slice holds right now counts as pending (docs/275 review, P1-3).
        pending = (len(self.unread) + len(self.read) + len(self.ready_cands) + self.in_hand
                   + len(self.observe_queue))
        # a root the watcher saw move is "building" until it is looked at:
        # the run that moved it may not be in the ledger yet
        inbox = bool(self.full_wanted or self.dirty)
        roots = list(self.roots.values())
        due, gave_up = self._failed_waiting()
        building = ((not self.ready) or inbox or pending > 0 or due > 0
                    or (self.sweep_initial and bool(self.sweep)))
        unreadable = [str(rs.path) for rs in roots if not rs.readable]
        if not self.syncable:
            # nothing to read: no data folder and no observed states
            state = "ready"
        elif self.slice_error is not None and not (self.slice_error_transient and building):
            # (a busy / locked ledger in the middle of a catch-up stays "building":
            # a half-built ledger is never presented as the answer -- final review)
            # S10 C1: a ledger that could not be opened, bound or written in
            # the last slice -- not "building" forever (S10 C6: a corrupt
            # ledger.sqlite left every list "being built" on a chip WITH a
            # data folder): what the ledger holds (if anything) is what can
            # be shown, a reader that cannot open it says so, and the next
            # good slice clears this
            state = "degraded"
        elif building:
            state = "building"
        elif unreadable or gave_up:
            state = "degraded"
        else:
            state = "ready"
        st = {
            "state": state,
            "phase": self.phase,
            "done": self.done,
            "total": self.total,
            "roots": [{"path": str(rs.path), "sources": list(rs.sources), "runs": len(rs.known),
                       "deferred": sorted(rs.deferred), "readable": rs.readable,
                       "failed": sorted(rs.failed)} for rs in roots],
            "deferred": sum(len(rs.deferred) for rs in roots),
            "failed": due + gave_up,
            "unreadable": unreadable,
            "active": self.active,
            "counts": dict(self.counts),
            "errors": list(self.errors),
            "last_slice_ms": round(self.last_slice_ms, 1),
            "slices": self.slices,
        }
        if self.slice_error is not None:
            st["ledger_error"] = self.slice_error
        if not roots:
            st["note"] = "no data folder is registered to this chip"
            if state == "degraded":
                st["note"] += ("; the change ledger could not be opened now, so it holds only "
                               "what it held before")
        elif state == "degraded" and self.slice_error is not None:
            st["note"] = ("the change ledger could not be opened or written now, so it holds only "
                          "what it held before")
        elif state == "degraded":
            st["note"] = ("a data folder cannot be read now" if unreadable
                          else "some runs could not be ingested") + "; the ledger holds everything else"
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
            with txn(store):
                if store.meta("chip_identity") is None:
                    store.set_meta("chip_identity", json_bytes(self.identity).decode("utf-8"))
        for rs in list(self.roots.values()):
            if rs.root_id is None:
                self._register(store, rs)
                reset = True
        return reset

    def _link(self, store: HubStore) -> None:
        """S10 C1.5: record that the folder that opened the chip registered
        each of its data roots (``root_links``): a run is in that folder's
        view of the ledger when one of its roots is linked to it."""
        self.links_due = False
        if not self.folder:
            return
        ids = [rs.root_id for rs in list(self.roots.values()) if rs.root_id is not None]
        if not ids:
            return
        have = {r[0] for r in store.conn.execute("SELECT root_id FROM root_links WHERE folder=?",
                                                 (self.folder,))}
        if set(ids) <= have:
            return
        with txn(store):
            for rid in ids:
                store.link_root(rid, self.folder)

    def _register(self, store: HubStore, rs: RootState) -> None:
        normalized = os.path.normcase(str(rs.path.resolve()))
        row = store.conn.execute("SELECT root_id, folder_key, offset_hint FROM roots WHERE path=?",
                                 (normalized,)).fetchone()
        if row is None:
            with txn(store):
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
            "WHERE l.root_id=? ORDER BY e.t_utc_us DESC, e.run_id DESC LIMIT 1", (rs.root_id,)).fetchone()
        rs.newest = (row[0], row[1] or 0, row[2]) if row else None

    # -- one slice -------------------------------------------------------

    def run_slice(self, store: HubStore, budget_s: float | None,
                  should_yield: Callable[[], bool] | None = None) -> bool:
        """Do up to *budget_s* of work (None: until done). Returns whether
        more work remains. Runs on the projector thread only; the RAM lock is
        taken just to read the inbox (``request`` / ``set_roots`` from other
        threads), so ``status()`` never waits for a slice. ``should_yield``
        (a user request is in flight) ends the slice after the current item."""
        t0 = time.perf_counter()
        deadline = None if budget_s is None else time.monotonic() + budget_s
        self.slices += 1
        with self.lock:
            full, dirty, listing = self.full_wanted, set(self.dirty), self.listing_wanted
            if full or dirty:
                # a moved root or a chip open: "building" until looked at --
                # set BEFORE the inbox is emptied, so status() never sees
                # an empty inbox and a stale "ready" (P1-3)
                self.ready = False
            self.full_wanted = self.listing_wanted = False
            self.dirty.clear()
        if full or dirty:
            if self.phase == "ready":
                self.done = self.total = 0          # a new burst of work
        if self._bind(store):
            full = True
        if self.links_due:
            self._link(store)
        if full or dirty or listing:
            self._list(full=full, dirty=dirty, listing=listing)
        steps = 0

        def late() -> bool:
            # every slice makes progress (at least one item), whatever the budget
            if steps == 0:
                return False
            if should_yield is not None and should_yield():
                return True
            return _late(deadline)

        while self.unread and not late():
            rs, rel, rewrite = self.unread.popleft()
            self.in_hand += 1
            try:
                self._read(rs, rel, rewrite)
            finally:
                self.in_hand -= 1
            steps += 1
        if not self.unread and self.read:
            self._resolve()
        while self.ready_cands and not late():
            cand = self.ready_cands.popleft()
            self.in_hand += 1
            try:
                self._ingest(store, cand)
            finally:
                self.in_hand -= 1
            steps += 1
        while self.sweep and not self.unread and not self.ready_cands and not late():
            rs, rel = self.sweep.pop()
            self.in_hand += 1
            try:
                self._check(store, rs, rel)
            finally:
                self.in_hand -= 1
            steps += 1
        if self.sweep and not self.unread and not self.ready_cands:
            self.phase = "sweeping"
        # docs/282 review P1-2: the observed snapshots, only once every known
        # run is in (an observation is compared with the runs around it)
        runs_settled = not (self.unread or self.read or self.ready_cands)
        ingested = sum(self.counts[k] for k in ("added", "inserted", "moved", "rewritten", "completed"))
        if runs_settled and self.observed_source is not None:
            if self.observe_wanted:
                self.observe_wanted = False
                self._observe_list(store)
            while self.observe_queue and not late():
                snap = self.observe_queue.popleft()
                self.in_hand += 1
                try:
                    self.counts["observed:" + attach_observed(store, snap)] += 1
                except Exception as exc:  # noqa: BLE001 -- one snapshot never stops the sync
                    logger.warning("hub sync: snapshot %s failed", snap.get("ts"), exc_info=True)
                    self.errors.append(f"snapshot {snap.get('ts')}: {type(exc).__name__}: {exc}")
                finally:
                    self.in_hand -= 1
                steps += 1
            if ingested != self.__dict__.get("_ingested_seen", 0):
                # a run that landed next to an observation of its own save
                # takes its change back (clock skew between the two PCs)
                self.__dict__["_ingested_seen"] = ingested
                self.counts["observed:dropped"] += drop_observed_runs(store)
        more = self.has_work()
        self.slice_error = None
        self.slice_error_transient = False
        if not more:
            self.phase = "ready"
            self.ready = True
            self.sweep_initial = False
        self.last_slice_ms = (time.perf_counter() - t0) * 1000.0
        return more

    def _observe_list(self, store: HubStore) -> None:
        """Queue the snapshots the ledger has not looked at yet, oldest first."""
        try:
            snaps = list(self.observed_source() or ())
        except Exception as exc:  # noqa: BLE001 -- history unreadable now: try at the next look
            self.errors.append(f"snapshots: {type(exc).__name__}: {exc}")
            return
        _observed_table(store.conn)
        self.counts["observed:offered_again"] += _observed_lanes(store, snaps)
        seen = {r[0] for r in store.conn.execute("SELECT ts FROM observed_snapshots")}
        queued = {s["ts"] for s in self.observe_queue}
        fresh = [s for s in snaps if s["ts"] not in seen and s["ts"] not in queued]
        fresh.sort(key=lambda s: (s["t_us"], s["ts"]))
        self.observe_queue.extend(fresh)

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
        now = time.monotonic()
        added = 0
        for rs in targets:
            new = self._list_root(rs, "full" if full else ("listing" if listing else "light"))
            # deferred runs, and failed runs whose retry is due, are looked
            # at again on every listing (P1-2: a run that raised is retried)
            retry = [rel for rel, f in rs.failed.items() if f[0] <= now and f[1] < MAX_RETRIES]
            for rel in [*new, *list(rs.deferred), *retry]:
                if (id(rs), rel) not in queued:
                    self.unread.append((rs, rel, rel in rs.known))
                    queued.add((id(rs), rel))
                    added += 1
            if not rs.readable:
                # an unlistable root (a share that dropped out) proves
                # nothing about its runs: no gone-check, status "degraded"
                continue
            if full:
                self.sweep.extend((rs, rel) for rel in rs.known)
                rs.sweep_cursor = 0
            else:
                picks = self._newest_runs(rs) if _norm(rs.path) in dirty else []
                if listing:
                    picks += self._sweep_chunk(rs)
                self.sweep.extend((rs, rel) for rel in dict.fromkeys(picks))
        self.total += added               # each queued item counts once in "n/N"
        if full:
            self.last_full = time.monotonic()
            if not self.ready:
                self.sweep_initial = True
        self.phase = "reading"

    @staticmethod
    def _newest_runs(rs: RootState) -> list[str]:
        """The newest RECENT_CHECK ingested runs by (day, run id) -- the ones
        a light tick stat-checks (a rewrite of a recent run)."""
        days = sorted({rel.partition("/")[0] for rel in rs.known})
        out: list[str] = []
        for day in reversed(days):
            mine = sorted((r for r in rs.known if r.startswith(day + "/")), key=_rel_order)
            out = mine + out
            if len(out) >= RECENT_CHECK:
                break
        return out[-RECENT_CHECK:]

    @staticmethod
    def _sweep_chunk(rs: RootState) -> list[str]:
        """The next SWEEP_CHUNK ingested runs, round robin: every run is
        stat-checked within about SWEEP_EVERY_S without a burst."""
        rels = sorted(rs.known, key=_rel_order)
        if not rels:
            return []
        start = rs.sweep_cursor % len(rels)
        chunk = (rels[start:] + rels[:start])[:SWEEP_CHUNK]
        rs.sweep_cursor = start + len(chunk)
        return chunk

    def _list_root(self, rs: RootState, mode: str) -> list[str]:
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
        newest = set(days[-NEWEST_DATES:]) | {d for d in days if d not in rs.dates}
        if mode == "full" or not rs.listed:
            look = days
        elif mode == "listing":
            # bounded round robin over the older days (P2: a deep archive)
            older = [d for d in days if d not in newest]
            start = rs.list_cursor % len(older) if older else 0
            chunk = (older[start:] + older[:start])[:LISTING_CHUNK]
            rs.list_cursor = start + len(chunk)
            look = sorted(newest | set(chunk))
        else:
            look = sorted(newest)
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
            if not rs.failed_day_pending(day, now):
                # P1-2: a day whose run failed is NOT marked listed until the
                # retry is due -- else an unchanged day would hide it forever
                rs.dates[day] = mtime
            for name in names:
                rel = f"{day}/{name}"
                if rel in rs.known or rel in rs.failed:
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
        # the offset vote counts once, when the run is ingested (P3: a
        # deferred run re-read every look used to vote every look)
        vote = None if rewrite else timefmt.archive_offset_hint([run.node])
        self.read.append(Cand(rs, rel, run, sig, rewrite, vote))

    def _resolve(self) -> None:
        batch: dict[int, Counter] = {}
        for cand in self.read:
            if cand.vote is not None:
                batch.setdefault(id(cand.root), Counter())[cand.vote] += 1
        ok = []
        for cand in self.read:
            try:
                hub_build.resolve_instant(cand.run, cand.root.hint(batch.get(id(cand.root))))
                ok.append(cand)
            except OSError as exc:
                # vanished between its listing and now (P3: one folder used
                # to wedge the sync, re-raising on every later slice)
                cand.root.deferred.pop(cand.rel, None)
                self.counts["vanished"] += 1
                self.done += 1
                self.errors.append(f"{cand.rel}: {exc}")
        self.ready_cands = deque(sorted([*self.ready_cands, *ok], key=lambda c: c.key))
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
                rs.failed.pop(cand.rel, None)
                self.counts["vanished"] += 1
                self.done += 1
                return
            raw, digest, error = _read_pair(cand.run.folder)

            def waiting() -> bool:
                # the newest run of its folder, or a saved pair whose files
                # are still being written (a copy in progress, P2)
                return (not cand.rewrite) and (self._is_newest(cand) or _fresh(cand.run.folder))
            if not cand.rewrite and ((error is not None and waiting())
                                     or (cand.run.error is not None and self._is_newest(cand))):
                # in flight (the newest run, or files still being written: a
                # copy in progress), or a crash we cannot tell from one yet:
                # wait for it to complete or for time/a later run to prove it final
                rs.deferred[cand.rel] = error or cand.run.error
                self.counts["deferred"] += 1
                self.done += 1
                return
            if cand.rewrite:
                outcome = self._rewrite(store, cand, raw, digest, error)
            else:
                try:
                    outcome = attach_run(store, cand, raw, digest, error, src="sm_sync", newest=waiting)
                except Deferred as why:
                    rs.deferred[cand.rel] = str(why)
                    self.counts["deferred"] += 1
                    self.done += 1
                    return
            if outcome == "retry":
                self.done += 1
                return
            rs.deferred.pop(cand.rel, None)
            rs.failed.pop(cand.rel, None)
            self.counts[outcome] += 1
            if outcome in ("added", "inserted", "location", "rewritten", "completed", "moved", "split",
                           "unchanged", "present", "gone"):
                eid = store.conn.execute("SELECT eid FROM locations WHERE root_id=? AND rel_path=?",
                                         (rs.root_id, cand.rel)).fetchone()
                if eid is not None:
                    rs.known[cand.rel] = [eid[0], cand.sig]
                    key = (cand.run.instant, cand.run.run_id, cand.rel)
                    if rs.newest is None or key > rs.newest:
                        rs.newest = key
                if outcome in ("added", "inserted") and cand.vote is not None:
                    rs.votes[cand.vote] += 1
                    with txn(store):
                        store.set_meta(f"offset_votes:{rs.root_id}", json_bytes(dict(rs.votes)).decode("utf-8"))
            self.done += 1
        except Exception as exc:  # noqa: BLE001 -- one bad folder never stops the sync
            logger.warning("hub sync: %s failed", cand.run.folder, exc_info=True)
            self.errors.append(f"{cand.rel}: {type(exc).__name__}: {exc}")
            self.counts["failed"] += 1
            prev = rs.failed.get(cand.rel)
            attempts = (prev[1] if prev else 0) + 1
            rs.failed[cand.rel] = [time.monotonic() + RETRY_AFTER_S, attempts, f"{type(exc).__name__}: {exc}"]
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
            if not rs.path.is_dir():
                rs.readable = False         # the whole root went away: prove nothing
                return
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
            # the event of this folder as the LEDGER says now (another window
            # may have moved it since this window's RAM was filled)
            loc = store.conn.execute("SELECT eid FROM locations WHERE root_id=? AND rel_path=?",
                                     (rs.root_id, rel)).fetchone()
            if loc is not None:
                # the event is gone when no OTHER copy still holds it (a copy
                # deleted earlier keeps its location row, marked gone)
                if not _live_copies(store, loc[0], rs.root_id, rel):
                    store.conn.execute("UPDATE events SET flags = flags | ? WHERE eid=?", (SOURCE_GONE, loc[0]))
                entry[0] = loc[0]
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
            hint = rs.hint()
            if error is not None:
                state_file = hub_build.state_paths(cand.run.folder)[0]
                if ev["error"] is not None:
                    # it never had a state and still has none: its node only
                    _update_run_meta(store, ev, cand, hint)
                    _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
                    return "unchanged"
                if not state_file.is_file():
                    # the saved state is gone: the ledger keeps what it
                    # ingested; the event is gone when no other copy holds it
                    if not _live_copies(store, ev["eid"], rs.root_id, cand.rel):
                        store.conn.execute("UPDATE events SET flags = flags | ? WHERE eid=?",
                                           (SOURCE_GONE, ev["eid"]))
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
                _update_run_meta(store, ev, cand, hint)
                flags = (ev["flags"] & ~(SOURCE_GONE | NODE_UNREADABLE)) | (
                    NODE_UNREADABLE if cand.run.error is not None else 0)
                store.conn.execute("UPDATE events SET flags=? WHERE eid=?", (flags, ev["eid"]))
                _reprove(store, ev, cand.run.node)
                _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
                return "unchanged"
            others = _live_copies(store, ev["eid"], rs.root_id, cand.rel)
            if others and (ev["error"] is not None or digest != ev["state_hash"] or not same_place):
                # a COPY of this run now holds other bytes, or its node.json
                # names another instant, than the copies left on the event:
                # this folder leaves the shared event (which keeps what the
                # other copies hold) and is ingested as what it holds now. One
                # event per (instant, run, bytes) -- what a build from the
                # files gives; every location replays its own bytes and sits
                # at its own instant (P2: copies)
                store.conn.execute("DELETE FROM locations WHERE root_id=? AND rel_path=?", (rs.root_id, cand.rel))
                attach_run(store, cand, raw, digest, None, src=ev["src"] or "sm_sync", in_txn=True)
                return "split"
            if not same_place:
                held = store.conn.execute(
                    "SELECT eid FROM events WHERE kind='run' AND t_utc_us=? AND run_id=? AND experiment=? "
                    "AND state_hash IS ? AND eid<>?",
                    (cand.run.instant, cand.run.run_id, cand.run.experiment, digest, ev["eid"])).fetchone()
                if held is not None:
                    # the folder now names a run the ledger already holds (a
                    # copy that moved there first): it becomes that run's
                    # location; an event no folder names any more is removed,
                    # one that only deleted copies name is gone
                    store.conn.execute("DELETE FROM locations WHERE root_id=? AND rel_path=?",
                                       (rs.root_id, cand.rel))
                    if not store.conn.execute("SELECT 1 FROM locations WHERE eid=?", (ev["eid"],)).fetchone():
                        remove_event(store, ev)
                    else:
                        store.conn.execute("UPDATE events SET flags = flags | ? WHERE eid=?",
                                           (SOURCE_GONE, ev["eid"]))
                    attach_run(store, cand, raw, digest, None, src=ev["src"] or "sm_sync", in_txn=True)
                    return "split"
                # node.json now names another instant: the run moves (its
                # neighbours on both sides are repaired) and keeps its eid;
                # it is REWRITTEN only when its saved bytes changed too
                changed = ev["error"] is None and digest != ev["state_hash"]
                attach_run(store, cand, raw, digest, None, src=ev["src"] or "sm_sync", in_txn=True,
                           extra_flags=(REWRITTEN if changed else 0) | (ev["flags"] & REWRITTEN), move=ev)
                return "moved"
            completed = ev["error"] is not None
            rediff_in_place(store, ev, cand, raw, digest, hint=hint, completed=completed)
            return "completed" if completed else "rewritten"


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


def _live_copies(store, eid: int, root_id: int, rel: str) -> int:
    """The OTHER locations of *eid* whose saved state is still on disk as far
    as the ledger knows: a copy marked gone, or whose state file went, no
    longer holds the event's bytes (docs/275 review: copies)."""
    return store.conn.execute(
        "SELECT COUNT(*) FROM locations l LEFT JOIN run_files f ON f.root_id=l.root_id AND f.rel_path=l.rel_path "
        "WHERE l.eid=? AND NOT (l.root_id=? AND l.rel_path=?) "
        "AND NOT (COALESCE(f.sig,'')='gone' OR COALESCE(f.sig,'') LIKE '-|%')", (eid, root_id, rel)).fetchone()[0]


def _chip(store) -> dict | None:
    raw = store.meta("chip_identity")
    return json.loads(raw) if raw else None


def _content_hash_of(state: dict, wiring: dict) -> str | None:
    """``working_copy.content_hash`` of a parsed pair -- the hash an SM write
    names as its base (P1-5 causal floor)."""
    from quam_state_manager.core import working_copy
    try:
        return working_copy.content_hash(state, wiring)
    except (ValueError, TypeError):
        return None


def _content_hash(raw) -> str | None:
    try:
        state, wiring = (json.loads(data) for data in raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(state, dict) or not isinstance(wiring, dict):
        return None
    return _content_hash_of(state, wiring)


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


def _detach(store: HubStore, ev) -> None:
    """Take a run out of its place (inside a transaction) WITHOUT deleting
    it: its successor is re-diffed against its predecessor (an SM successor
    anchored first), error events between them name the predecessor again,
    and its rows and checkpoint go. The caller places it again (a move)."""
    pred = store.conn.execute("SELECT * FROM events WHERE ord<? AND error IS NULL ORDER BY ord DESC LIMIT 1",
                              (ev["ord"],)).fetchone()
    succ = store.conn.execute("SELECT * FROM events WHERE ord>? AND error IS NULL ORDER BY ord LIMIT 1",
                              (ev["ord"],)).fetchone()
    succ_flat = _prepare_successor(store, succ) if ev["error"] is None else None
    pred_flat = store.flat_of(pred) if pred is not None else {}
    eid = ev["eid"]
    store.conn.execute("DELETE FROM changes WHERE eid=?", (eid,))
    store.conn.execute("DELETE FROM checkpoints WHERE eid=?", (eid,))
    # out of every ordering while it is re-placed: no t_ord, a rank below 0,
    # and not a "good" event any query could pick as a predecessor
    store.conn.execute("UPDATE events SET t_ord=NULL, ord=?, error=? WHERE eid=?",
                       (-float(eid), "(being re-placed)", eid))
    pred_hash = pred["state_hash"] if pred is not None else None
    if succ is not None and succ_flat is not None:
        _rediff_successor(store, succ, pred_flat, pred_hash, succ_flat)
    if ev["error"] is None:
        store.refresh_error_checkpoints(pred["ord"] if pred is not None else 0.0,
                                        succ["ord"] if succ is not None else None,
                                        lambda: store.state_at(pred["eid"]) if pred is not None else {})
        store.refresh_error_bases(pred["ord"] if pred is not None else 0.0,
                                  succ["ord"] if succ is not None else None, pred_hash)
    store.refresh_reverts([succ["eid"] if succ is not None else None,
                           *store.same_hash_after(ev["state_hash"], ev["ord"])])


def attach_run(store: HubStore, cand: Cand, raw, digest, error, *, src: str,
               in_txn: bool = False, extra_flags: int = 0, newest: bool = False, move=None) -> str:
    """Insert one run at its place (I1-I4). Returns ``added`` (at the head),
    ``inserted`` (before existing events), ``location`` (a copy of a run the
    ledger holds) or ``present`` (another writer ingested this folder).
    ``move``: an existing event of this folder that is re-placed (keeps its
    eid; its old place is repaired first)."""
    if not in_txn:
        with txn(store):
            return attach_run(store, cand, raw, digest, error, src=src, in_txn=True, extra_flags=extra_flags,
                              newest=newest, move=move)
    rs, run = cand.root, cand.run
    c = store.conn
    if move is None:
        if c.execute("SELECT 1 FROM locations WHERE root_id=? AND rel_path=?", (rs.root_id, cand.rel)).fetchone():
            _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
            return "present"
        known = c.execute("SELECT eid FROM events WHERE kind='run' AND t_utc_us=? AND run_id=? AND experiment=? "
                          "AND state_hash IS ?", (run.instant, run.run_id, run.experiment, digest)).fetchone()
        if known:
            c.execute("INSERT INTO locations VALUES(?,?,?)", (known[0], rs.root_id, cand.rel))
            _put_sig(store, rs.root_id, cand.rel, cand.sig, None)
            return "location"
    else:
        _detach(store, move)
    # the content hash SM writes name as their base (P1-5): the same bytes
    # an event already holds share it; new bytes get it from their parse
    chash = None
    if error is None:
        row = c.execute("SELECT chash FROM events WHERE state_hash=? AND chash IS NOT NULL LIMIT 1",
                        (digest,)).fetchone()
        chash = row[0] if row else None

    def place(t_ord):
        lo, hi = store.neighbors((t_ord, rs.fkey, run.run_id, run.experiment, cand.rel, _NEW))
        return lo, hi, store.good_at_or_before(lo), store.good_at_or_after(hi)

    # P1-5: a run never lands after an SM write whose base IS this run's
    # content (a run on a PC whose clock is ahead of SM's)
    ceiling = store.causal_ceiling(chash, run.instant)
    t_ord = run.instant if ceiling is None else ceiling - 1
    lo, hi, pred, succ = place(t_ord)
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
                doc, flat, id_flags, chip, pair = hub_build.parse_state(raw, run.folder, chip, want_pair=True)
                flags |= id_flags
                if chash is None:
                    chash = _content_hash_of(*pair)
                    late = store.causal_ceiling(chash, run.instant)
                    if late is not None and ceiling is None:
                        # its content is the base of an SM write already placed
                        # before this clock: re-place it before that write
                        ceiling, t_ord = late, late - 1
                        lo, hi, pred, succ = place(t_ord)
                        pred_hash = pred["state_hash"] if pred is not None else None
                        pred_flat = store.flat_of(pred) if pred is not None else {}
                rows = rules.diff(pred_flat, flat)
            except (OSError, ValueError, TypeError) as exc:
                if newest and (newest() if callable(newest) else newest):
                    # a torn save of a run still being written is a write in
                    # flight, not a final error (the transaction rolls back)
                    raise Deferred(f"saved state not readable yet: {exc}") from exc
                error, doc, flat, rows, chash = str(exc), None, None, [], None
    if error is not None:
        flags |= CHIP_UNCERTAIN
    succ_flat = _prepare_successor(store, succ) if error is None else None
    # a fact about timing, whatever this run saved (P3: same result in
    # either landing order, a stateless run included)
    if store.overlaps_sm_write(_run_start(run, cand.root.hint()), run.instant):
        flags |= OVERLAPS_SM_WRITE
    ord_ = store.alloc_ord(lo, hi)
    event = hub_build.event_fields(run, root_id=rs.root_id, rel=cand.rel, hint=cand.root.hint(), digest=digest,
                                   base_hash=pred_hash, flags=flags, error=error, src=src)
    if error is None and shape_hash is None:
        shape_hash = store.shape_and_arrays(doc, flat)
    event.update(ord=ord_, shape_hash=shape_hash, n_changes=len(rows), t_ord=t_ord, chash=chash)
    if move is None:
        columns = ",".join(event)
        eid = c.execute(f"INSERT INTO events({columns}) VALUES({','.join('?' for _ in event)})",
                        tuple(event.values())).lastrowid
        c.execute("INSERT INTO locations VALUES(?,?,?)", (eid, rs.root_id, cand.rel))
    else:
        eid = move["eid"]
        event.pop("kind")
        sets = ",".join(f"{k}=?" for k in event)
        c.execute(f"UPDATE events SET {sets} WHERE eid=?", (*event.values(), eid))
    _put_sig(store, rs.root_id, cand.rel, cand.sig, time.time_ns() // 1000 if extra_flags & REWRITTEN else None)
    if chip is not None and store.meta("chip_identity") is None:
        store.set_meta("chip_identity", json_bytes(chip).decode("utf-8"))
    if succ is not None:
        succ = store.event(succ["eid"])              # alloc_ord may have renumbered
    hi_ord = succ["ord"] if succ is not None else None
    if error is None:
        store.write_rows(eid, rows, hub_build._proven(run.node, rows, flat))
        row = store.event(eid)
        store.remember_flat(row, flat)

        def doc_fn():
            return doc if doc is not None else store.state_at(eid)
        if succ is not None and succ_flat is not None:
            _rediff_successor(store, succ, flat, digest, succ_flat)
        store.refresh_error_checkpoints(ord_, hi_ord, doc_fn)
        store.refresh_error_bases(ord_, hi_ord, digest)
        store.maybe_checkpoint(eid, ord_, doc_fn)
    store.refresh_reverts([eid, succ["eid"] if succ is not None else None,
                           *store.same_hash_after(digest, ord_)])
    if move is not None:
        return "moved"
    return "added" if hi is None else "inserted"


OBSERVED_KIND = "observed"


def _observed_table(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS observed_snapshots("
                  "ts TEXT PRIMARY KEY, outcome TEXT NOT NULL, eid INTEGER)")
    # S10 C1.5: a snapshot row looked at under the per-folder rule (its
    # event's folder recorded; a dedupe against another folder's neighbour
    # offered again). A row made before has none here.
    conn.execute("CREATE TABLE IF NOT EXISTS observed_lanes(ts TEXT PRIMARY KEY)")


def folder_key(raw) -> str | None:
    """S10 C1.5: the comparison key of a recorded folder path -- the ONE key
    Param History compares folders with (``history._source_key``)."""
    from quam_state_manager.core.history import _source_key
    return _source_key(raw)


def _root_folders(store: HubStore, root_id) -> set:
    return {r[0] for r in store.conn.execute("SELECT folder FROM root_links WHERE root_id=?", (root_id,))}


def same_lane(store: HubStore, row, key: str | None) -> bool:
    """S10 C1.5: does event *row* belong to the folder *key*'s lane, for the
    observed dedupe? An SM write / observed state when its recorded folder is
    that folder; a run when one of its data roots is linked to that folder,
    or carries no link at all (a root registered before links were kept). A
    snapshot whose folder cannot be shown (*key* None) is compared with its
    neighbours as before."""
    if row is None or key is None:
        return True
    if row["kind"] == "run":
        roots = [r[0] for r in store.conn.execute("SELECT root_id FROM locations WHERE eid=?", (row["eid"],))]
        for rid in roots or [row["root_id"]]:
            links = _root_folders(store, rid)
            if not links or key in links:
                return True
        return False
    try:
        live = row["live"]
    except (IndexError, KeyError):
        live = None
    return folder_key(live) == key


def _lane_good(store: HubStore, edge, key: str | None, *, before: bool):
    """The nearest good event of folder *key*'s lane at or before (after)
    *edge* (an event row, or None)."""
    if edge is None:
        return None
    if key is None:
        return store.good_at_or_before(edge) if before else store.good_at_or_after(edge)
    sql = ("SELECT * FROM events WHERE ord<=? AND error IS NULL ORDER BY ord DESC" if before
           else "SELECT * FROM events WHERE ord>=? AND error IS NULL ORDER BY ord")
    for row in store.conn.execute(sql, (edge["ord"],)):
        if same_lane(store, row, key):
            return row
    return None


def _observed_lanes(store: HubStore, snaps: list[dict]) -> int:
    """S10 C1.5 backfill, once per snapshot row made before the per-folder
    rule: the observed event's folder is recorded from its snapshot's source,
    and a snapshot left out as equal to a neighbour of ANOTHER folder (that
    folder held it; this one may not have) is offered again. Returns how many
    were offered again."""
    rows = store.conn.execute("SELECT o.ts, o.outcome, o.eid FROM observed_snapshots o "
                              "LEFT JOIN observed_lanes l USING(ts) WHERE l.ts IS NULL").fetchall()
    if not rows:
        return 0
    by_ts = {s["ts"]: s for s in snaps}
    redo = 0
    with txn(store):
        for ts, outcome, eid in rows:
            snap = by_ts.get(ts)
            if snap is None:
                continue                 # another folder's snapshot: its own listing records it
            live = snap.get("live")
            key = folder_key(live)
            if eid is not None and live:
                store.conn.execute("UPDATE events SET live=? WHERE eid=? AND live IS NULL", (live, eid))
            if outcome in ("same_before", "same_after") and key is not None:
                t_us = int(snap["t_us"])
                lo, hi = store.neighbors((t_us, "", 0, "", "snapshot:" + ts, _NEW))
                near = store.good_at_or_before(lo) if outcome == "same_before" else store.good_at_or_after(hi)
                if near is not None and not same_lane(store, near, key):
                    store.conn.execute("DELETE FROM observed_snapshots WHERE ts=?", (ts,))
                    redo += 1
                    continue
            store.conn.execute("INSERT OR IGNORE INTO observed_lanes(ts) VALUES(?)", (ts,))
    return redo


def snapshot_instant_us(ts: str) -> int | None:
    """A Param History capture stamp (``YYYYMMDD_HHMMSS[_ffff..]``, UTC) as
    UTC microseconds; the fraction is the capture's own sub-second clock."""
    from datetime import datetime, timezone
    parts = str(ts).split("_")
    try:
        d = datetime.strptime(parts[0] + parts[1], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None
    us = 0
    if len(parts) > 2 and parts[2].isdigit() and len(parts[2]) >= 4:
        us = int(parts[2][:6].ljust(6, "0"))
    return int(d.timestamp()) * 1_000_000 + us


def attach_observed(store: HubStore, snap: dict, *, in_txn: bool = False) -> str:
    """docs/282 review P1-2: one Param History snapshot that is not a run --
    a state SM itself observed (an ``auto`` capture after an outside edit, a
    ``save`` from before the ledger, a ``backup`` taken before an apply) --
    placed by its instant as an ``observed`` event whose rows are its diff
    against the event before it (its successor is re-diffed, I2).

    Nothing is imported that the ledger already explains: a snapshot equal to
    the state before it or after it (an SM write's own ``save``/``backup``
    copy, a run's save seen by SM) is recorded as looked at and left out.
    ``snap``: ``{"ts", "t_us", "trigger", "dir", "actor"?}``. Returns the
    outcome: ``added`` / ``inserted`` / ``same_before`` / ``same_after`` /
    ``unreadable`` / ``present``."""
    if not in_txn:
        with txn(store):
            return attach_observed(store, snap, in_txn=True)
    c = store.conn
    _observed_table(c)
    if c.execute("SELECT 1 FROM observed_snapshots WHERE ts=?", (snap["ts"],)).fetchone():
        return "present"

    def done(outcome, eid=None):
        c.execute("INSERT OR REPLACE INTO observed_snapshots VALUES(?,?,?)", (snap["ts"], outcome, eid))
        c.execute("INSERT OR IGNORE INTO observed_lanes(ts) VALUES(?)", (snap["ts"],))
        return outcome
    folder = Path(snap["dir"])
    try:
        raw = (hub_build._read_shared(folder / "state.json"), hub_build._read_shared(folder / "wiring.json"))
        state, wiring = (json.loads(data) for data in raw)
        if not isinstance(state, dict) or not isinstance(wiring, dict):
            raise ValueError("not a state/wiring pair")
        doc = rules.merged(state, wiring)
        flat = rules.flatten(doc)
    except (OSError, ValueError) as exc:
        logger.debug("hub sync: snapshot %s unreadable: %s", snap["ts"], exc)
        return done("unreadable")
    digest = rules.state_hash(*raw)
    t_us = int(snap["t_us"])
    rel = "snapshot:" + snap["ts"]
    lo, hi = store.neighbors((t_us, "", 0, "", rel, _NEW))
    pred, succ = store.good_at_or_before(lo), store.good_at_or_after(hi)
    # S10 C1.5: a snapshot is left out as "already explained" only against a
    # neighbour of ITS OWN folder's lane: another folder holding this state
    # says nothing about whether this folder held it
    key = folder_key(snap.get("live"))
    lpred = pred if same_lane(store, pred, key) else _lane_good(store, lo, key, before=True)
    lsucc = succ if same_lane(store, succ, key) else _lane_good(store, hi, key, before=False)
    lpred_flat = store.flat_of(lpred) if lpred is not None else {}
    if not rules.diff(lpred_flat, flat):
        return done("same_before")
    if lsucc is not None and not rules.diff(store.flat_of(lsucc), flat):
        # the next event's own state, seen a moment early (a save copy, or a
        # run's save on a PC whose clock is ahead): it belongs to that event
        return done("same_after")
    # the stored rows stay the diff against the GLOBAL predecessor (I2)
    if pred is not None and (lpred is None or lpred["eid"] != pred["eid"]):
        pred_flat = store.flat_of(pred)
    else:
        pred_flat = lpred_flat if pred is not None else {}
    rows = rules.diff(pred_flat, flat)
    succ_flat = _prepare_successor(store, succ)
    chash = _content_hash_of(state, wiring)
    ord_ = store.alloc_ord(lo, hi)
    trigger = str(snap.get("trigger") or "snapshot")
    event = dict(kind=OBSERVED_KIND, t_utc_us=t_us, t_src=snap["ts"], t_quality="sm_clock", ord=ord_,
                 root_id=None, rel_path=rel, run_id=None, experiment=None, status=trigger,
                 run_start_us=None, run_end_us=None, parents=None, targets=None, patches_n=0,
                 actor=snap.get("actor"), plan_id=None, src="param_history:" + trigger,
                 live=snap.get("live"),
                 state_hash=digest, base_hash=pred["state_hash"] if pred is not None else None,
                 state_ref=str(folder), n_changes=len(rows), flags=0,
                 shape_hash=store.shape_and_arrays(doc, flat), error=None, t_ord=t_us, chash=chash)
    columns = ",".join(event)
    eid = c.execute(f"INSERT INTO events({columns}) VALUES({','.join('?' for _ in event)})",
                    tuple(event.values())).lastrowid
    if succ is not None:
        succ = store.event(succ["eid"])              # alloc_ord may have renumbered
    hi_ord = succ["ord"] if succ is not None else None
    store.write_rows(eid, rows, set())
    row = store.event(eid)
    store.remember_flat(row, flat)

    def doc_fn():
        return doc
    if succ is not None and succ_flat is not None:
        _rediff_successor(store, succ, flat, digest, succ_flat)
    store.refresh_error_checkpoints(ord_, hi_ord, doc_fn)
    store.refresh_error_bases(ord_, hi_ord, digest)
    store.maybe_checkpoint(eid, ord_, doc_fn)
    store.refresh_reverts([eid, succ["eid"] if succ is not None else None,
                           *store.same_hash_after(digest, ord_)])
    return done("added" if hi is None else "inserted", eid)


def drop_observed_runs(store: HubStore) -> int:
    """An observed event whose next event is a run that saved exactly the
    same state was that run's save, seen by SM before the run's folder (clock
    skew): the observation is removed and the run takes its change back.
    Returns how many were removed."""
    _observed_table(store.conn)
    dropped = 0
    for ev in store.conn.execute("SELECT * FROM events WHERE kind=? AND error IS NULL",
                                 (OBSERVED_KIND,)).fetchall():
        nxt = store.conn.execute("SELECT * FROM events WHERE ord>? AND error IS NULL ORDER BY ord LIMIT 1",
                                 (ev["ord"],)).fetchone()
        if nxt is None or nxt["kind"] != "run":
            continue
        try:
            key = folder_key(ev["live"])
        except (IndexError, KeyError):
            key = None
        if not same_lane(store, nxt, key):
            continue                # S10 C1.5: a run of another folder's lane explains nothing
        # cheap first: the same bytes, or the same parsed content
        if not ((nxt["state_hash"] and nxt["state_hash"] == ev["state_hash"])
                or (nxt["chash"] and nxt["chash"] == ev["chash"])):
            continue
        if rules.diff(store.flat_of(nxt), store.flat_of(ev)):
            continue
        with txn(store):
            remove_event(store, ev)
            store.conn.execute("UPDATE observed_snapshots SET outcome='same_after', eid=NULL WHERE eid=?",
                               (ev["eid"],))
        dropped += 1
    return dropped


def remove_event(store: HubStore, ev) -> None:
    """Take a run out of the chain and delete it (inside a transaction)."""
    _detach(store, ev)
    for table in ("locations",):
        store.conn.execute(f"DELETE FROM {table} WHERE eid=?", (ev["eid"],))
    store.conn.execute("DELETE FROM events WHERE eid=?", (ev["eid"],))


def rediff_in_place(store: HubStore, ev, cand: Cand, raw, digest, *, hint, completed: bool = False) -> None:
    """A rewritten run folder (inside a transaction): the event keeps its
    place and eid; its rows become the diff of its CURRENT saved pair against
    its predecessor, its successor is re-diffed against it, and the
    checkpoints that held its state are refreshed. SM events after it are
    facts: an un-anchored one is anchored first, none is re-diffed.
    ``completed``: an error event whose state arrived -- not a rewrite."""
    run = cand.run
    pred = store.conn.execute("SELECT * FROM events WHERE ord<? AND error IS NULL ORDER BY ord DESC LIMIT 1",
                              (ev["ord"],)).fetchone()
    succ = store.conn.execute("SELECT * FROM events WHERE ord>? AND error IS NULL ORDER BY ord LIMIT 1",
                              (ev["ord"],)).fetchone()
    succ_flat = _prepare_successor(store, succ)
    pred_flat = store.flat_of(pred) if pred is not None else {}
    chip = _chip(store)
    doc, flat, id_flags, chip, pair = hub_build.parse_state(raw, run.folder, chip, want_pair=True)
    rows = rules.diff(pred_flat, flat)
    store.conn.execute("DELETE FROM changes WHERE eid=?", (ev["eid"],))
    store.write_rows(ev["eid"], rows, hub_build._proven(run.node, rows, flat))
    shape_hash = store.shape_and_arrays(doc, flat)
    keep = ev["flags"] & ~(CHIP_UNCERTAIN | NODE_UNREADABLE | SOURCE_GONE | TIME_ASSUMED)
    flags = keep | id_flags | (0 if completed else REWRITTEN)
    if run.error is not None:
        flags |= NODE_UNREADABLE
    if run.quality in ("assumed_local", "mtime"):
        flags |= TIME_ASSUMED
    fields = hub_build.event_fields(run, root_id=ev["root_id"], rel=cand.rel, hint=hint, digest=digest,
                                    base_hash=pred["state_hash"] if pred is not None else None,
                                    flags=flags, error=None, src=ev["src"])
    for k in ("kind", "t_utc_us", "root_id", "rel_path", "run_id", "experiment", "src"):
        fields.pop(k)
    fields.update(shape_hash=shape_hash, n_changes=len(rows), chash=_content_hash_of(*pair))
    sets = ",".join(f"{k}=?" for k in fields)
    store.conn.execute(f"UPDATE events SET {sets} WHERE eid=?", (*fields.values(), ev["eid"]))
    old_hash = ev["state_hash"]
    row = store.event(ev["eid"])
    store.remember_flat(row, flat)
    if store.conn.execute("SELECT 1 FROM checkpoints WHERE eid=?", (ev["eid"],)).fetchone():
        store.conn.execute("UPDATE checkpoints SET hash=? WHERE eid=?", (store.put_blob(json_bytes(doc)), ev["eid"]))
    if succ is not None and succ_flat is not None:
        _rediff_successor(store, succ, flat, digest, succ_flat)
    hi_ord = succ["ord"] if succ is not None else None
    store.refresh_error_checkpoints(ev["ord"], hi_ord, lambda: doc)
    store.refresh_error_bases(ev["ord"], hi_ord, digest)
    if chip is not None and store.meta("chip_identity") is None:
        store.set_meta("chip_identity", json_bytes(chip).decode("utf-8"))
    store.refresh_reverts([ev["eid"], succ["eid"] if succ is not None else None,
                           *store.same_hash_after(old_hash, ev["ord"]),
                           *store.same_hash_after(digest, ev["ord"])])
    _put_sig(store, ev["root_id"], cand.rel, cand.sig, None if completed else time.time_ns() // 1000)


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
              kick: bool = True, exclusive: bool = True,
              observed: Callable[[], list[dict]] | None = None,
              folder: str | None = None) -> ChipSync:
    """A chip was activated: register its roots and catch every one of them
    up in the background (no page visit needed). ``exclusive``: every other
    chip's sync goes idle -- not watched, not swept -- until it is opened
    again (docs/275 review: every chip ever opened used to stay swept)."""
    cs = sync_for(chip_dir)
    if exclusive:
        for other in registered():
            if other is not cs:
                other.active = False
    cs.active = True
    cs.set_roots(roots)
    if folder is not None:
        # S10 C1.5: the opening folder (its comparison key); its data roots
        # are linked to it at the next slice
        cs.folder = folder
        cs.links_due = True
    if identity:
        cs.identity = identity
    if observed is not None:
        cs.observed_source = observed
    cs.request(full=True)
    if kick and cs.syncable:
        _kick(cs)
    return cs


def on_roots_moved(moved: Iterable[str]) -> int:
    """The run watcher's listener: wake every active chip whose registered
    folder moved. Never blocks (the work runs on the projector thread)."""
    moved = list(moved)
    n = 0
    for cs in registered():
        if cs.active and cs.request(roots=moved):
            _kick(cs)
            n += 1
    return n


def periodic(now: float | None = None) -> int:
    """The projector's periodic look (every ~30 s) at every ACTIVE chip:
    a bounded listing of its day folders and a chunk of its stat sweep, a
    re-look at deferred runs and at failed runs whose retry is due, and a
    rescue for a chip that has work but no slice queued (P1-6)."""
    now = time.monotonic() if now is None else now
    n = 0
    for cs in registered():
        # S10 C1: a chip with no data folder is looked at too -- its observed
        # states (Param History snapshots SM captured since) are re-listed
        # every LISTING_EVERY_S
        if not cs.syncable or not cs.active:
            continue
        if cs.has_work():
            if not _queued(cs):
                _kick(cs)
                n += 1
            continue
        if now - cs.last_listing >= LISTING_EVERY_S:
            cs.request(listing=True)
        else:
            # an in-flight run whose state lands by rewriting a file inside
            # an existing quam_state folder moves no directory the watcher
            # looks at; a failed run's retry may be due: look again (a few reads)
            waiting = [str(rs.path) for rs in list(cs.roots.values())
                       if rs.deferred or any(f[0] <= now and f[1] < MAX_RETRIES for f in rs.failed.values())]
            if not waiting:
                continue
            cs.request(roots=waiting)
        _kick(cs)
        n += 1
    return n


def _queued(cs: ChipSync) -> bool:
    from quam_state_manager.core import hub
    proj = hub._PROJECTOR
    return proj.inline is False and proj.sync_queued(hub.Hub.for_chip(cs.dir))


def _kick(cs: ChipSync) -> None:
    from quam_state_manager.core import hub
    hub.kick_sync(hub.Hub.for_chip(cs.dir))


def kick(cs: ChipSync) -> None:
    """Queue a slice for a registered chip (when it has roots, or observed
    states to import -- S10 C1)."""
    if cs.syncable:
        _kick(cs)


def slice_failed(chip_dir, exc: BaseException) -> None:
    """The hub's projector could not run a slice for *chip_dir* (the ledger
    could not be opened, bound or written). Recorded for ``status()``; the
    projector retries. Never raises."""
    try:
        with _SYNCS_LOCK:
            cs = _SYNCS.get(_norm(chip_dir))
        if cs is not None:
            cs.slice_error = f"{type(exc).__name__}: {exc}"
            msg = str(exc).lower()
            cs.slice_error_transient = "locked" in msg or "busy" in msg
            cs.errors.append("slice: " + cs.slice_error)
    except Exception:  # noqa: BLE001 -- bookkeeping only
        logger.debug("hub sync: recording a failed slice failed", exc_info=True)


def run(chip_dir, store: HubStore, budget_s: float | None,
        should_yield: Callable[[], bool] | None = None) -> bool:
    """One slice for *chip_dir* on *store* (the projector's connection, under
    the chip's writer lock). Returns whether more work remains."""
    return sync_for(chip_dir).run_slice(store, budget_s, should_yield)


def status(chip_dir) -> dict:
    """``{"state": "building" | "ready" | "degraded", "done", "total", ...}``
    from RAM. ``degraded``: nothing is in progress, but a data folder cannot
    be listed now or some run could not be ingested -- the ledger holds
    everything else."""
    with _SYNCS_LOCK:
        cs = _SYNCS.get(_norm(chip_dir))
    if cs is None:
        return {"state": "idle", "note": "no run sync is registered for this chip in this window",
                "done": 0, "total": 0}
    return cs.status()


def require_ready(chip_dir) -> dict:
    """Raise :class:`Building` while the ledger is catching up. ``degraded``
    is returned (not raised): its answer is complete for what can be read,
    and a surface says which folder or run is missing."""
    st = status(chip_dir)
    if st["state"] == "building":
        raise Building(chip_dir, st)
    return st


# ----------------------------------------------------------------------
# invariants, for tests, the rig and a crash check
# ----------------------------------------------------------------------

def verify(store: HubStore, *, sample: int | None = None, read_runs: bool = True) -> dict:
    """Check I1-I4 on a ledger: canonical order, every run's base hash, every
    run's ``state_at`` against the saved pair of EVERY one of its locations
    (when that folder still holds the ingested bytes), every checkpoint,
    every run's location. Returns counts and a list of problems."""
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
                    got = None
                    for loc in store.conn.execute(
                            "SELECT r.path, l.rel_path, f.sig FROM locations l JOIN roots r USING(root_id) "
                            "LEFT JOIN run_files f ON f.root_id=l.root_id AND f.rel_path=l.rel_path "
                            "WHERE l.eid=?", (r["eid"],)).fetchall():
                        folder = Path(loc[0]) / loc[1]
                        if not folder.is_dir() or (loc[2] is not None and file_sig(folder) != loc[2]):
                            continue        # changed since: the sweep will re-read it
                        try:
                            own = rules.flatten(hub_build.read_doc(folder))
                        except (OSError, ValueError, TypeError):
                            continue
                        if got is None:
                            got = rules.flatten(store.state_at(r["eid"]))
                        if rules.diff(got, own):
                            problems.append(f"eid {r['eid']}: state_at differs from the saved pair of {loc[1]}")
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
