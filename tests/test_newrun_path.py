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

    def test_the_tick_pre_renders_what_a_cold_render_would_draw(self, tmp_path, monkeypatch):
        data = tmp_path / "data"
        _mk_run(data, "2026-03-01", 1)
        app, c = _app(tmp_path, data)
        c.get("/workspace/tree")
        time.sleep(_TICK)
        _mk_run(data, "2026-03-01", 2)
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
        c.get("/workspace/tree")
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
        assert renders == ["A", "B"]
