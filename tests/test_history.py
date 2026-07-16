"""Tests for quam_state_manager.core.history.HistoryManager.

Covers snapshot creation, no-op when unchanged, list ordering, load from
snapshot, diff between snapshots, diff vs current, auto-prune, corrupted
meta.json handling, and LRU cache eviction.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.core.history import HistoryManager, SnapshotMeta
from quam_state_manager.core.loader import QuamStore


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _base_state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "T1": 8834,
                "grid_location": "0,2",
                "xy": {"RF_frequency": 6.25e9},
                "resonator": {"f_01": 7.64e9},
                "z": {"joint_offset": 0.081},
            },
        },
        "qubit_pairs": {},
    }


def _base_wiring():
    return {
        "wiring": {
            "qubits": {
                "qA1": {"xy": {"opx_output": "MW-FEM/1/2"}},
            },
        },
        "network": {"host": "10.1.1.18"},
    }


def _write_quam_state(folder: Path, state: dict, wiring: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=2), encoding="utf-8")


@pytest.fixture
def quam_path(tmp_path: Path) -> Path:
    """Create a synthetic quam_state folder inside an experiment-like parent."""
    path = tmp_path / "experiment_01" / "quam_state"
    _write_quam_state(path, _base_state(), _base_wiring())
    return path


@pytest.fixture
def hm(tmp_path: Path) -> HistoryManager:
    """A HistoryManager writing to a tmp instance folder."""
    return HistoryManager(tmp_path / "instance", max_snapshots=50, cache_size=3)


# ---------------------------------------------------------------------------
# Snapshot creation
# ---------------------------------------------------------------------------


def test_first_snapshot_creates_history_dir(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "auto")
    assert meta is not None
    assert meta.trigger == "auto"
    hist_dir = hm._history_dir(quam_path)
    assert hist_dir.is_dir()
    snap_dirs = [d for d in hist_dir.iterdir() if d.is_dir()]
    assert len(snap_dirs) == 1
    assert (snap_dirs[0] / "state.json").exists()
    assert (snap_dirs[0] / "wiring.json").exists()
    assert (snap_dirs[0] / "meta.json").exists()


def test_first_snapshot_has_zero_diff(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "auto")
    assert meta.diff_summary["total"] == 0


def test_snapshot_on_change(hm: HistoryManager, quam_path: Path):
    hm.check_and_snapshot(quam_path, "auto")

    # Modify state.json — sleep to ensure mtime changes (Windows has ~2s resolution)
    time.sleep(2.1)
    state = _base_state()
    state["qubits"]["qA1"]["f_01"] = 6.30e9
    (quam_path / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    meta = hm.check_and_snapshot(quam_path, "auto")
    assert meta is not None
    assert meta.diff_summary["modified"] >= 1
    assert meta.diff_summary["total"] >= 1


def test_no_snapshot_when_unchanged(hm: HistoryManager, quam_path: Path):
    hm.check_and_snapshot(quam_path, "auto")
    result = hm.check_and_snapshot(quam_path, "auto")
    assert result is None


def test_force_snapshot_even_when_unchanged(hm: HistoryManager, quam_path: Path):
    hm.check_and_snapshot(quam_path, "auto")
    result = hm.check_and_snapshot(quam_path, "manual", force=True)
    assert result is not None
    assert result.trigger == "manual"


def test_save_trigger(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "save")
    assert meta.trigger == "save"


# ---------------------------------------------------------------------------
# List snapshots
# ---------------------------------------------------------------------------


def test_list_snapshots_sorted_newest_first(hm: HistoryManager, quam_path: Path):
    hm.check_and_snapshot(quam_path, "auto", force=True)
    time.sleep(1.1)  # Ensure different timestamp
    hm.check_and_snapshot(quam_path, "manual", force=True)

    snapshots = hm.list_snapshots(quam_path)
    assert len(snapshots) == 2
    # Newest first
    assert snapshots[0].timestamp >= snapshots[1].timestamp


def test_list_snapshots_empty_when_no_history(hm: HistoryManager, quam_path: Path):
    assert hm.list_snapshots(quam_path) == []


# ---------------------------------------------------------------------------
# Load snapshot
# ---------------------------------------------------------------------------


def test_load_snapshot(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "auto")
    store = hm.load_snapshot(quam_path, meta.timestamp)
    assert "qubits" in store.merged
    assert store.qubit_names == ["qA1"]


def test_load_snapshot_cached(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "auto")
    store1 = hm.load_snapshot(quam_path, meta.timestamp)
    store2 = hm.load_snapshot(quam_path, meta.timestamp)
    assert store1 is store2


# ---------------------------------------------------------------------------
# Diff operations
# ---------------------------------------------------------------------------


def test_diff_current(hm: HistoryManager, quam_path: Path):
    hm.check_and_snapshot(quam_path, "auto")
    snapshots = hm.list_snapshots(quam_path)
    ts = snapshots[0].timestamp

    # No changes yet
    entries = hm.diff_current(quam_path, ts)
    assert len(entries) == 0

    # Modify state
    state = _base_state()
    state["qubits"]["qA1"]["f_01"] = 6.30e9
    (quam_path / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")

    entries = hm.diff_current(quam_path, ts)
    assert len(entries) >= 1
    paths = [e.dot_path for e in entries]
    assert any("f_01" in p for p in paths)


def test_diff_current_uses_current_store(hm: HistoryManager, quam_path: Path):
    """diff_current(current_store=...) diffs the store, never re-reading disk."""
    hm.check_and_snapshot(quam_path, "auto")
    ts = hm.list_snapshots(quam_path)[0].timestamp

    # Build a store matching the snapshot, then change the path on disk.
    current = QuamStore(quam_path)
    changed = _base_state()
    changed["qubits"]["qA1"]["f_01"] = 1.23e9
    (quam_path / "state.json").write_text(json.dumps(changed, indent=2), encoding="utf-8")

    # Diff vs the store: still matches the snapshot despite the disk change.
    assert hm.diff_current(quam_path, ts, current_store=current) == []
    # Diff vs the path (fallback) does see the disk change.
    assert len(hm.diff_current(quam_path, ts)) >= 1


def test_diff_snapshots(hm: HistoryManager, quam_path: Path):
    meta1 = hm.check_and_snapshot(quam_path, "auto")

    # Modify and create second snapshot. ``force=True`` because what we
    # are testing is ``diff_snapshots``, not change-detection; on tmpfs
    # the write_text below can otherwise land in the same microsecond
    # tick as meta1's stat read, making ``check_and_snapshot`` treat
    # the file as unchanged and skip the snapshot.
    state = _base_state()
    state["qubits"]["qA1"]["T1"] = 9999
    (quam_path / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    time.sleep(1.1)
    meta2 = hm.check_and_snapshot(quam_path, "auto", force=True)

    entries = hm.diff_snapshots(quam_path, meta1.timestamp, meta2.timestamp)
    assert len(entries) >= 1
    paths = [e.dot_path for e in entries]
    assert any("T1" in p for p in paths)


# ---------------------------------------------------------------------------
# Prune
# ---------------------------------------------------------------------------


def test_prune_oldest(tmp_path: Path, quam_path: Path):
    hm = HistoryManager(tmp_path / "instance", max_snapshots=3)

    for i in range(5):
        hm.check_and_snapshot(quam_path, "auto", force=True)
        time.sleep(1.1)

    snapshots = hm.list_snapshots(quam_path)
    assert len(snapshots) <= 3


# ---------------------------------------------------------------------------
# Corrupted / edge cases
# ---------------------------------------------------------------------------


def test_corrupted_meta_skipped(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "auto")

    # Corrupt the meta.json
    hist_dir = hm._history_dir(quam_path)
    snap_dir = hist_dir / meta.timestamp
    (snap_dir / "meta.json").write_text("NOT JSON", encoding="utf-8")

    hm.clear_cache()
    snapshots = hm.list_snapshots(quam_path)
    assert len(snapshots) == 0


def test_missing_meta_skipped(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "auto")

    # Delete meta.json
    hist_dir = hm._history_dir(quam_path)
    (hist_dir / meta.timestamp / "meta.json").unlink()

    hm.clear_cache()
    snapshots = hm.list_snapshots(quam_path)
    assert len(snapshots) == 0


# ---------------------------------------------------------------------------
# Cache eviction
# ---------------------------------------------------------------------------


def test_cache_eviction(tmp_path: Path, quam_path: Path):
    hm = HistoryManager(tmp_path / "instance", cache_size=2)

    # Create 3 snapshots
    timestamps = []
    for i in range(3):
        meta = hm.check_and_snapshot(quam_path, "auto", force=True)
        timestamps.append(meta.timestamp)
        time.sleep(1.1)

    # Load all 3 — the first should be evicted from cache
    hm.load_snapshot(quam_path, timestamps[0])
    hm.load_snapshot(quam_path, timestamps[1])
    hm.load_snapshot(quam_path, timestamps[2])

    # Cache should have at most 2 entries
    assert len(hm._store_cache) <= 2


# ---------------------------------------------------------------------------
# has_changed / get_last_mtime
# ---------------------------------------------------------------------------


def test_has_changed_true_on_first_check(hm: HistoryManager, quam_path: Path):
    assert hm.has_changed(quam_path) is True


def test_has_changed_false_after_snapshot(hm: HistoryManager, quam_path: Path):
    hm.check_and_snapshot(quam_path, "auto")
    assert hm.has_changed(quam_path) is False


def test_get_last_mtime_none_initially(hm: HistoryManager, quam_path: Path):
    assert hm.get_last_mtime(quam_path) is None


def test_get_last_mtime_set_after_snapshot(hm: HistoryManager, quam_path: Path):
    hm.check_and_snapshot(quam_path, "auto")
    mt = hm.get_last_mtime(quam_path)
    assert mt is not None
    assert isinstance(mt, tuple)
    assert len(mt) == 2


# ---------------------------------------------------------------------------
# SnapshotMeta fields
# ---------------------------------------------------------------------------


def test_meta_has_source_path(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "auto")
    assert str(quam_path.resolve()) == meta.source_path


def test_meta_has_file_sizes(hm: HistoryManager, quam_path: Path):
    meta = hm.check_and_snapshot(quam_path, "auto")
    assert meta.state_size > 0
    assert meta.wiring_size > 0


# ---------------------------------------------------------------------------
# Robustness — copy failures, missing source
# ---------------------------------------------------------------------------


def test_snapshot_returns_none_on_missing_source(hm: HistoryManager, tmp_path: Path):
    """If the source quam_state folder doesn't exist, snapshot returns None."""
    bogus = tmp_path / "does_not_exist" / "quam_state"
    result = hm.check_and_snapshot(bogus, "auto")
    assert result is None


def test_snapshot_no_orphaned_meta_on_copy_failure(hm: HistoryManager, quam_path: Path):
    """If file copy fails, meta.json should not be written (no orphaned snapshot)."""
    # Take a normal snapshot first
    hm.check_and_snapshot(quam_path, "auto")

    # Now delete state.json so copy will fail
    (quam_path / "state.json").unlink()

    # Force a new snapshot attempt — should fail gracefully
    import time
    time.sleep(1.1)
    result = hm.check_and_snapshot(quam_path, "auto", force=True)
    # Either returns None (copy failed) or raises — should not leave orphaned meta.json
    if result is None:
        hist_dir = hm._history_dir(quam_path)
        for snap_dir in (d for d in hist_dir.iterdir() if d.is_dir()):
            meta = snap_dir / "meta.json"
            if meta.exists():
                # Every meta.json should have corresponding data files
                assert (snap_dir / "state.json").exists() or not meta.exists()


# ---------------------------------------------------------------------------
# Param History — index, extraction, pointer resolution, backfill
# ---------------------------------------------------------------------------

from quam_state_manager.core.history import DEFAULT_MAX_SNAPSHOTS


def _trended_state(t1=30e-6, x180_amp=0.15, gate_fid=0.994):
    """A richer fixture with all the fields tracked by Param History."""
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "T1": t1,
                "T2ramsey": 22e-6,
                "T2echo": 28e-6,
                "grid_location": "0,2",
                "gate_fidelity": {"averaged": gate_fid, "x180": gate_fid - 0.001, "x90": gate_fid + 0.001},
                "xy": {
                    "RF_frequency": 6.25e9,
                    "operations": {
                        "x180_DragCosine": {"amplitude": x180_amp, "length": 40},
                        # x90 amplitude is a pointer to the source-of-truth x180 amplitude
                        "x90_DragCosine": {"amplitude": "#../x180_DragCosine/amplitude", "length": 40},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "operations": {"readout": {"amplitude": 0.04, "length": 1500, "threshold": 0.0}},
                    "confusion_matrix": [[0.97, 0.03], [0.05, 0.95]],
                },
                "z": {"joint_offset": 0.081},
            },
        },
        "qubit_pairs": {},
    }


@pytest.fixture
def trended_quam_path(tmp_path: Path) -> Path:
    path = tmp_path / "exp_full" / "quam_state"
    _write_quam_state(path, _trended_state(), _base_wiring())
    return path


def test_max_snapshots_default_is_100000():
    assert DEFAULT_MAX_SNAPSHOTS == 100_000


def test_snapshot_meta_has_experiment_fields(hm, trended_quam_path):
    meta = hm.check_and_snapshot(trended_quam_path, "auto")
    assert meta is not None
    assert meta.experiment_name is None
    assert meta.run_id is None
    assert meta.experiment_folder_path is None


def test_check_and_snapshot_accepts_experiment_kwargs(hm, trended_quam_path):
    meta = hm.check_and_snapshot(
        trended_quam_path, "experiment",
        experiment_name="qubit_spectroscopy", run_id=34,
        experiment_folder_path="/data/2026-04-30/#34_qubit_spectroscopy_173214",
        new_experiments=["qubit_spectroscopy"],
    )
    assert meta is not None
    assert meta.trigger == "experiment"
    assert meta.experiment_name == "qubit_spectroscopy"
    assert meta.run_id == 34
    assert meta.new_experiments == ["qubit_spectroscopy"]


def test_index_built_incrementally(hm, trended_quam_path):
    hm.check_and_snapshot(trended_quam_path, "auto")
    idx = hm._index_path(trended_quam_path)
    assert idx.exists()

    import sqlite3
    conn = sqlite3.connect(str(idx))
    rows1 = conn.execute("SELECT COUNT(*) FROM param_history").fetchone()[0]
    distinct_ts1 = conn.execute("SELECT COUNT(DISTINCT timestamp) FROM param_history").fetchone()[0]
    conn.close()
    assert rows1 > 0
    assert distinct_ts1 == 1

    time.sleep(1.1)
    state = _trended_state(t1=31e-6)
    (trended_quam_path / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    hm.check_and_snapshot(trended_quam_path, "auto")

    conn = sqlite3.connect(str(idx))
    distinct_ts2 = conn.execute("SELECT COUNT(DISTINCT timestamp) FROM param_history").fetchone()[0]
    conn.close()
    assert distinct_ts2 == 2


def test_index_self_heals_when_missing(hm, trended_quam_path):
    hm.check_and_snapshot(trended_quam_path, "auto")
    idx = hm._index_path(trended_quam_path)
    assert idx.exists()
    idx.unlink()
    assert not idx.exists()

    rows = hm.extract_property_history(trended_quam_path, ["T1"])
    assert idx.exists()
    assert any(r["qubit"] == "qA1" and r["property"] == "T1" for r in rows)


def test_extract_cache_buckets_now_relative_since_and_caps_lru(hm, trended_quam_path):
    from quam_state_manager.core.history import _EXTRACT_CACHE_CAP
    hm.check_and_snapshot(trended_quam_path, "save")

    # Two now-relative cutoffs in the SAME minute must share one cache entry —
    # otherwise every render (second-resolution cutoff) leaked a fresh, never-hit
    # entry and the cache grew unboundedly.
    hm._extract_history_cache.clear()
    hm.extract_property_history(trended_quam_path, ["T1"], since="20260710_143001_000")
    n1 = len(hm._extract_history_cache)
    hm.extract_property_history(trended_quam_path, ["T1"], since="20260710_143059_000")
    assert len(hm._extract_history_cache) == n1   # bucketed key → cache HIT, no new entry

    # LRU: distinct-minute cutoffs beyond the cap evict the oldest.
    for m in range(_EXTRACT_CACHE_CAP + 5):
        hm.extract_property_history(trended_quam_path, ["T1"],
                                    since=f"20260710_14{m:02d}00_000")
    assert len(hm._extract_history_cache) <= _EXTRACT_CACHE_CAP


def test_extract_property_history_resolves_pointer_amplitude(hm, trended_quam_path):
    # Snapshot 1: x180 amplitude = 0.15, x90 points to it
    hm.check_and_snapshot(trended_quam_path, "save")

    # Snapshot 2: change x180 to 0.20, x90 still points to it
    time.sleep(1.1)
    state = _trended_state(x180_amp=0.20)
    (trended_quam_path / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    hm.check_and_snapshot(trended_quam_path, "save")

    rows = hm.extract_property_history(
        trended_quam_path, ["x180_amplitude", "x90_amplitude"], qubit_filter=["qA1"],
    )
    by_prop = {r["property"]: r for r in rows}
    x180_vals = [v["value"] for v in by_prop["x180_amplitude"]["values"]]
    x90_vals = [v["value"] for v in by_prop["x90_amplitude"]["values"]]

    assert x180_vals == x90_vals  # x90 mirrors x180 via pointer
    assert sorted(x180_vals) == [0.15, 0.20]
    assert by_prop["x90_amplitude"]["raw_pointer"] == "#../x180_DragCosine/amplitude"
    assert by_prop["x180_amplitude"]["raw_pointer"] is None


def test_extract_property_history_filters_by_trigger(hm, trended_quam_path):
    hm.check_and_snapshot(trended_quam_path, "save")
    time.sleep(1.1)
    (trended_quam_path / "state.json").write_text(
        json.dumps(_trended_state(t1=31e-6), indent=2), encoding="utf-8")
    hm.check_and_snapshot(trended_quam_path, "experiment", run_id=42, experiment_name="rb")

    save_rows = hm.extract_property_history(
        trended_quam_path, ["T1"], qubit_filter=["qA1"], triggers=["save"])
    exp_rows = hm.extract_property_history(
        trended_quam_path, ["T1"], qubit_filter=["qA1"], triggers=["experiment"])

    assert len(save_rows[0]["values"]) == 1
    assert save_rows[0]["values"][0]["trigger"] == "save"
    assert len(exp_rows[0]["values"]) == 1
    assert exp_rows[0]["values"][0]["trigger"] == "experiment"
    assert exp_rows[0]["values"][0]["run_id"] == 42


def test_extract_property_history_filters_by_date_range(hm, trended_quam_path):
    hm.check_and_snapshot(trended_quam_path, "save")
    time.sleep(1.1)
    (trended_quam_path / "state.json").write_text(
        json.dumps(_trended_state(t1=31e-6), indent=2), encoding="utf-8")
    hm.check_and_snapshot(trended_quam_path, "save")

    snaps = hm.list_snapshots(trended_quam_path)
    # snapshots are newest-first; use the older one's timestamp as `since` cutoff
    cutoff = snaps[0].timestamp  # only the newest is >= cutoff
    rows = hm.extract_property_history(trended_quam_path, ["T1"], since=cutoff)
    assert len(rows[0]["values"]) == 1


def test_extract_property_history_downsample_lttb(hm, tmp_path):
    # Build many snapshots quickly via the synthetic-timestamp path of backfill
    # by directly inserting into the index. Easier: just call _lttb_downsample.
    pts = [(f"{i:020d}", float(i % 50)) for i in range(2000)]
    out = HistoryManager._lttb_downsample(pts, 100)
    assert len(out) == 100
    # First and last are always preserved
    assert out[0][0] == pts[0][0]
    assert out[-1][0] == pts[-1][0]


def test_backfill_from_workspace_ingests_experiment_folders(hm, trended_quam_path, tmp_path):
    # Build a fake workspace tree with three experiment runs that each
    # have their own quam_state/ folder.
    ws_root = tmp_path / "data_root"
    for run_id, name, when in [(10, "rabi", "120000"), (11, "ramsey", "121000"), (12, "rb", "122000")]:
        run = ws_root / "2026-04-30" / f"#{run_id}_{name}_{when}"
        qs = run / "quam_state"
        _write_quam_state(qs, _trended_state(t1=run_id * 1e-6), _base_wiring())
        node = run / "node.json"
        node.write_text(json.dumps({
            "id": run_id,
            "created_at": f"2026-04-30T{when[:2]}:{when[2:4]}:{when[4:]}",
            "metadata": {"name": name, "status": "completed"},
        }), encoding="utf-8")

    from quam_state_manager.core.scanner import Workspace
    ws = Workspace()
    ws.add_root(ws_root)

    report = hm.backfill_from_workspace(trended_quam_path, ws)
    assert report["ingested"] == 3
    assert report["skipped_renamed"] == 0
    assert report["skipped_different"] == 0

    # Idempotent: running again ingests nothing new
    report2 = hm.backfill_from_workspace(trended_quam_path, ws)
    assert report2["ingested"] == 0

    rows = hm.extract_property_history(trended_quam_path, ["T1"], qubit_filter=["qA1"])
    pts = rows[0]["values"]
    assert len(pts) == 3
    assert all(p["trigger"] == "experiment" for p in pts)
    assert {p["run_id"] for p in pts} == {10, 11, 12}


# ---------------------------------------------------------------------------
# Backfill failure capture (drives the Param History failed-import banner +
# the JS loop guard against re-firing the auto-backfill when ingest fails)
# ---------------------------------------------------------------------------


def _ws_with_runs(tmp_path: Path, runs: list[tuple[int, str, str]]):
    """Build a workspace tree of fully-valid experiment runs. Returns
    ``(ws_root, [run_dirs...])``. Each run gets a valid quam_state +
    node.json so it passes alignment; tests can then inject a failure
    later in the ingest path via monkeypatch."""
    from quam_state_manager.core.scanner import Workspace
    ws_root = tmp_path / "data_root"
    run_dirs = []
    for run_id, name, when in runs:
        run = ws_root / "2026-04-30" / f"#{run_id}_{name}_{when}"
        qs = run / "quam_state"
        _write_quam_state(qs, _trended_state(t1=run_id * 1e-6), _base_wiring())
        (run / "node.json").write_text(json.dumps({
            "id": run_id,
            "created_at": f"2026-04-30T{when[:2]}:{when[2:4]}:{when[4:]}",
            "metadata": {"name": name, "status": "completed"},
        }), encoding="utf-8")
        run_dirs.append(qs)
    ws = Workspace()
    ws.add_root(ws_root)
    return ws, run_dirs


def test_backfill_records_failed_entries_when_state_missing(hm, trended_quam_path, tmp_path):
    """Source state.json existing at scan time but missing at ingest time
    (a real TOCTOU: experiment program could have moved/renamed it
    between alignment scan and the copy step). Must be captured in
    ``failed_entries`` so the workspace-vs-index gap is visible and the
    UI loop guard can break the auto-backfill cycle."""
    ws, run_dirs = _ws_with_runs(tmp_path, [
        (30, "valid", "120000"),
        (31, "vanishing", "120100"),
    ])

    # Simulate TOCTOU: after the workspace scan but before the backfill
    # reaches ingest, the second entry's state.json disappears.
    # Backfill alignment uses _cached_fingerprint, which was already
    # populated by ws.add_root → scanner walk; deleting just before
    # backfill_from_workspace means alignment still sees it as ALIGNED
    # (cached fingerprint), then ingest fails the existence check.
    # NOTE: alignment caches by (state_mtime, wiring_mtime). To avoid
    # the cache being invalidated by the deletion (mtime changes on the
    # parent dir, not on the file itself), we touch the cache manually
    # by calling scan_workspace_alignment once first.
    hm.scan_workspace_alignment(trended_quam_path, ws)
    (run_dirs[1] / "state.json").unlink()
    (run_dirs[1] / "wiring.json").unlink()

    report = hm.backfill_from_workspace(trended_quam_path, ws)

    # The valid one ingested; the vanishing one is reported as a failure
    # rather than silently dropped. (It may also be re-categorized as
    # ``unknown`` by alignment if the cache missed — accept either path
    # as long as we account for it explicitly somewhere.)
    assert report["ingested"] == 1
    accounted = report["failed_count"] + report.get("skipped_unknown", 0)
    assert accounted >= 1, (
        f"vanishing entry should be in failed_entries or skipped_unknown; "
        f"report={report}"
    )
    if report["failed_count"] >= 1:
        failures = report["failed_entries"]
        assert any(
            f["run_id"] == "#31" and "state.json not found" in f["reason"]
            for f in failures
        ), failures


def test_backfill_records_failed_entries_when_write_raises(
    hm, trended_quam_path, tmp_path, monkeypatch,
):
    """Ingest-time copy failure (the realistic case behind the loop bug:
    file locked by an active experiment writeback on Windows, etc.).
    The entry passes alignment but ``safe_io.write_state_wiring`` raises
    inside ``_ingest_entries_into``. The failure must surface in
    ``failed_entries`` and the SQLite index must stay clean (otherwise
    a repeat backfill would race against the same broken state)."""
    ws, run_dirs = _ws_with_runs(tmp_path, [
        (40, "valid", "120000"),
        (41, "locked", "120100"),
    ])

    locked_state_path = (run_dirs[1] / "state.json").resolve()

    from quam_state_manager.core import history as history_mod
    real_write = history_mod.safe_io.write_state_wiring

    def fake_write(target_dir, state, wiring):
        # Raise only when ingesting the "locked" entry — identify by
        # the snap-dir's parent matching the failing source's parent name.
        # Cleaner: raise whenever the state we're being asked to write
        # matches the locked entry's state.
        from quam_state_manager.core import safe_io as _si
        # Identify the offending entry by comparing the state content.
        if state.get("qubits", {}).get("qA1", {}).get("T1") == 41 * 1e-6:
            raise OSError(
                "Permission denied: file locked by active experiment"
            )
        return real_write(target_dir, state, wiring)

    monkeypatch.setattr(history_mod.safe_io, "write_state_wiring", fake_write)

    report = hm.backfill_from_workspace(trended_quam_path, ws)

    assert report["ingested"] == 1
    assert report["failed_count"] == 1
    failures = report["failed_entries"]
    assert len(failures) == 1
    assert failures[0]["run_id"] == "#41"
    # Reason mentions the read/copy failure with the exception class so
    # the banner can show actionable detail.
    assert "read/copy failed" in failures[0]["reason"]
    assert "Permission denied" in failures[0]["reason"]

    # Crucially: only the valid entry shows up as an experiment-trigger row.
    rows = hm.extract_property_history(trended_quam_path, ["T1"], qubit_filter=["qA1"])
    pts = rows[0]["values"] if rows else []
    exp_pts = [p for p in pts if p["trigger"] == "experiment"]
    assert len(exp_pts) == 1
    assert exp_pts[0]["run_id"] == 40


# ---------------------------------------------------------------------------
# Multi-chip identity, alignment, and chip discovery
# ---------------------------------------------------------------------------

from quam_state_manager.core.history import (
    chip_name_for, fingerprint_of, align,
    ALIGN_ALIGNED, ALIGN_RENAMED, ALIGN_DIFFERENT_CHIP, ALIGN_UNKNOWN,
)


class TestChipIdentity:
    def test_chip_name_per_experiment_layout(self, tmp_path):
        # <workspace>/<chip>/<date>/#N_<exp>_HHMMSS/quam_state/
        p = tmp_path / "ExampleChip9Q" / "ExampleChip 1Q" / "2026-03-30" / "#4_03_resonator_spectroscopy_single_202031" / "quam_state"
        p.mkdir(parents=True)
        assert chip_name_for(p) == "ExampleChip 1Q"

    def test_chip_name_standalone_layout(self, tmp_path):
        p = tmp_path / "quam_state_examplechip_variantb" / "quam_state"
        p.mkdir(parents=True)
        assert chip_name_for(p) == "quam_state_examplechip_variantb"

    def test_chip_name_falls_back_when_no_date_folder(self, tmp_path):
        # parent matches per-exp pattern but parent.parent isn't a date — fall back
        p = tmp_path / "ExampleChip 1Q" / "junk_folder" / "#4_03_some_exp_120000" / "quam_state"
        p.mkdir(parents=True)
        assert chip_name_for(p) == "#4_03_some_exp_120000"

    def test_key_for_consolidates_per_experiment_loads(self, hm, tmp_path):
        # Two different per-experiment loads of the same chip should yield the same key.
        a = tmp_path / "ws" / "MyChip" / "2026-04-30" / "#1_one_120000" / "quam_state"
        b = tmp_path / "ws" / "MyChip" / "2026-04-30" / "#2_two_130000" / "quam_state"
        for p in (a, b):
            _write_quam_state(p, _trended_state(), _base_wiring())
        assert hm._key_for(a) == hm._key_for(b)
        assert hm._key_for(a) == "MyChip"


class TestChipFingerprint:
    def _write_chip(self, tmp_path, *, host="10.1.1.18", cluster="cluster_A",
                    qubits=("qA1", "qA2"), pairs=("qA1-qA2",)):
        p = tmp_path / "chip" / "quam_state"
        p.mkdir(parents=True, exist_ok=True)
        state = {
            "qubits": {q: {"id": q, "T1": None} for q in qubits},
            "qubit_pairs": {pair: {"id": pair} for pair in pairs},
        }
        wiring = {"network": {"host": host, "cluster_name": cluster, "extra": "ignored"}}
        (p / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (p / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        return p

    def test_fingerprint_reads_network_and_qubits(self, tmp_path):
        p = self._write_chip(tmp_path, qubits=("q0", "q1"), pairs=())
        fp = fingerprint_of(p)
        assert fp is not None
        assert dict(fp.network).get("host") == "10.1.1.18"
        assert dict(fp.network).get("cluster_name") == "cluster_A"
        assert fp.qubits == frozenset({"q0", "q1"})

    def test_fingerprint_returns_none_when_state_missing(self, tmp_path):
        p = tmp_path / "missing" / "quam_state"
        p.mkdir(parents=True)
        assert fingerprint_of(p) is None

    def test_align_aligned(self, tmp_path):
        p1 = self._write_chip(tmp_path / "a", qubits=("q0", "q1"))
        p2 = self._write_chip(tmp_path / "b", qubits=("q0", "q1"))
        assert align(fingerprint_of(p1), fingerprint_of(p2)) == ALIGN_ALIGNED

    def test_align_renamed_same_hardware_different_qubits(self, tmp_path):
        p1 = self._write_chip(tmp_path / "a", qubits=("q0", "q1"))
        p2 = self._write_chip(tmp_path / "b", qubits=("qA0", "qA1"))  # same network, renamed
        assert align(fingerprint_of(p1), fingerprint_of(p2)) == ALIGN_RENAMED

    def test_align_different_chip(self, tmp_path):
        p1 = self._write_chip(tmp_path / "a", host="10.1.1.18")
        p2 = self._write_chip(tmp_path / "b", host="10.2.2.20")
        assert align(fingerprint_of(p1), fingerprint_of(p2)) == ALIGN_DIFFERENT_CHIP

    def test_align_unknown_when_one_missing(self, tmp_path):
        p1 = self._write_chip(tmp_path / "a")
        assert align(None, fingerprint_of(p1)) == ALIGN_UNKNOWN
        assert align(fingerprint_of(p1), None) == ALIGN_UNKNOWN


class TestWorkspaceAlignmentScan:
    def _make_workspace_with_two_chips(self, tmp_path):
        from quam_state_manager.core.scanner import Workspace
        ws_root = tmp_path / "data_root"

        # Three chip-A experiments (ExampleChip 1Q with q0..q2) — use slightly
        # different T1 per run so content hashes differ (dedup logic skips
        # bytewise-identical state.json files).
        for run_id, when in [(10, "120000"), (11, "121000"), (12, "122000")]:
            run = ws_root / "ExampleChip 1Q" / "2026-04-30" / f"#{run_id}_alpha_{when}"
            qs = run / "quam_state"
            _write_quam_state(qs, {
                "qubits": {q: {"id": q, "T1": run_id * 1e-6} for q in ("q0", "q1", "q2")},
                "qubit_pairs": {},
            }, {"network": {"host": "10.1.1.18", "cluster_name": "A"}})
            (run / "node.json").write_text(json.dumps({
                "id": run_id, "created_at": f"2026-04-30T{when[:2]}:{when[2:4]}:{when[4:]}",
                "metadata": {"name": "alpha", "status": "completed"},
            }), encoding="utf-8")

        # Two chip-B experiments (different host) — also unique state per run
        for run_id, when in [(20, "130000"), (21, "131000")]:
            run = ws_root / "LabB_1Q" / "2026-04-30" / f"#{run_id}_beta_{when}"
            qs = run / "quam_state"
            _write_quam_state(qs, {
                "qubits": {q: {"id": q, "T1": run_id * 1e-6} for q in ("q0", "q1")},
                "qubit_pairs": {},
            }, {"network": {"host": "10.9.9.99", "cluster_name": "B"}})
            (run / "node.json").write_text(json.dumps({
                "id": run_id, "created_at": f"2026-04-30T{when[:2]}:{when[2:4]}:{when[4:]}",
                "metadata": {"name": "beta", "status": "completed"},
            }), encoding="utf-8")

        # One renamed chip-A experiment (same host, qubits renamed q0→qA0)
        renamed_run = ws_root / "ExampleChip 1Q" / "2026-04-30" / "#30_renamed_140000"
        qs = renamed_run / "quam_state"
        _write_quam_state(qs, {
            "qubits": {q: {"id": q} for q in ("qA0", "qA1", "qA2")},
            "qubit_pairs": {},
        }, {"network": {"host": "10.1.1.18", "cluster_name": "A"}})
        (renamed_run / "node.json").write_text(json.dumps({
            "id": 30, "created_at": "2026-04-30T14:00:00",
            "metadata": {"name": "renamed", "status": "completed"},
        }), encoding="utf-8")

        ws = Workspace()
        ws.add_root(ws_root)
        return ws

    def test_scan_groups_aligned_renamed_and_different(self, hm, tmp_path):
        ws = self._make_workspace_with_two_chips(tmp_path)
        # Loaded chip = ExampleChip 1Q live state with q0..q2 (aligned baseline)
        loaded = tmp_path / "loaded" / "ExampleChip 1Q" / "quam_state"
        _write_quam_state(loaded, {
            "qubits": {q: {"id": q} for q in ("q0", "q1", "q2")},
            "qubit_pairs": {},
        }, {"network": {"host": "10.1.1.18", "cluster_name": "A"}})

        scan = hm.scan_workspace_alignment(loaded, ws)
        assert scan["counts"]["aligned"] == 3       # 3 chip-A experiments
        assert scan["counts"]["renamed"] == 1       # 1 same-host renamed
        assert scan["counts"]["different_chip"] == 2  # 2 chip-B experiments
        assert "LabB_1Q" in scan["different_chip"]

    def test_backfill_routes_different_chip_to_native_dir(self, hm, tmp_path):
        """different_chip groups are auto-routed to their own chip dir
        (was: silently skipped). The alignment-banner 'view <chip>' link
        now lands on populated data."""
        ws = self._make_workspace_with_two_chips(tmp_path)
        loaded = tmp_path / "loaded" / "ExampleChip 1Q" / "quam_state"
        _write_quam_state(loaded, {
            "qubits": {q: {"id": q} for q in ("q0", "q1", "q2")},
            "qubit_pairs": {},
        }, {"network": {"host": "10.1.1.18", "cluster_name": "A"}})

        report = hm.backfill_from_workspace(loaded, ws)
        # Loaded chip dir gets the 3 aligned entries
        assert report["ingested"] == 3
        assert report["skipped_renamed"] == 1
        # 'different_chip' (host=10.9.9.99, "LabB_1Q") goes to its own dir
        assert "LabB_1Q" in report["other_chips"]
        assert report["other_chips"]["LabB_1Q"]["ingested"] == 2
        # Nothing left "skipped" after routing — they were ingested elsewhere
        assert report["skipped_different"] == 0

    def test_backfill_with_force_renamed_includes_renamed(self, hm, tmp_path):
        ws = self._make_workspace_with_two_chips(tmp_path)
        loaded = tmp_path / "loaded" / "ExampleChip 1Q" / "quam_state"
        _write_quam_state(loaded, {
            "qubits": {q: {"id": q} for q in ("q0", "q1", "q2")},
            "qubit_pairs": {},
        }, {"network": {"host": "10.1.1.18", "cluster_name": "A"}})

        report = hm.backfill_from_workspace(loaded, ws, force_renamed=True)
        assert report["ingested"] == 4              # 3 aligned + 1 renamed
        assert report["skipped_renamed"] == 0
        # different_chip still routed to its own dir
        assert report["other_chips"]["LabB_1Q"]["ingested"] == 2

    def test_progress_cb_fires_per_entry_across_chip_groups(self, hm, tmp_path):
        """progress_cb should tick monotonically as entries are processed,
        across both the loaded-chip group AND each different_chip group —
        not stall + jump to 100% at the end."""
        ws = self._make_workspace_with_two_chips(tmp_path)
        loaded = tmp_path / "loaded" / "ExampleChip 1Q" / "quam_state"
        _write_quam_state(loaded, {
            "qubits": {q: {"id": q} for q in ("q0", "q1", "q2")},
            "qubit_pairs": {},
        }, {"network": {"host": "10.1.1.18", "cluster_name": "A"}})

        ticks: list[tuple[int, int]] = []
        hm.backfill_from_workspace(loaded, ws, progress_cb=lambda d, t: ticks.append((d, t)))

        # 3 aligned (loaded) + 1 renamed (skipped) + 2 different_chip = 5 visible
        # to the progress; we should see at least 5 ticks (one per processed entry).
        assert len(ticks) >= 5
        # All ticks share the same total
        totals = {t for _, t in ticks}
        assert len(totals) == 1
        # Ticks are monotonically non-decreasing
        for prev, curr in zip(ticks, ticks[1:]):
            assert curr[0] >= prev[0]
        # Final tick reaches the total
        assert ticks[-1][0] == ticks[-1][1]

    def test_other_chip_dir_is_browsable_after_backfill(self, hm, tmp_path):
        """End-to-end: user's reported scenario — load chip A, backfill a
        workspace that contains chip B experiments. Click chip B in the
        chip selector → it should have data, not 'No history yet'."""
        ws = self._make_workspace_with_two_chips(tmp_path)
        loaded = tmp_path / "loaded" / "ExampleChip 1Q" / "quam_state"
        _write_quam_state(loaded, {
            "qubits": {q: {"id": q} for q in ("q0", "q1", "q2")},
            "qubit_pairs": {},
        }, {"network": {"host": "10.1.1.18", "cluster_name": "A"}})

        hm.backfill_from_workspace(loaded, ws)

        # The OTHER chip's dir should now be populated and queryable
        # via extract_property_history with chip_key=LabB_1Q.
        other_dir = hm._root / "LabB_1Q"
        assert other_dir.is_dir()
        # Use the same path-trick the route uses to query a non-loaded chip
        synth_labb_path = Path("/__chip_key__") / "LabB_1Q" / "quam_state"
        rows = hm.extract_property_history(synth_labb_path, ["T1"])
        # 2 different_chip entries → 2 snapshots → 2 timestamps with values
        timestamps_seen = {p["timestamp"] for r in rows for p in r["values"]}
        assert len(timestamps_seen) == 2


class TestListChipHistories:
    def test_skips_pytest_and_temp(self, hm, trended_quam_path):
        # Create a real chip history
        hm.check_and_snapshot(trended_quam_path, "save")
        # Synthesize fake pytest + Temp dirs that should be filtered out
        (hm._root / "pytest-99").mkdir(parents=True, exist_ok=True)
        (hm._root / "pytest-99" / "index.sqlite").write_text("")  # garbage but indexable
        (hm._root / "Temp").mkdir(parents=True, exist_ok=True)

        chips = hm.list_chip_histories()
        keys = {c["key"] for c in chips}
        assert "pytest-99" not in keys
        assert "Temp" not in keys

    def test_skips_dirs_without_index(self, hm, trended_quam_path):
        hm.check_and_snapshot(trended_quam_path, "save")
        (hm._root / "no_index_chip").mkdir(parents=True, exist_ok=True)
        chips = hm.list_chip_histories()
        keys = {c["key"] for c in chips}
        assert "no_index_chip" not in keys

    def test_returns_metadata_sorted_by_recency(self, hm, tmp_path):
        # Make two chip histories with different timestamps
        chip_a = tmp_path / "ws" / "ChipA" / "quam_state"
        chip_b = tmp_path / "ws" / "ChipB" / "quam_state"
        _write_quam_state(chip_a, _trended_state(), _base_wiring())
        _write_quam_state(chip_b, _trended_state(), _base_wiring())

        hm.check_and_snapshot(chip_a, "save")
        time.sleep(1.1)
        hm.check_and_snapshot(chip_b, "save")

        chips = hm.list_chip_histories()
        keys_in_order = [c["key"] for c in chips]
        # ChipB was snapshotted after ChipA, so it should come first
        idx_a = keys_in_order.index("ChipA")
        idx_b = keys_in_order.index("ChipB")
        assert idx_b < idx_a
        # Each entry has the expected metadata
        for c in chips:
            assert "snapshot_count" in c and c["snapshot_count"] >= 1
            assert "latest_timestamp" in c
            assert "qubits" in c


# ---------------------------------------------------------------------------
# Legacy history migration
# ---------------------------------------------------------------------------

from quam_state_manager.core.history import migrate_legacy_histories


class TestLegacyMigration:
    def _make_legacy_snapshot(self, legacy_dir, ts, source_path, qubit_state):
        """Create a synthetic snapshot inside a legacy-keyed dir."""
        snap = legacy_dir / ts
        snap.mkdir(parents=True)
        (snap / "state.json").write_text(json.dumps({
            "qubits": {q: {"id": q} for q in qubit_state},
            "qubit_pairs": {},
        }), encoding="utf-8")
        (snap / "wiring.json").write_text(json.dumps({
            "network": {"host": "10.1.1.1", "cluster_name": "C1"},
        }), encoding="utf-8")
        (snap / "meta.json").write_text(json.dumps({
            "timestamp": ts,
            "trigger": "experiment",
            "diff_summary": {"added": 0, "removed": 0, "modified": 0, "total": 0},
            "new_experiments": [],
            "source_path": source_path,
            "state_size": 0,
            "wiring_size": 0,
            "experiment_name": "exp",
            "run_id": 1,
            "experiment_folder_path": str(Path(source_path).parent),
        }), encoding="utf-8")
        return snap

    def _seed_legacy_index(self, legacy_dir, timestamps_with_qubit):
        """Initialize a SQLite index in the legacy dir with rows for each timestamp."""
        import sqlite3
        idx = legacy_dir / "index.sqlite"
        conn = sqlite3.connect(str(idx))
        conn.execute("""
            CREATE TABLE param_history (
                timestamp TEXT NOT NULL,
                qubit TEXT NOT NULL,
                property TEXT NOT NULL,
                value REAL,
                raw_pointer TEXT,
                trigger TEXT NOT NULL,
                run_id INTEGER,
                experiment TEXT,
                PRIMARY KEY (timestamp, qubit, property)
            )
        """)
        for ts, q in timestamps_with_qubit:
            conn.execute(
                "INSERT INTO param_history VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (ts, q, "T1", 30e-6, None, "experiment", 1, "exp"),
            )
        conn.commit()
        conn.close()
        return idx

    def test_migration_moves_snapshots_to_proper_chip_key(self, tmp_path):
        # Build a legacy fragmented dir with two snapshots whose source_path
        # belongs to a chip called "ExampleChip 1Q"
        instance = tmp_path / "instance"
        history = instance / "history"
        legacy = history / "_4_03_resonator_spectroscopy_single_202031"
        legacy.mkdir(parents=True)

        # The "real" chip path that source_path will point at
        chip_root = tmp_path / "ws" / "ExampleChip 1Q"
        per_exp = chip_root / "2026-04-30" / "#4_resonator_120000" / "quam_state"
        per_exp.mkdir(parents=True)

        ts1 = "20260430_120000_001"
        ts2 = "20260430_120100_002"
        self._make_legacy_snapshot(legacy, ts1, str(per_exp), ("q0", "q1"))
        self._make_legacy_snapshot(legacy, ts2, str(per_exp), ("q0", "q1"))
        self._seed_legacy_index(legacy, [(ts1, "q0"), (ts2, "q0")])

        report = migrate_legacy_histories(instance)

        assert report["status"] == "migrated"
        assert report["moved"] == 2
        assert "_4_03_resonator_spectroscopy_single_202031" in report["backed_up"]
        # Snapshots now live under "ExampleChip_1Q"
        target = history / "ExampleChip_1Q"
        assert (target / ts1).is_dir()
        assert (target / ts2).is_dir()
        # Index has the migrated rows
        import sqlite3
        conn = sqlite3.connect(str(target / "index.sqlite"))
        rows = conn.execute("SELECT timestamp FROM param_history ORDER BY timestamp").fetchall()
        conn.close()
        assert {r[0] for r in rows} == {ts1, ts2}
        # Backup folder exists
        backup = instance / "history_legacy_backup"
        assert (backup / "_4_03_resonator_spectroscopy_single_202031").is_dir()

    def test_migration_handles_mixed_chips_in_one_legacy_dir(self, tmp_path):
        # A legacy dir containing snapshots from TWO different chips
        # (this happens when the user backfilled a workspace with multiple chips)
        instance = tmp_path / "instance"
        history = instance / "history"
        legacy = history / "_5_test_140000"
        legacy.mkdir(parents=True)

        chip_a = tmp_path / "ws" / "ChipA" / "2026-04-30" / "#1_x_100000" / "quam_state"
        chip_b = tmp_path / "ws" / "ChipB" / "2026-04-30" / "#2_y_110000" / "quam_state"
        chip_a.mkdir(parents=True)
        chip_b.mkdir(parents=True)

        ts_a = "20260430_100000_001"
        ts_b = "20260430_110000_002"
        self._make_legacy_snapshot(legacy, ts_a, str(chip_a), ("q0",))
        self._make_legacy_snapshot(legacy, ts_b, str(chip_b), ("qA0",))
        self._seed_legacy_index(legacy, [(ts_a, "q0"), (ts_b, "qA0")])

        report = migrate_legacy_histories(instance)

        assert report["status"] == "migrated"
        assert report["moved"] == 2
        # Each snapshot landed in its own chip dir
        assert (history / "ChipA" / ts_a).is_dir()
        assert (history / "ChipB" / ts_b).is_dir()

    def test_migration_idempotent(self, tmp_path):
        instance = tmp_path / "instance"
        history = instance / "history"
        history.mkdir(parents=True)

        first = migrate_legacy_histories(instance)
        assert first["status"] in ("nothing_to_migrate", "migrated")
        # Flag created
        assert (instance / "migrated_v1.flag").exists()

        # Second call is a no-op
        second = migrate_legacy_histories(instance)
        assert second["status"] == "already_migrated"

    def test_migration_skips_when_target_timestamp_already_exists(self, tmp_path):
        instance = tmp_path / "instance"
        history = instance / "history"
        legacy = history / "_4_03_some_exp_120000"
        legacy.mkdir(parents=True)

        chip_root = tmp_path / "ws" / "MyChip"
        per_exp = chip_root / "2026-04-30" / "#4_x_120000" / "quam_state"
        per_exp.mkdir(parents=True)

        # Pre-create the target chip dir with the same timestamp already present
        target = history / "MyChip"
        target.mkdir(parents=True)
        ts = "20260430_120000_001"
        (target / ts).mkdir()
        (target / ts / "state.json").write_text("{}", encoding="utf-8")

        # Now create the legacy snapshot with the same timestamp
        self._make_legacy_snapshot(legacy, ts, str(per_exp), ("q0",))
        self._seed_legacy_index(legacy, [(ts, "q0")])

        report = migrate_legacy_histories(instance)

        # The conflicting timestamp was skipped (target wins, legacy preserved)
        assert report["status"] == "migrated"
        assert report["moved"] == 0
        assert report["skipped"] == 1

    def test_migration_no_legacy_dirs_creates_flag(self, tmp_path):
        instance = tmp_path / "instance"
        history = instance / "history"
        history.mkdir(parents=True)
        # Create a properly-keyed dir that ISN'T legacy
        (history / "ExampleChip_1Q").mkdir()

        report = migrate_legacy_histories(instance)
        assert report["status"] == "nothing_to_migrate"
        assert (instance / "migrated_v1.flag").exists()
        # The properly-keyed dir is untouched
        assert (history / "ExampleChip_1Q").is_dir()


class TestBackfillSourcePath:
    """Verify backfill records each snapshot's true experiment path,
    NOT the chip the user happened to be loaded into at backfill time."""

    def test_backfill_source_path_is_entry_quam_state_not_loaded(
        self, hm, trended_quam_path, tmp_path,
    ):
        # Workspace with three experiments under a different folder structure
        from quam_state_manager.core.scanner import Workspace
        ws_root = tmp_path / "data_root"
        per_exp_paths = []
        for run_id, name, when in [(10, "exp_a", "120000"), (11, "exp_b", "121000")]:
            run = ws_root / "MyChip" / "2026-04-30" / f"#{run_id}_{name}_{when}"
            qs = run / "quam_state"
            _write_quam_state(qs, _trended_state(t1=run_id * 1e-6), _base_wiring())
            (run / "node.json").write_text(json.dumps({
                "id": run_id, "created_at": f"2026-04-30T{when[:2]}:{when[2:4]}:{when[4:]}",
                "metadata": {"name": name, "status": "completed"},
            }), encoding="utf-8")
            per_exp_paths.append(qs.resolve())

        ws = Workspace()
        ws.add_root(ws_root)

        # Backfill against trended_quam_path (the LOADED state). Each
        # ingested snapshot's source_path should be the per-experiment
        # entry's path — different per snapshot — NOT trended_quam_path.
        report = hm.backfill_from_workspace(trended_quam_path, ws)
        assert report["ingested"] == 2

        hist_dir = hm._history_dir(Path(trended_quam_path))
        sources_seen: set[str] = set()
        loaded_path = str(Path(trended_quam_path).resolve())
        for snap in hist_dir.iterdir():
            if not snap.is_dir():
                continue
            meta_p = snap / "meta.json"
            if not meta_p.exists():
                continue
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
            sp = meta.get("source_path")
            if sp:
                sources_seen.add(sp)
                # Critical: source_path must NEVER be the loaded path
                assert sp != loaded_path, (
                    f"backfill leaked LOADED path into source_path: {sp}"
                )
        # Each entry has its own distinct source_path
        expected = {str(p) for p in per_exp_paths}
        assert sources_seen == expected


class TestContentHashDedup:
    """Same state+wiring content should not produce duplicate snapshots."""

    def test_canonical_hash_ignores_whitespace_and_key_order(self, tmp_path):
        from quam_state_manager.core.history import _canonical_content_hash
        # Two semantically-equal files with different formatting
        a = tmp_path / "a"; a.mkdir()
        b = tmp_path / "b"; b.mkdir()
        (a / "state.json").write_text('{"qubits":{"q0":{"id":"q0"}},"qubit_pairs":{}}')
        (a / "wiring.json").write_text('{"network":{"host":"10.1.1.1"}}')
        (b / "state.json").write_text(
            '{\n  "qubit_pairs": {},\n  "qubits": {\n    "q0": {"id": "q0"}\n  }\n}'
        )
        (b / "wiring.json").write_text(
            '{"network": {"host": "10.1.1.1"}}'
        )
        h1 = _canonical_content_hash(a / "state.json", a / "wiring.json")
        h2 = _canonical_content_hash(b / "state.json", b / "wiring.json")
        assert h1 == h2 != None

    def test_data_folder_name_extracts_label(self, tmp_path):
        from quam_state_manager.core.history import _data_folder_name
        # Standard qualibration layout
        p = tmp_path / "LabB" / "graphs" / "data" / "LabB_1Q" / "2026-04-30" / "#1_x_120000" / "quam_state"
        p.mkdir(parents=True)
        assert _data_folder_name(p) == "LabB_1Q"
        # Path without 'data' segment
        p2 = tmp_path / "ExampleChip" / "quam_state"
        p2.mkdir(parents=True)
        assert _data_folder_name(p2) is None

    def test_dedup_skips_identical_content_in_check_and_snapshot(self, hm, quam_path):
        # First snapshot
        m1 = hm.check_and_snapshot(quam_path, "auto")
        assert m1 is not None
        assert m1.state_hash is not None
        # Second snapshot with no file changes — would normally be skipped via mtime,
        # but force=True bypasses mtime; dedup should NOT fire (force overrides).
        m2 = hm.check_and_snapshot(quam_path, "manual", force=True)
        assert m2 is not None  # force=True bypasses dedup
        # Now without force, identical content (same mtime, same content) → None
        # (mtime bypass doesn't apply since it didn't change)
        m3 = hm.check_and_snapshot(quam_path, "auto")
        assert m3 is None  # no file change

    def test_dedup_via_backfill(self, hm, trended_quam_path, tmp_path):
        from quam_state_manager.core.scanner import Workspace
        ws_root = tmp_path / "data_root"
        # Two experiments with IDENTICAL state.json content
        for run_id, when in [(10, "120000"), (11, "121000")]:
            run = ws_root / "MyChip" / "2026-04-30" / f"#{run_id}_exp_{when}"
            qs = run / "quam_state"
            _write_quam_state(qs, _trended_state(), _base_wiring())  # same content
            (run / "node.json").write_text(json.dumps({
                "id": run_id, "created_at": f"2026-04-30T{when[:2]}:{when[2:4]}:{when[4:]}",
                "metadata": {"name": "exp", "status": "completed"},
            }), encoding="utf-8")

        ws = Workspace()
        ws.add_root(ws_root)
        report = hm.backfill_from_workspace(trended_quam_path, ws)
        # 2 entries but identical content → only 1 ingested, 1 deduplicated
        assert report["ingested"] == 1
        assert report["skipped_duplicate"] == 1


class TestChipSwapAutoRouting:
    """check_and_snapshot routes new snapshots to a different chip dir
    when the loaded path's content fingerprint diverges from the existing
    chip dir's fingerprint."""

    def _write_quam(self, p, *, host, qubits):
        p.mkdir(parents=True, exist_ok=True)
        (p / "state.json").write_text(json.dumps({
            "qubits": {q: {"id": q} for q in qubits},
            "qubit_pairs": {},
        }), encoding="utf-8")
        (p / "wiring.json").write_text(json.dumps({
            "network": {"host": host, "cluster_name": "C"},
        }), encoding="utf-8")

    def test_swap_to_new_dir_when_fingerprint_diverges(self, hm, tmp_path):
        # First load: ExampleChip-style (host A, q0..q1)
        loaded = tmp_path / "shared" / "quam_state"
        self._write_quam(loaded, host="10.1.1.1", qubits=["q0", "q1"])
        m1 = hm.check_and_snapshot(loaded, "save")
        assert m1 is not None
        assert m1.chip_swap_detected is None  # first snapshot, no swap

        # Now overwrite the SAME path with a different chip (host B)
        time.sleep(1.1)
        self._write_quam(loaded, host="10.2.2.2", qubits=["qA0", "qA1"])
        m2 = hm.check_and_snapshot(loaded, "save")
        assert m2 is not None
        assert m2.chip_swap_detected is not None
        assert m2.chip_swap_detected["type"] == "swap_to_new"
        assert m2.chip_swap_detected["from_key"] == "shared"
        # The new dir should exist on disk
        new_key = m2.chip_swap_detected["to_key"]
        assert (hm._root / new_key).is_dir()

    def test_swap_to_existing_when_matching_dir_exists(self, hm, tmp_path):
        # Pre-create a chip-B dir by snapshotting a chip-B state at a different path
        chip_b_path = tmp_path / "chipB" / "quam_state"
        self._write_quam(chip_b_path, host="10.2.2.2", qubits=["qA0", "qA1"])
        # Add a unique field so this snapshot's hash differs from the next chip-B
        # one at `loaded` (otherwise dedup would skip the second one)
        sj = json.loads((chip_b_path / "state.json").read_text(encoding="utf-8"))
        sj["qubits"]["qA0"]["T1"] = 30e-6
        (chip_b_path / "state.json").write_text(json.dumps(sj), encoding="utf-8")
        m_b = hm.check_and_snapshot(chip_b_path, "save")
        assert m_b is not None

        # Now load the "shared" path with chip-A first
        loaded = tmp_path / "shared" / "quam_state"
        self._write_quam(loaded, host="10.1.1.1", qubits=["q0", "q1"])
        hm.check_and_snapshot(loaded, "save")

        # Switch to chip B at the SAME loaded path (with slightly different
        # T1 so the hash differs from the chipB snapshot above)
        time.sleep(1.1)
        self._write_quam(loaded, host="10.2.2.2", qubits=["qA0", "qA1"])
        sj = json.loads((loaded / "state.json").read_text(encoding="utf-8"))
        sj["qubits"]["qA0"]["T1"] = 35e-6
        (loaded / "state.json").write_text(json.dumps(sj), encoding="utf-8")
        m2 = hm.check_and_snapshot(loaded, "save")
        assert m2 is not None
        # Should have routed to the existing chipB dir, not create a new one
        assert m2.chip_swap_detected is not None
        assert m2.chip_swap_detected["type"] == "swap_to_existing"
        assert m2.chip_swap_detected["to_key"] == "chipB"

    def test_no_swap_when_content_matches(self, hm, tmp_path):
        loaded = tmp_path / "p" / "quam_state"
        self._write_quam(loaded, host="10.1.1.1", qubits=["q0"])
        hm.check_and_snapshot(loaded, "save")
        time.sleep(1.1)
        # Modify a non-fingerprint field so mtime changes but fingerprint same
        state = json.loads((loaded / "state.json").read_text(encoding="utf-8"))
        state["qubits"]["q0"]["T1"] = 30e-6  # only T1 changes; qubits/network identical
        (loaded / "state.json").write_text(json.dumps(state), encoding="utf-8")
        m2 = hm.check_and_snapshot(loaded, "save")
        assert m2 is not None
        assert m2.chip_swap_detected is None


class TestChipDecisionsAndAmbiguity:
    """Backfill defers ingest when wiring matches but data folder name differs
    from the loaded chip — until the user records a 'same' or 'different'
    decision via save_chip_decision."""

    def _make_chip_a_workspace(self, tmp_path):
        from quam_state_manager.core.scanner import Workspace
        ws_root = tmp_path / "data_root"
        # Loaded chip's data folder will be 'data/MyChip/'. Workspace also
        # contains 'data/SisterChip/' with the SAME wiring → ambiguous.
        for run_id, when, df in [(10, "120000", "MyChip"), (11, "121000", "SisterChip")]:
            run = ws_root / "data" / df / "2026-04-30" / f"#{run_id}_x_{when}"
            qs = run / "quam_state"
            _write_quam_state(qs, {
                "qubits": {q: {"id": q, "T1": run_id * 1e-6} for q in ("q0", "q1")},
                "qubit_pairs": {},
            }, {"network": {"host": "10.1.1.1", "cluster_name": "C"}})
            (run / "node.json").write_text(json.dumps({
                "id": run_id, "created_at": f"2026-04-30T{when[:2]}:{when[2:4]}:{when[4:]}",
                "metadata": {"name": "x", "status": "completed"},
            }), encoding="utf-8")
        ws = Workspace()
        ws.add_root(ws_root)
        return ws

    def _make_loaded(self, tmp_path):
        # Loaded path is in data/MyChip/...
        loaded = tmp_path / "data_root" / "data" / "MyChip" / "quam_state"
        _write_quam_state(loaded, {
            "qubits": {q: {"id": q, "T1": 25e-6} for q in ("q0", "q1")},
            "qubit_pairs": {},
        }, {"network": {"host": "10.1.1.1", "cluster_name": "C"}})
        return loaded

    def test_ambiguous_workspace_defers_with_pending_decisions(self, hm, tmp_path):
        ws = self._make_chip_a_workspace(tmp_path)
        loaded = self._make_loaded(tmp_path)

        report = hm.backfill_from_workspace(loaded, ws, instance_path=tmp_path / "instance")
        # MyChip experiments: ingest into loaded chip's dir
        # SisterChip experiments: deferred → pending_decisions
        assert report["ingested"] == 1   # the MyChip experiment
        assert report["skipped_pending_decision"] == 1
        assert len(report["pending_decisions"]) == 1
        pd = report["pending_decisions"][0]
        assert pd["data_folder"] == "SisterChip"
        assert pd["count"] == 1

    def test_decision_same_ingests_on_next_backfill(self, hm, tmp_path):
        from quam_state_manager.core.history import save_chip_decision
        ws = self._make_chip_a_workspace(tmp_path)
        loaded = self._make_loaded(tmp_path)
        instance_path = tmp_path / "instance"

        # First backfill: ambiguous, deferred
        report1 = hm.backfill_from_workspace(loaded, ws, instance_path=instance_path)
        assert report1["skipped_pending_decision"] == 1

        # User decides 'same'
        chip_key = hm._key_for(loaded)
        save_chip_decision(instance_path, chip_key, "SisterChip", "same")

        # Second backfill: SisterChip experiment now ingested
        report2 = hm.backfill_from_workspace(loaded, ws, instance_path=instance_path)
        assert report2["ingested"] == 1   # the previously-deferred SisterChip experiment
        assert report2["skipped_pending_decision"] == 0
        assert len(report2["pending_decisions"]) == 0

    def test_decision_different_skips_silently(self, hm, tmp_path):
        from quam_state_manager.core.history import save_chip_decision
        ws = self._make_chip_a_workspace(tmp_path)
        loaded = self._make_loaded(tmp_path)
        instance_path = tmp_path / "instance"

        # First backfill (no decision yet)
        hm.backfill_from_workspace(loaded, ws, instance_path=instance_path)

        # User decides 'different'
        chip_key = hm._key_for(loaded)
        save_chip_decision(instance_path, chip_key, "SisterChip", "different")

        # Second backfill: SisterChip is silently skipped
        report = hm.backfill_from_workspace(loaded, ws, instance_path=instance_path)
        assert report["skipped_decision_different"] == 1
        assert len(report["pending_decisions"]) == 0


class TestMigrationV2Fingerprint:
    """Migration v2 routes by fingerprint of state.json + wiring.json,
    not by meta source_path. This corrects v1's mis-attribution caused
    by the backfill source_path bug."""

    def _make_snap(self, parent, ts, *, host, qubits, source_path):
        snap = parent / ts
        snap.mkdir(parents=True)
        (snap / "state.json").write_text(json.dumps({
            "qubits": {q: {"id": q} for q in qubits},
            "qubit_pairs": {},
        }), encoding="utf-8")
        (snap / "wiring.json").write_text(json.dumps({
            "network": {"host": host, "cluster_name": "C"},
        }), encoding="utf-8")
        (snap / "meta.json").write_text(json.dumps({
            "timestamp": ts,
            "trigger": "experiment",
            "diff_summary": {"added": 0, "removed": 0, "modified": 0, "total": 0},
            "new_experiments": [],
            "source_path": source_path,
            "state_size": 0, "wiring_size": 0,
        }), encoding="utf-8")
        return snap

    def test_v2_uses_fingerprint_not_source_path(self, tmp_path):
        from quam_state_manager.core.history import migrate_legacy_histories_v2
        instance = tmp_path / "instance"
        history = instance / "history"

        # Pre-existing chip dir for LabB (will be the matching target for
        # any snapshot whose host=10.1.1.6 and qubits=qA0..qA1).
        labb_dir = history / "LabB_1Q"
        labb_dir.mkdir(parents=True)
        self._make_snap(labb_dir, "20260101_000000_001",
                         host="10.1.1.6", qubits=["qA0", "qA1"],
                         source_path="/labb/sample")

        # A "ExampleChip_1Q" dir polluted with LabB snapshots — buggy v1 sent
        # them here because source_path lied.
        nov_dir = history / "ExampleChip_1Q"
        nov_dir.mkdir(parents=True)
        # Truly-ExampleChip snapshot (host=192.168.88.254, q0..q1)
        self._make_snap(nov_dir, "20260430_120000_001",
                         host="192.168.88.254", qubits=["q0", "q1"],
                         source_path="/wrong/source/ExampleChip 1Q/quam_state")
        # Mis-attributed LabB snapshot (host=10.1.1.6, qA0..qA1) but
        # source_path lies and points at a ExampleChip path
        self._make_snap(nov_dir, "20260430_130000_002",
                         host="10.1.1.6", qubits=["qA0", "qA1"],
                         source_path="/wrong/source/ExampleChip 1Q/quam_state")

        report = migrate_legacy_histories_v2(instance)
        assert report["status"] == "migrated"

        # The ExampleChip snapshot stays in ExampleChip_1Q
        assert (nov_dir / "20260430_120000_001").is_dir()
        # The LabB snapshot moves to LabB_1Q
        assert (labb_dir / "20260430_130000_002").is_dir()
        # Original mis-attributed location is no longer there
        assert not (nov_dir / "20260430_130000_002").exists()

    def test_v2_idempotent(self, tmp_path):
        from quam_state_manager.core.history import migrate_legacy_histories_v2
        instance = tmp_path / "instance"
        instance.mkdir(parents=True)

        first = migrate_legacy_histories_v2(instance)
        assert first["status"] in ("migrated", "no_history")
        assert (instance / "migrated_v2.flag").exists()
        # Re-running is a no-op
        second = migrate_legacy_histories_v2(instance)
        assert second["status"] == "already_migrated"

    # ------------------------------------------------------------------
    # Post-fix regressions: O(N) indexed routing
    # ------------------------------------------------------------------
    # The previous ``_resolve_chip_key_by_fingerprint`` sampled one snap
    # per dir then ``break``-ed, which (a) misrouted snaps when the first
    # sample happened to be misattributed and (b) was O(N x M x S) per
    # migration. The replacement builds a single
    # ``{ChipFingerprint -> chip_dir_name}`` index up-front via
    # ``_build_fingerprint_index`` and does O(1) lookups per snap.
    # These four tests pin the new behaviour.

    def test_v2_routes_correctly_regardless_of_iterdir_order(self, tmp_path, monkeypatch):
        """When a polluted chip dir contains [misattributed, real-snap],
        routing must NOT depend on which snap iterdir yields first. The
        index sees every snap, so order is irrelevant."""
        from pathlib import Path as _RealPath
        from quam_state_manager.core.history import migrate_legacy_histories_v2

        instance = tmp_path / "instance"
        history = instance / "history"
        labb_dir = history / "LabB_1Q"
        labb_dir.mkdir(parents=True)
        self._make_snap(labb_dir, "20260101_000000_001",
                         host="10.1.1.6", qubits=["qA0", "qA1"],
                         source_path="/labb/sample")

        nov_dir = history / "ExampleChip_1Q"
        nov_dir.mkdir(parents=True)
        # Layout the LabB-misattributed snap with an alphabetically-earlier
        # timestamp so it sorts FIRST in any naive scan, hiding the real
        # ExampleChip snap.
        self._make_snap(nov_dir, "20260101_000000_001",
                         host="10.1.1.6", qubits=["qA0", "qA1"],
                         source_path="/wrong/ExampleChip/path")
        self._make_snap(nov_dir, "20260430_120000_999",
                         host="192.168.88.254", qubits=["q0", "q1"],
                         source_path="/wrong/ExampleChip/path")

        # Force reverse-iterdir for an extra paranoid check: the
        # misattributed LabB snap is yielded first in every iterdir.
        real_iterdir = _RealPath.iterdir

        def reversed_iterdir(self):
            return iter(sorted(real_iterdir(self), key=lambda p: p.name))

        monkeypatch.setattr(_RealPath, "iterdir", reversed_iterdir)

        report = migrate_legacy_histories_v2(instance)
        assert report["status"] == "migrated"
        # Real ExampleChip snap stays where it is.
        assert (nov_dir / "20260430_120000_999").is_dir()
        # Misattributed LabB snap (timestamp 20260101_...) collides with the
        # genuine LabB snap of the same timestamp -> skipped, not synthesised
        # away. Either way it must NOT remain orphaned in nov_dir.
        assert not (nov_dir / "20260101_000000_001").exists() or \
            report["skipped"] >= 1

    def test_v2_synthesises_key_when_no_match(self, tmp_path):
        """A snap whose fingerprint has no peer in any existing dir falls
        back to ``chip_<host>_<qcount>q`` — same naming as the index
        builder uses internally, via ``_synthesise_chip_key``."""
        from quam_state_manager.core.history import migrate_legacy_histories_v2

        instance = tmp_path / "instance"
        history = instance / "history"
        # Single misnamed dir, one snap; nothing else to match against.
        src = history / "_legacy_will_be_resolved"
        # Use a non-legacy-pattern name so the dir isn't skipped, but
        # which doesn't already happen to equal the synthesised target.
        src = history / "misc_dump"
        src.mkdir(parents=True)
        self._make_snap(src, "20260430_120000_001",
                         host="192.168.99.99", qubits=["q0", "q1", "q2"],
                         source_path="/somewhere")

        report = migrate_legacy_histories_v2(instance)
        assert report["status"] == "migrated"
        # Index has only one fp -> only candidate dir wins. So the snap
        # stays in misc_dump. Re-running this with the snap relocated
        # would be the "no match anywhere" scenario, but our point here
        # is the SYNTHESISE helper's existence + agreement with the
        # builder. Exercise it directly:
        from quam_state_manager.core.history import (
            ChipFingerprint, _synthesise_chip_key,
        )
        fp = ChipFingerprint(
            network=(("host", "192.168.99.99"), ("cluster_name", "C")),
            qubits=frozenset({"q0", "q1", "q2"}),
            pairs=frozenset(),
        )
        assert _synthesise_chip_key(fp) == "chip_192_168_99_99_3q"

    def test_v2_index_built_once_perf(self, tmp_path, monkeypatch):
        """``fingerprint_of`` must be invoked at most 2*N times for N
        total snaps — once per snap during index build, once per snap
        during routing. The old per-snap scan was N*M+N reads; this is
        the perf regression guard for the index approach.
        """
        from quam_state_manager.core import history as histmod
        from quam_state_manager.core.history import migrate_legacy_histories_v2

        instance = tmp_path / "instance"
        history = instance / "history"
        # 3 dirs x 4 snaps = 12 snaps. Old scan: per snap, walk 3 dirs
        # and read 1 snap each => ~12*3 = 36 reads. New scan: 12 (build)
        # + 12 (route) = 24.
        n_dirs, n_snaps = 3, 4
        for i in range(n_dirs):
            d = history / f"chip_{i}"
            d.mkdir(parents=True)
            for j in range(n_snaps):
                self._make_snap(
                    d, f"2026010{i}_00{j:04d}",
                    host=f"10.0.0.{i}", qubits=[f"q{i}_{j}"],
                    source_path="/x",
                )

        calls = {"n": 0}
        real_fp = histmod.fingerprint_of

        def counted(p):
            calls["n"] += 1
            return real_fp(p)

        monkeypatch.setattr(histmod, "fingerprint_of", counted)
        migrate_legacy_histories_v2(instance)

        total_snaps = n_dirs * n_snaps
        assert calls["n"] <= 2 * total_snaps, (
            f"fingerprint_of called {calls['n']} times for {total_snaps} snaps;"
            f" index approach should cap at 2*N = {2 * total_snaps}"
        )

    def test_v2_tiebreak_purity_wins(self, tmp_path):
        """When two dirs both contain a snap for the same fingerprint,
        the dir with the higher *purity ratio* (this-fp-count / total-
        snaps-in-dir) wins. A clean ``LabB_1Q`` (1/1=1.0) outranks a
        polluted ``ExampleChip_1Q`` (1/2=0.5) even though both have the same
        absolute count of LabB snaps."""
        from quam_state_manager.core.history import (
            ChipFingerprint, _build_fingerprint_index,
        )
        history = tmp_path / "history"
        labb_dir = history / "LabB_1Q"
        labb_dir.mkdir(parents=True)
        self._make_snap(labb_dir, "20260101_000000_001",
                         host="10.1.1.6", qubits=["qA0", "qA1"],
                         source_path="/labb")

        nov_dir = history / "ExampleChip_1Q"
        nov_dir.mkdir(parents=True)
        self._make_snap(nov_dir, "20260430_120000_001",
                         host="192.168.88.254", qubits=["q0", "q1"],
                         source_path="/examplechip")
        self._make_snap(nov_dir, "20260430_130000_002",
                         host="10.1.1.6", qubits=["qA0", "qA1"],
                         source_path="/examplechip")

        index = _build_fingerprint_index(history)
        labb_fp = ChipFingerprint(
            network=(("host", "10.1.1.6"), ("cluster_name", "C")),
            qubits=frozenset({"qA0", "qA1"}),
            pairs=frozenset(),
        )
        nov_fp = ChipFingerprint(
            network=(("host", "192.168.88.254"), ("cluster_name", "C")),
            qubits=frozenset({"q0", "q1"}),
            pairs=frozenset(),
        )
        assert index[labb_fp] == "LabB_1Q", (
            f"purity tie-break failed: LabB fp routed to {index[labb_fp]!r}, "
            "expected LabB_1Q (purity 1.0 vs ExampleChip_1Q's 0.5)"
        )
        assert index[nov_fp] == "ExampleChip_1Q"


# ---------------------------------------------------------------------------
# Phase 1 perf caches (see docs/23_param_history_performance.md)
# ---------------------------------------------------------------------------

class TestParamHistoryCaches:
    """Cache invariants for Phase 1 of the param-history performance work.

    These confirm the caches return correct results on hit AND get
    invalidated when a snapshot is created — so callers always see fresh
    data without paying the recomputation cost on every read.
    """

    def test_index_summary_cached_until_capture(self, hm: HistoryManager, quam_path: Path):
        # First snapshot → first summary call computes.
        hm.check_and_snapshot(quam_path, force=True)
        s1 = hm.index_summary(quam_path)
        assert s1["total"] == 1
        # Second call should hit cache (same dict identity isn't required;
        # we assert the value matches and the cache slot is populated).
        chip_dir = hm._history_dir(quam_path)
        assert str(chip_dir) in hm._index_summary_cache
        s2 = hm.index_summary(quam_path)
        assert s2["total"] == 1

        # Mutate state and capture another snapshot → summary must reflect it.
        time.sleep(0.05)
        state = json.loads((quam_path / "state.json").read_text())
        state["qubits"]["qA1"]["T1"] = 9000
        (quam_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        hm.check_and_snapshot(quam_path, force=True)
        s3 = hm.index_summary(quam_path)
        assert s3["total"] == 2

    def test_list_chip_histories_cached(self, hm: HistoryManager, quam_path: Path):
        hm.check_and_snapshot(quam_path, force=True)
        first = hm.list_chip_histories()
        assert any(c["snapshot_count"] >= 1 for c in first)
        # Cache slot populated after first call.
        assert hm._chip_histories_cache is not None
        cached_token, _ = hm._chip_histories_cache
        # Second call without changes returns the same cached object.
        second = hm.list_chip_histories()
        assert second is hm._chip_histories_cache[1]
        assert hm._chip_histories_cache[0] == cached_token

        # Capture invalidates the cache.
        time.sleep(0.05)
        state = json.loads((quam_path / "state.json").read_text())
        state["qubits"]["qA1"]["T1"] = 9001
        (quam_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        hm.check_and_snapshot(quam_path, force=True)
        third = hm.list_chip_histories()
        assert third[0]["snapshot_count"] >= 2

    def test_fingerprint_cache_reuses_when_unchanged(self, hm: HistoryManager, quam_path: Path):
        fp1 = hm._cached_fingerprint(quam_path)
        assert fp1 is not None
        # Cache slot populated.
        assert str(quam_path) in hm._fingerprint_cache
        # Second call returns the same fingerprint instance from cache.
        fp2 = hm._cached_fingerprint(quam_path)
        assert fp2 is fp1

    def test_fingerprint_cache_invalidates_on_mtime_change(self, hm: HistoryManager, quam_path: Path):
        fp1 = hm._cached_fingerprint(quam_path)
        # Modify state.json and bump its mtime.
        time.sleep(0.05)
        state = json.loads((quam_path / "state.json").read_text())
        state["qubits"]["qB1"] = {"id": "qB1", "f_01": 6.5e9}
        (quam_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        # Mtime change should make the cache miss → recomputed fingerprint.
        fp2 = hm._cached_fingerprint(quam_path)
        assert fp2 is not None
        assert fp2 != fp1  # qubit set changed (qA1 → qA1+qB1)

    def test_alignment_cache_reuses_until_workspace_or_loaded_changes(
        self, hm: HistoryManager, quam_path: Path, tmp_path: Path,
    ):
        # Need a Workspace with at least one root for the scan to be meaningful.
        from quam_state_manager.core.scanner import Workspace
        ws = Workspace()
        # Pretend the experiment dir is a workspace root (minimal fixture).
        ws.add_root(str(quam_path.parent.parent))
        r1 = hm.scan_workspace_alignment(quam_path, ws)
        assert r1 is not None
        # Cache hit: second call returns the same object.
        r2 = hm.scan_workspace_alignment(quam_path, ws)
        assert r2 is r1

        # Modifying the loaded chip's fingerprint invalidates the cache.
        time.sleep(0.05)
        state = json.loads((quam_path / "state.json").read_text())
        state["qubits"]["qX1"] = {"id": "qX1", "f_01": 7e9}
        (quam_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        r3 = hm.scan_workspace_alignment(quam_path, ws)
        assert r3 is not r1

    def test_render_sparkline_svg_inner_basic(self):
        values = [
            {"value": 1.0, "trigger": "save"},
            {"value": 2.0, "trigger": "manual"},
            {"value": 3.0, "trigger": "auto"},
        ]
        svg = HistoryManager.render_sparkline_svg_inner(values)
        assert "<polyline" in svg
        assert "<path " in svg
        # Each trigger class shows up.
        assert "hs-pt-save" in svg
        assert "hs-pt-manual" in svg
        # Last point gets r="2" (larger marker).
        assert 'r="2"' in svg

    def test_render_sparkline_svg_inner_handles_current_overlay(self):
        values = [
            {"value": 0.0, "trigger": "save"},
            {"value": 10.0, "trigger": "save"},
            {"value": 5.0, "trigger": "save"},
        ]
        svg = HistoryManager.render_sparkline_svg_inner(values, current=5.0)
        assert "hs-current" in svg

    def test_render_sparkline_svg_inner_filters_non_numeric(self):
        # Values that are None / strings / NaN must be skipped, not crash.
        values = [
            {"value": 1.0, "trigger": "save"},
            {"value": None, "trigger": "save"},
            {"value": "bogus", "trigger": "save"},
            {"value": float("nan"), "trigger": "save"},
            {"value": 2.0, "trigger": "save"},
        ]
        svg = HistoryManager.render_sparkline_svg_inner(values)
        assert svg  # non-empty: 2 finite numbers is enough

    def test_render_sparkline_svg_inner_returns_empty_for_too_few_points(self):
        # 1 point: no line to draw.
        svg = HistoryManager.render_sparkline_svg_inner([{"value": 1.0, "trigger": "save"}])
        assert svg == ""
        # 0 points: empty.
        assert HistoryManager.render_sparkline_svg_inner([]) == ""

    def test_rebuild_index_invalidates_summary_cache(self, hm: HistoryManager, quam_path: Path):
        # Capture a snapshot so the chip dir + index exist.
        hm.check_and_snapshot(quam_path, force=True)
        s1 = hm.index_summary(quam_path)
        assert s1["total"] == 1

        # Simulate a snapshot dir being added externally (no
        # check_and_snapshot path → no _bump_chip_version). After this,
        # the on-disk count is 2 but the index still reflects 1.
        hist_dir = hm._history_dir(quam_path)
        synthetic_ts = "20260501_120000_000"
        snap_dir = hist_dir / synthetic_ts
        snap_dir.mkdir(parents=True, exist_ok=True)
        # Copy the existing snapshot's files into the new dir.
        existing = next(d for d in hist_dir.iterdir() if d.is_dir() and d.name != synthetic_ts)
        for fn in ("state.json", "wiring.json", "meta.json"):
            (snap_dir / fn).write_text(
                (existing / fn).read_text(encoding="utf-8"), encoding="utf-8"
            )
        # Rewrite meta.json's timestamp to match the new dir name.
        meta = json.loads((snap_dir / "meta.json").read_text(encoding="utf-8"))
        meta["timestamp"] = synthetic_ts
        (snap_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        # Force a snapshot-list cache clear so list_snapshots picks up the new dir.
        hm._snapshot_list_cache.clear()
        # Also clear last_index_check so _ensure_index_fresh doesn't shortcut.
        hm._last_index_check.clear()

        # index_summary should now self-heal (rebuild_index runs) AND
        # return updated counts — proving the cache was invalidated by
        # rebuild_index's _bump_chip_version call.
        s2 = hm.index_summary(quam_path)
        assert s2["total"] == 2


# ---------------------------------------------------------------------------
# Phase 2.1 — chip-decisions atomicity + module lock (§1.1)
# ---------------------------------------------------------------------------


class TestSaveChipDecisionAtomic:
    """Phase 2 §1.1: ``save_chip_decision`` must persist atomically AND
    propagate write failures. Before the fix it ``write_text``'d (non-
    atomic — partial writes wiped every prior decision) and swallowed the
    OSError silently.
    """

    def test_load_returns_empty_when_decisions_file_partially_written(self, tmp_path):
        """A partially-written file (no atomic replace) is the failure mode
        the fix prevents. With atomic_write_json, the on-disk file is
        either the prior content or the new content — never a half-written
        invalid JSON."""
        from quam_state_manager.core.history import (
            _chip_decisions_file, load_chip_decisions, save_chip_decision,
        )

        # Seed a real decision.
        save_chip_decision(tmp_path, "chipA", "LabB_1Q", "same")
        assert load_chip_decisions(tmp_path) == {"chipA::LabB_1Q": "same"}

        # If a write somehow left a partial file behind, load returns {}
        # — atomic_write_json eliminates that scenario in production.
        _chip_decisions_file(tmp_path).write_text("{invalid", encoding="utf-8")
        assert load_chip_decisions(tmp_path) == {}

    def test_save_propagates_os_error(self, tmp_path, monkeypatch):
        """The fix raises OSError so callers can surface the failure to
        the user instead of telling them ‘Saved’ while the file isn't."""
        from quam_state_manager.core import history as histmod
        from quam_state_manager.core.history import save_chip_decision

        def boom(path, data):
            raise OSError("simulated disk failure")

        monkeypatch.setattr(histmod.safe_io, "atomic_write_json", boom)
        with pytest.raises(OSError):
            save_chip_decision(tmp_path, "chipA", "LabB_1Q", "same")

    def test_save_uses_atomic_write_json(self, tmp_path, monkeypatch):
        """Sanity-check that the implementation goes through safe_io —
        defends against a future refactor accidentally reverting to
        plain ``write_text``."""
        from quam_state_manager.core import history as histmod
        from quam_state_manager.core.history import save_chip_decision

        calls = []
        real = histmod.safe_io.atomic_write_json

        def spy(path, data):
            calls.append((path, data))
            real(path, data)

        monkeypatch.setattr(histmod.safe_io, "atomic_write_json", spy)
        save_chip_decision(tmp_path, "chipA", "LabB_1Q", "same")
        assert calls, "save_chip_decision did not call safe_io.atomic_write_json"
        assert calls[0][1] == {"chipA::LabB_1Q": "same"}


# ---------------------------------------------------------------------------
# Phase 2.1 — migration flag-file atomic writes (§1.4)
# ---------------------------------------------------------------------------


class TestMigrationFlagAtomic:
    """The migration flag files (``migrated_v1.flag`` / ``migrated_v2.flag``)
    are gating idempotency tokens. A crash mid-write previously left them
    absent (forcing the next launch to re-run an expensive migration);
    safe_io.atomic_write_json makes the write atomic."""

    def test_v1_flag_written_via_safe_io(self, tmp_path, monkeypatch):
        from quam_state_manager.core import history as histmod

        calls: list = []
        real = histmod.safe_io.atomic_write_json

        def spy(path, data):
            calls.append(path)
            real(path, data)

        monkeypatch.setattr(histmod.safe_io, "atomic_write_json", spy)
        histmod.migrate_legacy_histories(tmp_path)
        flag_writes = [c for c in calls if str(c).endswith("migrated_v1.flag")]
        assert flag_writes, "v1 migration did not write the flag via safe_io"

    def test_v2_flag_written_via_safe_io(self, tmp_path, monkeypatch):
        from quam_state_manager.core import history as histmod

        calls: list = []
        real = histmod.safe_io.atomic_write_json

        def spy(path, data):
            calls.append(path)
            real(path, data)

        monkeypatch.setattr(histmod.safe_io, "atomic_write_json", spy)
        histmod.migrate_legacy_histories_v2(tmp_path)
        flag_writes = [c for c in calls if str(c).endswith("migrated_v2.flag")]
        assert flag_writes, "v2 migration did not write the flag via safe_io"


# ---------------------------------------------------------------------------
# Phase 3 — scaling regressions
# ---------------------------------------------------------------------------


class TestPhase3SqlDownsample:
    """Phase 3 §1.1 — extract_property_history must thin rows in SQL, not
    materialise everything in Python before LTTB."""

    def test_pull_capped_by_sql_when_partition_huge(self, tmp_path):
        from quam_state_manager.core.history import (
            HistoryManager, _SQL_PULL_MULTIPLIER,
        )
        hm = HistoryManager(tmp_path / "instance")
        path = tmp_path / "chip" / "quam_state"
        _write_quam_state(path, _base_state(), _base_wiring())
        # Insert 5_000 synthetic rows for one (qubit, prop) into the
        # SQLite index directly so we don't have to materialise 5k
        # snapshots on disk.
        import sqlite3
        idx = hm._index_path(path)
        hm._history_dir(path).mkdir(parents=True, exist_ok=True)
        from quam_state_manager.core.history import _ensure_param_history_schema
        _ensure_param_history_schema(idx)
        conn = sqlite3.connect(str(idx), isolation_level=None)
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO param_history "
                "(timestamp, qubit, property, value, raw_pointer, trigger, run_id, experiment) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (f"2026{i:020d}", "qA1", "T1", 1.0e-5 + i, None, "auto", None, None)
                    for i in range(5000)
                ],
            )
        finally:
            conn.close()
        # Make the snapshot list non-empty so _ensure_index_fresh
        # doesn't tear down what we just inserted.
        (hm._history_dir(path) / "20260101_000000_000").mkdir(parents=True, exist_ok=True)
        (hm._history_dir(path) / "20260101_000000_000" / "meta.json").write_text(
            json.dumps({"timestamp": "20260101_000000_000", "trigger": "auto",
                        "diff_summary": {"added":0,"removed":0,"modified":0,"total":0},
                        "new_experiments": [], "source_path": str(path),
                        "state_size":0, "wiring_size":0}), encoding="utf-8")

        # downsample=100 with 5_000 rows must pull at most ~1000 rows
        # (downsample * _SQL_PULL_MULTIPLIER) into Python.
        rows = hm.extract_property_history(path, ["T1"], downsample=100)
        assert rows, "expected at least one bucket"
        # bucket["values"] has been LTTB-thinned to ~100 entries.
        bucket = rows[0]
        assert len(bucket["values"]) <= 100, (
            f"LTTB output exceeded downsample target: {len(bucket['values'])}"
        )

    def test_no_downsample_returns_everything(self, tmp_path):
        """``downsample=None`` (or 0) must skip the SQL thinning entirely
        — used by the trends / CSV-export paths that want raw rows."""
        from quam_state_manager.core.history import HistoryManager
        hm = HistoryManager(tmp_path / "instance")
        path = tmp_path / "chip" / "quam_state"
        _write_quam_state(path, _base_state(), _base_wiring())
        import sqlite3
        idx = hm._index_path(path)
        hm._history_dir(path).mkdir(parents=True, exist_ok=True)
        from quam_state_manager.core.history import _ensure_param_history_schema
        _ensure_param_history_schema(idx)
        conn = sqlite3.connect(str(idx), isolation_level=None)
        try:
            conn.executemany(
                "INSERT OR REPLACE INTO param_history "
                "(timestamp, qubit, property, value, raw_pointer, trigger, run_id, experiment) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (f"2026{i:020d}", "qA1", "T1", 1.0e-5 + i, None, "auto", None, None)
                    for i in range(200)
                ],
            )
        finally:
            conn.close()
        (hm._history_dir(path) / "20260101_000000_000").mkdir(parents=True, exist_ok=True)
        (hm._history_dir(path) / "20260101_000000_000" / "meta.json").write_text(
            json.dumps({"timestamp": "20260101_000000_000", "trigger": "auto",
                        "diff_summary": {"added":0,"removed":0,"modified":0,"total":0},
                        "new_experiments": [], "source_path": str(path),
                        "state_size":0, "wiring_size":0}), encoding="utf-8")
        rows = hm.extract_property_history(path, ["T1"], downsample=None)
        assert len(rows[0]["values"]) == 200


class TestPhase3BackfillSingleConnection:
    """Phase 3 §1.2 — backfill must reuse one SQLite connection across N
    snaps rather than open + close N connections."""

    def test_backfill_opens_one_index_connection(self, tmp_path, monkeypatch):
        """``sqlite3.connect`` for the target index should be called at most
        once per ``_ingest_entries_into`` invocation. The check accepts the
        existing-timestamps lookup connection (legacy split) collapsing into
        the same one we use for inserts."""
        from quam_state_manager.core.history import HistoryManager
        import sqlite3 as sql_mod

        hm = HistoryManager(tmp_path / "instance")
        target_dir = tmp_path / "instance" / "history" / "chipA"
        target_dir.mkdir(parents=True)

        class _FakeEntry:
            def __init__(self, src, ts, run_id):
                self.quam_state_path = src
                self.folder_path = src.parent
                self.experiment_name = "test"
                self.run_id = run_id
                self.date_str = "2026-05-28"
                self.timestamp = f"2026-05-28T01:00:{run_id:02d}"

        # Seed 5 fake experiment folders with state.json/wiring.json.
        entries = []
        for i in range(5):
            qs = tmp_path / "ws" / f"#{i}" / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps({"qubits": {"qA1": {"T1": 1.0e-5 + i}}}), encoding="utf-8")
            (qs / "wiring.json").write_text(json.dumps({"network": {"host": "10.1.1.1"}}), encoding="utf-8")
            entries.append(_FakeEntry(qs, f"ts_{i}", i))

        calls = {"target_idx": 0, "other": 0}
        target_idx_str = str(target_dir / "index.sqlite")
        real_connect = sql_mod.connect

        def counting_connect(p, *a, **kw):
            if str(p) == target_idx_str:
                calls["target_idx"] += 1
            else:
                calls["other"] += 1
            return real_connect(p, *a, **kw)

        monkeypatch.setattr(sql_mod, "connect", counting_connect)

        hm._ingest_entries_into(target_dir, entries)
        # 1 for _ensure_param_history_schema's bootstrap + 1 for the
        # main ingest connection. The win vs pre-Phase-3 is the latter
        # being reused for ALL N entries instead of opened per snap.
        assert calls["target_idx"] <= 2, (
            f"expected ≤ 2 connections to target index for N entries, got {calls['target_idx']}"
        )


class TestPhase3RawDictExtractor:
    """Phase 3 §1.3 — _extract_index_rows_from_state must produce the same
    rows as the QuamStore-based legacy path for shared inputs."""

    def test_fast_extractor_matches_legacy_path(self, tmp_path):
        from quam_state_manager.core.history import (
            HistoryManager, SnapshotMeta, _extract_index_rows_from_state,
        )
        hm = HistoryManager(tmp_path / "instance")
        # Build a small snap on disk and compare.
        snap_dir = tmp_path / "snap"
        snap_dir.mkdir()
        state = _base_state()
        state["qubits"]["qA1"]["T1"] = 7.5e-6
        state["qubits"]["qA1"]["xy"] = {"operations": {"x180_DragCosine": {"amplitude": 0.123}}}
        (snap_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (snap_dir / "wiring.json").write_text(json.dumps(_base_wiring()), encoding="utf-8")
        meta = SnapshotMeta(
            timestamp="ts1", trigger="auto",
            diff_summary={"added":0,"removed":0,"modified":0,"total":0},
            new_experiments=[], source_path=str(snap_dir),
        )
        legacy_rows = hm._extract_index_rows(snap_dir, meta)
        fast_rows = _extract_index_rows_from_state(state, meta)
        # Sort both for comparison.
        assert sorted(legacy_rows) == sorted(fast_rows)


class TestPhase3HashSidecar:
    """Phase 3 §2.3 — _known_hashes_for_chip must persist its set so a
    second process (or HistoryManager) reads it without walking N
    meta.json files."""

    def _make_snap(self, parent, ts, state_hash):
        snap = parent / ts
        snap.mkdir(parents=True)
        (snap / "meta.json").write_text(json.dumps({
            "timestamp": ts, "trigger": "auto",
            "diff_summary": {"added":0,"removed":0,"modified":0,"total":0},
            "new_experiments": [], "source_path": "", "state_hash": state_hash,
        }), encoding="utf-8")
        return snap

    def test_sidecar_written_on_first_walk_and_used_on_second_process(self, tmp_path):
        from quam_state_manager.core.history import HistoryManager
        hist_dir = tmp_path / "instance" / "history" / "chipA"
        hist_dir.mkdir(parents=True)
        for i in range(5):
            self._make_snap(hist_dir, f"ts_{i}", f"hash_{i:08x}")

        hm = HistoryManager(tmp_path / "instance")
        s = hm._known_hashes_for_chip(hist_dir)
        assert len(s) == 5
        sidecar = hist_dir / "_hashes.json"
        assert sidecar.exists(), "sidecar was not persisted after walk"

        # Simulate a fresh process: drop the in-memory cache, then
        # delete the meta.json files. If the sidecar is the source of
        # truth, the second call returns 5 hashes without re-walking.
        hm._hash_cache.clear()
        for snap in hist_dir.iterdir():
            if snap.is_dir():
                (snap / "meta.json").unlink()

        s2 = hm._known_hashes_for_chip(hist_dir)
        assert s2 == s, "sidecar didn't avoid the meta.json walk"


class TestPhase3PerEntryAlignmentCache:
    """Phase 3 §3.2 — one workspace entry changing must NOT trigger a full
    rescan of every other entry."""

    def test_only_changed_entry_re_aligned(self, tmp_path, monkeypatch):
        """Touch one entry's state.json mtime; align() should be called at
        most once on the next scan, not for every workspace entry.
        """
        from quam_state_manager.core import history as histmod
        from quam_state_manager.core.history import HistoryManager
        from quam_state_manager.core.scanner import Workspace

        hm = HistoryManager(tmp_path / "instance")
        # Seed 4 workspace experiments.
        ws_root = tmp_path / "ws"
        for i in range(4):
            qs = ws_root / "2026-05-28" / f"#{i}_test_010000" / "quam_state"
            qs.mkdir(parents=True)
            (qs / "state.json").write_text(json.dumps({"qubits": {"q1": {}}}), encoding="utf-8")
            (qs / "wiring.json").write_text(json.dumps({"network": {"host": "10.1.1.1"}}), encoding="utf-8")
            (qs.parent / "node.json").write_text(json.dumps({
                "metadata": {"name": "test"}, "data": {"parameters": {"model": {"qubits": ["q1"]}}},
                "id": i, "parents": [], "created_at": "2026-05-28T01:00:00",
            }), encoding="utf-8")

        ws = Workspace()
        ws.add_root(ws_root)

        loaded = tmp_path / "chip" / "quam_state"
        _write_quam_state(loaded, _base_state(), _base_wiring())

        # First scan — populates per-entry cache.
        hm.scan_workspace_alignment(loaded, ws)
        # Drop the outer cache so the next call falls through to the
        # per-entry path (the realistic case where workspace mtime moved).
        with hm._lock:
            hm._alignment_cache.clear()

        # Bump exactly one entry's state.json mtime.
        first_entry_state = sorted(ws_root.rglob("state.json"))[0]
        import os, time as _t
        new_mt = _t.time() + 100
        os.utime(first_entry_state, (new_mt, new_mt))

        align_calls = {"n": 0}
        real_align = histmod.align

        def counted(a, b):
            align_calls["n"] += 1
            return real_align(a, b)

        monkeypatch.setattr(histmod, "align", counted)
        hm.scan_workspace_alignment(loaded, ws)
        # Only the moved entry should re-align; others hit the per-entry
        # cache. Allow ≤ 2 to absorb any align() call inside helpers.
        assert align_calls["n"] <= 2, (
            f"per-entry cache failed: align() called {align_calls['n']} times for 1 changed entry"
        )


class TestPhase3KeyMetricCached:
    """Phase 3 §4.1 — RunInfo.key_metric must be set at parse time so
    list_runs_compact doesn't recompute per row at request time."""

    def test_run_info_carries_key_metric(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        # Folder name → experiment_name via _RUN_FOLDER_RE. Use "t1" so
        # _extract_key_metric's metric_map pattern "t1" matches.
        date = tmp_path / "2026-05-28"
        run = date / "#1_t1_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "t1", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": 1, "parents": [], "created_at": "2026-05-28T01:00:00",
        }), encoding="utf-8")
        (run / "data.json").write_text(json.dumps({
            "fit_results": {"q1": {"T1": 8e-6}},
        }), encoding="utf-8")
        ds = DatasetStore(tmp_path)
        info = next(iter(ds.runs.values()))
        # metric_map: "t1" → ("T1", "s"); 8e-6 s → "8.00 µs".
        assert info.key_metric == "8.00 µs", (
            f"unexpected key_metric: {info.key_metric!r}"
        )


class TestPhase3ParamHistoryRenderCache:
    """Phase 3 §5.1 — extract_property_history result is cached per chip
    dir version, so a second page load with the same filters skips the
    SQL pull + Python grouping entirely."""

    def test_second_call_hits_cache(self, tmp_path, monkeypatch):
        from quam_state_manager.core.history import HistoryManager
        hm = HistoryManager(tmp_path / "instance")
        path = tmp_path / "chip" / "quam_state"
        _write_quam_state(path, _base_state(), _base_wiring())
        meta = hm.check_and_snapshot(path, "auto")
        assert meta is not None

        # First call — populates cache.
        hm.extract_property_history(path, ["T1"], downsample=500)

        # Spy on the SQL connection to confirm the second call doesn't
        # open a connection at all (cache hit short-circuits before SQL).
        import sqlite3 as sql_mod
        calls = {"n": 0}
        real_connect = sql_mod.connect

        def counting_connect(p, *a, **kw):
            calls["n"] += 1
            return real_connect(p, *a, **kw)

        monkeypatch.setattr(sql_mod, "connect", counting_connect)
        hm.extract_property_history(path, ["T1"], downsample=500)
        assert calls["n"] == 0, (
            "extract_property_history cache miss: opened "
            f"{calls['n']} SQLite connections on the second call"
        )

    def test_new_snapshot_invalidates_cache(self, tmp_path):
        from quam_state_manager.core.history import HistoryManager
        import time as _t
        hm = HistoryManager(tmp_path / "instance")
        path = tmp_path / "chip" / "quam_state"
        _write_quam_state(path, _base_state(), _base_wiring())
        hm.check_and_snapshot(path, "auto")
        first = hm.extract_property_history(path, ["T1"], downsample=500)
        # Modify state + new snapshot.
        state = _base_state()
        state["qubits"]["qA1"]["T1"] = 99999
        (path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        _t.sleep(1.1)
        hm.check_and_snapshot(path, "auto", force=True)
        second = hm.extract_property_history(path, ["T1"], downsample=500)
        # Different bucket sizes (or different values) prove the cache
        # was invalidated by _bump_chip_version.
        assert second != first


# ---------------------------------------------------------------------------
# _workspace_token: must flip when a new run lands inside an EXISTING date dir
# (finding C33 — the token used to stat only root folders, so a run added under
# <chip>/<date>/ left the root mtime unchanged → stale alignment scan → the new
# run was silently absent from the importable list).
# ---------------------------------------------------------------------------


def test_workspace_token_changes_on_new_run_in_existing_date_dir(tmp_path):
    import os as _os
    from quam_state_manager.core.history import HistoryManager
    from quam_state_manager.core.scanner import Workspace

    # Canonical layout: <root>/<chip>/<date>/<run>/quam_state/
    root = tmp_path / "workspace"
    date_dir = root / "chipA" / "2026-06-17"
    run1 = date_dir / "#1_exp_120000"
    _write_quam_state(run1 / "quam_state", _base_state(), _base_wiring())

    ws = Workspace()
    ws.add_root(root)

    token_before = HistoryManager._workspace_token(ws)

    # No-op re-statting the same tree must leave the token stable so we
    # don't regress the perf the token exists for (skip-scan when nothing
    # changed).
    assert HistoryManager._workspace_token(ws) == token_before

    # Add a NEW run folder inside the SAME existing date dir. This bumps the
    # date dir's mtime but NOT necessarily the chip/root mtime. Force the
    # date dir's mtime clearly forward to avoid 1s-granularity flakiness —
    # this is exactly the mtime signal a real qualibrate run write produces.
    run2 = date_dir / "#2_exp_120500"
    _write_quam_state(run2 / "quam_state", _base_state(), _base_wiring())
    bumped = date_dir.stat().st_mtime + 10.0
    _os.utime(date_dir, (bumped, bumped))

    token_after = HistoryManager._workspace_token(ws)
    assert token_after != token_before, (
        "workspace token did not change when a new run was added inside an "
        "existing date dir — alignment scan would serve a stale result (C33)"
    )


# ---------------------------------------------------------------------------
# Apply-to-live perf fixes (2026-07-02): deferred SQLite indexing + the
# baseline parse cache. Both must preserve correctness exactly.
# ---------------------------------------------------------------------------

class TestDeferIndex:
    def _indexed_count(self, hm, path):
        import sqlite3
        idx = hm._index_path(Path(path))
        if not idx.exists():
            return 0
        conn = sqlite3.connect(str(idx))
        try:
            return conn.execute(
                "SELECT COUNT(DISTINCT timestamp) FROM param_history").fetchone()[0]
        except sqlite3.OperationalError:
            return 0   # schema not created yet (deferred thread mid-flight)
        finally:
            conn.close()

    def test_defer_index_files_sync_rows_eventually(self, hm, quam_path):
        """Snapshot FILES + meta are written synchronously (timeline sees them
        immediately); only the SQLite rows may lag, and they land shortly."""
        import time
        meta = hm.check_and_snapshot(quam_path, "save", force=True, defer_index=True)
        assert meta is not None
        # Files + meta visible immediately (the State-History race fix):
        snaps = hm.list_snapshots(quam_path)
        assert any(s.timestamp == meta.timestamp for s in snaps)
        # SQLite rows land shortly (deferred thread) …
        for _ in range(80):
            if self._indexed_count(hm, quam_path) >= 1:
                break
            time.sleep(0.05)
        assert self._indexed_count(hm, quam_path) >= 1

    def test_sync_mode_unchanged(self, hm, quam_path):
        meta = hm.check_and_snapshot(quam_path, "save", force=True)  # default: sync
        assert meta is not None
        assert self._indexed_count(hm, quam_path) >= 1   # rows exist immediately

    def test_self_heal_covers_a_dead_index_thread(self, hm, quam_path, monkeypatch):
        """If the deferred insert never runs, _ensure_index_fresh rebuilds."""
        monkeypatch.setattr(
            "threading.Thread.start", lambda self: None)  # thread never runs
        meta = hm.check_and_snapshot(quam_path, "save", force=True, defer_index=True)
        assert meta is not None
        assert self._indexed_count(hm, quam_path) == 0   # insert didn't run
        monkeypatch.undo()
        hm._ensure_index_fresh(Path(quam_path))          # the self-heal
        assert self._indexed_count(hm, quam_path) >= 1


class TestBaselineCache:
    def test_get_after_set_returns_set_content(self, hm, quam_path):
        state, wiring = _base_state(), _base_wiring()
        hm.set_live_baseline(quam_path, state, wiring)
        base = hm.get_live_baseline(quam_path)
        assert base is not None and base["state"] == state

    def test_rewrite_invalidates_cache(self, hm, quam_path):
        s1 = _base_state()
        hm.set_live_baseline(quam_path, s1, _base_wiring())
        assert hm.get_live_baseline(quam_path)["state"] == s1
        s2 = _base_state(); s2["qubits"]["qA1"]["f_01"] = 9.9e9
        hm.set_live_baseline(quam_path, s2, _base_wiring())
        assert hm.get_live_baseline(quam_path)["state"]["qubits"]["qA1"]["f_01"] == 9.9e9

    def test_missing_file_returns_none(self, hm, quam_path):
        assert hm.get_live_baseline(quam_path) is None

    def test_external_delete_invalidates(self, hm, quam_path):
        hm.set_live_baseline(quam_path, _base_state(), _base_wiring())
        assert hm.get_live_baseline(quam_path) is not None
        hm._baseline_file(Path(quam_path)).unlink()
        assert hm.get_live_baseline(quam_path) is None


def test_chip_swap_diff_computed_against_routed_dir(tmp_path):
    """Fingerprint routing: when a snapshot is routed to a DIFFERENT chip dir
    (chip swap), its diff must be computed against THAT dir's prior snapshot,
    not the path-derived dir's. The writer used to list priors from the
    path-derived dir and join a prior timestamp onto the routed dir — a
    nonexistent path — so the diff threw and was silently recorded as zero."""
    def _state(f_01):
        s = _base_state()
        s["qubits"]["qA1"]["f_01"] = f_01
        return s

    hm = HistoryManager(tmp_path / "instance", max_snapshots=50)

    # Chip B lives at folder chipB (host .20); build its two-snapshot timeline.
    wB = _base_wiring()
    wB["network"]["host"] = "10.2.2.20"
    pB = tmp_path / "chipB" / "quam_state"
    _write_quam_state(pB, _state(6.0e9), wB)
    hm.check_and_snapshot(pB, "manual", force=True)
    _write_quam_state(pB, _state(6.1e9), wB)
    m_b2 = hm.check_and_snapshot(pB, "manual", force=True)
    assert m_b2.diff_summary["total"] >= 1        # sanity: normal diff works

    # Chip A lives at folder chipA (host .18); its own dir + timeline.
    pA = tmp_path / "chipA" / "quam_state"
    _write_quam_state(pA, _state(5.0e9), _base_wiring())
    hm.check_and_snapshot(pA, "manual", force=True)

    # Write CHIP B content into path A → the new snapshot routes to chip B's
    # dir (swap_to_existing). Its diff must be vs chip B's prior (6.1e9), i.e.
    # a real change to 6.2e9 — NOT a bogus zero from diffing a missing dir.
    _write_quam_state(pA, _state(6.2e9), wB)
    m_swap = hm.check_and_snapshot(pA, "manual", force=True)
    assert m_swap is not None
    assert m_swap.chip_swap_detected is not None
    assert m_swap.chip_swap_detected["type"] == "swap_to_existing"
    assert m_swap.diff_summary["total"] >= 1      # diff vs routed dir's prior


class TestPairTrendRows:
    """docs/54 — PAIR-scope trend rows (sparse; entity column = pair id)."""

    def _cr_state(self):
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).parent))
        from cr_fixtures import make_flavor_b
        return make_flavor_b()

    def test_pair_rows_extracted_from_cr_state(self):
        from quam_state_manager.core.history import (
            SnapshotMeta, _extract_index_rows_from_state,
        )
        state, _w = self._cr_state()
        meta = SnapshotMeta(
            timestamp="20260716_000000_000", trigger="manual",
            diff_summary={"added": 0, "removed": 0, "modified": 0, "total": 0},
            new_experiments=[], source_path="x", state_size=0, wiring_size=0)
        rows = _extract_index_rows_from_state(state, meta)
        pair_rows = [r for r in rows if r[1] == "q0-1"]
        props = {r[2]: r[3] for r in pair_rows}
        assert props["pair_drive_amplitude_scaling"] == 1.0
        assert props["pair_drive_phase"] == 0.11
        # macro fidelity is null on this fixture and channel bell absent →
        # sparse: NO pair_bell_fidelity row at all (never a NULL row)
        assert "pair_bell_fidelity" not in props

    def test_pair_bell_row_from_channel_fallback(self):
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).parent))
        from cr_fixtures import make_flavor_a
        from quam_state_manager.core.history import (
            SnapshotMeta, _extract_index_rows_from_state,
        )
        state, _w = make_flavor_a()
        meta = SnapshotMeta(
            timestamp="20260716_000000_000", trigger="manual",
            diff_summary={"added": 0, "removed": 0, "modified": 0, "total": 0},
            new_experiments=[], source_path="x", state_size=0, wiring_size=0)
        rows = _extract_index_rows_from_state(state, meta)
        bell = [r for r in rows if r[2] == "pair_bell_fidelity"]
        assert [(r[1], r[3]) for r in bell] == [("q1-2", 0.93)]

    def test_cz_reference_gets_no_lever_rows(self):
        import sys as _s
        from pathlib import Path as _P
        _s.path.insert(0, str(_P(__file__).parent))
        from cr_fixtures import make_cz_reference
        from quam_state_manager.core.history import (
            SnapshotMeta, _extract_index_rows_from_state,
        )
        state, _w = make_cz_reference()
        meta = SnapshotMeta(
            timestamp="20260716_000000_000", trigger="manual",
            diff_summary={"added": 0, "removed": 0, "modified": 0, "total": 0},
            new_experiments=[], source_path="x", state_size=0, wiring_size=0)
        rows = _extract_index_rows_from_state(state, meta)
        assert not any(r[2].startswith("pair_drive") for r in rows)

    def test_v1_index_upgraded_once(self, tmp_path):
        """A pre-v2 index (user_version 0) force-rebuilds exactly once, gaining
        pair rows for EXISTING snapshots; a v2 index is left alone."""
        import sqlite3
        from quam_state_manager.core.history import (
            HistoryManager, _INDEX_SCHEMA_VERSION,
        )
        hm = HistoryManager(tmp_path / "instance")
        path = tmp_path / "chip" / "quam_state"
        state, wiring = self._cr_state()
        _write_quam_state(path, state, wiring)
        hm.check_and_snapshot(path, "manual", force=True)

        idx = hm._index_path(path)
        conn = sqlite3.connect(str(idx), isolation_level=None)
        try:
            # simulate a v1 index: downgrade the stamp + delete the pair rows
            conn.execute("PRAGMA user_version=0")
            conn.execute("DELETE FROM param_history WHERE property LIKE 'pair_%'")
        finally:
            conn.close()
        hm._schema_verified.discard(str(hm._history_dir(path)))

        hm._ensure_index_fresh(path)
        conn = sqlite3.connect(str(idx), isolation_level=None)
        try:
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
            n_pair = conn.execute(
                "SELECT COUNT(*) FROM param_history WHERE property LIKE 'pair_%'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert ver == _INDEX_SCHEMA_VERSION
        assert n_pair > 0
