"""SB-09: flagless instance migrations preserve every saved state and row."""
import json
import sqlite3
from contextlib import closing

import pytest

from quam_state_manager.core import history, hub, safe_io, working_copy
from quam_state_manager.core.history_rekey import migrate_history_rekey_v4
from quam_state_manager.core.loader import ChangeEntry


@pytest.fixture(autouse=True)
def _inline_projection():
    previous = hub._PROJECTOR.inline
    hub.set_inline(True)
    yield
    hub.set_inline(previous)


def _snapshot(directory, timestamp, value):
    state = {"qubits": {"q1": {"id": "q1", "T1": value}}, "qubit_pairs": {}}
    wiring = {"network": {"host": "127.0.0.1", "cluster_name": "default"}}
    folder = directory / timestamp
    safe_io.write_state_wiring(folder, state, wiring)
    safe_io.atomic_write_json(folder / "meta.json", {
        "timestamp": timestamp, "trigger": "manual", "kind": "manual",
        "source_path": str(directory.parent.parent / directory.name / "quam_state"),
        "diff_summary": {}, "new_experiments": [], "state_size": 0, "wiring_size": 0,
    })
    return state, wiring


def _index(directory, rows):
    idx = directory / "index.sqlite"
    history._ensure_param_history_schema(idx)
    with closing(sqlite3.connect(idx)) as conn:
        conn.executemany("INSERT INTO param_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)", [
            (ts, "q1", "T1", value, None, "manual", None, None) for ts, value in rows])
        conn.commit()


def _ledger(directory, value):
    state = {"qubits": {"q1": {"T1": value}}}
    pair = (json.dumps(state).encode(), b"{}")
    h = hub.Hub.for_chip(directory)
    rec = h.record("apply", "human", [ChangeEntry("qubits.q1.T1", 0, value, "state")],
                   None, pair, "apply", post_hash=working_copy.content_hash(state, {}))
    rec.landed()
    assert hub.flush(30)
    h.release()


def _inventory(instance):
    snapshots, index_rows, ledger_rows, journals = set(), set(), set(), set()
    for meta in instance.rglob("meta.json"):
        snap = meta.parent
        if (snap / "state.json").exists():
            snapshots.add(tuple((snap / name).read_bytes()
                                for name in ("state.json", "wiring.json", "meta.json")))
    for idx in instance.rglob("index.sqlite"):
        with closing(sqlite3.connect(idx)) as conn:
            index_rows.update(conn.execute("SELECT * FROM param_history"))
    for ledger in instance.rglob("ledger.sqlite"):
        with closing(sqlite3.connect(ledger)) as conn:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")]
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                ledger_rows.update((ledger.parent.name, table, tuple(row))
                                   for row in conn.execute("SELECT * FROM " + quoted))
    for journal in instance.rglob("events.jsonl"):
        journals.add((journal.parent.name, journal.read_bytes()))
    return {"snapshots": snapshots, "index": index_rows,
            "ledger": ledger_rows, "journals": journals}


def _instance(tmp_path):
    instance = tmp_path / "instance"
    source, target = (instance / "history" / key for key in ("chip_a", "chip_b"))
    t1, t2, t3 = (f"20261001_00000{i}_001" for i in range(3))
    _snapshot(source, t1, 10)
    _snapshot(target, t2, 20)
    _snapshot(target, t3, 30)
    # Two matching dirs: the one holding more snapshots is the v2 target.
    # The source also has a pruned row and a conflicting foreign row.
    _index(source, [("20260930_000000_001", 11), (t1, 10), (t2, 42)])
    _index(target, [(t2, 20), (t3, 30)])
    _ledger(source, 10)
    _ledger(target, 20)
    assert len(list((instance / "history").iterdir())) == 2
    assert not list(instance.glob("migrated_v*.flag"))
    return instance, source, target, t1


@pytest.mark.parametrize("merge_fails", [False, True])
def test_flagless_migrations_preserve_all_snapshots_index_and_ledger_rows(tmp_path, monkeypatch, merge_fails):
    instance, source, target, moved_ts = _instance(tmp_path)
    before = _inventory(instance)
    assert len(before["snapshots"]) == 3 and len(before["index"]) == 5
    assert before["ledger"] and len(before["journals"]) == 2
    if merge_fails:
        def unavailable(*args, **kwargs):
            raise sqlite3.OperationalError("index is busy")
        monkeypatch.setattr(history, "_merge_index_for_timestamps", unavailable)
    v1 = history.migrate_legacy_histories(instance)
    v2 = history.migrate_legacy_histories_v2(instance)
    v3 = history.migrate_index_attribution_v3(instance)
    v4 = migrate_history_rekey_v4(instance)
    after = _inventory(instance)
    print("SB-09", {"v1": v1, "v2": v2, "v3": v3, "v4": v4,
                    "before": {k: len(v) for k, v in before.items()},
                    "after": {k: len(v) for k, v in after.items()}})
    assert after == before, {k: before[k] - after[k] for k in before}
    assert v2["moved"] == 1 and (target / moved_ts).is_dir()
    assert not (source / moved_ts).exists()
    assert (source / "ledger.sqlite").exists() and (source / "index.sqlite").exists()
