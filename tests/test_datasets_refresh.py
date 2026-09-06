"""docs/170 -- the Datasets panel, the Rescan button and the "N new" chip.

Two customer reports (2026-09-05): the run table's loading "got slow again"
and its refresh button with it; and the "N new" chip on the sync pill kept
counting up, and clicking it did nothing but make it disappear.

What was measured (real Chrome over CDP, the 2,655-run CQT archive):

* The server code on the Datasets path is byte-identical between the 09-02
  build the customer praised and the 09-05 build they reported. What is slow
  is structural: the cold build of a data folder (~11 file operations per
  run, 29,000 on this archive) is bounded at 3 s (docs/142 E) and then
  CONTINUED, unbounded, by the next request that rescans -- the user's own
  first click on Datasets. 2.2 s here; 31.6 s at the customer's share latency.
  And three requests at page load each built their own store (the LRU kept
  one).
* Rescan re-parsed every run (3.5-5.8 s here, 20 s budget on the share) and
  then reloaded the whole page (~45 requests).
* The chip: app.js registered its click handler against `window.SyncBadge`
  at load time, and base.html loaded sync-badge.js AFTER app.js. Never
  registered; `_ackStamp` never moved; the count could only grow.

The fixes are pinned here and in newrun_poll_selfcheck.cjs /
dataset_poll_selfcheck.cjs / test_sync_badge.py.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parents[1]
_ROUTES = (_ROOT / "quam_state_manager" / "web" / "routes.py").read_text(encoding="utf-8")
_APP_JS = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
_DS_HTML = (_ROOT / "quam_state_manager" / "web" / "templates" / "_datasets.html").read_text(encoding="utf-8")


def _seed_run(root: Path, run_id: int, *, date: str, hhmmss: str = "010000",
              name: str = "test_experiment", t1: float = 8.0e-6) -> Path:
    date_dir = root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    run = date_dir / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir()
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": [f"q{run_id}"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({
        "fit_results": {f"q{run_id}": {"T1": t1}},
    }), encoding="utf-8")
    return run


def _app(tmp_path: Path, root: Path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    r = c.post("/workspace/add", data={"folder": str(root)})
    assert r.status_code in (200, 204), r.status_code
    return app, c


HX = {"HX-Request": "true"}


# ---------------------------------------------------------------------------
# 1. The store: what a truncated build reports, and what a re-read costs
# ---------------------------------------------------------------------------

class TestScanTruncatedIsHonest:
    def test_a_build_cut_at_its_deadline_says_so_and_the_next_walk_clears_it(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        for i in range(1, 4):
            _seed_run(root, i, date=f"2026-05-0{i}")
        from quam_state_manager.core import dataset as D
        # a cold budget of zero: the walk stops at once
        monkeypatch.setattr(D, "_COLD_SCAN_BUDGET_S", 0.0)
        store = DatasetStore(root)
        assert store.scan_truncated is True
        assert len(store.runs) < 3
        # the gate stays open: the next (unbounded) walk finishes the job
        store.rescan_if_stale()
        assert store.scan_truncated is False
        assert set(store.runs) == {1, 2, 3}

    def test_a_complete_build_is_not_marked_partial(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        assert DatasetStore(root).scan_truncated is False

    def test_the_deadline_cuts_inside_a_date_dir_and_that_dir_is_walked_again(self, tmp_path, monkeypatch):
        """One busy day is ~1,900 stats on the pilot archive (3.4 s on the
        customer's share), so a per-dir check let a 3 s budget run to 5.4 s
        and a 20 s Rescan to 44 s. The walk now stops between RUNS -- and the
        dir it stopped inside gets no fingerprint, so the B27 short-circuit
        cannot serve a partial run set as a complete one on the next scan."""
        root = tmp_path / "data"
        for i in range(1, 31):
            _seed_run(root, i, date="2026-05-01", hhmmss=f"{i:02d}0000")
        day = root / "2026-05-01"
        real_fp = DatasetStore._stat_fp

        def slow_fp(path):
            time.sleep(0.004)              # ~12 ms per run, ~360 ms for the day
            return real_fp(path)
        monkeypatch.setattr(DatasetStore, "_stat_fp", staticmethod(slow_fp))
        from quam_state_manager.core import dataset as D
        monkeypatch.setattr(D, "_COLD_SCAN_BUDGET_S", 0.05)
        store = DatasetStore(root)
        assert store.scan_truncated is True
        assert 0 < len(store.runs) < 30, "cut INSIDE the day, not before or after it"
        assert day not in store._date_fp, "a dir cut mid-walk must not be fingerprinted"
        store.rescan_if_stale()                 # unbounded: the continuation
        assert store.scan_truncated is False
        assert set(store.runs) == set(range(1, 31))
        assert day in store._date_fp


class TestRereadWithoutReparse:
    """The explicit Rescan re-READS every run (its docs/105 #5 contract); a
    run whose two files hash the same is handed back as the very object
    already held, unparsed, with its delta cursor untouched."""

    def test_unchanged_runs_keep_their_objects_and_cursor(self, tmp_path):
        root = tmp_path / "data"
        for i in range(1, 6):
            _seed_run(root, i, date="2026-05-01", hhmmss=f"0{i}0000")
        store = DatasetStore(root)
        before = {rid: (r, r.last_parsed) for rid, r in store.runs.items()}
        assert all(r.content_fp is not None for r, _ in before.values())
        time.sleep(0.02)
        store.force_rescan()
        for rid, (obj, lp) in before.items():
            assert store.runs[rid] is obj, f"run {rid} was re-parsed for nothing"
            assert store.runs[rid].last_parsed == lp

    def test_a_same_stat_rewrite_is_still_re_read(self, tmp_path):
        """docs/105 #5's own case, unchanged: the bytes differ, so the hash
        differs, so it is parsed -- whatever the stat says."""
        root = tmp_path / "data"
        run = _seed_run(root, 1, date="2026-05-01", t1=8.25e-6)
        store = DatasetStore(root)
        data_path = run / "data.json"
        st = data_path.stat()
        data_path.write_text(json.dumps({"fit_results": {"q1": {"T1": 9.75e-6}}}), encoding="utf-8")
        os.utime(data_path, ns=(st.st_atime_ns, st.st_mtime_ns))
        assert data_path.stat().st_size == st.st_size
        store.force_rescan()
        assert store.runs[1].fit_results["q1"]["T1"] == pytest.approx(9.75e-6)

    def test_a_file_landing_beside_unchanged_json_refreshes_the_flags(self, tmp_path):
        """node.json + data.json byte-identical, but an h5 appeared: the
        folder's own mtime moved, so the reused object re-stats its has_*
        flags -- and the row's cursor advances so the delta poll ships it."""
        root = tmp_path / "data"
        run = _seed_run(root, 1, date="2026-05-01")
        store = DatasetStore(root)
        assert store.runs[1].has_ds_raw is False
        lp = store.runs[1].last_parsed
        time.sleep(0.02)
        (run / "ds_raw.h5").write_bytes(b"\x89HDF")
        os.utime(run, None)
        store.force_rescan()
        assert store.runs[1].has_ds_raw is True
        assert store.runs[1].last_parsed > lp

    def test_a_half_written_run_is_never_reused(self, tmp_path):
        root = tmp_path / "data"
        run = _seed_run(root, 1, date="2026-05-01")
        (run / "node.json").write_text("{\"metadata\": {", encoding="utf-8")   # mid-write
        store = DatasetStore(root)
        assert store.runs[1].incomplete is True
        obj = store.runs[1]
        # the writer finishes with the SAME bytes it started (a re-run)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "test_experiment", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        }), encoding="utf-8")
        store.force_rescan()
        assert store.runs[1] is not obj
        assert store.runs[1].incomplete is False
        assert store.runs[1].status == "successful"


# ---------------------------------------------------------------------------
# 2. The routes: one builder, bounded renders, a Rescan that swaps the table
# ---------------------------------------------------------------------------

class TestOneBuilderPerFolder:
    def test_concurrent_first_requests_build_the_store_once(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, _c = _app(tmp_path, root)
        built = []
        real = routes.DatasetStore

        class Counting(real):
            def __init__(self, folder, **kw):
                built.append(threading.get_ident())
                time.sleep(0.15)                 # long enough for the others to arrive
                super().__init__(folder, **kw)
        monkeypatch.setattr(routes, "DatasetStore", Counting)
        got = []

        def one():
            with app.app_context():
                got.append(routes._get_or_create_store(root.resolve()))
        ts = [threading.Thread(target=one) for _ in range(4)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert len(built) == 1, f"the store was built {len(built)} times"
        assert len({id(s) for s in got}) == 1, "every caller shares the one store"


class TestTheRenderIsBounded:
    def test_the_render_hands_its_stores_a_deadline(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, c = _app(tmp_path, root)
        seen: list = []
        orig = DatasetStore.rescan_if_stale

        def spy(self, deadline=None):
            seen.append(deadline)
            return orig(self, deadline=deadline)
        monkeypatch.setattr(DatasetStore, "rescan_if_stale", spy)
        assert c.get("/datasets", headers=HX).status_code == 200   # builds the store
        t0 = time.monotonic()
        r = c.get("/datasets", headers=HX)                          # rescans it
        assert r.status_code == 200
        assert seen and seen[-1] is not None
        assert t0 < seen[-1] <= t0 + routes._RENDER_SCAN_BUDGET_S + 1.0

    def test_the_new_run_poll_is_bounded_too(self):
        body = _ROUTES[_ROUTES.index("def datasets_poll("):]
        body = body[:body.index("\n@bp.route")]
        assert "deadline=time.monotonic() + _POLL_BUDGET_S" in body

    def test_a_partial_render_says_so_and_a_complete_one_does_not(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, c = _app(tmp_path, root)
        r = c.get("/datasets", headers=HX)
        assert r.status_code == 200 and b"ds-scan-note" not in r.data
        with app.app_context():
            store = routes._get_or_create_store(root.resolve(), rescan=False)
        monkeypatch.setattr(store, "_last_scan_truncated", True, raising=False)
        r = c.get("/datasets", headers=HX)
        assert b'id="ds-scan-note"' in r.data
        assert b"still indexing" in r.data


class TestRescanSwapsTheTable:
    def test_htmx_rescan_answers_with_the_table_not_a_reload(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        _seed_run(root, 2, date="2026-05-02")
        app, c = _app(tmp_path, root)
        r = c.post("/datasets/rescan", headers=HX)
        assert r.status_code == 200
        assert "HX-Redirect" not in r.headers
        assert b'id="dataset-search"' in r.data          # the same partial the link swaps
        assert b'id="ds-rows-data"' in r.data

    def test_rescan_keeps_the_date_tab_it_was_pressed_from(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        _seed_run(root, 2, date="2026-05-02")
        app, c = _app(tmp_path, root)
        r = c.post("/datasets/rescan", data={"date": "2026-05-01"}, headers=HX)
        m = re.search(rb'id="ds-rows-data"[^>]*>(.*?)</script>', r.data, re.S)
        rows = json.loads(m.group(1))
        assert [row["id"] for row in rows] == [1]
        assert b'id="ds-active-date" name="date" value="2026-05-01"' in r.data

    def test_the_button_sends_its_date_and_view_and_cannot_double_fire(self):
        i = _DS_HTML.index('hx-post="/datasets/rescan"')
        btn = _DS_HTML[i - 200:i + 400]
        assert 'hx-include="#ds-active-date"' in btn
        assert "hx-vals='{\"view\": \"{{ view_mode }}\"}'" in btn
        assert 'hx-disabled-elt="this"' in btn

    def test_collections_rescan_stays_on_collections(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, c = _app(tmp_path, root)
        r = c.post("/datasets/rescan", data={"view": "collections"}, headers=HX)
        assert r.status_code == 200
        assert b"<h2" in r.data and b"Collections" in r.data

    def test_a_plain_browser_post_still_redirects(self, tmp_path):
        root = tmp_path / "data"
        _seed_run(root, 1, date="2026-05-01")
        app, c = _app(tmp_path, root)
        r = c.post("/datasets/rescan")
        assert r.status_code in (302, 303)


# ---------------------------------------------------------------------------
# 3. The chip's click: the wiring app.js must carry (behaviour is pinned in
#    newrun_poll_selfcheck.cjs; these are the facts a refactor drops silently)
# ---------------------------------------------------------------------------

class TestTheChipClickWiring:
    def test_the_click_refreshes_and_opens_no_card(self):
        i = _APP_JS.index("function _armNewRunAck()")
        body = _APP_JS[i:i + 600]
        assert "window.refreshRunLists()" in body
        assert "_showNewRunPopup" not in body

    def test_registration_survives_the_wrong_script_order(self):
        i = _APP_JS.index("if (!_armNewRunAck()) {")
        body = _APP_JS[i:i + 300]
        assert "DOMContentLoaded" in body and "setTimeout(_armNewRunAck, 0)" in body

    def test_refresh_run_lists_presses_the_two_real_buttons(self):
        i = _APP_JS.index("window.refreshRunLists = function () {")
        body = _APP_JS[i:i + 700]
        assert "'.btn-workspace-refresh'" in body
        assert "button[hx-post=\"/datasets/rescan\"]" in body
        assert "htmx-request" in body                  # never a second request on top of one in flight

    def test_pressing_a_refresh_button_yourself_acknowledges(self):
        i = _APP_JS.index("function _ackNewRuns()")
        body = _APP_JS[i:i + 1400]
        assert ".closest('.btn-workspace-refresh, button[hx-post=\"/datasets/rescan\"]')" in body
