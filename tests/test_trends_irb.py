"""Regression pins for customer IRB Trends failures (F1-F6)."""
import json
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from quam_state_manager.core import leaf_index as li
from quam_state_manager.core.history import HistoryManager
from tests.test_leaf_index import TestFreshnessGate as FreshnessHelper, _chip
from tests.test_trends_all_entities import _hot_cold_chip, _charts, PAIRS

IRB = "qubit_pairs.*.macros.*.fidelity.InterleavedRB"


def join_repairs(hm):
    with hm._leaf_rebuild_lock:
        workers = list(hm._leaf_rebuild_threads.values())
    for worker in workers:
        worker.join(10)
        assert not worker.is_alive()


@pytest.fixture
def history(tmp_path):
    hm = HistoryManager(tmp_path / "inst", max_snapshots=50)
    live = tmp_path / "chip"
    helper = FreshnessHelper()
    helper._capture(hm, live, 1e-5)
    return hm, live, helper


def test_dirty_reads_return_and_share_one_worker(history, monkeypatch):
    hm, live, _ = history
    conn = hm._open_index(live)
    li.mark_dirty(conn)
    conn.close()
    entered, release = threading.Event(), threading.Event()
    real = li.rebuild
    calls, starts = [], []
    real_start = threading.Thread.start
    def start(worker):
        if worker.name == "leaf-history-rebuild":
            starts.append(worker)
        return real_start(worker)
    monkeypatch.setattr(threading.Thread, "start", start)
    def blocked(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(10)
        return real(*args, **kwargs)
    monkeypatch.setattr(li, "rebuild", blocked)
    pool = ThreadPoolExecutor(2)
    try:
        jobs = [pool.submit(hm.leaf_field_series, live, "qubits.qA1.T1") for _ in range(2)]
        assert all(job.result(timeout=2) for job in jobs)
        assert entered.wait(2)
        # The worker now holds BEGIN IMMEDIATE: a reader still sees the old WAL commit.
        assert hm.leaf_field_series(live, "qubits.qA1.T1")
        assert len(starts) == 1
        assert len(calls) == 1
    finally:
        release.set()
        pool.shutdown()
        for worker in starts:
            worker.join(10)
        join_repairs(hm)
    assert not hm.leaf_stats(live)["dirty"]


def test_queued_rebuild_rechecks_under_write_lock(history, monkeypatch):
    hm, live, _ = history
    lock = hm._open_index(live)
    li.mark_dirty(lock)
    lock.execute("BEGIN IMMEDIATE")
    real = li.rebuild
    calls = []
    monkeypatch.setattr(li, "rebuild", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    # Both workers have opened their connection before either can get the lock.
    opened = threading.Barrier(3)
    real_open = hm._open_index
    def open_index(path):
        conn = real_open(path)
        opened.wait(timeout=5)
        return conn
    monkeypatch.setattr(hm, "_open_index", open_index)
    with ThreadPoolExecutor(2) as pool:
        jobs = [pool.submit(hm.rebuild_leaf_index, live) for _ in range(2)]
        opened.wait(timeout=5)
        lock.execute("COMMIT")
        lock.close()
        for job in jobs:
            job.result(timeout=10)
    assert calls == [1]


def test_backfill_repairs_out_of_order_index(history, monkeypatch):
    hm, live, helper = history
    helper._orphan(hm, live, "20000101_000000", state=_chip(t1=9e-5))
    conn = hm._open_index(live)
    li.mark_dirty(conn)
    conn.close()
    monkeypatch.setattr(hm, "scan_workspace_alignment", lambda *a: {
        "aligned": [], "renamed": [], "different_chip": {}, "unknown": [],
        "counts": {"different_chip": 0, "unknown": 0}})
    hm.backfill_from_workspace(live, SimpleNamespace())
    assert not hm.leaf_stats(live)["dirty"]
    assert hm.leaf_stats(live)["snapshots"] == 2


def test_held_endpoint_and_disappearance(history):
    hm, live, helper = history
    helper._orphan(hm, live, "20990101_000000", state=_chip(t1=1e-5))
    hm.rebuild_leaf_index(live)
    dp = "qubits.qA1.T1"
    rows = hm.leaf_field_series_many(live, [dp], hold_to_newest=True)[dp]
    assert rows[-1][0] == "20990101_000000"
    assert rows[-1][6] == rows[0][0]
    assert rows[-1][2:6] == (None, None, None, None)
    gone = _chip(t1=1e-5)
    del gone["qubits"]["qA1"]["T1"]
    helper._orphan(hm, live, "20990102_000000", state=gone)
    helper._orphan(hm, live, "20990103_000000", state=gone)
    hm.rebuild_leaf_index(live)
    rows = hm.leaf_field_series_many(live, [dp], hold_to_newest=True)[dp]
    assert rows[-1][0] == "20990102_000000" and rows[-1][1] is None
    assert all(len(row) == 6 for row in rows)


@pytest.fixture
def irb_client(tmp_path):
    return _hot_cold_chip(tmp_path, "irb", {"macros.cz_SNZ.fidelity.InterleavedRB_alpha": .9}, {
        "macros.cz_SNZ.fidelity.InterleavedRB": .99,
        "macros.cz_GNZ.fidelity.InterleavedRB": .98}, steps=2)


def test_held_family_draws_and_labels_each_variant(irb_client):
    body = irb_client.get("/topology/trends?metrics=&paths=" + IRB).get_data(as_text=True)
    chart, = _charts(body)
    assert len(chart["series"]) == 2 * len(PAIRS)
    assert chart["n_entities"] == len(PAIRS)
    assert {s["entity"] for s in chart["series"]} == {p + " · " + gate for p in PAIRS for gate in ("cz_SNZ", "cz_GNZ")}
    assert all(len(s["points"]) == 2 and s["held"] for s in chart["series"])
    assert "no trend yet" not in body


def test_search_enter_matches_and_unknown(irb_client):
    body = irb_client.get("/topology/trends?metrics=&path=interleaved").get_data(as_text=True)
    assert 'class="topo-trend-matches"' in body
    assert "Nothing recorded for" not in body
    body = irb_client.get("/topology/trends?metrics=&path=nonexistent_xyz").get_data(as_text=True)
    assert "No recorded parameter on this chip matches <code>nonexistent_xyz</code>" in body
    assert "Nothing recorded for" not in body


def test_default_irb_pressed_and_explicit_off_sticks(irb_client):
    body = irb_client.get("/topology/trends").get_data(as_text=True)
    assert any(c["metric"] == "macros.*.fidelity.InterleavedRB" and c["series"] for c in _charts(body))
    assert 'data-trend-path="' + IRB + '"' in body
    import re
    assert re.search('data-trend-path="' + re.escape(IRB) + '"[^>]*aria-pressed="true"', body)
    for query in ("metrics=", "paths="):
        body = irb_client.get("/topology/trends?" + query).get_data(as_text=True)
        assert not any(c["metric"] == "macros.*.fidelity.InterleavedRB" for c in _charts(body))


def test_stale_route_reports_progress_and_retries(irb_client, monkeypatch):
    hm = irb_client.application.config["history_manager"]
    monkeypatch.setattr(hm, "leaf_index_updating", lambda p: True)
    monkeypatch.setattr(hm, "_ensure_leaf_index_fresh", lambda p: None)
    body = irb_client.get("/topology/trends?metrics=").get_data(as_text=True)
    assert "History index updating (" in body
    assert 'data-trends-updating="1"' in body
    # docs/208 D1: the note must not fetch its OWN url -- that request aborted
    # the user's badge press and brought the old selection back. The client
    # re-fetches the current selection (trends_irb_selfcheck.cjs).
    note = body[body.index("data-trends-updating"):]
    note = note[:note.index("</p>")]
    assert "hx-get" not in body[body.rindex("<p", 0, body.index("data-trends-updating")):body.index("data-trends-updating") + len(note)]


def test_irb_client_selfcheck():
    result = subprocess.run(["node", "tests/trends_irb_selfcheck.cjs"], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_a_failed_repair_is_not_retried_per_read(history, monkeypatch):
    """A rebuild that keeps failing must not turn every read (or the Trends
    note's own 3 s re-fetch) into a new attempt."""
    hm, live, _ = history
    conn = hm._open_index(live)
    li.mark_dirty(conn)
    conn.close()
    attempts = []

    def boom(path):
        attempts.append(1)
        raise RuntimeError("simulated rebuild failure")
    monkeypatch.setattr(hm, "_repair_leaf_index_if_needed", boom)
    for _ in range(3):
        hm.leaf_field_series(live, "qubits.qA1.T1")
        join_repairs(hm)
    assert attempts == [1]
    # "Updating" means a repair is RUNNING, not "the index is dirty": the index
    # is still dirty here, and a note keyed on dirtiness would re-fetch the
    # section every 3 s for as long as the page stayed open.
    assert hm.leaf_stats(live)["dirty"], "setup: the failed repair left it dirty"
    assert not hm.leaf_index_updating(live)


def test_a_disappearance_is_a_gap_not_a_joined_line(tmp_path):
    """The value vanished from the state for a while: the chart breaks the
    line there (a null point) instead of joining straight through."""
    c = _hot_cold_chip(tmp_path, "gap", {"macros.cz.phase_shift_target": 0.2},
                       {"coupler.decouple_offset": 0.1}, steps=1)
    folder = tmp_path / "gap"
    for step, present in enumerate((False, True)):
        d = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        for p in PAIRS:
            node = d["qubit_pairs"][p]
            node["macros"]["cz"]["phase_shift_target"] = 0.3 + step * 0.01
            if present:
                node.setdefault("coupler", {})["decouple_offset"] = 0.15
            else:
                node.get("coupler", {}).pop("decouple_offset", None)
        (folder / "state.json").write_text(json.dumps(d), encoding="utf-8")
        c.post("/state/archive", data={"tag": f"g{step}"})   # as _hot_cold_chip does
        time.sleep(1.05)
    p = "qubit_pairs.*.coupler.decouple_offset"
    chart, = _charts(c.get("/topology/trends?metrics=&paths=" + p).get_data(as_text=True))
    ys = [pt[1] for pt in chart["series"][0]["points"]]
    assert None in ys and ys[0] is not None and ys[-1] is not None, ys


def test_index_reads_do_not_move_the_history_signal(history):
    """docs/208 D3: every index read creates and deletes index.sqlite-wal/-shm
    in the history dir, which moves its mtime; read as "another process
    captured something", the drift poll re-fetched an open Trends section
    every ~5 s forever. The signal is the set of snapshot dirs."""
    hm, live, helper = history
    seq = hm.history_seq_for(live)
    assert seq > 0
    hist = hm._history_dir(live)
    for _ in range(3):
        hm.leaf_field_series(live, "qubits.qA1.T1")
        join_repairs(hm)
        (hist / "stray.tmp").write_text("x", encoding="utf-8")
        (hist / "stray.tmp").unlink()
        assert hm.history_seq_for(live) == seq
    # A snapshot dir created inside the SAME clock tick as the last -wal
    # delete leaves the dir mtime unchanged (Windows file times step in
    # ~1-16 ms); pin that case deterministically by restoring the mtime.
    st = hist.stat()
    helper._orphan(hm, live, "20990101_000000", state=_chip(t1=3e-5))
    os.utime(hist, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert hm.history_seq_for(live) != seq, "a new snapshot dir still moves it"


def test_a_wildcard_family_beside_one_variant_names_what_it_spans(irb_client):
    """docs/208 D4: charted together, the family read "... (IRB) · * · N pairs"."""
    body = irb_client.get("/topology/trends?metrics=&paths=" + IRB + ","
                          "qubit_pairs.*.macros.cz_SNZ.fidelity.InterleavedRB"
                          ).get_data(as_text=True)
    labels = {c["metric"]: c["label"] for c in _charts(body)}
    assert labels["macros.*.fidelity.InterleavedRB"].endswith("all variants"), labels
    assert labels["macros.cz_SNZ.fidelity.InterleavedRB"].endswith("cz_SNZ"), labels
    assert "· *" not in body
