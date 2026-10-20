"""R6e: a measured node's failed state capture stays visible on its item."""
import json
import os
import subprocess
from pathlib import Path

import pytest
from tests.test_node_persist import rex
from quam_state_manager.core import scheduler


@pytest.mark.parametrize("failure", ["PermissionError('write denied')", "ValueError('state.json is torn')"])
def test_measured_node_reports_capture_failure(rex, tmp_path, monkeypatch, failure):
    target = tmp_path / "node.py"
    target.write_text("class Machine:\n    def save(self):\n        raise " + failure +
                      "\nclass Node:\n    machine = Machine()\nnode = Node()\n", encoding="utf-8")
    monkeypatch.setattr(rex, "_library_versions", lambda: {})
    monkeypatch.setattr(rex, "_qualibrate_version", lambda: "test")
    work = tmp_path / "result"
    assert rex.main(["--mode", "run", "--target", str(target), "--state-path", str(tmp_path), "--out", str(work)]) == 0
    body = json.loads((work / "_result.json").read_text())
    assert body["status"] == "ok", body
    status, warning = scheduler._classify_result(work, 0)
    assert status == "done" and warning and "SM could not record the node's state" in warning, (status, warning, body)
    assert ("write denied" if "Permission" in failure else "state.json is torn") in warning
    # The structured result must carry the reason; log scraping is insufficient.
    assert body.get("state_capture_warning"), body


def test_capture_warning_is_visible_on_the_item():
    r = subprocess.run(["node", str(Path(__file__).with_name("v151_r6e_capture_warning_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8", timeout=60,
                       env=dict(os.environ, NODE_PATH="D:/work/statemanager/node_modules"))
    assert r.returncode == 0, r.stdout + r.stderr


def test_isolated_capture_failure_is_reported(rex, tmp_path, monkeypatch):
    def capture(*a, **kw):
        recorder = rex._UpdateRecorder()
        recorder.blocks = 1
        recorder.writes = {"/value": 2}
        def denied(*a, **kw):
            raise PermissionError("scratch write denied")
        monkeypatch.setattr(rex, "_apply_pointer_writes", denied)
        report = rex._persist_isolated({}, str(tmp_path), None, {}, recorder)
        assert report["source"] == "error", report
    monkeypatch.setattr(rex, "run_target", capture)
    monkeypatch.setattr(rex, "_library_versions", lambda: {})
    monkeypatch.setattr(rex, "_qualibrate_version", lambda: "test")
    work = tmp_path / "result"
    assert rex.main(["--mode", "run", "--target", "node.py", "--out", str(work)]) == 0
    status, warning = scheduler._classify_result(work, 0)
    assert status == "done" and "scratch write denied" in (warning or "")
    # Reusing the runner module cannot carry a previous node's warning.
    monkeypatch.setattr(rex, "run_target", lambda *a, **kw: None)
    assert rex.main(["--mode", "run", "--target", "node.py", "--out", str(work)]) == 0
    assert scheduler._classify_result(work, 0) == ("done", None)
