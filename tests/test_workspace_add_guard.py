"""QA datasets-r2-11 -- the sidebar "Dataset Load" door registered a missing
path and a FILE path as workspace roots, persisted both to
workspace_roots.json and said nothing (the "Added N experiment(s)" message
sits in an ``{% elif %}`` that a non-empty tree can never reach). An empty
folder added as a root rendered a bare header with no word either.

Now: a missing path / a file is refused with a 400 whose ``<p>`` the global
responseError handler turns into a toast, nothing is registered or written;
an empty folder is still accepted (a storage folder before its first run is
legitimate) and its root says "No runs found under this folder yet."
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

EMPTY_NOTE = "tree-empty-root-note"


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "inst"))


def _roots_on_disk(app):
    f = Path(app.instance_path) / "workspace_roots.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def _ws_roots(app):
    from quam_state_manager.web import routes as rm
    with app.app_context():
        return [str(r) for r in rm._ws().root_folders]


def _wait_hydrated(app, timeout=10.0):
    from quam_state_manager.web import routes as rm
    with app.app_context():
        ws = rm._ws()
        end = time.time() + timeout
        while ws.hydrating_roots() and time.time() < end:
            time.sleep(0.05)


class TestTheDoorChecksThePath:
    def test_a_missing_path_is_refused_and_not_saved(self, app, tmp_path):
        c = app.test_client()
        nope = tmp_path / "nope_folder"
        r = c.post("/workspace/add", data={"folder": str(nope)})
        assert r.status_code == 400
        assert "Folder not found" in r.get_data(as_text=True)
        assert _ws_roots(app) == []
        assert _roots_on_disk(app) == []

    def test_a_file_path_is_refused_and_not_saved(self, app, tmp_path):
        c = app.test_client()
        f = tmp_path / "srv.bat"
        f.write_text("@echo off\n", encoding="utf-8")
        r = c.post("/workspace/add", data={"folder": str(f)})
        assert r.status_code == 400
        assert "Not a folder" in r.get_data(as_text=True)
        assert _ws_roots(app) == []
        assert _roots_on_disk(app) == []

    def test_a_refusal_leaves_an_existing_root_file_alone(self, app, tmp_path):
        c = app.test_client()
        good = tmp_path / "good"
        good.mkdir()
        assert c.post("/workspace/add", data={"folder": str(good)}).status_code == 200
        before = _roots_on_disk(app)
        assert len(before) == 1
        r = c.post("/workspace/add", data={"folder": str(tmp_path / "missing")})
        assert r.status_code == 400
        assert _roots_on_disk(app) == before

    def test_the_refusal_body_is_what_the_toast_reads(self, app, tmp_path):
        """app.js's htmx:responseError handler shows the first <p>'s text."""
        import re
        c = app.test_client()
        r = c.post("/workspace/add", data={"folder": str(tmp_path / "x")},
                   headers={"HX-Request": "true"})
        m = re.search(r"<p[^>]*>([\s\S]*?)</p>", r.get_data(as_text=True))
        assert m and "Folder not found" in m.group(1)

    def test_a_quoted_copy_as_path_is_accepted(self, app, tmp_path):
        c = app.test_client()
        d = tmp_path / "quoted dir"
        d.mkdir()
        r = c.post("/workspace/add", data={"folder": f'"{d}"'})
        assert r.status_code == 200
        assert [Path(p).name for p in _roots_on_disk(app)] == ["quoted dir"]


class TestAnEmptyRootSaysSo:
    def test_an_empty_folder_is_accepted_and_says_no_runs(self, app, tmp_path):
        c = app.test_client()
        empty = tmp_path / "emptyroot"
        empty.mkdir()
        assert c.post("/workspace/add", data={"folder": str(empty)}).status_code == 200
        _wait_hydrated(app)
        body = c.get("/workspace/tree").get_data(as_text=True)
        assert "emptyroot" in body
        assert EMPTY_NOTE in body

    def test_a_root_with_runs_carries_no_empty_note(self, app, tmp_path):
        c = app.test_client()
        root = tmp_path / "withruns"
        run = root / "2026-08-01" / "#1_exp1_120001"
        (run / "quam_state").mkdir(parents=True)
        (run / "quam_state" / "state.json").write_text(
            json.dumps({"qubits": {"q1": {"f_01": 5e9}}}), encoding="utf-8")
        (run / "quam_state" / "wiring.json").write_text(
            json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")
        (run / "node.json").write_text(json.dumps(
            {"id": 1, "metadata": {"name": "exp1", "status": "finished"},
             "created_at": "2026-08-01T12:00:00", "parameters": {"model": {}}}),
            encoding="utf-8")
        assert c.post("/workspace/add", data={"folder": str(root)}).status_code == 200
        _wait_hydrated(app)
        body = c.get("/workspace/tree").get_data(as_text=True)
        assert "#1" in body or "exp1" in body
        assert EMPTY_NOTE not in body
