"""Late files inside an indexed run must not need a date-dir change."""
import os
import time
import json

import pytest

from quam_state_manager.core.dataset import DatasetStore
from tests.test_poll_scan_budgets import _write_run


@pytest.mark.parametrize("refresh", ["poll", "detail", "restart"])
def test_late_files_refresh_capabilities(tmp_path, refresh):
    root = tmp_path / "data"
    folder = _write_run(root, "2026-08-01", 1)
    store = DatasetStore(root, cache_dir=tmp_path / "cache")
    cursor = store.changes_since(0)["now"]
    generation = store.generation
    date_mtime = folder.parent.stat().st_mtime_ns
    if refresh == "restart":
        store._save_store_cache()
    (folder / "ds_raw.h5").write_bytes(b"raw")
    (folder / "ds_fit.h5").write_bytes(b"fit")
    (folder / "quam_state").mkdir()
    (folder / "quam_state" / "state.json").write_text("{}")
    (folder / "quam_state" / "wiring.json").write_text("{}")
    os.utime(folder.parent, ns=(date_mtime, date_mtime))
    if refresh == "restart":
        store = DatasetStore(root, cache_dir=tmp_path / "cache")
    elif refresh == "poll":
        store.rescan_if_stale()
    if refresh != "detail":
        assert store.runs[1].has_ds_raw and store.runs[1].has_quam_state
    run = store.get_run(1)
    assert run["has_ds_raw"] and run["has_ds_fit"] and run["has_quam_state"]
    if refresh != "restart":
        assert store.generation > generation
        assert [row["id"] for row in store.changes_since(cursor)["updated"]] == [1]
        assert store.changes_since(store.changes_since(cursor)["now"])["updated"] == []


def test_archive_poll_has_no_per_run_stats(tmp_path, monkeypatch):
    root = tmp_path / "data"
    for rid in range(1, 101):
        folder = _write_run(root, "2026-08-01", rid)
        os.utime(folder, (time.time() - 3600, time.time() - 3600))
    store = DatasetStore(root)
    calls = []
    original = store._stat_fp
    monkeypatch.setattr(store, "_stat_fp", lambda path: (calls.append(path), original(path))[1])
    store.rescan_if_stale()
    assert calls == []


def test_recent_checks_are_bounded_and_expire(tmp_path, monkeypatch):
    from quam_state_manager.core import dataset
    root = tmp_path / "data"
    for rid in range(1, 81):
        _write_run(root, "2026-08-01", rid)
    store = DatasetStore(root)
    assert len(store._settling_paths) == dataset._RUN_SETTLE_MAX
    calls = []
    original = store._stat_fp
    monkeypatch.setattr(store, "_stat_fp", lambda path: (calls.append(path), original(path))[1])
    store.rescan_if_stale()
    assert len(calls) == 8 * dataset._RUN_SETTLE_MAX
    expired = max(store._settling_paths.values()) + 1
    monkeypatch.setattr(dataset._time, "time", lambda: expired)
    calls.clear()
    store.rescan_if_stale()
    assert calls == []
    assert not store._settling_paths


def test_settling_metadata_preserves_run_annotations(tmp_path):
    folder = _write_run(tmp_path / "data", "2026-08-01", 1)
    store = DatasetStore(tmp_path / "data")
    store.add_tag(1, "good")
    store.set_note(1, "inspect this run")
    store.toggle_bookmark(1)
    store.rescan_if_stale()
    (folder / "data.json").write_text(json.dumps({"fit_results": {"q1": {"T1": 2e-6}}}))
    store.rescan_if_stale()
    run = store.runs[1]
    assert run.fit_results["q1"]["T1"] == 2e-6
    assert run.bookmarked and run.note == "inspect this run" and "good" in run.tags


def test_existing_late_files_finishing_in_place_refresh_open_detail(tmp_path):
    folder = _write_run(tmp_path / "data", "2026-08-01", 1)
    (folder / "ds_raw.h5").write_bytes(b"writing")
    (folder / "quam_state").mkdir()
    store = DatasetStore(tmp_path / "data")
    cursor = store.changes_since(0)["now"]
    version = store.runs[1].files_version
    (folder / "ds_raw.h5").write_bytes(b"finished dataset")
    (folder / "quam_state" / "state.json").write_text("{}")
    (folder / "quam_state" / "wiring.json").write_text("{}")
    store.rescan_if_stale()
    assert store.runs[1].files_version != version
    assert len(store.changes_since(cursor)["updated"]) == 1


def test_detail_open_never_waits_for_a_running_scan(tmp_path):
    import threading
    root = tmp_path / "data"
    _write_run(root, "2026-08-01", 1)
    store = DatasetStore(root)
    held, release = threading.Event(), threading.Event()

    def scan_in_progress():
        with store._scan_lock:
            held.set()
            release.wait(10)

    worker = threading.Thread(target=scan_in_progress)
    worker.start()
    assert held.wait(5)
    try:
        started = time.monotonic()
        run = store.get_run(1)
        elapsed = time.monotonic() - started
    finally:
        release.set()
        worker.join()
    assert run is not None and elapsed < 2.0


def test_settled_folders_cost_one_stat_for_their_files_version(tmp_path, monkeypatch):
    root = tmp_path / "data"
    folder = _write_run(root, "2026-08-01", 1)
    (folder / "ds_raw.h5").write_bytes(b"raw")
    store = DatasetStore(root)
    calls = []
    original = store._stat_fp
    monkeypatch.setattr(store, "_stat_fp", lambda path: (calls.append(path), original(path))[1])
    hour_ago = time.time() - 3600
    os.utime(folder, (hour_ago, hour_ago))
    assert store._run_files_version(folder) == "" and calls == [folder]
    os.utime(folder, None)
    calls.clear()
    assert store._run_files_version(folder) != "" and len(calls) == 5
