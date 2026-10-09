"""The C0 inventory reads committed state without changing source files."""

import json
import sqlite3

from tools.audit_hub_fallback import audit, token


def test_audit_counts_wal_events_roots_and_decisions_without_source_writes(tmp_path):
    instance = tmp_path / "instance"
    chip = instance / "history" / "key_1"
    chip.mkdir(parents=True)
    (instance / "history" / "key_2").mkdir()
    conn = sqlite3.connect(chip / "ledger.sqlite")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA wal_autocheckpoint=0")
    conn.execute("CREATE TABLE events(kind TEXT)")
    conn.execute("CREATE TABLE roots(root_id INTEGER, path TEXT, folder_key TEXT)")
    conn.executemany("INSERT INTO events VALUES(?)", [(k,) for k in ("run", "run", "observed", "sm_apply", "undo")])
    conn.execute("INSERT INTO roots VALUES(1, 'root_path', 'root_key')")
    conn.commit()
    # A later, uncommitted frame must not enter the inventory.
    conn.execute("INSERT INTO events VALUES('run')")
    (instance / "chip_decisions.json").write_text(json.dumps({"key_1::root_path": "same"}), encoding="utf-8")
    def fingerprint():
        return {p.relative_to(instance): (p.stat().st_size, p.stat().st_mtime_ns,
                                          None if p.name.endswith("-shm") else p.read_bytes())
                for p in instance.rglob("*") if p.is_file()}
    before = fingerprint()
    try:
        rows = audit(instance)
        assert rows[0]["run"] == 2 and rows[0]["observed"] == 1 and rows[0]["sm"] == 2
        assert rows[0]["verdict"] == "ledger with runs"
        assert rows[0]["roots"] == [{"root_id": 1, "path_token": token("root_path"),
                                     "folder_key_token": token("root_key")}]
        assert rows[0]["decisions"] == [{"key_token": token("root_path"), "decision": "same"}]
        assert rows[1]["verdict"] == "no ledger (unavailable)"
        after = fingerprint()
        assert after == before
    finally:
        conn.rollback()
        conn.close()


def test_sm_only_and_corrupt_ledgers_are_reported(tmp_path):
    root = tmp_path / "history"
    chip = root / "key_1"
    chip.mkdir(parents=True)
    conn = sqlite3.connect(chip / "ledger.sqlite")
    conn.execute("CREATE TABLE events(kind TEXT)")
    conn.execute("CREATE TABLE roots(root_id INTEGER, path TEXT, folder_key TEXT)")
    conn.execute("INSERT INTO events VALUES('sm_apply')")
    conn.commit()
    conn.close()
    bad = root / "key_2"
    bad.mkdir()
    (bad / "ledger.sqlite").write_bytes(b"invalid database")
    rows = audit(tmp_path)
    assert rows[0]["verdict"] == "no_folder_linked (offer)" and rows[0]["sm"] == 1
    assert rows[1]["ledger_present"] and rows[1]["error"] == "DatabaseError"
