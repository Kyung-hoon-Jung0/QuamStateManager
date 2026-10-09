"""docs/258: the history index never loses an Apply write.

On a fresh chip the first Apply starts two deferred index writes (the backup
and the save). Both bootstrap the brand-new ``index.sqlite`` at once, and the
loser's ``PRAGMA journal_mode=WAL`` failed with "database is locked" -- SQLite
does not run the busy handler for that switch, so ``timeout=10`` never helped.
Its snapshot was then missing from the index, and the 🕘 popover's tracked
tier -- which accepts any non-empty row set as the whole timeline -- showed
one of the folder's own values as never having existed.

The hooks below never inject an error: they only PAUSE a real connection (at
its WAL switch holding a real ``BEGIN IMMEDIATE`` reservation, or just before
its ``BEGIN IMMEDIATE``). Every failure a mutation produces comes from the
unmodified SQLite engine.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from quam_state_manager.core import history, leaf_index
from quam_state_manager.core.history import HistoryManager

_WAL = "PRAGMA journal_mode=WAL"
_BEGIN = "BEGIN IMMEDIATE"


def _seed(live: Path, value: float) -> None:
    live.mkdir(parents=True, exist_ok=True)
    (live / "state.json").write_text(json.dumps({
        "extras": {"chip_name": "index-test"},
        "qubits": {"q1": {"T1": value, "custom": value}},
        "active_qubit_names": ["q1"],
    }), encoding="utf-8")
    (live / "wiring.json").write_text("{}", encoding="utf-8")


def _pause_first(monkeypatch, sql: str):
    """Pause the FIRST connection that executes *sql* until released.

    For the WAL switch the paused connection first takes a real write
    reservation (``BEGIN IMMEDIATE``) on the still-rollback-journal file, the
    state a bootstrap is in while it creates the schema. Returns
    (held, release)."""
    held, release = threading.Event(), threading.Event()
    real_connect = sqlite3.connect
    state = {"first": True}
    guard = threading.Lock()

    class Connection(sqlite3.Connection):
        def execute(self, stmt, *args, **kwargs):
            pause = False
            if stmt == sql:
                with guard:
                    if state["first"]:
                        state["first"] = False
                        pause = True
            if pause:
                if sql == _WAL:
                    super().execute(_BEGIN)
                held.set()
                try:
                    assert release.wait(10), "test must release the pause"
                finally:
                    if sql == _WAL:
                        super().execute("ROLLBACK")
            return super().execute(stmt, *args, **kwargs)

    def connect(*args, **kwargs):
        kwargs["factory"] = Connection
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(history.sqlite3, "connect", connect)
    return held, release


def _apply(tmp_path, monkeypatch, pause_sql: str = _WAL):
    """An Apply's two deferred index writes, the save's entering while the
    backup's is paused at *pause_sql*."""
    hm = HistoryManager(tmp_path / "inst")
    live = tmp_path / "live"
    held, release = _pause_first(monkeypatch, pause_sql)
    contender = threading.Event()
    real_insert = hm._index_snapshot_into

    def insert(*args, **kwargs):
        if args[2].trigger == "save":
            contender.set()
        return real_insert(*args, **kwargs)

    monkeypatch.setattr(hm, "_index_snapshot_into", insert)
    try:
        _seed(live, 1.0)
        backup = hm.check_and_snapshot(live, "backup", force=True, defer_index=True)
        assert held.wait(10)
        _seed(live, 2.0)
        save = hm.check_and_snapshot(live, "save", force=True, defer_index=True)
        assert contender.wait(10)
        # Give an unserialised save time to do its damage while the backup is
        # paused; a serialised one stays queued on the index's write lock.
        with hm._deferred_index_lock:
            threads = list(hm._deferred_index_threads)
        threads[-1].join(0.3)
    finally:
        release.set()
        hm._join_deferred_index(timeout=10)
    assert not hm._deferred_index_threads
    return hm, live, backup, save


# S10 C4: drawer index tier -> ledger fixture, retain the fresh-chip race contract.
def test_fresh_chip_race_keeps_both_values_in_the_ledger(tmp_path, monkeypatch, caplog):
    from tests.ledger_fixture import observed_view
    hm, live, _, _ = _apply(tmp_path, monkeypatch)
    out = observed_view(hm, live, "qubits.q1.T1")
    assert [p["value"] for p in out["points"]] == [2.0, 1.0]
    assert "Deferred index of snapshot" not in caplog.text


def test_concurrent_writers_commit_every_snapshot_without_read_repair(tmp_path, monkeypatch):
    hm, live, backup, save = _apply(tmp_path, monkeypatch)
    # Opened directly: a reader's self-heal must not hide a lost write here.
    with sqlite3.connect(hm._index_path(live)) as conn:
        rows = conn.execute(
            "SELECT timestamp, value FROM param_history WHERE property='T1'"
        ).fetchall()
        assert dict(rows) == {backup.timestamp: 1.0, save.timestamp: 2.0}
        assert {r[0] for r in conn.execute("SELECT ts FROM leaf_snaps")} == {
            backup.timestamp, save.timestamp}


def test_the_save_never_commits_before_the_backup(tmp_path, monkeypatch):
    """The lock is held through COMMIT, not only the bootstrap: a save that
    committed first turned the backup into an OUT-OF-ORDER leaf ingest, which
    writes nothing and marks the leaf index dirty (a full background rebuild
    on the next leaf read). The backup is paused after its bootstrap, just
    before its transaction -- on an established chip as much as a fresh one."""
    hm, live, backup, save = _apply(tmp_path, monkeypatch, pause_sql=_BEGIN)
    with sqlite3.connect(hm._index_path(live)) as conn:
        assert {r[0] for r in conn.execute("SELECT ts FROM leaf_snaps")} == {
            backup.timestamp, save.timestamp}
        assert not leaf_index.is_dirty(conn)


def _in_thread(fn):
    errors: list = []

    def run():
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 -- the pin reports it
            errors.append(exc)

    t = threading.Thread(target=run)
    t.start()
    return t, errors


def _capture_paused(tmp_path, monkeypatch, pause_sql: str, *, established=False):
    hm = HistoryManager(tmp_path / "inst")
    live = tmp_path / "live"
    _seed(live, 0.5)
    if established:
        hm.check_and_snapshot(live, "save", force=True)        # file is WAL now
    _seed(live, 1.0)
    held, release = _pause_first(monkeypatch, pause_sql)
    meta = hm.check_and_snapshot(live, "save", force=True, defer_index=True)
    assert held.wait(10)
    return hm, live, meta, release


def _finish(hm, release, *threads):
    release.set()
    hm._join_deferred_index(timeout=10)
    for t in threads:
        t.join(10)


def test_a_migration_bootstrap_waits_for_a_capture_bootstrap(tmp_path, monkeypatch):
    """Backfill/migration bootstrap the same file through
    ``_ensure_param_history_schema`` -- e.g. the auto-import on a fresh chip's
    first Param History open, beside that chip's first Apply."""
    hm, live, meta, release = _capture_paused(tmp_path, monkeypatch, _WAL)
    try:
        t, errors = _in_thread(
            lambda: history._ensure_param_history_schema(hm._index_path(live)))
        t.join(0.3)
    finally:
        _finish(hm, release, t)
    assert not errors, f"the second bootstrap failed the WAL switch: {errors}"
    with sqlite3.connect(hm._index_path(live)) as conn:
        assert conn.execute("SELECT value FROM param_history WHERE timestamp=? "
                            "AND property='T1'", (meta.timestamp,)).fetchall() == [(1.0,)]


def test_a_readers_first_open_of_a_fresh_file_waits_for_a_capture(tmp_path, monkeypatch):
    """A reader's first ``_open_index`` of a not-yet-WAL file is a bootstrap
    too (WAL + CREATEs). Unserialised it failed itself or, winning, made the
    capture's write fail -- the same lost write."""
    hm, live, meta, release = _capture_paused(tmp_path, monkeypatch, _WAL)
    try:
        t, errors = _in_thread(lambda: hm._open_index(live).close())
        t.join(0.3)
    finally:
        _finish(hm, release, t)
    assert not errors, f"the reader's first open failed the bootstrap: {errors}"
    with sqlite3.connect(hm._index_path(live)) as conn:
        assert conn.execute("SELECT value FROM param_history WHERE timestamp=? "
                            "AND property='T1'", (meta.timestamp,)).fetchall() == [(1.0,)]


def test_a_readers_first_open_of_an_established_file_never_waits(tmp_path, monkeypatch):
    """The other half of the contract: once the file is WAL nothing in a
    reader's first open needs the lock, so a page render (Trends, Chip Status,
    a second window's first read) never queues behind a capture's insert."""
    hm, live, _, release = _capture_paused(tmp_path, monkeypatch, _BEGIN,
                                           established=True)
    reader_hm = HistoryManager(tmp_path / "inst")    # its first open of this file
    try:
        t, errors = _in_thread(lambda: reader_hm._open_index(live).close())
        t.join(5)
        waited = t.is_alive()
    finally:
        _finish(hm, release, t)
    assert not waited, "a reader of an established index queued on the write lock"
    assert not errors, errors


def _two_snapshots_second_unindexed(tmp_path):
    """Two committed snapshots; the second's curated rows lost -- what the
    race (or a killed deferred writer) leaves behind."""
    hm = HistoryManager(tmp_path / "inst")
    live = tmp_path / "live"
    _seed(live, 1.0)
    first = hm.check_and_snapshot(live, "experiment", force=True, run_id=1)
    _seed(live, 2.0)
    second = hm.check_and_snapshot(live, "experiment", force=True, run_id=2)
    with sqlite3.connect(hm._index_path(live)) as conn:
        conn.execute("DELETE FROM param_history WHERE timestamp=?", (second.timestamp,))
    return hm, live, first, second


def test_calibration_column_reads_heal_a_lost_write(tmp_path):
    """Calibration columns heal missing curated rows before reading the index."""
    hm, live, first, second = _two_snapshots_second_unindexed(tmp_path)
    # S10 C4: field tier -> calibration column, retain lost-write healing.
    out = hm.column_history(live, {"q1": "qubits.q1.T1"})
    assert [(r[0], r[1]) for r in out["q1"]] == [
        (first.timestamp, 1.0), (second.timestamp, 2.0)]


def test_a_failing_heal_never_costs_the_read(tmp_path, monkeypatch):
    hm, live, _, _ = _two_snapshots_second_unindexed(tmp_path)

    def boom(_path):
        raise RuntimeError("heal failed")

    monkeypatch.setattr(hm, "_ensure_index_fresh", boom)
    # S10 C4: field tier -> calibration column, failed healing keeps usable rows.
    out = hm.column_history(live, {"q1": "qubits.q1.T1"})
    assert [p[1] for p in out["q1"]] == [1.0]
