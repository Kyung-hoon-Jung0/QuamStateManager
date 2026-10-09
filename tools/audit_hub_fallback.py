"""Read-only C0 inventory: python tools/audit_hub_fallback.py INSTANCE --output DIR --label instance_1.

SQLite bytes and committed WAL frames are queried in memory (no WAL/SHM
creation or source checkpoint). Reports expose chip keys, root IDs and anonymous root/decision
tokens; paths and folder labels are never written to reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import struct


SM_KINDS = ("sm_apply", "agent", "autofit", "restore", "undo", "redo")


def token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _checksum(body: bytes, order: str, seed=(0, 0)) -> tuple[int, int]:
    a, b = seed
    words = struct.unpack(order + "I" * (len(body) // 4), body)
    for i in range(0, len(words), 2):
        a = (a + words[i] + b) & 0xFFFFFFFF
        b = (b + words[i + 1] + a) & 0xFFFFFFFF
    return a, b


def ledger_connection(path: Path) -> sqlite3.Connection:
    """Use the last committed WAL snapshot without writing beside the database."""
    data = bytearray(path.read_bytes())
    wal_path = path.with_name(path.name + "-wal")
    if wal_path.exists():
        wal = wal_path.read_bytes()
        if len(wal) >= 32:
            magic = struct.unpack(">I", wal[:4])[0]
            if magic not in (0x377F0682, 0x377F0683):
                raise ValueError("invalid WAL header")
            order = "<" if magic == 0x377F0682 else ">"
            checksum = _checksum(wal[:24], order)
            if checksum != struct.unpack(">II", wal[24:32]):
                raise ValueError("invalid WAL header checksum")
            page_size = struct.unpack(">I", wal[8:12])[0]
            if page_size == 1:
                page_size = 65536
            if page_size < 512 or page_size > 65536 or page_size & (page_size - 1):
                raise ValueError("invalid WAL page size")
            frames, committed = [], None
            for start in range(32, len(wal) - 23 - page_size, 24 + page_size):
                frame = wal[start:start + 24 + page_size]
                if frame[8:16] != wal[16:24]:
                    break
                page, size = struct.unpack(">II", frame[:8])
                if not page:
                    break
                next_checksum = _checksum(frame[:8] + frame[24:], order, checksum)
                if next_checksum != struct.unpack(">II", frame[16:24]):
                    break
                checksum = next_checksum
                frames.append((page, frame[24:]))
                if size:
                    committed = (len(frames), size)
            if committed:
                count, size = committed
                data.extend(b"\0" * max(0, size * page_size - len(data)))
                for page, body in frames[:count]:
                    if page <= size:
                        data[(page - 1) * page_size:page * page_size] = body
                del data[size * page_size:]
    # A deserialized WAL-mode database otherwise tries to find a sidecar.
    if len(data) >= 20:
        data[18:20] = b"\1\1"
    conn = sqlite3.connect(":memory:")
    conn.deserialize(bytes(data))
    conn.execute("PRAGMA query_only=ON")
    return conn


def audit(instance: Path) -> list[dict]:
    decisions_path = instance / "chip_decisions.json"
    decisions_error = None
    try:
        decisions = json.loads(decisions_path.read_text(encoding="utf-8")) if decisions_path.exists() else {}
        if not isinstance(decisions, dict):
            raise ValueError("decisions must be an object")
    except (OSError, ValueError):
        decisions, decisions_error = {}, "unreadable decisions"
    rows = []
    for directory in sorted((instance / "history").glob("*/")):
        key = directory.name
        ledger = directory / "ledger.sqlite"
        row = {"chip_key": key, "ledger_present": ledger.is_file(),
               "run": 0, "observed": 0, "sm": 0, "roots": [], "decisions": [],
               "verdict": "no ledger (unavailable)"}
        for decision_key, value in sorted(decisions.items()):
            if decision_key.startswith(key + "::"):
                row["decisions"].append({"key_token": token(decision_key.split("::", 1)[1]),
                                         "decision": value})
        if decisions_error:
            row["decisions_error"] = decisions_error
        if ledger.is_file():
            try:
                conn = ledger_connection(ledger)
                try:
                    counts = dict(conn.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind"))
                    row.update(run=counts.get("run", 0), observed=counts.get("observed", 0),
                               sm=sum(counts.get(k, 0) for k in SM_KINDS))
                    row["roots"] = [{"root_id": rid, "path_token": token(path),
                                     "folder_key_token": token(folder)}
                                    for rid, path, folder in conn.execute(
                                        "SELECT root_id, path, folder_key FROM roots ORDER BY root_id")]
                finally:
                    conn.close()
                row["verdict"] = "ledger with runs" if row["run"] else "no_folder_linked (offer)"
                if row["roots"] and not row["run"]:
                    row["qualification"] = "registered roots but no run events; verify sync before offering"
            except (sqlite3.Error, OSError, ValueError) as exc:
                row["error"] = type(exc).__name__
                row["qualification"] = "ledger present but unreadable (unavailable)"
        rows.append(row)
    return rows


def table(rows: list[dict]) -> str:
    lines = ["| Chip key | Ledger | Run | Observed | SM | Root IDs / path tokens | Decisions | Verdict |",
             "|---|---|---:|---:|---:|---|---|---|"]
    for r in rows:
        roots = ", ".join(f"{x['root_id']}:{x['path_token']}" for x in r["roots"]) or "-"
        decisions = ", ".join(f"{x['key_token']}:{x['decision']}" for x in r["decisions"]) or "-"
        lines.append(f"| {r['chip_key']} | {r['ledger_present']} | {r['run']} | {r['observed']} | "
                     f"{r['sm']} | {roots} | {decisions} | {r['verdict']} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("instance", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--label", default="instance")
    args = parser.parse_args()
    if not args.instance.is_dir():
        parser.error("instance directory does not exist")
    if not args.label.replace("_", "").isalnum():
        parser.error("label must contain only letters, numbers, and underscores")
    rows = audit(args.instance)
    payload = {"label": args.label, "roots_redacted": True, "chips": rows}
    rendered = table(rows)
    print(rendered)
    print(json.dumps(payload, indent=2))
    if args.output:
        source, target = args.instance.resolve(), args.output.resolve()
        if target == source or source in target.parents:
            parser.error("output must be outside the source instance")
        target.mkdir(parents=True, exist_ok=True)
        (target / f"{args.label}.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        (target / f"{args.label}.md").write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
