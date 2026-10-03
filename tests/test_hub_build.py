import json
import shutil

import pytest

from quam_state_manager.core import hub_build, hub_rules as rules, run_time
from quam_state_manager.core.hub_store import CHIP_UNCERTAIN, OPS, REVERTS_TO_EARLIER, TIME_ASSUMED, HubStore


def run(root, rid, doc, *, clock=None, wiring=None, node_extra=None, folder_clock="120000"):
    folder = root / "2026-01-01" / f"#{rid}_scan_{folder_clock}"
    (folder / "quam_state").mkdir(parents=True)
    node = {"created_at": clock or f"2026-01-01T12:00:{rid:02d}+00:00",
            "metadata": {"status": "finished", "name": "scan"}, "parents": [rid - 1]}
    node.update(node_extra or {})
    (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")
    (folder / "quam_state" / "state.json").write_text(json.dumps(doc), encoding="utf-8")
    (folder / "quam_state" / "wiring.json").write_text(json.dumps(wiring or {}), encoding="utf-8")
    return folder


def events(out):
    with HubStore(out) as store:
        return [dict(r) for r in store.conn.execute("SELECT * FROM events ORDER BY ord")]


def test_order_is_canonical_offset_instant_with_microseconds(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1}, clock="2026-01-01T12:00:00.100-04:00")
    run(root, 2, {"v": 2}, clock="2026-01-01T16:00:00.099Z")
    run(root, 3, {"v": 3}, clock="2026-01-01T16:00:00.100Z")
    hub_build.build(root, out)
    rows = events(out)
    assert [r["run_id"] for r in rows] == [2, 1, 3]
    assert rows[1]["t_utc_us"] - rows[0]["t_utc_us"] == 1000
    assert all(r["t_quality"] == "offset" for r in rows)
    assert rows[1]["t_utc_us"] == run_time.iso_instant("2026-01-01T12:00:00.100-04:00")[0]


def test_archive_offset_hint_and_assumed_local_flag(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1}, clock="2026-01-01T12:00:01-04:00")
    run(root, 2, {"v": 2}, clock="2026-01-01T12:00:02")
    assert hub_build.build(root, out)["offset_hint"] == "-04:00"
    rows = events(out)
    assert rows[1]["t_quality"] == "archive_offset"
    assert rows[1]["t_utc_us"] == rows[0]["t_utc_us"] + 1_000_000
    assert not rows[1]["flags"] & TIME_ASSUMED
    root2, out2 = tmp_path / "naive", tmp_path / "naive-ledger"
    run(root2, 1, {}, clock="2026-01-01T12:00:01")
    hub_build.build(root2, out2)
    assert events(out2)[0]["flags"] & TIME_ASSUMED


def test_run_end_fallback_and_metadata(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1}, clock="invalid", node_extra={
        "metadata": {"run_start": "2026-01-01T11:59:00-04:00", "run_end": "2026-01-01T12:00:01-04:00", "status": "finished"},
        "data": {"parameters": {"model": {"qubits": ["q0"], "cz_macro_name": "gate"}}}})
    hub_build.build(root, out)
    row = events(out)[0]
    assert row["t_src"] == "2026-01-01T12:00:01-04:00"
    assert row["t_utc_us"] == row["run_end_us"]
    assert row["run_start_us"] < row["t_utc_us"]
    assert json.loads(row["targets"]) == {"qubits": ["q0"], "cz_macro_name": "gate"}
    assert json.loads(row["parents"]) == [0]


def test_identical_hash_keeps_zero_event_without_parsing(tmp_path, monkeypatch):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1})
    run(root, 2, {"v": 1})
    calls = []
    original = rules.merged
    monkeypatch.setattr(rules, "merged", lambda *a: (calls.append(1), original(*a))[1])
    report = hub_build.build(root, out)
    rows = events(out)
    assert report["events"] == 2 and len(calls) == 1
    assert report["raw_zero_change"] == 1 and rows[1]["n_changes"] == 0
    assert rows[1]["base_hash"] == rows[0]["state_hash"] == rows[1]["state_hash"]


def test_raw_hash_changes_but_numeric_equal_has_no_rows(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1})
    run(root, 2, {"v": 1.0})
    hub_build.build(root, out)
    rows = events(out)
    assert rows[0]["state_hash"] != rows[1]["state_hash"]
    assert rows[1]["n_changes"] == 0


def test_revert_is_plain_head_diff_and_only_nonadjacent_flag(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    for i, v in enumerate((1, 2, 1, 1), 1):
        run(root, i, {"v": v})
    hub_build.build(root, out)
    rows = events(out)
    assert [r["n_changes"] for r in rows] == [1, 1, 1, 0]
    assert [bool(r["flags"] & REVERTS_TO_EARLIER) for r in rows] == [False, False, True, False]
    assert rows[2]["base_hash"] == rows[1]["state_hash"]
    with HubStore(out) as store:
        c = store.conn.execute("SELECT old_num,num FROM changes WHERE eid=?", (rows[2]["eid"],)).fetchone()
        assert tuple(c) == (2, 1)
    assert not hasattr(hub_build, "OFF_LIVE")


def test_archive_copy_adds_location_not_event(tmp_path):
    root, copy, out = tmp_path / "archive", tmp_path / "copy", tmp_path / "ledger"
    run(root, 1, {"v": 1})
    run(root, 2, {"v": 2})
    hub_build.build(root, out)
    shutil.copytree(root, copy)
    report = hub_build.build(copy, out)
    assert report["added"] == 0 and report["extra_locations"] == 2 and report["events"] == 2
    with HubStore(out) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM locations").fetchone()[0] == 4
        assert store.conn.execute("SELECT COUNT(*) FROM roots").fetchone()[0] == 2


def test_run_identity_does_not_collapse_overlapping_ids(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    a = run(root, 1, {"v": 1}, folder_clock="120001")
    b = run(root, 1, {"v": 2}, folder_clock="120002")
    assert a != b
    hub_build.build(root, out)
    assert len(events(out)) == 2


def test_run_identity_includes_time_id_and_experiment(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1}, folder_clock="120001")
    run(root, 2, {"v": 1}, clock="2026-01-01T12:00:01Z", folder_clock="120002")
    run(root, 1, {"v": 1}, clock="2026-01-01T12:00:02Z", folder_clock="120003")
    run(root, 1, {"v": 1}, node_extra={"metadata": {"name": "sweep"}}, folder_clock="120004")
    hub_build.build(root, out)
    assert len(events(out)) == 4


def test_checkpoint_replay_and_shared_merge(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    folders = [run(root, i, {"nested": {"a": i, "v": 99}, "array": list(range(17)), "empty": []},
                   wiring={"nested": {"v": i * 2}}) for i in range(1, 6)]
    hub_build.build(root, out, checkpoint_interval=2)
    with HubStore(out, checkpoint_interval=2) as store:
        assert store.conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0] == 2
        for eid, folder in enumerate(folders, 1):
            assert rules.same(store.state_at(eid), hub_build.read_doc(folder))
        shutil.rmtree(root)
        assert store.state_at(5)["nested"] == {"a": 5, "v": 10}


def test_resume_after_exception_and_idempotent_rebuild(tmp_path, monkeypatch):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    for i in range(1, 6):
        run(root, i, {"v": i})
    original = HubStore.append
    calls = []

    def interrupt(self, *a, **kw):
        if len(calls) == 2:
            raise RuntimeError("interrupted")
        calls.append(1)
        return original(self, *a, **kw)

    monkeypatch.setattr(HubStore, "append", interrupt)
    with pytest.raises(RuntimeError, match="interrupted"):
        hub_build.build(root, out)
    assert len(events(out)) == 2
    with HubStore(out) as store:
        assert json.loads(store.meta("watermark:1"))["eid"] == 2
    monkeypatch.setattr(HubStore, "append", original)
    assert hub_build.build(root, out)["added"] == 3
    before = events(out)
    assert hub_build.build(root, out)["added"] == 0
    assert events(out) == before


def test_limit_counts_new_events_and_cli(tmp_path, capsys):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    for i in range(1, 4):
        run(root, i, {"v": i})
    assert hub_build.main([str(root), "--out", str(out), "--limit", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["added"] == 1
    assert hub_build.build(root, out, limit=1)["events"] == 2
    assert hub_build.build(root, out, limit=0)["added"] == 0
    with pytest.raises(ValueError):
        hub_build.build(root, out, limit=-1)


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_bad_state_is_kept_as_error_and_next_run_uses_good_head(tmp_path, damage):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1})
    broken = run(root, 2, {"v": 2}) / "quam_state" / "state.json"
    run(root, 3, {"v": 3})
    if damage == "missing":
        broken.unlink()
    else:
        broken.write_text("{bad", encoding="utf-8")
    result = hub_build.build(root, out)
    rows = events(out)
    assert result["events"] == 3 and result["errors"] == 1
    assert rows[1]["status"] == "error" and rows[1]["error"] and rows[1]["n_changes"] == 0
    assert rows[2]["base_hash"] == rows[0]["state_hash"]
    assert hub_build.build(root, out)["added"] == 0
    with HubStore(out) as store:
        assert store.state_at(3) == {"v": 3}


def test_chip_uncertain_and_patch_provenance(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1}, node_extra={"patches": [{"op": "replace", "path": "/quam/v", "value": 1}]})
    run(root, 2, {"v": 2}, node_extra={"patches": [{"op": "replace", "path": "/quam/v", "value": 9}]})
    hub_build.build(root, out)
    assert all(r["flags"] & CHIP_UNCERTAIN for r in events(out))
    with HubStore(out) as store:
        assert [r[0] for r in store.conn.execute("SELECT proven FROM changes ORDER BY eid")] == [1, 0]
        assert [r[0] for r in store.conn.execute("SELECT patches_n FROM events ORDER BY eid")] == [1, 1]


def test_late_unknown_run_fails_without_wrong_diff(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 2, {"v": 2})
    hub_build.build(root, out)
    run(root, 1, {"v": 1})
    with pytest.raises(ValueError, match="precedes ledger head"):
        hub_build.build(root, out)
    assert len(events(out)) == 1


def test_state_pair_changed_during_read_is_rejected(tmp_path, monkeypatch):
    root = tmp_path / "archive"
    folder = run(root, 1, {"v": 1})
    path_type = type(folder)
    original = path_type.read_bytes

    def changing(path):
        data = original(path)
        if path.name == "state.json":
            path.write_bytes(data + b" ")
        return data

    monkeypatch.setattr(path_type, "read_bytes", changing)
    with pytest.raises(ValueError, match="changed during read"):
        hub_build.read_pair(folder)


def test_long_array_patch_is_proven_under_shared_rules(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"array": [1.0] * 17}, node_extra={
        "patches": [{"op": "replace", "path": "/quam/array", "value": [1] * 17}]})
    hub_build.build(root, out)
    with HubStore(out) as store:
        assert store.conn.execute("SELECT proven FROM changes").fetchone()[0] == 1


def test_naive_run_bounds_share_archive_offset(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1}, clock="2026-01-01T12:00:01-04:00", node_extra={
        "metadata": {"run_start": "2026-01-01T12:00:00", "run_end": "2026-01-01T12:00:01"}})
    hub_build.build(root, out)
    row = events(out)[0]
    assert row["run_end_us"] == row["t_utc_us"]
    assert row["run_start_us"] == row["t_utc_us"] - 1_000_000


def test_discovery_is_frozen_and_next_build_catches_new_runs(tmp_path, monkeypatch):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1})
    run(root, 2, {"v": 2})
    original = HubStore.append
    calls = []

    def arriving(self, *a, **kw):
        result = original(self, *a, **kw)
        if not calls:
            run(root, 3, {"v": 3})
        calls.append(1)
        return result

    monkeypatch.setattr(HubStore, "append", arriving)
    assert hub_build.build(root, out)["events"] == 2
    monkeypatch.setattr(HubStore, "append", original)
    assert hub_build.build(root, out)["added"] == 1
    assert len(events(out)) == 3


def test_declared_chip_and_conflicting_identity_are_retained(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"extras": {"chip_name": "device"}, "v": 1})
    for i in (2, 3):
        run(root, i, {"extras": {"chip_name": "other-device"}, "v": 2})
    hub_build.build(root, out)
    assert [r["flags"] & CHIP_UNCERTAIN for r in events(out)] == [0, CHIP_UNCERTAIN, CHIP_UNCERTAIN]
    with HubStore(out) as store:
        assert json.loads(store.meta("chip_identity"))["name"] == "device"


def test_error_checkpoint_keeps_successful_head_and_replays_past_it(tmp_path):
    root, out = tmp_path / "archive", tmp_path / "ledger"
    run(root, 1, {"v": 1})
    broken = run(root, 2, {"v": 2}) / "quam_state" / "state.json"
    broken.unlink()
    run(root, 3, {"v": 3})
    hub_build.build(root, out, checkpoint_interval=2)
    with HubStore(out, checkpoint_interval=2) as store:
        cp = store.conn.execute("SELECT eid,hash FROM checkpoints").fetchone()
        assert cp is not None and cp["eid"] == 2
        assert store.blob(cp["hash"]) == {"v": 1}
        assert store.state_at(3) == {"v": 3}
        with pytest.raises(ValueError, match="no saved state"):
            store.state_at(2)
