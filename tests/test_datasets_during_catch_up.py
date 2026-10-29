"""v1.3.0 (dsperf): the Datasets page opened while a history catch-up runs.

Measured on a copy of a 21-qubit chip with ~3,500 runs and a cold Datasets
index, the page opened right after the chip open: the catch-up took 36-57 s
instead of ~16 s with no page, the first rows took 4-6 s and the index was
complete only 20 s after the catch-up. Three things held the interpreter
(py-spy, GIL samples in 4 s windows):

* the delta poll shipped every run of the index being built as an "updated"
  row and queued each as an EXP-version candidate; the identity gate decoded
  every run's saved state.json (~1 MB): 65-80% of the interpreter during the
  catch-up, and most of it for a minute after;
* every yielding scan (the delta poll, the render, the warmer) walked every
  date dir still unread -- ~13,000 stats -- to parse one 64-run chunk;
* the render walked and parsed a chunk before showing the rows it held (and
  just after the catch-up, its 3 s render budget of parsing).

Pins (scheduling decisions, not timings):

* a yielding scan stops walking once it holds a chunk to parse and should
  yield, the dirs it did not walk still give their dates and experiment names,
  and the next scans finish the very index a plain scan builds;
* ``DatasetStore.index_complete`` is False until a walk finishes;
* the delta poll queues EXP-version candidates from a store whose index was
  complete before the poll, never from one still building its first index;
* while a catch-up runs, the delta poll and the new-run poll answer from what
  a store still building its first index holds -- no scan -- and wake the
  index warmer; a complete store is still scanned (a run that landed shows);
* the render serves a store still building its first index as it is (and
  wakes the index warmer), during a catch-up or after it, while a complete
  store is still rescanned (a run that landed shows).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import dataset, hub_sync
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app


def _run(root: Path, rid: int, *, date: str, exp: str = "scan", quam_state: bool = False) -> Path:
    hhmmss = f"{(rid // 3600) % 24:02d}{(rid // 60) % 60:02d}{rid % 60:02d}"
    run = root / date / f"#{rid}_{exp}_{hhmmss}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": exp, "status": "successful"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": rid, "parents": []}), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({"fit_results": {"q1": {"amp": 0.1 * rid}}}),
                                   encoding="utf-8")
    if quam_state:
        qs = run / "quam_state"
        qs.mkdir()
        (qs / "state.json").write_text("{}", encoding="utf-8")
        (qs / "wiring.json").write_text("{}", encoding="utf-8")
    return run


def _two_days(root: Path, per_day: int = 40) -> Path:
    """Newest day 2026-09-02 (experiment "new_exp"), older day 2026-09-01 ("old_exp")."""
    for rid in range(1, per_day + 1):
        _run(root, rid, date="2026-09-01", exp="old_exp")
    for rid in range(per_day + 1, 2 * per_day + 1):
        _run(root, rid, date="2026-09-02", exp="new_exp")
    return root


class _CountStats:
    def __init__(self, monkeypatch):
        self.n = 0
        real = DatasetStore._stat_fp

        def counted(path):
            self.n += 1
            return real(path)
        monkeypatch.setattr(DatasetStore, "_stat_fp", staticmethod(counted))


# ---------------------------------------------------------------------------
# 1. a yielding scan walks only what it parses
# ---------------------------------------------------------------------------

def test_a_yielding_scan_stops_walking_once_it_holds_a_chunk(tmp_path, monkeypatch):
    root = _two_days(tmp_path / "data")
    stats = _CountStats(monkeypatch)
    with dataset.yielding_scan(lambda: True, chunk=4):
        ds = DatasetStore(root)
    assert ds.scan_truncated and ds.run_count == 4, ds.run_count
    # 80 runs x 3 stats = 240 for a full walk; the cut is asked every 8th run
    assert stats.n <= 3 * 16, f"walked {stats.n // 3} runs to parse 4"
    # the newest day is the one parsed
    assert {r.date for r in ds.runs.values()} == {"2026-09-02"}


def test_the_dirs_a_yielding_scan_did_not_walk_still_list_their_dates_and_experiments(tmp_path):
    root = _two_days(tmp_path / "data")
    with dataset.yielding_scan(lambda: True, chunk=4):
        ds = DatasetStore(root)
    plain = DatasetStore(root)
    assert ds.run_count == 4
    assert ds.dates == plain.dates == ["2026-09-02", "2026-09-01"]
    assert ds.experiment_types == plain.experiment_types == ["new_exp", "old_exp"]


def test_yielding_scans_continue_to_the_index_a_plain_scan_builds(tmp_path):
    root = _two_days(tmp_path / "data")
    with dataset.yielding_scan(lambda: True, chunk=4):
        ds = DatasetStore(root)
        for _ in range(40):
            if not ds.scan_truncated:
                break
            ds.rescan_if_stale()
    plain = DatasetStore(root)
    assert not ds.scan_truncated and ds.run_count == plain.run_count == 80
    assert ds.list_runs_compact() == plain.list_runs_compact()
    assert ds.dates == plain.dates and ds.experiment_types == plain.experiment_types


def test_a_scan_nothing_waits_for_still_walks_everything(tmp_path, monkeypatch):
    root = _two_days(tmp_path / "data")
    stats = _CountStats(monkeypatch)
    with dataset.yielding_scan(lambda: False, chunk=4):
        ds = DatasetStore(root)
    assert ds.run_count == 80 and not ds.scan_truncated
    assert stats.n >= 3 * 80


# ---------------------------------------------------------------------------
# 2. index_complete
# ---------------------------------------------------------------------------

def test_index_complete_is_false_until_a_walk_finishes(tmp_path):
    root = _two_days(tmp_path / "data")
    with dataset.yielding_scan(lambda: True, chunk=4):
        ds = DatasetStore(root)
    assert not ds.index_complete
    ds.rescan_if_stale()
    assert ds.index_complete and not ds.scan_truncated
    assert DatasetStore(root).index_complete


# ---------------------------------------------------------------------------
# 3. the delta poll and EXP-version candidates
# ---------------------------------------------------------------------------

@pytest.fixture
def qs_app(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for rid in range(1, 41):
        _run(data, rid, date="2026-09-01", quam_state=True)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    queued: list = []
    monkeypatch.setattr(routes_mod, "_enqueue_exp_ingest",
                        lambda runs, **kw: queued.extend(r.run_id for r in runs))
    return app, c, data, queued


def test_the_delta_poll_never_queues_the_first_index_as_exp_candidates(qs_app, monkeypatch):
    app, c, data, queued = qs_app
    with app.app_context():
        with dataset.yielding_scan(lambda: True, chunk=8):
            st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.scan_truncated and not st.index_complete
    # This test is about the poll that finishes the index, not about yielding:
    # a background catch-up or prewarm left over on a slow CI runner would make
    # the poll hold the store as partial, so nothing else claims the interpreter.
    monkeypatch.setattr(routes_mod, "_catch_up_should_yield", lambda: False)
    got = c.get("/datasets/changes-since?ts=0").get_json()   # finishes the index
    assert not got["partial"] and st.index_complete and st.run_count == 40
    assert len(got["updated"]) == 40 and all(r["hs"] for r in got["updated"])
    assert queued == [], f"the index being built was queued as arrivals: {queued[:5]}"


def test_a_run_that_lands_in_a_complete_index_is_still_an_exp_candidate(qs_app):
    app, c, data, queued = qs_app
    with app.app_context():
        st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.index_complete
    ts = c.get("/datasets/changes-since?ts=0").get_json()["now"]
    queued.clear()
    _run(data, 41, date="2026-09-03", quam_state=True)
    got = c.get(f"/datasets/changes-since?ts={ts}").get_json()
    assert [r["id"] for r in got["updated"]] == [41]
    assert queued == [41]


def test_during_a_catch_up_the_polls_do_not_scan_a_store_still_building_its_index(
        qs_app, monkeypatch):
    app, c, data, _ = qs_app
    with app.app_context():
        with dataset.yielding_scan(lambda: True, chunk=8):
            st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.run_count == 8 and not st.index_complete
    kicks = []
    monkeypatch.setattr(routes_mod, "_kick_dataset_index_warmer", lambda: kicks.append(1))
    monkeypatch.setattr(hub_sync, "catching_up", lambda: True)
    got = c.get("/datasets/changes-since?ts=0").get_json()
    assert got["partial"] and len(got["updated"]) == 8
    assert st.run_count == 8, f"the delta poll scanned during a catch-up: {st.run_count}"
    assert kicks, "the delta poll did not wake the index warmer"
    kicks.clear()
    new_run = c.get("/datasets/poll").get_json()
    assert new_run["run_id"] in st.runs
    assert st.run_count == 8, f"the new-run poll scanned during a catch-up: {st.run_count}"
    assert kicks, "the new-run poll did not wake the index warmer"
    monkeypatch.setattr(hub_sync, "catching_up", lambda: False)
    got = c.get("/datasets/changes-since?ts=0").get_json()
    assert not got["partial"] and st.run_count == 40 and st.index_complete


def test_during_a_catch_up_the_delta_poll_still_scans_a_complete_store(qs_app, monkeypatch):
    app, c, data, queued = qs_app
    with app.app_context():
        st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.index_complete
    ts = c.get("/datasets/changes-since?ts=0").get_json()["now"]
    queued.clear()
    _run(data, 41, date="2026-09-03", quam_state=True)
    monkeypatch.setattr(hub_sync, "catching_up", lambda: True)
    got = c.get(f"/datasets/changes-since?ts={ts}").get_json()
    assert [r["id"] for r in got["updated"]] == [41] and queued == [41]


def test_changes_since_without_a_rescan_reports_what_is_indexed(tmp_path):
    root = _two_days(tmp_path / "data")
    with dataset.yielding_scan(lambda: True, chunk=4):
        ds = DatasetStore(root)
    got = ds.changes_since(0.0, rescan=False)
    assert ds.run_count == 4 and len(got["updated"]) == 4 and got["partial"]
    got = ds.changes_since(0.0)
    assert ds.run_count == 80 and len(got["updated"]) == 80 and not got["partial"]


# ---------------------------------------------------------------------------
# 4. the render of a store still building its first index
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("catching_up", [True, False])
def test_the_render_serves_a_store_still_building_its_first_index_as_it_is(
        qs_app, monkeypatch, catching_up):
    app, c, data, _ = qs_app
    for rid in range(41, 41 + 3 * dataset._YIELD_CHUNK):
        _run(data, rid, date="2026-09-02")
    with app.app_context():
        with dataset.yielding_scan(lambda: True, chunk=8):
            st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.run_count == 8 and not st.index_complete
    kicks = []
    monkeypatch.setattr(routes_mod, "_kick_dataset_index_warmer", lambda: kicks.append(1))
    monkeypatch.setattr(hub_sync, "catching_up", lambda: catching_up)
    r = c.get("/datasets")
    assert r.status_code == 200 and b'id="ds-scan-note"' in r.data
    assert st.run_count == 8, f"the render scanned a filling store: {st.run_count}"
    assert kicks, "the index warmer was not woken"
    # the delta poll continues it (catch-up over)
    monkeypatch.setattr(hub_sync, "catching_up", lambda: False)
    assert not c.get("/datasets/changes-since?ts=0").get_json()["partial"]
    assert st.run_count == 40 + 3 * dataset._YIELD_CHUNK and st.index_complete


def test_during_a_catch_up_the_render_still_rescans_a_complete_store(qs_app, monkeypatch):
    app, c, data, _ = qs_app
    with app.app_context():
        st = routes_mod._get_or_create_store(data, rescan=False)
    assert st.index_complete and st.run_count == 40
    _run(data, 41, date="2026-09-03")
    monkeypatch.setattr(hub_sync, "catching_up", lambda: True)
    assert c.get("/datasets").status_code == 200
    assert 41 in st.runs, "a run that landed was not shown by the render"
