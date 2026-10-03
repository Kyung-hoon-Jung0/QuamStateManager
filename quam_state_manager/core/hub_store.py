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
OPS = {"set": 0, "add": 1, "gone": 2, "retarget": 3}

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
        self.conn = sqlite3.connect(self.directory / "ledger.sqlite")
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

    def put_blob(self, payload: bytes, *, expected_hash: str | None = None) -> str:
        digest = hashlib.sha1(payload).hexdigest()
        if expected_hash is not None and digest != expected_hash:
            raise ValueError("array blob disagrees with S2 hash")
        if not self.conn.execute("SELECT 1 FROM blobs WHERE hash=?", (digest,)).fetchone():
            self.conn.execute("INSERT INTO blobs VALUES(?,?)", (digest, gzip.compress(payload, mtime=0)))
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

    def state_at(self, eid: int) -> dict:
        event = self.event(eid)
        if event["error"]:
            raise ValueError(f"run has no saved state: {event['error']}")
        checkpoint = self.conn.execute("SELECT c.eid,c.hash,e.ord FROM checkpoints c "
                                       "JOIN events e USING(eid) WHERE e.ord<=? ORDER BY e.ord DESC LIMIT 1",
                                       (event["ord"],)).fetchone()
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
