"""Rebuildable offline saved-state ledger. See docs/270 for its S3 contract."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import sqlite3
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
OPS = {"set": 0, "add": 1, "gone": 2, "retarget": 3}
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
"""


def json_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def segments(path: str) -> list[str]:
    """Decode the escaped holder path produced by S2 (not a pointer path)."""
    if path == "":
        return []
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


class HubStore:
    """One SQLite projection, with atomic event/rows/checkpoint/watermark writes.

    ``state_at`` needs no archive files. Numeric type-only saves replay under
    S2.same; container shapes and all long-array payloads survive source loss.
    Offline S3 appends in canonical order; late insertion belongs to S5.
    """

    def __init__(self, directory: str | Path, *, checkpoint_interval: int = CHECKPOINT_INTERVAL):
        if checkpoint_interval < 1:
            raise ValueError("checkpoint interval must be positive")
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        # timeout: two SM windows on one chip project into the same file
        # (docs/271); WAL lets readers proceed, writers wait their turn.
        self.conn = sqlite3.connect(self.directory / "ledger.sqlite", timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("PRAGMA cache_size=-16384")
        for stmt in _SCHEMA.split(";"):
            if stmt.strip():
                self.conn.execute(stmt)
        expected = {"schema_version": str(SCHEMA_VERSION), "rule_version": RULE_VERSION,
                    "checkpoint_interval": str(checkpoint_interval)}
        for key, val in expected.items():
            existing = self.meta(key)
            if existing is not None and existing != val:
                self.conn.close()
                raise ValueError(f"incompatible ledger {key}: {existing}; expected {val}")
            self.set_meta(key, val)
        self.conn.commit()
        self.checkpoint_interval = checkpoint_interval
        self._pids = dict(self.conn.execute("SELECT path,pid FROM paths"))

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
                  state_hash: str | None, error: str | None, pair_payload: bytes | None,
                  entries_gz: bytes | None, journal_end: int, anchor_every: int,
                  keep_entries_bytes: int) -> int | None:
        """Project one journal line (docs/271) in ONE transaction, together with
        the journal offset it advances to. Idempotent per ``sm_id``: a line
        another SM window already projected is skipped. ``BEGIN IMMEDIATE``
        serialises two windows' projectors on the order rank.

        An SM event is an *anchor* (its post-state pair blob is kept) when its
        base is not the post-state of the event before it, when its entries
        are too large to keep for replay, or every ``anchor_every`` SM events;
        otherwise ``state_at`` replays it from its predecessor with its own
        entries, which is exact because the base IS the predecessor."""
        old_pids = self._pids.copy()
        if self.conn.in_transaction:
            self.conn.commit()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                if self.conn.execute("SELECT 1 FROM sm_events WHERE sm_id=?", (line["id"],)).fetchone():
                    self.set_meta("journal_offset", str(journal_end))
                    self.conn.execute("COMMIT")
                    return None
                ord_ = (self.conn.execute("SELECT COALESCE(MAX(ord),0) FROM events").fetchone()[0] or 0) + 1
                landed_ok = outcome == "landed" and error is None
                anchor_hash = None
                keep_entries = (entries_gz if entries_gz is not None and len(entries_gz) <= keep_entries_bytes
                                else None)
                if landed_ok and pair_payload is not None:
                    prev = self.conn.execute(
                        "SELECT e.kind, s.post_chash FROM events e LEFT JOIN sm_events s USING(eid) "
                        "WHERE e.error IS NULL ORDER BY e.ord DESC LIMIT 1").fetchone()
                    chained = (prev is not None and prev["kind"] in SM_KINDS
                               and prev["post_chash"] is not None
                               and prev["post_chash"] == line.get("base_hash"))
                    since = self.conn.execute(
                        "SELECT COUNT(*) FROM events e WHERE e.kind IN (%s) AND e.error IS NULL AND e.ord > "
                        "COALESCE((SELECT MAX(e2.ord) FROM sm_anchors a JOIN events e2 USING(eid)), 0)"
                        % ",".join("?" * len(SM_KINDS)), SM_KINDS).fetchone()[0]
                    if not chained or keep_entries is None or since + 1 >= anchor_every:
                        payload = pair_payload() if callable(pair_payload) else pair_payload
                        anchor_hash = self.put_blob(payload, level=1)
                if landed_ok and anchor_hash is None and keep_entries is None and entries_gz is not None:
                    # replay needs the entries and there are no bytes to anchor
                    # on: keep them whatever their size
                    keep_entries = entries_gz
                if state_hash is not None and landed_ok:
                    head = self.conn.execute("SELECT state_hash FROM events WHERE error IS NULL "
                                             "ORDER BY ord DESC LIMIT 1").fetchone()
                    if ((head is None or head[0] != state_hash) and self.conn.execute(
                            "SELECT 1 FROM events WHERE state_hash=? AND error IS NULL LIMIT 1",
                            (state_hash,)).fetchone()):
                        flags |= REVERTS_TO_EARLIER
                event = dict(
                    kind=line.get("kind") or "sm_apply", t_utc_us=int(line["t_utc_us"]), t_src=line.get("t"),
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
                    "entries_n,entries,live) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (eid, line["id"], outcome, line.get("base_hash"), line.get("post_hash"), line.get("run_uid"),
                     json_bytes(line.get("units") or []).decode("utf-8"),
                     json_bytes(line["undoes"]).decode("utf-8") if line.get("undoes") else None,
                     int(line.get("n") or 0), keep_entries if landed_ok else None, line.get("live")))
                self.set_meta("journal_offset", str(journal_end))
                self.conn.execute("COMMIT")
                return eid
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
        except BaseException:
            self._pids = old_pids
            raise

    def _pid(self, path: str) -> int:
        pid = self._pids.get(path)
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
