"""core/run_ingest -- a new run absorbed OFF the request path (design
ram_design.md §3 "Run-watch tick"; P3's "after a new run <= 30 ms").

Two halves, both pinned:

* SPEED: after the watcher's tick and one ingest pass, the first Trends
  request after a run lands does NO compute -- the index was appended and the
  recently asked series view re-encoded in the background (memo compute
  counters unchanged across the request), and the store's gate is closed.
* CORRECTNESS: what that request serves equals a cold recompute, and a tick
  that never came (or came before the run) changes nothing but time.
"""
from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path

import pytest

from quam_state_manager.core import run_ingest, run_watch
from quam_state_manager.core import trend_index as ti
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

from tests.test_trend_index import A, B, _run, cold


@pytest.fixture(autouse=True)
def _fresh():
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO, routes_mod._TRENDS_SAME_CHIP):
        m.clear()
    with ti._recent_lock:
        ti._recent.clear()
    yield
    for m in (ti.INDEX_MEMO, ti.SERIES_MEMO, ti.PARAMS_MEMO):
        m.clear()


# ---------------------------------------------------------------------------
# the watcher's listener
# ---------------------------------------------------------------------------

class TestWatcherListener:
    def test_a_moved_root_is_handed_to_the_listener_and_nothing_else_is(self):
        sigs = {"r1": 1, "r2": 1}
        w = run_watch.RunWatcher(signature_fn=lambda r: sigs[r])
        got: list[list[str]] = []
        w.add_listener(got.append)
        w.set_roots(["r1", "r2"])
        assert w.poll_once() is False and got == []          # baseline, no change
        sigs["r2"] = 2
        assert w.poll_once() is True and got == [["r2"]]      # only the root that moved
        assert w.poll_once() is False and got == [["r2"]]     # unchanged: not called

    def test_a_failing_listener_never_breaks_the_tick(self):
        sigs = {"r": 1}
        w = run_watch.RunWatcher(signature_fn=lambda r: sigs[r])

        def boom(_roots):
            raise RuntimeError("listener bug")

        w.add_listener(boom)
        w.set_roots(["r"])
        t0 = w.tick
        sigs["r"] = 2
        assert w.poll_once() is True and w.tick == t0 + 1


# ---------------------------------------------------------------------------
# the worker, against a real store
# ---------------------------------------------------------------------------

@pytest.fixture
def root(tmp_path):
    r = tmp_path / "data"
    for rid in range(1, 16):
        _run(r, rid, A if rid % 3 else B, date="2026-09-01")
    return r


def _land(root: Path, rid: int, exp: str = A) -> None:
    _run(root, rid, exp, date="2026-09-01")
    # the date dir's mtime is what opens the store's gate; on a coarse clock
    # the copy can land in the same tick, so move it explicitly
    d = root / "2026-09-01"
    st = d.stat()
    import os
    os.utime(d, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))


class TestIngestPass:
    def test_after_one_pass_the_request_computes_nothing_and_equals_cold(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        ti.series_blob(sel, A, None)                   # the view the user has open
        ti.series_blob(sel, A, "q1")
        _land(root, 99)
        ing = run_ingest.RunIngest(lambda roots: [store])
        ing.kick([str(root)])
        assert ing.run_once() == 1
        assert store._current_mtime() == store._last_mtime   # the gate is closed
        idx = ti.INDEX_MEMO.peek(ti._key(store=store.instance_seq, index_exp=A))[1]
        assert idx.run_ids[-1] == 99                   # appended in the background
        c_idx, c_ser = ti.INDEX_MEMO.computes, ti.SERIES_MEMO.computes
        for q in (None, "q1"):
            got = json.loads(ti.series_blob(sel, A, q).json_bytes())
            assert got == cold(sel, A, q) and got["runs"][-1][0] == 99
        assert (ti.INDEX_MEMO.computes, ti.SERIES_MEMO.computes) == (c_idx, c_ser)

    def test_other_experiments_indexes_are_refreshed_too(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        ti.series_blob(sel, A, None)
        ti.series_blob(sel, B, None)
        _land(root, 98, B)
        ing = run_ingest.RunIngest(lambda roots: [store])
        ing.kick(["x"]); ing.run_once()
        c = ti.SERIES_MEMO.computes
        assert json.loads(ti.series_blob(sel, B, None).json_bytes()) == cold(sel, B)
        assert json.loads(ti.series_blob(sel, A, None).json_bytes()) == cold(sel, A)
        assert ti.SERIES_MEMO.computes == c

    def test_an_index_nobody_is_viewing_as_series_is_appended_too(self, root):
        """The Parameter Differences of a view whose series left the recent
        list still reads the index: it must already be current."""
        store = DatasetStore(root)
        sel = [("k", store)]
        ti.series_blob(sel, A, None)
        with ti._recent_lock:
            ti._recent.clear()
        _land(root, 95)
        ing = run_ingest.RunIngest(lambda roots: [store])
        ing.kick(["x"]); ing.run_once()
        c = ti.INDEX_MEMO.computes
        got = json.loads(ti.series_blob(sel, A, None).json_bytes())
        assert got == cold(sel, A) and got["runs"][-1][0] == 95
        assert ti.INDEX_MEMO.computes == c

    def test_without_a_pass_the_request_still_serves_the_new_run(self, root):
        """The tick is speed only: no ingest at all -> the request pays, but
        the answer is the same."""
        store = DatasetStore(root)
        sel = [("k", store)]
        ti.series_blob(sel, A, None)
        _land(root, 97)
        store.rescan_if_stale()                        # what the route does itself
        got = json.loads(ti.series_blob(sel, A, None).json_bytes())
        assert got == cold(sel, A) and got["runs"][-1][0] == 97

    def test_a_pass_before_the_run_changes_nothing(self, root):
        store = DatasetStore(root)
        sel = [("k", store)]
        ti.series_blob(sel, A, None)
        ing = run_ingest.RunIngest(lambda roots: [store])
        ing.kick(["x"]); ing.run_once()   # a tick with nothing new
        _land(root, 96)
        store.rescan_if_stale()
        got = json.loads(ti.series_blob(sel, A, None).json_bytes())
        assert got == cold(sel, A) and got["runs"][-1][0] == 96

    def test_a_collected_store_is_not_kept_alive_or_touched(self, root):
        import gc, weakref
        store = DatasetStore(root)
        ti.series_blob([("k", store)], A, None)
        ref = weakref.ref(store)
        del store
        gc.collect()
        assert ref() is None
        other = DatasetStore(root)
        assert ti.refresh_store(other) == {"indexes": 0, "series": 0}

    def test_a_failing_store_is_counted_not_raised(self, root):
        class Bad:
            folder_path = root

            def rescan_if_stale(self, deadline=None):
                raise OSError("share gone")

        ing = run_ingest.RunIngest(lambda roots: [Bad()], refresh=lambda s: None)
        ing.kick(["x"])
        assert ing.run_once() == 0 and ing.errors == 1


# ---------------------------------------------------------------------------
# the route wiring: /datasets/wait's watcher -> the app's ingest -> the store
# the routes hold
# ---------------------------------------------------------------------------

def test_the_app_wires_the_watcher_to_the_ingest_and_the_routes_store(tmp_path, monkeypatch):
    data = tmp_path / "data"
    for rid in range(1, 12):
        _run(data, rid, A, date="2026-09-01")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    key = routes_mod._folder_key(data)
    url = f"/trends/series?experiment={A}&folders={key}"
    assert c.get(url).status_code == 200
    # the handshake registers the roots and creates watcher + ingest
    assert c.get("/datasets/wait?since=-1").status_code == 200
    w, ing = app.config["run_watcher"], app.config["run_ingest"]
    w.stop(); ing.stop()                               # drive both by hand
    _land(data, 50)
    assert w.poll_once() is True                       # the tick sees the run ...
    assert ing.run_once() == 1                         # ... and the ingest takes it
    c_ser = ti.SERIES_MEMO.computes
    r = c.get(url, headers={"Accept-Encoding": "gzip"})
    body = json.loads(gzip.decompress(r.data) if r.headers.get("Content-Encoding") == "gzip"
                      else r.data)
    assert body["runs"][-1][0] == 50 and ti.SERIES_MEMO.computes == c_ser
    ram = c.get("/debug/ram").get_json()
    assert ram["run_ingest"]["passes"] >= 1 and ram["run_ingest"]["errors"] == 0
