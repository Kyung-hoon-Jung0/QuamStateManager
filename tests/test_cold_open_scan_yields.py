"""v1.3.0 (perf, A1): on a cold chip open, a Datasets index scan nobody is
waiting for never competes with the chip's history catch-up.

Measured on a copy of a 21-qubit chip with ~3,500 runs: the live strip's poll
(unbounded rescan) and the component map's topology request (the per-gate RB
run lookup) finished a cold Datasets index on request threads -- ~12 s of the
interpreter whatever the parse pool's size -- while the history catch-up ran
beside it (~22 s instead of ~9 s), and a click that landed in the scan waited
6-16 s for its page. Pins:

* the component map asks for the structure-only topology, which reads no
  Datasets store and no ledger;
* a BACKGROUND scan (``dataset.yielding_scan``) stops at its next chunk once
  ``should_yield`` is true, and the next scan continues it to the very index a
  plain scan builds; another thread's scan is never affected;
* the global long poll builds its store under that yield;
* the live strip's read never builds a store and never continues a cold scan,
  while a complete store still shows a run that just landed;
* ``hub_sync.catching_up`` is true exactly while a ledger is catching up;
* the index warmer waits for quiet, finishes a truncated store, and stops
  mid-step when the interpreter is wanted; the app hands it every store a cold
  build left filling.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from quam_state_manager.core import dataset, dataset_warm, hub, hub_sync
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app


def _run(root: Path, rid: int, *, date: str = "2026-09-01", hhmmss: str | None = None,
         exp: str = "scan") -> Path:
    hhmmss = hhmmss or f"{(rid // 3600) % 24:02d}{(rid // 60) % 60:02d}{rid % 60:02d}"
    run = root / date / f"#{rid}_{exp}_{hhmmss}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": exp, "status": "successful"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": rid, "parents": []}), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({"fit_results": {"q1": {"amp": 0.1 * rid}}}),
                                   encoding="utf-8")
    return run


def _archive(root: Path, n: int) -> Path:
    for rid in range(1, n + 1):
        _run(root, rid)
    return root


def _wait(pred, timeout=20.0):
    t = time.monotonic()
    while time.monotonic() - t < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


# ---------------------------------------------------------------------------
# 1. the component map's topology reads no Datasets store
# ---------------------------------------------------------------------------

def test_the_layout_topology_never_reads_a_datasets_store(tmp_path, monkeypatch):
    from tests.test_web import _make_state, _make_wiring
    chip = tmp_path / "chip"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps(_make_state()), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps(_make_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)

    def boom(*_a, **_k):
        raise AssertionError("the structure-only topology read the Chip Status enrichment")
    monkeypatch.setattr(routes_mod, "_topology_for_chip_status", boom)
    monkeypatch.setattr(routes_mod, "_active_dataset_stores", boom)
    r = c.get("/api/topology?layout=1")
    assert r.status_code == 200, r.data[:300]
    got = r.get_json()
    with app.app_context():
        want = json.loads(json.dumps(routes_mod._engine().get_topology()))
    assert got == want and got["nodes"]


# ---------------------------------------------------------------------------
# 2. yielding_scan
# ---------------------------------------------------------------------------

def test_a_yielding_scan_stops_at_its_next_chunk_and_the_next_scan_finishes_it(tmp_path):
    root = _archive(tmp_path / "data", 60)
    with dataset.yielding_scan(lambda: True, chunk=8):
        ds = DatasetStore(root)
    assert ds.run_count == 8 and ds.scan_truncated, (ds.run_count, ds.scan_truncated)
    ds.rescan_if_stale()
    plain = DatasetStore(root)
    assert ds.run_count == plain.run_count == 60 and not ds.scan_truncated
    assert ds.list_runs_compact() == plain.list_runs_compact()


def test_a_yielding_scan_runs_whole_while_nothing_wants_the_interpreter(tmp_path):
    root = _archive(tmp_path / "data", 60)
    calls = []
    with dataset.yielding_scan(lambda: calls.append(1) or False, chunk=8):
        ds = DatasetStore(root)
    assert ds.run_count == 60 and not ds.scan_truncated
    assert len(calls) >= 7, "asked between chunks"


def test_the_yield_is_the_calling_threads_alone(tmp_path):
    root = _archive(tmp_path / "data", 40)
    out = {}
    entered, release = threading.Event(), threading.Event()

    def background():
        with dataset.yielding_scan(lambda: True, chunk=4):
            entered.set()
            release.wait(10)
            out["bg"] = DatasetStore(root).run_count

    t = threading.Thread(target=background)
    t.start()
    assert entered.wait(10)
    out["fg"] = DatasetStore(root).run_count       # a foreground scan meanwhile
    release.set()
    t.join(20)
    assert out == {"bg": 4, "fg": 40}, out


# ---------------------------------------------------------------------------
# 3. the long poll builds under the yield
# ---------------------------------------------------------------------------

def test_the_long_poll_builds_its_store_under_the_yield(tmp_path, monkeypatch):
    data = _archive(tmp_path / "data", 2 * dataset._YIELD_CHUNK)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    monkeypatch.setattr(routes_mod, "_background_scan_should_yield", lambda: True)
    assert c.get("/datasets/wait?since=-1&timeout=0").status_code == 200
    for w in ("run_watcher", "run_ingest"):
        if app.config.get(w) is not None:
            app.config[w].stop()
    stores = [s for s in app.config["dataset_store_lru"].values() if s.folder_path == data]
    assert len(stores) == 1
    st = stores[0]
    assert st.scan_truncated and st.run_count == dataset._YIELD_CHUNK, st.run_count


# ---------------------------------------------------------------------------
# 4. the live strip reads light
# ---------------------------------------------------------------------------

def _today_run(root: Path, rid: int, minutes_ago: float = 5) -> Path:
    now = datetime.now()
    when = now - timedelta(minutes=minutes_ago)
    return _run(root, rid, date=now.strftime("%Y-%m-%d"), hhmmss=when.strftime("%H%M%S"),
                exp="12_T1")


@pytest.fixture
def ws_app(tmp_path):
    data = tmp_path / "data"
    for rid in range(1, 41):
        _run(data, rid, date="2026-09-01")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    return app, c, data


def test_the_live_strip_never_builds_a_store(ws_app):
    app, c, data = ws_app
    app.config.pop("dataset_store_lru", None)
    app.config.pop("dataset_store", None)
    d = c.get("/api/agent/now").get_json()
    assert d["ok"] and d["state"] == "idle"
    assert not (app.config.get("dataset_store_lru") or {}), "a store was built by the live strip"


def test_the_live_strip_never_continues_a_cold_scan(ws_app):
    app, c, data = ws_app
    with app.app_context():
        with dataset.yielding_scan(lambda: True, chunk=8):
            st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.scan_truncated and st.run_count == 8
    c.get("/api/agent/now")
    c.get("/api/agent/now")
    assert st.run_count == 8 and st.scan_truncated, "the live strip continued the cold scan"


def test_a_complete_store_still_shows_a_run_that_just_landed(ws_app):
    app, c, data = ws_app
    with app.app_context():
        st = routes_mod._get_or_create_store(data, rescan=False)
    assert not st.scan_truncated and st.run_count == 40
    _today_run(data, 41)
    d = c.get("/api/agent/now").get_json()
    assert d["state"] == "human-ran" and d["human_ran"]["run_id"] == 41, d


# ---------------------------------------------------------------------------
# 5. catching_up
# ---------------------------------------------------------------------------

def test_catching_up_is_true_exactly_while_a_ledger_catches_up(tmp_path):
    from quam_state_manager.core.hub_store import HubStore
    from tests.test_hub_versions import WIRING, chip_state, run as hub_run
    data = tmp_path / "data"
    for i in (1, 2, 3):
        hub_run(data, i, chip_state(t1=1e-5 * i))
    chip_dir = tmp_path / "chip_ledger"
    chip_dir.mkdir()
    old = dict(hub_sync._SYNCS)
    hub_sync._SYNCS.clear()
    try:
        assert hub_sync.catching_up() is False
        cs = hub_sync.sync_for(chip_dir)
        cs.set_roots([(str(data), "declared")])
        cs.request(full=True)
        assert hub_sync.catching_up() is True
        with HubStore(chip_dir) as store:
            while hub_sync.run(chip_dir, store, None):
                pass
        assert cs.status()["state"] == "ready"
        assert hub_sync.catching_up() is False
        cs.request(full=True)                # a moved root: building again
        assert hub_sync.catching_up() is True
        cs.active = False                    # another chip's idle sync never counts
        assert hub_sync.catching_up() is False
    finally:
        hub_sync._SYNCS.clear()
        hub_sync._SYNCS.update(old)
    assert WIRING                              # (the fixture's wiring is what the runs saved)


# ---------------------------------------------------------------------------
# 6. the index warmer
# ---------------------------------------------------------------------------

def _truncated_store(root: Path, chunk: int = 8) -> DatasetStore:
    with dataset.yielding_scan(lambda: True, chunk=chunk):
        st = DatasetStore(root)
    assert st.scan_truncated
    return st


def test_the_warmer_waits_for_quiet_then_finishes_the_store(tmp_path):
    st = _truncated_store(_archive(tmp_path / "data", 50))
    busy = {"n": 4}

    def should_yield():
        busy["n"] -= 1
        return busy["n"] >= 0
    w = dataset_warm.IndexWarmer(lambda: [st], should_yield, idle_poll_s=0.01)
    before = st.run_count
    while w.run_once():
        pass
    assert w.waited_s > 0.02, "it stepped while the interpreter was wanted"
    assert st.run_count == 50 and not st.scan_truncated and before == 8


def test_a_warmer_step_stops_mid_scan_when_the_interpreter_is_wanted(tmp_path):
    st = _truncated_store(_archive(tmp_path / "data", 8 + 3 * dataset._YIELD_CHUNK))
    flips = {"asked": 0}

    def should_yield():
        flips["asked"] += 1
        return flips["asked"] > 2       # quiet for the wait, wanted mid-step
    w = dataset_warm.IndexWarmer(lambda: [st], should_yield, idle_poll_s=0.01, max_wait_s=0.05)
    w.run_once()
    assert st.scan_truncated and st.run_count == 8 + dataset._YIELD_CHUNK, st.run_count


def test_the_app_hands_a_store_left_filling_to_the_warmer(tmp_path, monkeypatch):
    total = 3 * dataset._YIELD_CHUNK
    data = _archive(tmp_path / "data", total)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    app.config["DATASET_INDEX_WARM"] = True
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    gate = {"busy": True}
    monkeypatch.setattr(routes_mod, "_background_scan_should_yield", lambda: gate["busy"])
    assert c.get("/datasets/wait?since=-1&timeout=0").status_code == 200
    st = next(s for s in app.config["dataset_store_lru"].values() if s.folder_path == data)
    assert st.scan_truncated and st.run_count == dataset._YIELD_CHUNK
    time.sleep(0.5)
    assert st.run_count < total, "the warmer worked while the interpreter was wanted"
    gate["busy"] = False
    try:
        assert _wait(lambda: not st.scan_truncated and st.run_count == total), st.run_count
    finally:
        for k in ("dataset_index_warmer", "run_watcher", "run_ingest"):
            if app.config.get(k) is not None:
                app.config[k].stop()


def test_the_prewarm_probe_reads_the_index_thread(monkeypatch):
    assert hub.index_prewarm_busy() in (False, True)
    monkeypatch.setattr(hub, "_PREWARM_THREAD", object())
    assert hub.index_prewarm_busy() is True
    monkeypatch.setattr(hub, "_PREWARM_THREAD", None)
    assert hub.index_prewarm_busy() is False


def test_the_datasets_delta_poll_yields_to_a_catch_up_only(ws_app, monkeypatch):
    """The page's own delta poll finishes a cold index -- but stops at the next
    chunk while a history catch-up builds (it reports partial; the next poll
    continues), and a user's other request never stops it."""
    app, c, data = ws_app
    for rid in range(41, 41 + 3 * dataset._YIELD_CHUNK):
        _run(data, rid, date="2026-09-02")
    with app.app_context():
        with dataset.yielding_scan(lambda: True, chunk=8):
            st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.scan_truncated and st.run_count == 8
    total = 40 + 3 * dataset._YIELD_CHUNK
    from quam_state_manager.core.run_ingest import FOREGROUND
    monkeypatch.setattr(hub_sync, "catching_up", lambda: True)
    got = c.get("/datasets/changes-since?ts=0").get_json()
    # v1.3.0 (dsperf): one chunk per poll -> none for a store still building
    # its first index: ~10 polls' chunks during a catch-up slowed it (measured)
    assert got["partial"] and st.run_count == 8, st.run_count
    monkeypatch.setattr(hub_sync, "catching_up", lambda: False)
    FOREGROUND.enter()                       # another request in flight: not a reason
    try:
        got = c.get("/datasets/changes-since?ts=0").get_json()
    finally:
        FOREGROUND.exit()
    assert not got["partial"] and st.run_count == total and not st.scan_truncated, st.run_count


def test_the_datasets_render_shows_what_is_indexed_while_a_catch_up_builds(ws_app, monkeypatch):
    app, c, data = ws_app
    for rid in range(41, 41 + 3 * dataset._YIELD_CHUNK):
        _run(data, rid, date="2026-09-02")
    with app.app_context():
        with dataset.yielding_scan(lambda: True, chunk=8):
            st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.run_count == 8
    monkeypatch.setattr(hub_sync, "catching_up", lambda: True)
    assert c.get("/datasets").status_code == 200
    # v1.3.0 (dsperf): indexed + one chunk -> indexed as it is: the render's
    # walk and chunk held the first rows 4-6 s during a catch-up (measured)
    assert st.scan_truncated and st.run_count == 8, st.run_count
    monkeypatch.setattr(hub_sync, "catching_up", lambda: False)
    # v1.3.0 (dsperf): after the catch-up the render no longer continues the
    # index (its 3 s parse held the first rows 4.5 s, measured) -> the delta
    # poll (and the index warmer) does
    assert c.get("/datasets").status_code == 200 and st.run_count == 8
    assert not c.get("/datasets/changes-since?ts=0").get_json()["partial"]
    assert not st.scan_truncated and st.run_count == 40 + 3 * dataset._YIELD_CHUNK


def test_the_new_run_poll_on_every_page_builds_under_the_yield(tmp_path, monkeypatch):
    data = _archive(tmp_path / "data", 2 * dataset._YIELD_CHUNK)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    app.config.pop("dataset_store_lru", None)
    monkeypatch.setattr(routes_mod, "_background_scan_should_yield", lambda: True)
    assert c.get("/datasets/poll").status_code == 200
    st = next(s for s in app.config["dataset_store_lru"].values() if s.folder_path == data)
    assert st.scan_truncated and st.run_count == dataset._YIELD_CHUNK, st.run_count
