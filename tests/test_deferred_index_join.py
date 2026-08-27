"""check_and_snapshot(defer_index=True) indexes on a thread (the apply path).
A leaf read landing while that thread runs used to find the index "behind"
and start a FULL rebuild the thread was about to make unnecessary. The
self-heal readers now join in-flight deferred threads first (2026-08-27)."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from quam_state_manager.core.history import HistoryManager


def _seed(folder: Path, t1: float) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"qA1": {"T1": t1, "f_01": 4.8e9}}, "active_qubit_names": ["qA1"]}))
    (folder / "wiring.json").write_text("{}")


def test_a_leaf_read_waits_for_the_deferred_index_instead_of_rebuilding(tmp_path, monkeypatch):
    live = tmp_path / "chip" / "quam_state"
    _seed(live, 1e-5)
    hm = HistoryManager(tmp_path / "inst")
    assert hm.check_and_snapshot(str(live), "manual", force=True) is not None   # sync baseline
    # make the deferred indexing observably SLOW
    real = hm._index_snapshot_into
    started = threading.Event()

    def slow(*a, **k):
        started.set()
        time.sleep(0.6)
        return real(*a, **k)
    monkeypatch.setattr(hm, "_index_snapshot_into", slow)
    rebuilds = {"n": 0}
    real_rebuild = hm.rebuild_leaf_index

    def counting_rebuild(path):
        rebuilds["n"] += 1
        return real_rebuild(path)
    monkeypatch.setattr(hm, "rebuild_leaf_index", counting_rebuild)

    _seed(live, 2e-5)
    meta = hm.check_and_snapshot(str(live), "save", force=True, defer_index=True)
    assert meta is not None and started.wait(2.0), "the deferred thread is running"
    t0 = time.monotonic()
    groups = hm.leaf_change_groups(str(live), limit_snaps=5)
    waited = time.monotonic() - t0
    assert waited >= 0.3, f"the read must have joined the in-flight thread (waited {waited:.2f}s)"
    assert rebuilds["n"] == 0, "no full rebuild while the deferred insert was in flight"
    tss = {g.get("ts") or g.get("timestamp") for g in groups} if isinstance(groups, list) else set()
    assert meta.timestamp in tss or any(meta.timestamp in json.dumps(g, default=str) for g in groups), \
        "the deferred snapshot is in the leaf index by the time the read returns"
    assert not hm._deferred_index_threads, "the finished thread left the registry"


def test_join_is_bounded_and_never_raises(tmp_path):
    hm = HistoryManager(tmp_path / "inst")
    stuck = threading.Thread(target=lambda: time.sleep(2.0), daemon=True)
    hm._deferred_index_threads.append(stuck)
    stuck.start()
    t0 = time.monotonic()
    hm._join_deferred_index(timeout=0.2)
    assert time.monotonic() - t0 < 1.0, "a thread past the budget is left to finish"
