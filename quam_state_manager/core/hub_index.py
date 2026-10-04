"""Read-only, columnar ledger search index. See docs/279."""

from __future__ import annotations

from array import array
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
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


def build_index(conn, zone: str) -> LedgerIndex:
    """Build only from the caller's SQLite read snapshot; no file writes."""
    tz = ZoneInfo(zone)
    ledger_id = conn.execute("SELECT v FROM meta WHERE k='ledger_id'").fetchone()[0]
    index = LedgerIndex(ledger_id, zone)
    pending = {key: {} for key in index.postings}

    def add(kind, token, eid):
        if token:
            pending[kind].setdefault(token.lower(), set()).add(eid)

    def intern(kind, name):
        names = index.names[kind]
        return names.setdefault(name, len(names))

    rows = conn.execute(
        "SELECT e.*, COALESCE(r.folder_key,'') AS folder_key FROM events e "
        "LEFT JOIN roots r USING(root_id) ORDER BY e.ord")
    for row in rows:
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
        day = (_EPOCH + timedelta(microseconds=row["t_utc_us"])).astimezone(tz).date().isoformat()
        add("day", day, eid)
        for target in _strings(json.loads(row["targets"] or "null")):
            for entity in _entities(target):
                add("entity", entity, eid)

    path_tokens = {}
    for row in conn.execute("SELECT pid,path,family FROM paths"):
        pid, path, family = row
        index.paths[path] = pid
        index.search_paths.setdefault(path.lower(), []).append(pid)
        index.path_postings[pid] = array("I")
        path_tokens[pid] = (family, {entity for part in segments(path)
                                    for entity in _entities(part)})
    for pid, eid in conn.execute("SELECT pid,eid FROM changes ORDER BY pid,eid"):
        index.path_postings[pid].append(eid)
        family, entities = path_tokens[pid]
        add("family", family, eid)
        for entity in entities:
            add("entity", entity, eid)
    index.postings = {kind: {token: array("I", sorted(ids)) for token, ids in table.items()}
                      for kind, table in pending.items()}
    return index


INDEX_CACHE = KeyedMemo("hub_read_index", sizeof=footprint)


class _Reader:
    def __init__(self, directory):
        self.path = Path(directory) / "ledger.sqlite"
        self.identity = self.file_identity()
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True,
                                    check_same_thread=False, timeout=5)
        self.conn.row_factory = sqlite3.Row

    def file_identity(self):
        stat = self.path.stat()
        return stat.st_dev, stat.st_ino


_READERS = OrderedDict()
_READERS_LOCK = threading.RLock()


def close_readers(directory=None):
    """Release read handles before deleting scratch ledgers or shutting down."""
    key = os.path.normcase(str(Path(directory).resolve())) if directory is not None else None
    with _READERS_LOCK:
        for slot in list(_READERS):
            if key is None or slot == key:
                reader = _READERS.pop(slot)
                with reader.lock:
                    reader.conn.close()
                INDEX_CACHE.drop_where(lambda candidate: candidate == slot)


@contextmanager
def snapshot(store):
    """One serialized read snapshot and index. Never use the writer connection."""
    raw = store.store if isinstance(store, ReadContext) else store
    directory = raw.directory
    hub_sync.require_ready(directory)
    store, zone = _binding(store)
    slot = os.path.normcase(str(Path(directory).resolve()))
    with _READERS_LOCK:
        reader = _READERS.get(slot)
        if reader is not None and reader.identity != reader.file_identity():
            close_readers(directory)
            reader = None
        if reader is None:
            reader = _Reader(directory)
            _READERS[slot] = reader
        _READERS.move_to_end(slot)
        reader.lock.acquire()
        # Bound connection resources separately from the RAM-cache budget.
        while len(_READERS) > 8:
            oldest = next(iter(_READERS))
            close_readers(oldest)
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
            token = (reader.identity, ledger_id, version, high, journal_size, zone)
            index = INDEX_CACHE.get(slot, token, lambda: build_index(conn, zone), wait_s=0)
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
