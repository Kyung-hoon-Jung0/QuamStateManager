"""SU-10: a shared working folder must never swallow another process's edit."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from tests.state_model.repros._rig import Chip


_CHILD = r'''
import json, os, sys, time
from pathlib import Path
root, live, inst, barrier, pause = sys.argv[1:]
sys.path.insert(0, root)
os.environ["SM_DISABLE_ENV_WARMUP"] = "1"
os.environ["SM_DISABLE_HISTORY_VERIFY"] = "1"
os.environ["QUALIBRATE_CONFIG_FILE"] = str(Path(barrier) / "no_config")
from quam_state_manager.core import hub, working_copy
from quam_state_manager.web.app import create_app
hub.set_inline(True)
app = create_app(testing=True, instance_path=inst)
c = app.test_client()
assert c.post("/load", data={"folder": live}).status_code in (200, 302)
ctx = next(v for v in app.config["contexts"].values() if v.get("type") == "quam")
assert c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "1.111e-05"}).status_code == 200
barrier = Path(barrier)
(barrier / "ready").write_text("1")
deadline = time.monotonic() + 55
while not (barrier / "go").exists():
    assert time.monotonic() < deadline, "parent did not start the child"
    time.sleep(0.01)
if pause == "1":
    original = working_copy.apply_to_live
    def paused(*args, **kwargs):
        (barrier / "saved").write_text("1")
        deadline = time.monotonic() + 55
        while not (barrier / "resume").exists():
            assert time.monotonic() < deadline, "parent did not resume the child"
            time.sleep(0.01)
        return original(*args, **kwargs)
    working_copy.apply_to_live = paused
r = c.post("/state/apply-to-live")
(barrier / "result.json").write_text(json.dumps({
    "status": r.status_code, "text": r.get_data(as_text=True),
    "log": [(e.dot_path, e.new_value) for e in ctx["store"].change_log]}))
'''


def _wait(path, proc):
    deadline = time.monotonic() + 90
    while not path.exists():
        assert proc.poll() is None, "child exited before the barrier"
        assert time.monotonic() < deadline, "child did not reach the barrier"
        time.sleep(0.01)


@pytest.mark.parametrize("pause", [False, True])
@pytest.mark.parametrize("force", [False, True])
def test_two_processes_keep_their_edits_when_the_working_copy_changes(tmp_path, pause, force):
    chip = Chip(tmp_path)
    barrier = tmp_path / "barrier"
    barrier.mkdir()
    script = barrier / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    root = Path(__file__).resolve().parents[3]
    with (barrier / "child.log").open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, str(script), str(root), str(chip.live), str(chip.inst),
             str(barrier), "1" if pause else "0"], cwd=root,
            env=dict(os.environ, PYTHONUTF8="1"), stdout=log, stderr=log)
        try:
            _wait(barrier / "ready", proc)
            chip.edit("qubits.q1.T1", "2.222e-05")
            (barrier / "go").write_text("1")
            _wait(barrier / ("saved" if pause else "result.json"), proc)
            before = (chip.log(), chip.journal())
            response = chip.c.post("/state/apply-to-live", data={"force": "1"} if force else {})
            assert response.status_code == 409, response.get_data(as_text=True)
            text = response.get_data(as_text=True).lower()
            assert "another sm window/process" in text
            assert "applied to the live chip" not in text
            assert (chip.log(), chip.journal()) == before
        finally:
            (barrier / "resume").write_text("1")
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
    assert proc.returncode == 0, (barrier / "child.log").read_text(encoding="utf-8")
    child = json.loads((barrier / "result.json").read_text())
    assert child["status"] == 200 and not child["log"]
    assert chip.live_state()["qubits"]["q2"]["T1"] == 1.111e-05
    assert ("qubits.q1.T1", 2.222e-05) in chip.log()


def test_a_same_size_working_rewrite_with_preserved_mtime_is_refused(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "2.222e-05")
    working = chip.ctx["working_copy"].working_folder / "state.json"
    raw = working.read_bytes()
    stat = working.stat()
    # Whitespace is content for ownership: another writer changed these bytes.
    changed = raw.replace(b" ", b"\t", 1)
    assert changed != raw and len(changed) == len(raw)
    working.write_bytes(changed)
    os.utime(working, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    before = (chip.log(), chip.journal(), chip.live_state(), changed)
    response = chip.c.post("/state/apply-to-live", data={"force": "1"})
    assert response.status_code == 409
    assert (chip.log(), chip.journal(), chip.live_state(), working.read_bytes()) == before


def test_apply_that_already_matches_live_reports_no_write(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "2.222e-05")
    chip.set_live("qubits.q1.T1", 2.222e-05)
    response = chip.c.post("/state/apply-to-live")
    assert response.status_code == 200
    text = response.get_data(as_text=True)
    assert "already holds this state" in text
    assert "Applied to the live chip" not in text
    assert not chip.log()


@pytest.mark.parametrize("walk", [False, True])
def test_a_replaced_saved_copy_cannot_be_adopted_as_a_noop(tmp_path, monkeypatch, walk):
    from quam_state_manager.core import safe_io, working_copy
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "2.222e-05")
    original = working_copy.apply_to_live

    def replace_then_apply(wc, **kwargs):
        state, wiring = safe_io.read_state_wiring(wc.live_folder)
        safe_io.write_state_wiring(wc.working_folder, state, wiring)
        return original(wc, **kwargs)

    monkeypatch.setattr(working_copy, "apply_to_live", replace_then_apply)
    before = (chip.log(), chip.journal(), chip.live_state())
    if walk:
        with chip.app.test_request_context("/state/sync", method="POST"):
            response = chip.app.make_response(routes._sync_pull_apply_to_live(chip.ctx, None))
    else:
        response = chip.c.post("/state/apply-to-live")
    assert response.status_code == 409
    assert "window/process" in response.get_data(as_text=True)
    assert (chip.log(), chip.journal(), chip.live_state()) == before


def test_our_own_save_and_later_applies_keep_the_ownership_anchor(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "2.222e-05")
    assert chip.c.post("/save").status_code == 200
    assert chip.c.post("/state/apply-to-live").status_code == 200
    chip.edit("qubits.q2.T1", "1.111e-05")
    assert chip.c.post("/state/apply-to-live").status_code == 200
    assert chip.live_state()["qubits"]["q1"]["T1"] == 2.222e-05
    assert chip.live_state()["qubits"]["q2"]["T1"] == 1.111e-05
