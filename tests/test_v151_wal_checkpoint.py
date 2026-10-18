"""v1.5.1 U4 soak: the ledger's WAL is checkpointed and capped.

A 3.5 h soak saw ``ledger.sqlite-wal`` grow 2.0 -> 2.7 MB with the database
file untouched: the hub's one writer connection lives as long as SM, SQLite
checkpoints automatically only at 1000 WAL pages, and ``journal_size_limit``
was -1 (the WAL keeps its high-water size). Pinned: every ledger connection
and every param-history index connection carries a finite
``journal_size_limit``, and a ledger write runs a PASSIVE checkpoint once the
interval has passed -- the database file then holds what the WAL held.
"""
from __future__ import annotations

import json
import time

from quam_state_manager.core import hub_rules as rules
from quam_state_manager.core import hub_store
from quam_state_manager.core.history import HistoryManager
from quam_state_manager.core.hub_store import HubStore

_SANE = 64 * 1024 * 1024      # a WAL cap, not "keep the high-water size"
_HOUR = 3600.0                # a write an hour later is past any sane interval


def _append(store, doc, n):
    root_id = store.register_root(store.directory / "archive", "+00:00")
    event = dict(kind="run", t_utc_us=n + 1, ord=n + 1, root_id=root_id,
                 rel_path=f"run{n}", run_id=n, experiment="scan", state_hash=f"hash{n}",
                 flags=0, error=None)
    flat = rules.flatten(doc)
    return store.append(event, rules.diff({}, flat), doc, flat)


def test_every_ledger_connection_caps_its_wal(tmp_path):
    with HubStore(tmp_path / "ledger") as store:
        got = store.conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert 0 < got <= _SANE, got


def test_the_param_history_index_caps_its_wal(tmp_path):
    hm = HistoryManager(tmp_path / "_inst")
    chip = tmp_path / "chip" / "quam_state"
    chip.mkdir(parents=True)
    (chip / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({"network": {}}), encoding="utf-8")
    conn = hm._open_index(chip)
    try:
        got = conn.execute("PRAGMA journal_size_limit").fetchone()[0]
        assert 0 < got <= _SANE, got
    finally:
        conn.close()


def test_a_write_after_the_interval_checkpoints_the_wal(tmp_path, monkeypatch):
    clock = {"t": 1000.0}

    class _Time:                      # hub_store's own clock only
        sleep = staticmethod(time.sleep)

        @staticmethod
        def monotonic():
            return clock["t"]

    monkeypatch.setattr(hub_store, "time", _Time)
    with HubStore(tmp_path / "ledger") as store:
        db = store.directory / "ledger.sqlite"
        big = {"qubits": {f"q{i}": {"T1": i * 1e-6, "note": "x" * 400} for i in range(200)}}
        _append(store, big, 0)
        size0 = db.stat().st_size
        # within the interval: nothing is copied into the database file
        clock["t"] += 1.0
        _append(store, {"qubits": {"q0": {"T1": 1.0}}}, 1)
        assert db.stat().st_size == size0, "no checkpoint before the interval"
        # past the interval: the next write checkpoints
        clock["t"] += _HOUR
        _append(store, {"qubits": {"q0": {"T1": 2.0}}}, 2)
        assert db.stat().st_size > size0, "the write ran a checkpoint: the db file holds the pages"
        busy, log, done = store.conn.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        assert busy == 0 and log == done
