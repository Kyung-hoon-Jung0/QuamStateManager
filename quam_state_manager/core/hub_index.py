"""Read-only, columnar ledger search index. See docs/279."""

from __future__ import annotations

from array import array
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import itertools
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
from typing import Any
from zoneinfo import ZoneInfo

from quam_state_manager.core import hub_sync, project_time, search_query
from quam_state_manager.core.hub_store import segments
from quam_state_manager.core.ramcache import KeyedMemo, Warming

_ENTITY = re.compile(r"q[a-z]*\d+(?:-q[a-z]*\d+)?$", re.I)
_DAY = re.compile(r"\d{4}-\d{2}-\d{2}$")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class ReadContext:
    """Bind a ledger to the existing project setting, or an explicit offline zone.

    A project binding re-reads display_zone on every query. An unset project
    zone fails explicitly; it never substitutes the server's local zone.
    Unbound HubStore inputs support non-calendar queries only.
    """

    store: Any
    instance: Any = None
    project: str | None = None
    zone: str | None = None


def context(store, *, instance=None, project=None, zone=None) -> ReadContext:
    if zone is not None and instance is not None:
        raise ValueError("choose a project setting or an explicit offline zone")
    return ReadContext(store, instance, project, zone)


def _binding(store):
    if isinstance(store, ReadContext):
        zone = (project_time.display_zone(store.instance, store.project)["zone"]
                if store.instance is not None else store.zone)
        if not zone and store.instance is not None:
            # A lab that set no project zone sees its days in THIS PC's zone --
            # the same fallback the report and the log's "today" already use.
            # Raising here left the Calibration log empty with an internal
            # message on every chip without a zone.
            zone = project_time.pc_zone()
        if not zone:
            raise ValueError("a project time zone is required for ledger queries")
        return store.store, zone
    return store, "UTC"


def require_day_zone(store):
    """Calendar answers require a declared zone, never an implicit default."""
    if not isinstance(store, ReadContext):
        raise ValueError("bind the project time zone before querying ledger days")


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


def _entities(text):
    text = text.lower()
    if _ENTITY.fullmatch(text):
        yield text
        if "-" in text:
            yield from text.split("-")
    elif text.startswith("cz_"):
        yield text


def footprint(value) -> int:
    """Retained Python bytes, including containers and arrays, counted once."""
    seen = set()

    def size(obj):
        ident = id(obj)
        if ident in seen:
            return 0
        seen.add(ident)
        n = sys.getsizeof(obj)
        if isinstance(obj, dict):
            n += sum(size(k) + size(v) for k, v in obj.items())
        elif isinstance(obj, (list, tuple, set, frozenset)):
            n += sum(map(size, obj))
        elif hasattr(obj, "__dict__"):
            n += size(vars(obj))
        return n

    return size(value)


@dataclass
class LedgerIndex:
    ledger_id: str
    zone: str
    eids: array = field(default_factory=lambda: array("I"))
    t: array = field(default_factory=lambda: array("q"))
    kind: array = field(default_factory=lambda: array("I"))
    root: array = field(default_factory=lambda: array("q"))
    run_id: array = field(default_factory=lambda: array("q"))
    experiment: array = field(default_factory=lambda: array("I"))
    flags: array = field(default_factory=lambda: array("I"))
    positions: dict = field(default_factory=dict)
    names: dict = field(default_factory=lambda: {"kind": {}, "experiment": {}})
    postings: dict = field(default_factory=lambda: {
        key: {} for key in ("experiment", "entity", "day", "actor", "family", "kind")})
    paths: dict = field(default_factory=dict)
    search_paths: dict = field(default_factory=dict)
    path_postings: dict = field(default_factory=dict)
    keys: dict = field(default_factory=dict)

    def classify(self, token):
        if _DAY.fullmatch(token):
            return "day"
        if _ENTITY.fullmatch(token) or token.startswith("cz_"):
            return "entity"
        if "." in token or "\\" in token or token in self.search_paths:
            return "path"
        if ":" in token or token in self.postings["actor"]:
            return "actor"
        if token in self.postings["family"]:
            return "family"
        return "experiment"

    def matching(self, token):
        kind = self.classify(token)
        if kind == "path":
            # Exact holder spelling: never resolve aliases or split escaped dots.
            return {eid for pid in self.search_paths.get(token, ())
                    for eid in self.path_postings.get(pid, ())}
        posting = self.postings[kind]
        if kind == "experiment":
            return {eid for name, ids in posting.items() if token in name for eid in ids}
        return set(posting.get(token, ()))

    def for_zone(self, zone: str) -> "LedgerIndex":
        """docs/282 review P2-3: the one RAM index of a ledger serves every
        zone. Only the day postings depend on the zone; a view shares every
        other array and map and adds that zone's days (built once per zone,
        from ``t``)."""
        views = self.__dict__.setdefault("_views", {})
        view = views.get(zone)
        if view is None:
            tz = ZoneInfo(zone)
            days: dict[str, set] = {}
            for eid, t in zip(self.eids, self.t):
                day = (_EPOCH + timedelta(microseconds=t)).astimezone(tz).date().isoformat()
                days.setdefault(day, set()).add(eid)
            postings = dict(self.postings)
            postings["day"] = {d: array("I", sorted(ids)) for d, ids in days.items()}
            view = LedgerIndex(self.ledger_id, zone, self.eids, self.t, self.kind, self.root,
                               self.run_id, self.experiment, self.flags, self.positions, self.names,
                               postings, self.paths, self.search_paths, self.path_postings, self.keys)
            views[zone] = view
        return view

    def search(self, text):
        result = None
        for group in search_query.groups(text):
            union = set()
            for token in group:
                union.update(self.matching(token))
            result = union if result is None else result.intersection(union)
            if not result:
                break
        return set(self.eids) if result is None else result


_EVENTS_SQL = ("SELECT e.*, COALESCE(r.folder_key,'') AS folder_key FROM events e "
               "LEFT JOIN roots r USING(root_id) ORDER BY e.ord")
#: what an already-indexed event must still be for an append-only update to
#: keep it (docs/282 review P2-3): its place, every field the index reads, and
#: the facts a re-diff of its rows changes (n_changes, base/state hash)
_SIG_COLS = ("ord", "t_utc_us", "kind", "root_id", "rel_path", "run_id", "experiment", "flags",
             "actor", "targets", "n_changes", "base_hash", "state_hash", "folder_key")


def _event_sig(row) -> tuple:
    return tuple(row[c] for c in _SIG_COLS)


def _path_tokens(path: str, family) -> tuple:
    # docs/282: only a segment starting with q or c can name an entity
    # (_ENTITY / "cz_"); skipping the rest is the same set, ~0.8 s less
    # on a 155k-path ledger
    return family, {entity for part in segments(path) if part[:1] in "qQcC"
                    for entity in _entities(part)}


def _add_event(index: LedgerIndex, row, add) -> None:
    def intern(kind, name):
        names = index.names[kind]
        return names.setdefault(name, len(names))
    eid = row["eid"]
    index.positions[eid] = len(index.eids)
    index.eids.append(eid)
    index.t.append(row["t_utc_us"])
    index.kind.append(intern("kind", row["kind"]))
    index.root.append(row["root_id"] if row["root_id"] is not None else -1)
    index.run_id.append(row["run_id"] if row["run_id"] is not None else -1)
    index.experiment.append(intern("experiment", row["experiment"] or ""))
    index.flags.append(row["flags"])
    index.keys[eid] = (row["t_utc_us"], row["folder_key"],
                       row["run_id"] if row["run_id"] is not None else -1,
                       row["experiment"] or "", row["rel_path"] or "", eid)
    add("kind", row["kind"], eid)
    add("experiment", row["experiment"] or "", eid)
    for token in re.split(r"[\s_]+", row["experiment"] or ""):
        add("experiment", token, eid)
    add("actor", row["actor"] or "", eid)
    for target in _strings(json.loads(row["targets"] or "null")):
        for entity in _entities(target):
            add("entity", entity, eid)


def build_index(conn, zone: str | None = None) -> LedgerIndex:
    """Build only from the caller's SQLite read snapshot; no file writes.
    *zone* None builds the zone-free base (no day postings); a zone returns
    that zone's view of it."""
    if zone is not None:
        return build_index(conn, None).for_zone(zone)
    ledger_id = conn.execute("SELECT v FROM meta WHERE k='ledger_id'").fetchone()[0]
    index = LedgerIndex(ledger_id, "")
    pending = {key: {} for key in index.postings}

    def add(kind, token, eid):
        if token:
            pending[kind].setdefault(token.lower(), set()).add(eid)

    sigs = {}
    for row in conn.execute(_EVENTS_SQL):
        _add_event(index, row, add)
        sigs[row["eid"]] = _event_sig(row)

    path_tokens = {}
    for row in conn.execute("SELECT pid,path,family FROM paths"):
        pid, path, family = row
        index.paths[path] = pid
        index.search_paths.setdefault(path.lower(), []).append(pid)
        index.path_postings[pid] = array("I")
        path_tokens[pid] = _path_tokens(path, family)
    for pid, eid in conn.execute("SELECT pid,eid FROM changes ORDER BY pid,eid"):
        index.path_postings[pid].append(eid)
        family, entities = path_tokens[pid]
        add("family", family, eid)
        for entity in entities:
            add("entity", entity, eid)
    index.postings = {kind: {token: array("I", sorted(ids)) for token, ids in table.items()}
                      for kind, table in pending.items()}
    index.__dict__["_sig"] = sigs
    return index


def extend_index(conn, prev: LedgerIndex | None) -> LedgerIndex:
    """docs/282 review P2-3: the index after a commit that only APPENDED
    events (a new run at the head, an SM write) -- ``prev`` extended in place
    with the new events, their paths and rows, instead of rebuilt (a full
    build is ~1.2 s on a 155k-path ledger). Anything else -- an event moved,
    re-diffed, re-flagged, removed, another ledger file -- is a full build.
    Safe in place: every reader of one chip holds that chip's read lock, so
    no other thread is inside ``prev`` while it changes."""
    if prev is None or prev.__dict__.get("_sig") is None:
        return build_index(conn)
    ledger_id = conn.execute("SELECT v FROM meta WHERE k='ledger_id'").fetchone()[0]
    if ledger_id != prev.ledger_id:
        return build_index(conn)
    rows = conn.execute(_EVENTS_SQL).fetchall()
    sigs = prev.__dict__["_sig"]
    n = len(prev.eids)
    if len(rows) < n:
        return build_index(conn)
    for i in range(n):
        if rows[i]["eid"] != prev.eids[i] or sigs.get(rows[i]["eid"]) != _event_sig(rows[i]):
            return build_index(conn)
    new = rows[n:]
    high = max(prev.eids, default=0)
    if any(r["eid"] <= high for r in new):
        return build_index(conn)
    pending = {key: {} for key in prev.postings}

    def add(kind, token, eid):
        if token:
            pending[kind].setdefault(token.lower(), set()).add(eid)

    for row in new:
        _add_event(prev, row, add)
        sigs[row["eid"]] = _event_sig(row)
    top_pid = max(prev.path_postings, default=0)
    new_paths = 0
    for pid, path, family in conn.execute("SELECT pid,path,family FROM paths WHERE pid>?", (top_pid,)):
        prev.paths[path] = pid
        prev.search_paths.setdefault(path.lower(), []).append(pid)
        prev.path_postings[pid] = array("I")
        new_paths += 1
    added = conn.execute("SELECT c.pid, c.eid, p.path, p.family FROM changes c JOIN paths p USING(pid) "
                         "WHERE c.eid>? ORDER BY c.pid, c.eid", (high,)).fetchall()
    tokens: dict = {}
    for pid, eid, path, family in added:
        prev.path_postings[pid].append(eid)
        if pid not in tokens:
            tokens[pid] = _path_tokens(path, family)
        fam, entities = tokens[pid]
        add("family", fam, eid)
        for entity in entities:
            add("entity", entity, eid)
    for kind, table in pending.items():
        target = prev.postings.setdefault(kind, {})
        for token, ids in table.items():
            arr = target.get(token)
            if arr is None:
                target[token] = array("I", sorted(ids))
            else:
                arr.extend(sorted(ids))     # new eids are all larger: still sorted
    prev.__dict__.pop("_views", None)       # day postings follow the new events
    if "_bytes" in prev.__dict__:
        prev.__dict__["_bytes"] += 160 * len(new) + 260 * new_paths + 16 * len(added)
    return prev


def _index_bytes(index: LedgerIndex) -> int:
    """Retained size: measured once per full build (``footprint``), then
    kept current by an estimate for each append (the full traversal costs
    ~0.7 s on a 155k-path ledger, more than the append itself)."""
    if "_bytes" not in index.__dict__:
        index.__dict__["_bytes"] = footprint(index)
    return index.__dict__["_bytes"]


INDEX_CACHE = KeyedMemo("hub_read_index", sizeof=_index_bytes)


_READER_GEN = itertools.count(1)


class _GenConnection(sqlite3.Connection):
    """A read connection that knows which opening it is (S8 review P1-1).

    ``PRAGMA data_version`` counts per CONNECTION: a freshly opened one starts
    over whatever the file holds, so after a reader is reopened (LRU eviction
    past eight chips, a replaced ledger file) the same number can describe
    different contents, and a cache keyed on it served the old answer. ``gen``
    is unique per opening in this process; a read's version carries both."""

    gen = 0


class _Reader:
    def __init__(self, directory):
        self.path = Path(directory) / "ledger.sqlite"
        self.identity = self.file_identity()
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                    check_same_thread=False, timeout=5, factory=_GenConnection)
        self.conn.gen = next(_READER_GEN)
        self.conn.row_factory = sqlite3.Row

    def file_identity(self):
        stat = self.path.stat()
        return stat.st_dev, stat.st_ino


_READERS = OrderedDict()
_READERS_LOCK = threading.RLock()
#: docs/282 review P2-3: how long a read waits for its chip's connection
#: (another read of THE SAME chip -- an index being built, say) before it
#: answers "preparing" (ramcache.Warming) instead of blocking the request
READ_WAIT_S = 0.25


#: docs/283: a chip folder's reader slot (its resolved, case-folded path),
#: resolved once per spelling -- ``Path.resolve`` was the largest single cost
#: of a warm read on Windows (two final-path system calls per read)
_SLOTS: dict = {}


def _slot(directory) -> str:
    key = str(directory)
    slot = _SLOTS.get(key)
    if slot is None:
        slot = os.path.normcase(str(Path(directory).resolve()))
        if len(_SLOTS) > 256:
            _SLOTS.clear()
        _SLOTS[key] = slot
    return slot


#: docs/298: called with a chip's reader slot when its reader is closed, so a
#: cache derived from that chip's ledger frees its memory with it (memory
#: only: every such cache validates what it serves on its own)
ON_CLOSE: list = []


def _close(reader, slot) -> None:
    with reader.lock:
        reader.conn.close()
    INDEX_CACHE.drop_where(lambda candidate: candidate == slot)
    for hook in list(ON_CLOSE):
        try:
            hook(slot)
        except Exception:  # noqa: BLE001 -- freeing memory never fails a close
            pass


def close_readers(directory=None):
    """Release read handles before deleting scratch ledgers or shutting down.
    A handle in use is closed once its reader is done; the global lock is
    never held while waiting for it (docs/282 review P2-3)."""
    key = _slot(directory) if directory is not None else None
    with _READERS_LOCK:
        gone = [(slot, _READERS.pop(slot)) for slot in list(_READERS)
                if key is None or slot == key]
    for slot, reader in gone:
        _close(reader, slot)


@contextmanager
def snapshot(store):
    """One serialized read snapshot and index. Never use the writer connection."""
    raw = store.store if isinstance(store, ReadContext) else store
    directory = raw.directory
    hub_sync.require_ready(directory)
    store, zone = _binding(store)
    slot = _slot(directory)
    stale = []
    with _READERS_LOCK:
        reader = _READERS.get(slot)
        if reader is not None and reader.identity != reader.file_identity():
            stale.append((slot, _READERS.pop(slot)))
            reader = None
        if reader is None:
            reader = _Reader(directory)
            _READERS[slot] = reader
        _READERS.move_to_end(slot)
        # Bound connection resources separately from the RAM-cache budget.
        while len(_READERS) > 8:
            oldest = next(iter(_READERS))
            stale.append((oldest, _READERS.pop(oldest)))
    # docs/282 review P2-3: closing and waiting happen OUTSIDE the global
    # lock -- a slow read of one chip never holds up a read of another
    for old_slot, old in stale:
        _close(old, old_slot)
    if not reader.lock.acquire(timeout=READ_WAIT_S):
        raise Warming("hub_read_lock", slot, READ_WAIT_S)
    try:
        conn = reader.conn
        version = conn.execute("PRAGMA data_version").fetchone()[0]
        conn.execute("BEGIN")
        try:
            ledger_id = conn.execute("SELECT v FROM meta WHERE k='ledger_id'").fetchone()[0]
            high = conn.execute("SELECT COALESCE(MAX(eid),0) FROM events").fetchone()[0]
            journal = Path(directory) / "events.jsonl"
            try:
                journal_size = journal.stat().st_size
            except FileNotFoundError:
                journal_size = 0
            # zone-free token: every zone shares the one base index
            token = (reader.identity, ledger_id, version, high, journal_size)
            index = INDEX_CACHE.get(slot, token, lambda prev: extend_index(conn, prev), wait_s=0,
                                    incremental=True).for_zone(zone)
            hub_sync.require_ready(directory)
            yield conn, index
        finally:
            conn.rollback()
        # A commit racing snapshot establishment can mix a cache token and
        # snapshot. Fail honestly, including when sync starts during the read.
        hub_sync.require_ready(directory)
        if conn.execute("PRAGMA data_version").fetchone()[0] != version:
            raise Warming("hub_read_snapshot", slot, 0)
    finally:
        reader.lock.release()


def prewarm(directory) -> bool:
    """Build the chip's read index now (docs/282 review P2-3), so the first
    reader after a ledger commit finds it ready. Off the request thread: the
    hub's projector calls it when a burst of commits is over. False when the
    ledger is still building, missing, or another read holds it."""
    from types import SimpleNamespace
    if not (Path(directory) / "ledger.sqlite").exists():
        return False
    try:
        with snapshot(SimpleNamespace(directory=directory)):
            return True
    except (Warming, sqlite3.Error, OSError):
        return False
