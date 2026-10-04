"""Rebuildable offline saved-state ledger. See docs/270 for its S3 contract."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import sqlite3
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any

from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core.hub_rules import _hash_scalar, _segment

SCHEMA_VERSION = 1
RULE_VERSION = "269-1"
CHECKPOINT_INTERVAL = 250
REVERTS_TO_EARLIER = 1
TIME_ASSUMED = 2
CHIP_UNCERTAIN = 4
# S4 (docs/271): an SM event whose effect a later undo/redo took back, as of
# now -- all of its journal units (UNDONE) or some of them (PARTLY_UNDONE).
# Recomputed from the undo links whenever one is projected, so a redo that
# re-does the change clears it again.
UNDONE = 8
PARTLY_UNDONE = 16
# (32 = DERIVED, hub.py, docs/271.)
# S5 (docs/275), runs ingested inside SM:
#: run_start < an SM write's instant < the run's own instant: the run's save
#: (the whole machine, at its end) went over that SM write (DESIGN 2.11).
OVERLAPS_SM_WRITE = 64
#: the run folder's saved pair was rewritten after it was first ingested; the
#: event's rows are re-diffed against its CURRENT bytes, in place.
REWRITTEN = 128
#: the run folder (or its saved pair) is gone from disk; the event, its rows
#: and its replay stay -- the ledger needs no archive files (docs/270).
SOURCE_GONE = 256
#: node.json could not be read; the saved pair is still the run's state and
#: the instant comes from the folder clock.
NODE_UNREADABLE = 512
OPS = {"set": 0, "add": 1, "gone": 2, "retarget": 3}
#: docs/275: ranks closer than this are renumbered before an insertion
ORD_EPS = 1e-6
#: docs/275: flats of recently used events kept by one store (bounded RAM)
FLAT_CACHE = 3
#: the eid component of a key for an event not yet inserted
_NEW = 1 << 62
#: docs/275 review (P1-5): how far apart two clocks may be for the causal
#: floor to reorder an SM write and the run its base names (one hour)
CAUSAL_WINDOW_US = 3_600_000_000
#: SM-event kinds (docs/271). Run events keep kind="run".
SM_KINDS = ("sm_apply", "agent", "autofit", "restore", "undo", "redo")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS roots(
 root_id INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
 folder_key TEXT NOT NULL, offset_hint TEXT);
CREATE TABLE IF NOT EXISTS events(
 eid INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
 t_utc_us INTEGER NOT NULL, t_src TEXT, t_quality TEXT, ord REAL NOT NULL UNIQUE,
 root_id INTEGER REFERENCES roots, rel_path TEXT, run_id INTEGER, experiment TEXT,
 status TEXT, run_start_us INTEGER, run_end_us INTEGER, parents TEXT, targets TEXT,
 patches_n INTEGER, actor TEXT, plan_id TEXT, src TEXT,
 state_hash TEXT, base_hash TEXT, state_ref TEXT, n_changes INTEGER NOT NULL,
 flags INTEGER NOT NULL, shape_hash TEXT, error TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS run_identity
 ON events(t_utc_us, run_id, experiment, COALESCE(state_hash, '')) WHERE kind='run';
CREATE TABLE IF NOT EXISTS locations(
 eid INTEGER NOT NULL REFERENCES events, root_id INTEGER NOT NULL REFERENCES roots,
 rel_path TEXT NOT NULL, PRIMARY KEY(root_id, rel_path)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS locations_by_event ON locations(eid);
CREATE TABLE IF NOT EXISTS paths(
 pid INTEGER PRIMARY KEY, path TEXT NOT NULL UNIQUE,
 entity TEXT, entity_kind TEXT, family TEXT, src_file TEXT);
CREATE TABLE IF NOT EXISTS changes(
 pid INTEGER NOT NULL REFERENCES paths, eid INTEGER NOT NULL REFERENCES events,
 op INTEGER NOT NULL, num REAL, txt TEXT, old_num REAL, old_txt TEXT,
 proven INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(pid,eid)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS changes_by_event ON changes(eid,pid);
CREATE TABLE IF NOT EXISTS blobs(hash TEXT PRIMARY KEY, gz BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS checkpoints(eid INTEGER PRIMARY KEY REFERENCES events, hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sm_events(
 eid INTEGER PRIMARY KEY REFERENCES events, sm_id TEXT NOT NULL UNIQUE,
 outcome TEXT NOT NULL, base_chash TEXT, post_chash TEXT, run_uid TEXT,
 units TEXT, undoes TEXT, entries_n INTEGER NOT NULL DEFAULT 0, entries BLOB, live TEXT);
CREATE TABLE IF NOT EXISTS sm_anchors(eid INTEGER PRIMARY KEY REFERENCES events, hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_by_state_hash ON events(state_hash);
CREATE INDEX IF NOT EXISTS events_by_time ON events(t_utc_us, ord);
CREATE TABLE IF NOT EXISTS run_files(
 root_id INTEGER NOT NULL REFERENCES roots, rel_path TEXT NOT NULL, sig TEXT,
 rewritten_us INTEGER, PRIMARY KEY(root_id, rel_path)) WITHOUT ROWID;
"""
# S5 (docs/275), additive like S4's tables: ``events_by_time`` places an event
# by its instant; ``run_files`` is the stat watermark (size + mtime of the
# saved pair and node.json) of every ingested run folder, so a rewrite is
# found by a stat, never by re-reading every run.
#
# S5 review (docs/275 "Review round"), additive columns:
#   events.t_ord   the ORDER instant: t_utc_us, except where causality moves an
#                  event (an SM write after the run it was based on although
#                  that run's clock is ahead; journal order between SM writes)
#   events.chash   working_copy.content_hash of the event's (state, wiring) --
#                  the hash an SM write names as its base
#   sm_events.jpos the line's journal position (journal order = causal order)
_COLUMNS = (("events", "t_ord", "INTEGER"), ("events", "chash", "TEXT"), ("sm_events", "jpos", "INTEGER"))
_SCHEMA_S5R = """
CREATE INDEX IF NOT EXISTS events_by_order_time ON events(t_ord, ord);
CREATE INDEX IF NOT EXISTS events_by_chash ON events(chash);
CREATE TRIGGER IF NOT EXISTS events_default_t_ord AFTER INSERT ON events WHEN NEW.t_ord IS NULL
 BEGIN UPDATE events SET t_ord=NEW.t_utc_us WHERE eid=NEW.eid; END;
"""
_SCHEMA_OBJECTS = ("meta", "roots", "events", "run_identity", "locations", "locations_by_event", "paths",
                   "changes", "changes_by_event", "blobs", "checkpoints", "sm_events", "sm_anchors",
                   "events_by_state_hash", "events_by_time", "run_files", "events_by_order_time",
                   "events_by_chash", "events_default_t_ord")


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def segments(path: str) -> list[str]:
    """Decode the escaped holder path produced by S2 (not a pointer path)."""
    if path == "":
        return []
    if "\\" not in path:
        # docs/282: no escape anywhere, so every dot is a separator. The
        # character loop below cost ~2.8 s of a 3.1 s index build on a
        # 155k-path ledger; this is the same answer for the common spelling.
        return path.split(".")
    parts, buf, escaped = [], "", False
    for ch in path:
        if escaped:
            buf += "\\" + ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == ".":
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    decoded = []
    for part in parts:
        if part == "\\e":
            decoded.append("")
            continue
        out, i = "", 0
        while i < len(part):
            if part[i] == "\\":
                i += 1
            out += part[i]
            i += 1
        decoded.append(out)
    return decoded


def holder(doc: Any, path: str) -> Any:
    for part in segments(path):
        doc = doc[int(part)] if isinstance(doc, list) else doc[part]
    return doc


def _shape(doc: Any, flat: dict[str, Any], path: str = "") -> Any:
    # Leaf membership comes from S2, including its long-array boundary.
    if path in flat:
        return None
    if isinstance(doc, dict):
        return {k: _shape(v, flat, path + "." + _segment(k) if path else _segment(k))
                for k, v in doc.items()}
    if isinstance(doc, list):
        return [_shape(v, flat, path + "." + str(i) if path else str(i)) for i, v in enumerate(doc)]
    raise ValueError("shape contains an unrecorded scalar")


def _persist_number(num: Any, txt: str | None) -> tuple[Any, str | None]:
    # REAL loses NaN and integers beyond its exact range. Text is lossless.
    if num is not None and ((isinstance(num, float) and not math.isfinite(num))
                            or (isinstance(num, int) and abs(num) > 2**53)):
        return None, json_bytes(num).decode("utf-8")
    return num, txt


def value(num: Any, txt: str | None) -> Any:
    return json.loads(txt) if txt is not None else num


def _enable_wal(connection, *, timeout_s=30):
    """Allow another window to finish changing the ledger's journal mode."""
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError as exc:
            code = getattr(exc, "sqlite_errorcode", None)
            # Extended result codes retain BUSY/LOCKED in their low byte.
            busy = (code & 255) in (5, 6) if code is not None else str(exc) in (
                "database is locked", "database table is locked")
            if not busy or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)


class HubStore:
    """One SQLite projection, with atomic event/rows/checkpoint/watermark writes.

    ``state_at`` needs no archive files. Numeric type-only saves replay under
    S2.same; container shapes and all long-array payloads survive source loss.
    Offline S3 appends in canonical order; late insertion belongs to S5.
    """

    def __init__(self, directory: str | Path, *, checkpoint_interval: int | None = None,
                 check_same_thread: bool = True):
        if checkpoint_interval is not None and checkpoint_interval < 1:
            raise ValueError("checkpoint interval must be positive")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        # timeout: two SM windows on one chip project into the same file
        # (docs/271); WAL lets readers proceed, writers wait their turn.
        # check_same_thread=False: the hub's one writer connection per chip
        # (docs/275) is used under the chip's writer lock from whichever thread
        # holds it (the projector, or a test's inline caller).
        self.conn = sqlite3.connect(self.directory / "ledger.sqlite", timeout=30,
                                    check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        _enable_wal(self.conn)
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-16384")
        if not self._schema_current():
            # docs/275 review: only a ledger that lacks part of its schema
            # takes the write lock on open (a second window used to wait up
            # to 21 s for another window's long transaction just to open)
            for stmt in _SCHEMA.split(";"):
                if stmt.strip():
                    self.conn.execute(stmt)
            for table, col, typ in _COLUMNS:
                have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
                if col not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            self.conn.execute("UPDATE events SET t_ord=t_utc_us WHERE t_ord IS NULL")
            self.conn.commit()
            self.conn.executescript(_SCHEMA_S5R)
        if checkpoint_interval is None:
            # docs/275: a reader or the in-SM sync that names no interval
            # adopts the ledger's own (a new ledger gets the default)
            stored = self.meta("checkpoint_interval")
            checkpoint_interval = int(stored) if stored else CHECKPOINT_INTERVAL
        expected = {"schema_version": str(SCHEMA_VERSION), "rule_version": RULE_VERSION,
                    "checkpoint_interval": str(checkpoint_interval)}
        for key, val in expected.items():
            existing = self.meta(key)
            if existing is not None and existing != val:
                self.conn.close()
                raise ValueError(f"incompatible ledger {key}: {existing}; expected {val}")
            if existing is None:
                self.set_meta(key, val)
        if self.meta("ledger_id") is None:
            # docs/275: the identity of THIS file; RAM caches built from a
            # ledger are dropped when the file is replaced or rebuilt
            self.set_meta("ledger_id", os.urandom(8).hex())
        if self.conn.in_transaction:
            self.conn.commit()
        self.checkpoint_interval = checkpoint_interval
        self._pids = dict(self.conn.execute("SELECT path,pid FROM paths"))

    def _schema_current(self) -> bool:
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN (%s)" % ",".join("?" * len(_SCHEMA_OBJECTS)),
            _SCHEMA_OBJECTS)}
        if names != set(_SCHEMA_OBJECTS):
            return False
        for table, col, _typ in _COLUMNS:
            if col not in {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}:
                return False
        return True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def close(self):
        self.conn.close()

    def meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key: str, val: str):
        self.conn.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (key, val))

    def register_root(self, root: Path, offset_hint: str | None) -> int:
        normalized = os.path.normcase(str(root.resolve()))
        folder_key = hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:12]
        with self.conn:
            self.conn.execute("INSERT INTO roots(path,folder_key,offset_hint) VALUES(?,?,?) "
                              "ON CONFLICT(path) DO UPDATE SET offset_hint=excluded.offset_hint",
                              (normalized, folder_key, offset_hint))
        return self.conn.execute("SELECT root_id FROM roots WHERE path=?", (normalized,)).fetchone()[0]

    def put_blob(self, payload: bytes, *, expected_hash: str | None = None, level: int = 9) -> str:
        digest = hashlib.sha1(payload).hexdigest()
        if expected_hash is not None and digest != expected_hash:
            raise ValueError("array blob disagrees with S2 hash")
        if not self.conn.execute("SELECT 1 FROM blobs WHERE hash=?", (digest,)).fetchone():
            self.conn.execute("INSERT INTO blobs VALUES(?,?)",
                              (digest, gzip.compress(payload, compresslevel=level, mtime=0)))
        return digest

    def blob(self, digest: str) -> Any:
        row = self.conn.execute("SELECT gz FROM blobs WHERE hash=?", (digest,)).fetchone()
        if row is None:
            raise ValueError(f"missing ledger blob {digest}")
        payload = gzip.decompress(row[0])
        if hashlib.sha1(payload).hexdigest() != digest:
            raise ValueError(f"corrupt ledger blob {digest}")
        return json.loads(payload)

    def shape_and_arrays(self, doc: dict, flat: dict) -> str:
        for path, leaf in flat.items():
            if isinstance(leaf, dict):
                # docs/275: the S2 marker already names the content address;
                # an array the ledger holds is not encoded again (a 1.6 MB
                # state spent ~0.1 s per run re-encoding unchanged waveforms)
                if self.conn.execute("SELECT 1 FROM blobs WHERE hash=?", (leaf["_hash"],)).fetchone():
                    continue
                array = holder(doc, path)
                normalized = [_hash_scalar(v) for v in array]
                self.put_blob(json_bytes(normalized), expected_hash=leaf["_hash"])
        return self.put_blob(json_bytes(_shape(doc, flat)))

    def event(self, eid: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM events WHERE eid=?", (eid,)).fetchone()
        if row is None:
            raise KeyError(eid)
        return row

    def head(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM events ORDER BY ord DESC LIMIT 1").fetchone()

    def sm_pair(self, digest: str) -> dict:
        """The merged document of an SM event's post-state pair blob (docs/271):
        ``{"s": state, "w": wiring}`` exactly as SM wrote them, merged by S2."""
        pair = self.blob(digest)
        return rules.merged(pair["s"], pair["w"])

    def sm_entries(self, eid: int) -> list[dict]:
        row = self.conn.execute("SELECT entries, entries_n FROM sm_events WHERE eid=?", (eid,)).fetchone()
        if row is not None and row[0] is None and not row[1]:
            return []                       # a write that named no entries
        if row is None or row[0] is None:
            raise ValueError(f"SM event {eid} keeps no entries to replay")
        return json.loads(gzip.decompress(row[0]))

    def _sm_state_at(self, event: sqlite3.Row) -> dict:
        """An SM event's document (docs/271): its anchor blob, or -- when it
        has none -- the nearest earlier base (a run/checkpointed event, or an
        anchored SM event) with every un-anchored SM event since replayed as
        SM wrote it (``hub_entries.apply_entries``). Exact when each replayed
        event's base is its predecessor, which is when the projector leaves an
        event un-anchored."""
        from quam_state_manager.core import hub_entries

        chain = []
        cur = event
        while True:
            anchor = self.conn.execute("SELECT hash FROM sm_anchors WHERE eid=?", (cur["eid"],)).fetchone()
            if anchor is not None:
                doc = self.sm_pair(anchor[0])
                break
            chain.append(cur["eid"])
            prev = self.conn.execute("SELECT * FROM events WHERE ord<? AND error IS NULL "
                                     "ORDER BY ord DESC LIMIT 1", (cur["ord"],)).fetchone()
            # an un-anchored event replays from the write its base IS: when an
            # event was later inserted in between (a late 'landed' relabel),
            # step over it to the event whose post-state matches
            base = self.conn.execute("SELECT base_chash FROM sm_events WHERE eid=?", (cur["eid"],)).fetchone()
            if prev is not None and base is not None and base[0]:
                match = self.conn.execute(
                    "SELECT e.* FROM events e JOIN sm_events s USING(eid) WHERE e.ord<? AND e.error IS NULL "
                    "AND s.post_chash=? ORDER BY e.ord DESC LIMIT 1", (cur["ord"], base[0])).fetchone()
                if match is not None and match["ord"] != prev["ord"]:
                    newer_run = self.conn.execute(
                        "SELECT 1 FROM events WHERE ord>? AND ord<? AND kind NOT IN (%s) LIMIT 1"
                        % ",".join("?" * len(SM_KINDS)), (match["ord"], cur["ord"], *SM_KINDS)).fetchone()
                    if newer_run is None:
                        prev = match
            if prev is None:
                doc = {}
                break
            if prev["kind"] not in SM_KINDS:
                doc = self.state_at(prev["eid"])
                break
            cur = prev
        for eid in reversed(chain):
            doc = hub_entries.apply_entries(doc, self.sm_entries(eid))
        return doc

    def state_at(self, eid: int) -> dict:
        event = self.event(eid)
        if event["error"]:
            raise ValueError(f"run has no saved state: {event['error']}")
        if event["kind"] in SM_KINDS:
            return self._sm_state_at(event)
        # docs/271: an anchored SM event is a starting point exactly like a
        # checkpoint; replay across SM events uses their exact S2 rows.
        anchor = self.conn.execute("SELECT a.eid,a.hash,e.ord FROM sm_anchors a "
                                   "JOIN events e USING(eid) WHERE e.ord<=? ORDER BY e.ord DESC LIMIT 1",
                                   (event["ord"],)).fetchone()
        checkpoint = self.conn.execute("SELECT c.eid,c.hash,e.ord FROM checkpoints c "
                                       "JOIN events e USING(eid) WHERE e.ord<=? ORDER BY e.ord DESC LIMIT 1",
                                       (event["ord"],)).fetchone()
        if anchor is not None and (checkpoint is None or anchor["ord"] > checkpoint["ord"]):
            flat = rules.flatten(self.sm_pair(anchor["hash"]))
            start = anchor["ord"]
        else:
            flat = rules.flatten(self.blob(checkpoint["hash"])) if checkpoint else {}
            start = checkpoint["ord"] if checkpoint else 0
        rows = self.conn.execute("SELECT p.path,c.op,c.num,c.txt FROM changes c JOIN paths p USING(pid) "
                                 "JOIN events e USING(eid) WHERE e.ord>? AND e.ord<=? ORDER BY e.ord,c.pid",
                                 (start, event["ord"]))
        for row in rows:
            if row["op"] == OPS["gone"]:
                flat.pop(row["path"], None)
            else:
                flat[row["path"]] = value(row["num"], row["txt"])
        shape = self.blob(event["shape_hash"])

        def fill(node, path=""):
            if node is None:
                leaf = flat[path]
                return self.blob(leaf["_hash"]) if isinstance(leaf, dict) else leaf
            if isinstance(node, dict):
                return {k: fill(v, path + "." + _segment(k) if path else _segment(k)) for k, v in node.items()}
            return [fill(v, path + "." + str(i) if path else str(i)) for i, v in enumerate(node)]

        return fill(shape)

    def diff(self, eid_a: int, eid_b: int) -> list[rules.Change]:
        return rules.diff(rules.flatten(self.state_at(eid_a)), rules.flatten(self.state_at(eid_b)))

    def append(self, event: dict, rows: list[rules.Change], doc: dict, flat: dict,
               *, shape_hash: str | None = None, proven: set[str] | None = None) -> int:
        """Commit exactly one event; rollback also restores the in-memory path cache."""
        old_pids = self._pids.copy()
        try:
            with self.conn:
                event = dict(event)
                event["shape_hash"] = shape_hash or self.shape_and_arrays(doc, flat)
                event["n_changes"] = len(rows)
                columns = ",".join(event)
                eid = self.conn.execute(f"INSERT INTO events({columns}) VALUES({','.join('?' for _ in event)})",
                                        tuple(event.values())).lastrowid
                self.conn.execute("INSERT INTO locations VALUES(?,?,?)", (eid, event["root_id"], event["rel_path"]))
                for row in rows:
                    pid = self._pids.get(row.path)
                    if pid is None:
                        parts = segments(row.path)
                        entity = parts[1] if len(parts) > 1 and parts[0] in ("qubits", "qubit_pairs") else None
                        pid = self.conn.execute("INSERT INTO paths(path,entity,entity_kind,family) VALUES(?,?,?,?)",
                                                (row.path, entity, parts[0] if entity else None,
                                                 parts[-1] if parts else "")).lastrowid
                        self._pids[row.path] = pid
                    num, txt = _persist_number(row.num, row.txt)
                    old_num, old_txt = _persist_number(row.old_num, row.old_txt)
                    self.conn.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?,?)",
                                      (pid, eid, OPS[row.op], num, txt, old_num, old_txt,
                                       int(row.path in (proven or set()))))
                if int(event["ord"]) % self.checkpoint_interval == 0:
                    digest = self.put_blob(json_bytes(doc))
                    self.conn.execute("INSERT INTO checkpoints VALUES(?,?)", (eid, digest))
                self.set_meta(f"watermark:{event['root_id']}", json_bytes(
                    {"eid": eid, "t_utc_us": event["t_utc_us"], "rel_path": event["rel_path"]}).decode("utf-8"))
                return eid
        except BaseException:
            self._pids = old_pids
            raise

    # ------------------------------------------------------------------
    # SM events (S4, docs/271)
    # ------------------------------------------------------------------

    def append_sm(self, *, line: dict, outcome: str, rows: list[rules.Change], flags: int,
                  state_hash: str | None, error: str | None, pair_payload,
                  entries_gz: bytes | None, journal_end: int | None, anchor_every: int,
                  keep_entries_bytes: int, replace: bool = False, jpos: int | None = None,
                  derive=None) -> int | None:
        """Project one journal line (docs/271) in ONE transaction, together with
        the journal offset it advances to. Idempotent per ``sm_id``: a line
        another SM window already projected is skipped. ``BEGIN IMMEDIATE``
        serialises two windows' projectors on the order rank.

        An SM event is an *anchor* (its post-state pair blob is kept) when its
        base is not the post-state of the event before it, when its entries
        are too large to keep for replay, or every ``anchor_every`` SM events;
        otherwise ``state_at`` replays it from its predecessor with its own
        entries, which is exact because the base IS the predecessor.

        ``replace`` (a late ``landed`` line for a write projected as failed or
        unconfirmed): the event is projected again IN ITS PLACE -- same order
        rank -- and the old projection removed, in the same transaction.
        ``pair_payload`` may be a callable (the bytes are built only when the
        event becomes an anchor)."""
        old_pids = self._pids.copy()
        if self.conn.in_transaction:
            self.conn.commit()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                old = self.conn.execute("SELECT e.eid, e.ord FROM sm_events s JOIN events e USING(eid) "
                                        "WHERE s.sm_id=?", (line["id"],)).fetchone()
                if old is not None and not replace:
                    if journal_end is not None:
                        self.set_meta("journal_offset", str(journal_end))
                    self.conn.execute("COMMIT")
                    return None
                if old is not None:
                    # S4 relabel: the old projection is a failed/unconfirmed
                    # event (no rows, no state, nothing replays from it); it is
                    # removed and the line takes its place by instant again
                    if jpos is None:
                        got = self.conn.execute("SELECT jpos FROM sm_events WHERE eid=?", (old["eid"],)).fetchone()
                        jpos = got[0] if got else None
                    for table in ("changes", "sm_anchors", "sm_events", "checkpoints"):
                        self.conn.execute(f"DELETE FROM {table} WHERE eid=?", (old["eid"],))
                    self.conn.execute("DELETE FROM events WHERE eid=?", (old["eid"],))
                landed_ok = outcome == "landed" and error is None
                # docs/275: an SM event takes its place by its own instant,
                # not by projection order (two windows, a projector behind a
                # run the sync already ingested)
                t_us = int(line["t_utc_us"])
                # docs/275 review (P1-5): never before the events it depends on
                # -- SM writes earlier in the journal, and the event its base
                # names -- whatever the wall clocks say
                t_ord = self.causal_t_ord(line, t_us, jpos)
                lo, hi = self.neighbors((t_ord, "", 0, "", "", _NEW))
                prev = self.good_at_or_before(lo)
                succ = self.good_at_or_after(hi)
                if landed_ok and derive is not None:
                    # P1-4: a write with no bytes is its PLACED predecessor +
                    # its entries, computed here, inside this transaction --
                    # another window cannot change the predecessor meanwhile
                    try:
                        rows = derive(self.state_at(prev["eid"]) if prev is not None else {})
                    except Exception as exc:  # noqa: BLE001
                        error = f"post-state unavailable: {type(exc).__name__}: {exc}"
                        landed_ok, rows = False, []
                anchor_hash = None
                keep_entries = (entries_gz if entries_gz is not None and len(entries_gz) <= keep_entries_bytes
                                else None)
                if landed_ok and pair_payload is not None:
                    prev_post = None
                    if prev is not None and prev["kind"] in SM_KINDS:
                        prev_post = self.conn.execute("SELECT post_chash FROM sm_events WHERE eid=?",
                                                      (prev["eid"],)).fetchone()
                    chained = (prev_post is not None and prev_post[0] is not None
                               and prev_post[0] == line.get("base_hash"))
                    bound = prev["ord"] if prev is not None else 0
                    since = self.conn.execute(
                        "SELECT COUNT(*) FROM events e WHERE e.kind IN (%s) AND e.error IS NULL AND e.ord<=? AND e.ord > "
                        "COALESCE((SELECT MAX(e2.ord) FROM sm_anchors a JOIN events e2 USING(eid) WHERE e2.ord<=?), 0)"
                        % ",".join("?" * len(SM_KINDS)), (*SM_KINDS, bound, bound)).fetchone()[0]
                    if replace or not chained or keep_entries is None or since + 1 >= anchor_every:
                        payload = pair_payload() if callable(pair_payload) else pair_payload
                        anchor_hash = self.put_blob(payload, level=1)
                if landed_ok and anchor_hash is None and keep_entries is None and entries_gz is not None:
                    # replay needs the entries and there are no bytes to anchor
                    # on: keep them whatever their size
                    keep_entries = entries_gz
                if state_hash is not None and landed_ok:
                    if ((prev is None or prev["state_hash"] != state_hash) and self.conn.execute(
                            "SELECT 1 FROM events WHERE state_hash=? AND error IS NULL AND ord<? LIMIT 1",
                            (state_hash, hi["ord"] if hi is not None else float("inf"))).fetchone()):
                        flags |= REVERTS_TO_EARLIER
                succ_flat = None
                if landed_ok and succ is not None:
                    # the successor's own state, BEFORE this event changes what
                    # it replays from (docs/275 local repair)
                    if succ["kind"] in SM_KINDS:
                        self.ensure_anchor(succ)
                    else:
                        succ_flat = self.flat_of(succ)
                ord_ = self.alloc_ord(lo, hi)
                event = dict(
                    kind=line.get("kind") or "sm_apply", t_utc_us=int(line["t_utc_us"]), t_ord=t_ord,
                    chash=line.get("post_hash") if landed_ok else None, t_src=line.get("t"),
                    t_quality="sm_clock", ord=ord_, root_id=None, rel_path=None, run_id=None,
                    experiment=None, status=outcome, actor=line.get("actor"), plan_id=line.get("plan_id"),
                    src=line.get("src"), state_hash=state_hash if landed_ok else None, base_hash=None,
                    state_ref=(f"pair:{anchor_hash}" if anchor_hash else ("replay" if landed_ok else None)),
                    n_changes=len(rows) if landed_ok else 0, flags=flags, shape_hash=None, error=error)
                columns = ",".join(event)
                eid = self.conn.execute(f"INSERT INTO events({columns}) VALUES({','.join('?' for _ in event)})",
                                        tuple(event.values())).lastrowid
                if landed_ok:
                    for row in rows:
                        pid = self._pid(row.path)
                        num, txt = _persist_number(row.num, row.txt)
                        old_num, old_txt = _persist_number(row.old_num, row.old_txt)
                        self.conn.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?,0)",
                                          (pid, eid, OPS[row.op], num, txt, old_num, old_txt))
                    if anchor_hash:
                        self.conn.execute("INSERT INTO sm_anchors VALUES(?,?)", (eid, anchor_hash))
                self.conn.execute(
                    "INSERT INTO sm_events(eid,sm_id,outcome,base_chash,post_chash,run_uid,units,undoes,"
                    "entries_n,entries,live,jpos) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, line["id"], outcome, line.get("base_hash"), line.get("post_hash"), line.get("run_uid"),
                     json_bytes(line.get("units") or []).decode("utf-8"),
                     json_bytes(line["undoes"]).decode("utf-8") if line.get("undoes") else None,
                     int(line.get("n") or 0), keep_entries if landed_ok else None, line.get("live"), jpos))
                if landed_ok:
                    self.after_sm_placed(eid, ord_, t_us, succ, succ_flat, state_hash)
                if journal_end is not None:
                    self.set_meta("journal_offset", str(journal_end))
                self.conn.execute("COMMIT")
                return eid
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        except BaseException:
            self._pids = old_pids
            raise

    # ------------------------------------------------------------------
    # S5 (docs/275): every event takes its place by its instant; an
    # out-of-order event is inserted and only its neighbours are repaired
    # ------------------------------------------------------------------
    #
    # Invariants (pinned in tests/test_hub_sync.py):
    #  I1  ``ord`` order == canonical order ``(t_utc_us, root folder_key,
    #      run_id, experiment, rel_path, eid)``; an SM event has an empty root
    #      key, so it sorts before a run of the same microsecond.
    #  I2  a run's rows = S2 diff(state_at(previous good event), its saved
    #      state); an SM event's rows are what SM wrote and never change.
    #  I3  an SM event with no anchor replays from its predecessor: whenever
    #      that predecessor would change, its exact state is anchored FIRST.
    #  I4  a checkpoint holds the state of its event (an error event's: the
    #      previous good event's), so state_at never needs an archive file.

    def folder_key_of(self, root_id) -> str:
        if root_id is None:
            return ""
        cache = self.__dict__.setdefault("_fkeys", {})
        key = cache.get(root_id)
        if key is None:
            row = self.conn.execute("SELECT folder_key FROM roots WHERE root_id=?", (root_id,)).fetchone()
            key = cache[root_id] = row[0] if row else ""
        return key

    def order_key(self, row) -> tuple:
        t = row["t_ord"] if row["t_ord"] is not None else row["t_utc_us"]
        return (t, self.folder_key_of(row["root_id"]), row["run_id"] or 0,
                row["experiment"] or "", row["rel_path"] or "", row["eid"])

    def neighbors(self, key: tuple):
        """The events immediately before and after *key* in canonical order
        (I1), any kind, errors included."""
        t = key[0]
        lo = self.conn.execute("SELECT * FROM events WHERE t_ord<? ORDER BY t_ord DESC, ord DESC LIMIT 1",
                               (t,)).fetchone()
        hi = self.conn.execute("SELECT * FROM events WHERE t_ord>? ORDER BY t_ord, ord LIMIT 1",
                               (t,)).fetchone()
        for row in self.conn.execute("SELECT * FROM events WHERE t_ord=?", (t,)).fetchall():
            k = self.order_key(row)
            if k < key:
                if lo is None or row["ord"] > lo["ord"]:
                    lo = row
            elif k > key and (hi is None or row["ord"] < hi["ord"]):
                hi = row
        return lo, hi

    def good_at_or_before(self, row):
        if row is None or row["error"] is None:
            return row
        return self.conn.execute("SELECT * FROM events WHERE ord<? AND error IS NULL ORDER BY ord DESC LIMIT 1",
                                 (row["ord"],)).fetchone()

    def good_at_or_after(self, row):
        if row is None or row["error"] is None:
            return row
        return self.conn.execute("SELECT * FROM events WHERE ord>? AND error IS NULL ORDER BY ord LIMIT 1",
                                 (row["ord"],)).fetchone()

    def alloc_ord(self, lo, hi) -> float:
        """A fresh rank strictly between *lo* and *hi*. Appends keep S3's
        integral ranks; an insertion takes the midpoint; when floating point
        runs out of room the whole ledger is renumbered (order preserved)."""
        for _ in range(2):
            a = lo["ord"] if lo is not None else 0.0
            if hi is None:
                return float(math.floor(a)) + 1.0
            b = hi["ord"]
            mid = (a + b) / 2.0
            if a < mid < b and b - a > ORD_EPS:
                return mid
            self.renumber()
            lo = self.event(lo["eid"]) if lo is not None else None
            hi = self.event(hi["eid"])
        raise RuntimeError("could not allocate an order rank")

    def renumber(self) -> None:
        """Ranks 1..N in the current order. Two steps, so the UNIQUE rank is
        never violated mid-update (SQLite checks it row by row). A run being
        re-placed holds a negative rank (``-eid``, hub_sync's ``_detach``): it
        is out of the order and keeps that rank, and the first step moves
        every other rank below all of those (docs/275 review round)."""
        eids = [r[0] for r in self.conn.execute("SELECT eid FROM events WHERE ord > 0 ORDER BY ord")]
        below = self.conn.execute("SELECT COALESCE(MAX(eid), 0) FROM events").fetchone()[0]
        self.conn.execute("UPDATE events SET ord = -ord - 2 - ? WHERE ord > 0", (below,))
        self.conn.executemany("UPDATE events SET ord=? WHERE eid=?",
                              [(float(i + 1), eid) for i, eid in enumerate(eids)])

    def flat_of(self, row) -> dict:
        """S2 flat of an event's exact state (small LRU: a cold build reuses
        its head, an interleaved merge its two neighbours)."""
        key = (row["eid"], row["state_hash"], row["shape_hash"], row["error"])
        flats = self.__dict__.setdefault("_flats", OrderedDict())
        flat = flats.get(key)
        if flat is None:
            flat = rules.flatten(self.state_at(row["eid"]))
            self.remember_flat(row, flat)
        else:
            flats.move_to_end(key)
        return flat

    def remember_flat(self, row, flat: dict) -> None:
        flats = self.__dict__.setdefault("_flats", OrderedDict())
        flats[(row["eid"], row["state_hash"], row["shape_hash"], row["error"])] = flat
        while len(flats) > FLAT_CACHE:
            flats.popitem(last=False)

    def ensure_anchor(self, row) -> None:
        """I3: an un-anchored SM event replays from its predecessor. Before
        that predecessor changes, keep its exact state as an anchor (the SM
        event is a fact; nothing inserted before it may change it)."""
        if row is None or row["kind"] not in SM_KINDS or row["error"] is not None:
            return
        if self.conn.execute("SELECT 1 FROM sm_anchors WHERE eid=?", (row["eid"],)).fetchone():
            return
        doc = self.state_at(row["eid"])
        digest = self.put_blob(b'{"s":' + json_bytes(doc) + b',"w":{}}', level=1)
        self.conn.execute("INSERT INTO sm_anchors VALUES(?,?)", (row["eid"], digest))
        self.conn.execute("UPDATE events SET state_ref=? WHERE eid=?", (f"anchored:{digest}", row["eid"]))

    def write_rows(self, eid: int, rows: list[rules.Change], proven: set[str] | None = None) -> None:
        for row in rows:
            pid = self._pid(row.path)
            num, txt = _persist_number(row.num, row.txt)
            old_num, old_txt = _persist_number(row.old_num, row.old_txt)
            self.conn.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?,?)",
                              (pid, eid, OPS[row.op], num, txt, old_num, old_txt,
                               int(row.path in (proven or set()))))

    def rediff_run(self, row, base_flat: dict, base_hash: str | None, own_flat: dict,
                   proven: set[str] | None = None) -> None:
        """I2 for one run whose predecessor changed: its rows become the diff
        against the new predecessor. Its own state does not change."""
        rows = rules.diff(base_flat, own_flat)
        if proven is None:
            # carry `proven` for a row whose path and new value are unchanged;
            # a new row's proof needs the run's patches (the caller passes them)
            old = {r["path"]: (r["num"], r["txt"], r["proven"]) for r in self.conn.execute(
                "SELECT p.path,c.num,c.txt,c.proven FROM changes c JOIN paths p USING(pid) WHERE c.eid=?",
                (row["eid"],))}
            proven = set()
            for r in rows:
                o = old.get(r.path)
                num, txt = _persist_number(r.num, r.txt)
                if o is not None and o[2] and (o[0], o[1]) == (num, txt):
                    proven.add(r.path)
        self.conn.execute("DELETE FROM changes WHERE eid=?", (row["eid"],))
        self.write_rows(row["eid"], rows, proven)
        self.conn.execute("UPDATE events SET base_hash=?, n_changes=? WHERE eid=?",
                          (base_hash, len(rows), row["eid"]))

    def refresh_error_bases(self, lo_ord: float, hi_ord: float | None, base_hash: str | None) -> None:
        """An error event's base_hash is the state before it (S3): after an
        insertion or removal the error events between two good events name
        the new predecessor (docs/275 review, P3)."""
        self.conn.execute("UPDATE events SET base_hash=? WHERE error IS NOT NULL AND kind='run' AND ord>? AND ord<?",
                          (base_hash, lo_ord, hi_ord if hi_ord is not None else float("inf")))

    def refresh_error_checkpoints(self, lo_ord: float, hi_ord: float | None, doc_fn) -> None:
        """I4 for error events between two good events: their checkpoint is
        the earlier good event's state."""
        rows = self.conn.execute(
            "SELECT c.eid FROM checkpoints c JOIN events e USING(eid) WHERE e.error IS NOT NULL "
            "AND e.ord>? AND e.ord<?", (lo_ord, hi_ord if hi_ord is not None else float("inf"))).fetchall()
        if rows:
            digest = self.put_blob(json_bytes(doc_fn()))
            self.conn.executemany("UPDATE checkpoints SET hash=? WHERE eid=?", [(digest, r[0]) for r in rows])

    def maybe_checkpoint(self, eid: int, ord_: float, doc_fn) -> bool:
        """Bound replay: a checkpoint at *eid* when the stretch between the
        start points (checkpoints, SM anchors) around it has reached the
        interval."""
        before = self.conn.execute(
            "SELECT MAX(o) FROM (SELECT e.ord o FROM checkpoints c JOIN events e USING(eid) WHERE e.ord<? "
            "UNION ALL SELECT e.ord FROM sm_anchors a JOIN events e USING(eid) WHERE e.ord<?)",
            (ord_, ord_)).fetchone()[0]
        after = self.conn.execute(
            "SELECT MIN(o) FROM (SELECT e.ord o FROM checkpoints c JOIN events e USING(eid) WHERE e.ord>? "
            "UNION ALL SELECT e.ord FROM sm_anchors a JOIN events e USING(eid) WHERE e.ord>?)",
            (ord_, ord_)).fetchone()[0]
        span = self.conn.execute("SELECT COUNT(*) FROM events WHERE ord>? AND ord<?",
                                 (before if before is not None else 0.0,
                                  after if after is not None else float("inf"))).fetchone()[0]
        if span < self.checkpoint_interval:
            return False
        digest = self.put_blob(json_bytes(doc_fn()))
        self.conn.execute("INSERT OR REPLACE INTO checkpoints VALUES(?,?)", (eid, digest))
        return True

    def refresh_reverts(self, eids) -> None:
        """REVERTS_TO_EARLIER as a fact of the CURRENT order: the state equals
        an earlier non-adjacent one."""
        for eid in dict.fromkeys(e for e in eids if e is not None):
            row = self.conn.execute("SELECT eid,ord,flags,error,state_hash FROM events WHERE eid=?",
                                    (eid,)).fetchone()
            if row is None:
                continue
            want = 0
            if row["error"] is None and row["state_hash"] is not None:
                prev = self.conn.execute("SELECT state_hash FROM events WHERE ord<? AND error IS NULL "
                                         "ORDER BY ord DESC LIMIT 1", (row["ord"],)).fetchone()
                if (prev is None or prev[0] != row["state_hash"]) and self.conn.execute(
                        "SELECT 1 FROM events WHERE state_hash=? AND error IS NULL AND ord<? LIMIT 1",
                        (row["state_hash"], row["ord"])).fetchone():
                    want = REVERTS_TO_EARLIER
            flags = (row["flags"] & ~REVERTS_TO_EARLIER) | want
            if flags != row["flags"]:
                self.conn.execute("UPDATE events SET flags=? WHERE eid=?", (flags, eid))

    def same_hash_after(self, digest: str | None, ord_: float) -> list[int]:
        if digest is None:
            return []
        return [r[0] for r in self.conn.execute(
            "SELECT eid FROM events WHERE state_hash=? AND ord>? AND error IS NULL", (digest, ord_))]

    def overlaps_sm_write(self, run_start_us, t_us) -> bool:
        if run_start_us is None:
            return False
        return self.conn.execute(
            "SELECT 1 FROM events WHERE kind IN (%s) AND error IS NULL AND t_utc_us>? AND t_utc_us<? LIMIT 1"
            % ",".join("?" * len(SM_KINDS)), (*SM_KINDS, run_start_us, t_us)).fetchone() is not None

    def causal_t_ord(self, line: dict, t_us: int, jpos: int | None) -> int:
        """The order instant of an SM write (docs/275 review, P1-5): its own
        clock, but never before (a) an SM write earlier in the journal (two
        windows, a clock stepped back) or (b) the event whose content its base
        names when that event is placed later than this clock says (a run on a
        PC whose clock is ahead). An event with that content already placed at
        or before this instant explains the base; then (b) moves nothing."""
        floor = None
        if jpos is not None:
            row = self.conn.execute(
                "SELECT MAX(e.t_ord) FROM sm_events s JOIN events e USING(eid) WHERE s.jpos<?",
                (jpos,)).fetchone()
            floor = row[0]
        base = line.get("base_hash")
        if base and not self.conn.execute("SELECT 1 FROM events WHERE chash=? AND t_ord<=? LIMIT 1",
                                          (base, t_us)).fetchone():
            row = self.conn.execute("SELECT MIN(t_ord) FROM events WHERE chash=? AND t_ord>? AND t_ord<=?",
                                    (base, t_us, t_us + CAUSAL_WINDOW_US)).fetchone()
            if row[0] is not None:
                floor = row[0] if floor is None else max(floor, row[0])
        return t_us if floor is None or floor < t_us else int(floor) + 1

    def causal_ceiling(self, chash: str | None, t_us: int) -> int | None:
        """The order instant a run must stay below (P1-5): an SM write placed
        before this run's clock whose base IS this run's content, unless an
        earlier event with that content already explains it."""
        if not chash:
            return None
        row = self.conn.execute(
            "SELECT MIN(e.t_ord) FROM sm_events s JOIN events e USING(eid) WHERE s.base_chash=? "
            "AND e.t_ord<? AND e.t_ord>=?", (chash, t_us, t_us - CAUSAL_WINDOW_US)).fetchone()
        if row[0] is None:
            return None
        if self.conn.execute("SELECT 1 FROM events WHERE chash=? AND t_ord<? LIMIT 1", (chash, row[0])).fetchone():
            return None
        return int(row[0])

    def after_sm_placed(self, eid: int, ord_: float, t_us: int, succ, succ_flat, state_hash) -> None:
        """The local repair after a landed SM event took its place: a run
        after it is re-diffed against it (I2), error checkpoints between them
        hold its state (I4), runs it overlaps are flagged, revert facts of
        the successor and of later equal states are refreshed."""
        if succ is not None:
            succ = self.event(succ["eid"])         # alloc_ord may have renumbered
        if succ is not None and succ_flat is not None:
            own = rules.flatten(self.state_at(eid))
            self.rediff_run(succ, own, state_hash, succ_flat)
        hi_ord = succ["ord"] if succ is not None else None
        self.refresh_error_checkpoints(ord_, hi_ord, lambda: self.state_at(eid))
        self.refresh_error_bases(ord_, hi_ord, state_hash)
        self.conn.execute(
            "UPDATE events SET flags = flags | ? WHERE kind='run' AND run_start_us IS NOT NULL "
            "AND run_start_us<? AND t_utc_us>? AND (flags & ?)=0",
            (OVERLAPS_SM_WRITE, t_us, t_us, OVERLAPS_SM_WRITE))
        self.refresh_reverts([succ["eid"] if succ is not None else None, *self.same_hash_after(state_hash, ord_)])

    def _pid(self, path: str) -> int:
        pid = self._pids.get(path)
        if pid is None:
            # docs/275: a long-lived connection's cache can miss a path another
            # SM window added; ask the file before inserting
            row = self.conn.execute("SELECT pid FROM paths WHERE path=?", (path,)).fetchone()
            if row is not None:
                self._pids[path] = row[0]
                return row[0]
        if pid is None:
            parts = segments(path)
            entity = parts[1] if len(parts) > 1 and parts[0] in ("qubits", "qubit_pairs") else None
            pid = self.conn.execute("INSERT INTO paths(path,entity,entity_kind,family) VALUES(?,?,?,?)",
                                    (path, entity, parts[0] if entity else None,
                                     parts[-1] if parts else "")).lastrowid
            self._pids[path] = pid
        return pid

    def recompute_undo_flags(self) -> int:
        """UNDONE / PARTLY_UNDONE on every SM event, as of now (docs/271).

        An undo or redo event names what it takes back: ``undoes = [{"event":
        sm_id, "units": [journal unit ids] | null}]`` (null = the whole
        event). Walking newest -> oldest, an event's own status is final once
        every newer event was seen (only a newer event can take it back); an
        event that is itself UNDONE takes nothing back -- so a redo (which
        undoes the undo) puts the original back in effect. Returns how many
        events changed flags."""
        rows = self.conn.execute(
            "SELECT e.eid, e.flags, s.sm_id, s.units, s.undoes FROM sm_events s JOIN events e USING(eid) "
            "WHERE s.outcome='landed' ORDER BY e.ord DESC").fetchall()
        covered: dict[str, set] = {}
        whole: set[str] = set()
        changed = 0
        with self.conn:
            for r in rows:
                units = set(json.loads(r["units"] or "[]"))
                got = covered.get(r["sm_id"], set())
                if r["sm_id"] in whole or (units and units <= got):
                    st = UNDONE
                elif got:
                    st = PARTLY_UNDONE
                else:
                    st = 0
                new_flags = (r["flags"] & ~(UNDONE | PARTLY_UNDONE)) | st
                if new_flags != r["flags"]:
                    self.conn.execute("UPDATE events SET flags=? WHERE eid=?", (new_flags, r["eid"]))
                    changed += 1
                if st == UNDONE or not r["undoes"]:
                    continue
                for u in json.loads(r["undoes"]):
                    if u.get("units") is None:
                        whole.add(u["event"])
                    else:
                        covered.setdefault(u["event"], set()).update(u["units"])
        return changed
