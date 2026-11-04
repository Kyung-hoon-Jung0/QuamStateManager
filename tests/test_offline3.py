"""Offline walk regressions, using synthetic chips only."""
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_sync_review_applied import env, _apply_t1, _outside, _st, _live
from quam_state_manager.core import agent_plans, approvals


@pytest.mark.parametrize("outside,lost", [(2e-5, False), (3.3e-5, True)])
def test_latest_apply_replaced_value(env, outside, lost):
    c = env["client"]
    _apply_t1(env)
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "4.4e-5"})
    c.post("/state/apply-to-live")
    _outside(env, _st(t1=outside))
    html = c.get("/state/review").get_data(as_text=True)
    assert ("1 value you applied" in html) == lost
    c.post("/state/sync?mode=apply&keep_applied=1")
    assert _live(env)["T1"] == (4.4e-5 if lost else outside)


@pytest.mark.parametrize("status,waiting,applied", [("approved", 0, 2), ("rejected", 0, 0), ("pending", 1, 0)])
def test_load_derives_waiting_from_approvals(tmp_path, status, waiting, applied):
    inst, chip = str(tmp_path), "synthetic"
    plan = agent_plans.add(inst, chip, title="test", mode="ask-writes", created_by="test",
                           steps=[{"node": "test", "targets": ["q1"]}])
    card = approvals.add(inst, chip, kind="writes", node="test", targets=["q1"], writes=[],
                         reason=None, why_held="test", actor="test", plan_id=plan["id"])
    agent_plans.step_update(inst, chip, plan["id"], 0, status="done", n_writes=2, approval=card["id"])
    agent_plans.update(inst, chip, plan["id"], status="done", summary={"waiting": 1, "applied": 0})
    if status != "pending":
        approvals.decide(inst, chip, card["id"], status=status, who="test")
    got = agent_plans.get(inst, chip, plan["id"])
    assert agent_plans.counts(got)["waiting"] == waiting
    assert agent_plans.counts(got)["applied"] == applied
    assert got["summary"]["waiting"] == waiting and got["summary"]["applied"] == applied


def test_take_live_omits_zero_edits(env):
    _apply_t1(env)
    _outside(env, _st(f01=5.2e9))
    c = env["client"]
    r = c.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q1.f_01", "value": "5.2e9", "create": False}], "source": "live"})
    assert r.status_code == 200 and r.get_json()["ok"]
    html = c.get("/state/review").get_data(as_text=True)
    assert "Lost: 1 value you applied" in html
    assert "your 0 unapplied edits" not in html


def test_loss_note_keeps_paths_in_tooltip(env):
    import re
    from tests.test_autosync_merge import _arm
    from tests.test_autosync_lost_applied import _outside_save_and_pull
    _arm(env, pull=True, push=False)
    _apply_t1(env)
    assert _outside_save_and_pull(env, _st(f01=5.2e9)).status_code == 200
    html = env["client"].get("/state/tray").get_data(as_text=True)
    note = re.search(r'<span class="sync-lost-note.*?</span>', html, re.S).group(0)
    title, text = note.split('">', 1)
    assert "qubits.q1.T1" in title
    assert "qubits.q1.T1" not in text
    assert len(re.sub(r"<[^>]+>", "", text).strip()) < 48


def test_redo_already_live(env):
    c = env["client"]
    _apply_t1(env)
    c.post("/undo")
    # A save races the redo after its drift preflight; the core adopts identical content.
    from quam_state_manager.web import routes
    original = routes.working_copy.apply_to_live
    observed = {}
    def race(wc, **kwargs):
        from tests.test_autosync_merge import _write_chip
        _write_chip(env["live"], _st(t1=3.3e-5))
        path = env["live"] / "state.json"
        before = path.stat().st_mtime_ns, path.read_bytes()
        result = original(wc, **kwargs)
        observed["unchanged"] = before == (path.stat().st_mtime_ns, path.read_bytes())
        return result
    from unittest.mock import patch
    with patch.object(routes.working_copy, "apply_to_live", race):
        r = c.post("/redo")
    payload = json.loads(r.headers["HX-Trigger"])["cellsReverted"]
    assert "already on the chip" in payload["message"]
    assert "written to the chip file" not in payload["message"]
    assert payload["live"] is False
    assert observed["unchanged"], "the no-write message must describe a real no-op"


def test_browser_pins():
    r = subprocess.run(["node", str(Path(__file__).with_name("offline3_selfcheck.cjs"))],
                       capture_output=True, text=True, encoding="utf-8", timeout=90)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "offline3 passed" in r.stdout
