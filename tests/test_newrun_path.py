"""RAM P7 -- the new-run path off the request (ram_design.md §3 "Run-watch
tick"): the sidebar's workspace rescan and the Datasets payload ride the run
watcher's tick (``routes._ingest_after_steps``), the rescan is single-flight
per root, and the rendered sidebar says which version it was drawn at so the
page's first poll can tell it is behind (it no longer did the rescan itself).
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import run_ingest
from quam_state_manager.core import scanner
from quam_state_manager.core.scanner import Workspace
from quam_state_manager.web import routes as R
from quam_state_manager.web.app import create_app

from tests.test_scanner_reuse import _mk_run, _TICK


@pytest.fixture(autouse=True)
def _fresh():
    R._DATASETS_PAYLOAD.clear()
    yield
    R._DATASETS_PAYLOAD.clear()


def _app(tmp_path, data):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/workspace/add", data={"folder": str(data)}).status_code in (200, 302)
    return app, c


def _steps(app):
    return {f.__name__: f for f in R._ingest_after_steps(app)}


def _stamp(html: str) -> int | None:
    m = re.search(r'data-ws-version="(\d+)"', html)
    return int(m.group(1)) if m else None


class TestSidebarStamp:
    def test_the_tree_carries_the_version_it_was_rendered_at(self, tmp_path):
        data = tmp_path / "data"
        _mk_run(data, "2026-03-01", 1)
        app, c = _app(tmp_path, data)
        ws = app.config["workspace"]
        html = c.get("/workspace/tree").get_data(as_text=True)
        assert _stamp(html) == ws.version

    def test_a_background_rescan_leaves_the_rendered_stamp_behind_the_poll(self, tmp_path):
        """What the page's first poll relies on: after the worker rescanned,
        the poll reports a version the tree on screen does not carry."""
        data = tmp_path / "data"
        _mk_run(data, "2026-03-01", 1)
        app, c = _app(tmp_path, data)
        shown = _stamp(c.get("/workspace/tree").get_data(as_text=True))
        time.sleep(_TICK)
        _mk_run(data, "2026-03-01", 2)
        _steps(app)["workspace_sidebar"]([str(data)])      # the worker's pass
        d = c.get("/workspace/tree/poll").get_json()
        assert d["rescanned"] is False and d["v"] != shown
        html = c.get("/workspace/tree").get_data(as_text=True)
        assert "#2_04_power_rabi" in html and _stamp(html) == d["v"]

    def test_the_base_page_poll_compares_the_stamp(self, tmp_path):
        """The client half: the baseline poll re-renders when the stamp on
        screen differs from the polled version (inline script in base.html)."""
        src = (Path(R.__file__).parent / "templates" / "base.html").read_text(encoding="utf-8")
        assert "querySelector('[data-ws-version]')" in src
        assert "_shown !== String(d.v)" in src


class TestWorkerSteps:
    def test_the_sidebar_step_does_the_rescan_the_request_used_to_pay(self, tmp_path, monkeypatch):
        data = tmp_path / "data"
        _mk_run(data, "2026-03-01", 1)
        app, c = _app(tmp_path, data)
        c.get("/workspace/tree")
        time.sleep(_TICK)
        _mk_run(data, "2026-03-01", 2)
        ing = run_ingest.RunIngest(lambda roots: [], refresh=lambda s: None)
        for f in R._ingest_after_steps(app):
            ing.add_after(f, f.__name__)
        ing.kick([str(data)])
        ing.run_once()
        calls = []
        real = scanner.Workspace.rescan_root
        monkeypatch.setattr(scanner.Workspace, "rescan_root",
                            lambda self, *a, **k: calls.append(1) or real(self, *a, **k))
        html = c.get("/workspace/tree").get_data(as_text=True)
        assert "#2_04_power_rabi" in html
        assert calls == [], "the request rescanned again"

    def test_the_datasets_step_warms_only_views_somebody_opened(self, tmp_path):
        data = tmp_path / "data"
        _mk_run(data, "2026-03-01", 1)
        app, c = _app(tmp_path, data)
        steps = _steps(app)
        n0 = R._DATASETS_PAYLOAD.computes                 # module memo: other tests count too
        steps["datasets_payload"]([str(data)])
        assert R._DATASETS_PAYLOAD.computes == n0         # nobody has the page open
        assert c.get("/datasets").status_code == 200
        n = R._DATASETS_PAYLOAD.computes
        time.sleep(_TICK)
        _mk_run(data, "2026-03-01", 2)
        with app.app_context():
            for f in R._active_dataset_stores(fast=True, rescan=False):
                f["store"].rescan_if_stale()
        steps["datasets_payload"]([str(data)])
        assert R._DATASETS_PAYLOAD.computes == n + 1     # re-encoded in the background
        body = c.get("/datasets").get_data(as_text=True)
        assert R._DATASETS_PAYLOAD.computes == n + 1     # ...so the page computed nothing
        assert '"id":2' in body

    def test_a_failing_step_is_counted_and_the_next_still_runs(self):
        ran = []
        ing = run_ingest.RunIngest(lambda roots: [], refresh=lambda s: None)

        def boom(roots):
            raise RuntimeError("x")
        ing.add_after(boom, "boom")
        ing.add_after(lambda roots: ran.append(roots), "after")
        ing.kick(["r"])
        ing.run_once()
        assert ing.errors == 1 and ran == [["r"]]
        assert set(ing.stats()["after_ms"]) == {"boom", "after"}


class TestSingleFlight:
    def test_a_second_caller_waits_for_the_rescan_in_flight(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 1)
        ws = Workspace()
        ws.add_root(root)
        time.sleep(_TICK)
        _mk_run(root, "2026-03-01", 2)
        calls = []
        real = scanner.Workspace.rescan_root

        def slow(self, *a, **k):
            calls.append(threading.get_ident())
            time.sleep(0.3)
            return real(self, *a, **k)
        monkeypatch.setattr(scanner.Workspace, "rescan_root", slow)
        out = []
        ts = [threading.Thread(target=lambda: out.append(ws.rescan_if_stale())) for _ in range(3)]
        for t in ts:
            t.start()
        for t in ts:
            t.join(10)
        assert len(calls) == 1 and sorted(out) == [False, False, True]
        assert {e.run_id for e in ws.all_entries} == {1, 2}


class TestTreePreRender:
    """RAM P7: the tick also DRAWS the tree, so the first /workspace/tree
    after a run is a memo hit, and the memo is keyed on everything the
    render reads (the workspace version AND the active chip)."""

    @staticmethod
    def _quiet(app):
        # the app's own run-ingest worker would render on its own schedule
        # and land in the render counters below: these tests drive the step
        ing = app.config.get("run_ingest")
        if ing is not None:
            ing.stop()

    def test_the_tick_pre_renders_what_a_cold_render_would_draw(self, tmp_path, monkeypatch):
        data = tmp_path / "data"
        _mk_run(data, "2026-03-01", 1)
        app, c = _app(tmp_path, data)
        self._quiet(app)
        c.get("/workspace/tree")
        time.sleep(_TICK)
        _mk_run(data, "2026-03-01", 2)
        time.sleep(_TICK)                      # no same-tick ambiguity left for the request
        _steps(app)["workspace_sidebar"]([str(data)])
        renders = []
        real = R._tree_render_ctx
        monkeypatch.setattr(R, "_tree_render_ctx",
                            lambda *a, **k: renders.append(1) or real(*a, **k))
        warm = c.get("/workspace/tree").get_data(as_text=True)
        assert renders == [], "the request re-rendered what the tick drew"
        assert "#2_04_power_rabi" in warm
        R._TREE_HTML_MEMO.clear()
        cold = c.get("/workspace/tree").get_data(as_text=True)
        assert renders == [1] and cold == warm

    def test_a_chip_switch_is_a_different_key(self, tmp_path, monkeypatch):
        data = tmp_path / "data"
        _mk_run(data, "2026-03-01", 1)
        app, c = _app(tmp_path, data)
        self._quiet(app)
        # let the scanner's same-tick ambiguity pass, so no request below
        # sees a stale root (a rescan would move the version: another key)
        time.sleep(_TICK)
        c.get("/workspace/tree")
        c.get("/workspace/tree")
        v = app.config["workspace"].version
        active = {"p": "A"}
        monkeypatch.setattr(R, "_active_path", lambda: active["p"])
        renders = []
        real = R._tree_render_ctx
        monkeypatch.setattr(R, "_tree_render_ctx",
                            lambda *a, **k: renders.append(active["p"]) or real(*a, **k))
        c.get("/workspace/tree")
        c.get("/workspace/tree")
        active["p"] = "B"
        c.get("/workspace/tree")
        assert app.config["workspace"].version == v
        assert renders == ["A", "B"]


class TestEntryGrandparents:
    def test_equals_the_path_parent_parent_it_replaced(self, tmp_path):
        import random
        from types import SimpleNamespace
        rng = random.Random(7)
        bases = [tmp_path, tmp_path / "KH", Path(tmp_path.anchor), Path("rel") / "x",
                 Path(r"\server\share\data")]
        entries = []
        for _ in range(400):
            b = rng.choice(bases)
            depth = rng.randrange(0, 4)
            p = b
            for d in range(depth):
                p = p / f"d{rng.randrange(3)}"
            entries.append(SimpleNamespace(folder_path=p / f"#{rng.randrange(99)}_run",
                                           is_standalone=rng.random() < 0.2))
        want = {e.folder_path.parent.parent for e in entries if not e.is_standalone}
        assert R._entry_grandparents(entries) == want
        assert len(want) > 5
        # a repeat (memo warm) answers the same and splits no path again
        calls = []
        real_dn = os.path.dirname
        try:
            os.path.dirname = lambda x: calls.append(x) or real_dn(x)
            assert R._entry_grandparents(entries) == want
            assert calls == []
            new = SimpleNamespace(folder_path=tmp_path / "KH" / "d9" / "#1_new",
                                  is_standalone=False)
            assert R._entry_grandparents(entries + [new]) == want | {tmp_path / "KH"}
            assert len(calls) == 2              # only the new run's path
        finally:
            os.path.dirname = real_dn


class TestForegroundYield:
    """RAM P7: the tick's precompute steps wait for the user's requests
    (measured: the first /datasets after a run went 124 -> 600 ms when the
    sidebar render and the alignment refresh ran beside it)."""

    def test_wait_idle_blocks_while_a_request_is_in_flight(self):
        fg = run_ingest.Foreground()
        fg.enter()
        done = threading.Event()
        waited = []
        t = threading.Thread(target=lambda: (waited.append(fg.wait_idle(quiet_s=0.05, max_s=5.0)),
                                             done.set()))
        t.start()
        assert not done.wait(0.3), "a step started while a request was in flight"
        fg.exit()
        assert done.wait(2.0)
        assert 0.3 <= waited[0] < 2.0

    def test_wait_idle_is_bounded(self):
        fg = run_ingest.Foreground()
        fg.enter()                      # a request that never ends
        t0 = time.monotonic()
        fg.wait_idle(quiet_s=0.05, max_s=0.2)
        assert time.monotonic() - t0 < 1.0

    def test_the_app_counts_page_requests_and_not_held_polls(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        seen = {}

        @app.route("/_fg_probe")
        def _fg_probe():
            seen["page"] = run_ingest.FOREGROUND.active
            return "ok"

        @app.route("/_fg_boom")
        def _fg_boom():
            raise RuntimeError("boom")

        c = app.test_client()
        base = run_ingest.FOREGROUND.active
        assert c.get("/_fg_probe").status_code == 200
        assert seen["page"] == base + 1
        assert run_ingest.FOREGROUND.active == base
        app.config["PROPAGATE_EXCEPTIONS"] = False
        assert c.get("/_fg_boom").status_code == 500
        assert run_ingest.FOREGROUND.active == base, "a raising view leaked its count"
        # a held-open poll is not a user waiting on a page
        orig = run_ingest.FOREGROUND.enter
        calls = []
        run_ingest.FOREGROUND.enter = lambda: calls.append(1) or orig()
        try:
            c.get("/datasets/wait?timeout=0")
            c.get("/static/app.js")
            # an agent's held node wait (verifier note: up to 3,600 s)
            c.get("/api/agent/run/nokey?wait_s=0")
            c.post("/api/agent/run-node", json={})
        finally:
            run_ingest.FOREGROUND.enter = orig
        assert calls == []

    def test_a_started_worker_yields_and_run_once_does_not(self):
        ing = run_ingest.RunIngest(lambda roots: [], refresh=lambda s: None)
        assert ing.yield_to_foreground is False
        order = []
        ing.add_after(lambda roots: order.append(run_ingest.FOREGROUND.active), "probe")
        run_ingest.FOREGROUND.enter()
        try:
            ing.yield_to_foreground = True
            ing.kick(["x"])
            t = threading.Thread(target=ing.run_once)
            t.start()
            time.sleep(0.3)
            assert order == [], "the step ran while a request was in flight"
        finally:
            run_ingest.FOREGROUND.exit()
        t.join(5.0)
        assert order == [0]
