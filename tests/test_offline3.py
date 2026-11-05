"""Offline walk regressions, using synthetic chips only."""
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_sync_review_applied import env, _apply_t1, _outside, _st, _live
from quam_state_manager.core import agent_plans, approvals


@pytest.mark.parametrize("outside,lost", [(2e-5, True), (3.3e-5, True),
                                         (4.4e-5, False), (5.5e-5, False)])
def test_applied_predecessor_chain(env, outside, lost):
    c = env["client"]
    _apply_t1(env)
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "4.4e-5"})
    c.post("/state/apply-to-live")
    _outside(env, _st(t1=outside))
    html = c.get("/state/review").get_data(as_text=True)
    assert ("1 value you applied" in html) == lost
    c.post("/state/sync?mode=apply&keep_applied=1")
    assert _live(env)["T1"] == (4.4e-5 if lost else outside)


def test_an_undo_that_reached_the_chip_extends_the_applied_chain(env):
    from quam_state_manager.web import routes
    c = env["client"]
    _apply_t1(env)
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "4.4e-5"})
    c.post("/state/apply-to-live")
    assert c.post("/undo").status_code == 200
    with env["app"].test_request_context():
        held = routes._applied_values_held(routes._active_ctx())
        assert held["qubits.q1.T1"] == 3.3e-5
        # the undo that reached the chip is an apply of 3.3e-5 replacing
        # 4.4e-5 (W3-5), so live back at 4.4e-5 is a loss too
        assert held.old["qubits.q1.T1"] == [2e-5, 4.4e-5]
    _outside(env, _st())
    assert "1 value you applied" in c.get("/state/review").get_data(as_text=True)
    c.post("/state/sync?mode=apply&keep_applied=1")
    assert _live(env)["T1"] == 3.3e-5


@pytest.mark.parametrize("door", ["pull", "push"])
def test_autosync_restores_applied_chain_without_asking(env, door):
    from tests.test_autosync_merge import _arm
    from quam_state_manager.web import routes
    c = env["client"]
    _arm(env, pull=True, push=True)
    _apply_t1(env)
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "4.4e-5"})
    c.post("/state/apply-to-live")
    if door == "push":
        c.post("/field/edit", data={"dot_path": "qubits.q1.T2ramsey", "value": "2.2e-5"})
    _outside(env, _st(f01=5.2e9))
    if door == "push":
        r = c.post("/state/apply-to-live")
        assert "autoSyncMerge" in r.headers.get("HX-Trigger", "")
        r = c.post("/state/sync?mode=apply")
        assert r.get_json()["status"] == "ok", r.get_json()
    else:
        r = c.post("/auto-sync/pull", data={"dom_dirty": "0"})
    assert r.status_code == 200
    assert _live(env)["T1"] == 4.4e-5
    assert _live(env)["f_01"] == 5.2e9
    if door == "push":
        assert _live(env)["T2ramsey"] == 2.2e-5
    with env["app"].test_request_context():
        ctx = routes._active_ctx()
        assert ctx["auto_apply"]["pull"]
        assert not ctx["auto_apply"].get("hold")
        assert ctx["auto_apply"].get("merge_tries", 0) == 0
    assert json.loads(r.headers["HX-Trigger"])["autoSyncRestored"]["count"] == 1
    if door == "push":
        assert r.get_json()["auto_restored_count"] == 1
    tray = c.get("/state/tray").get_data(as_text=True)
    assert "conflict-tray" not in tray
    assert c.get("/state/drift").get_json()["sync"]["state"] == "synced"


def test_push_only_disarms_on_applied_loss_without_prompt(env):
    from tests.test_autosync_merge import _arm
    from quam_state_manager.web import routes
    c = env["client"]
    _arm(env, pull=False, push=True)
    _apply_t1(env)
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "4.4e-5"})
    c.post("/state/apply-to-live")
    c.post("/field/edit", data={"dot_path": "qubits.q1.T2ramsey", "value": "2.2e-5"})
    _outside(env, _st(f01=5.2e9))
    assert c.post("/auto-sync/pull").status_code == 204
    r = c.post("/state/apply-to-live")
    triggers = json.loads(r.headers["HX-Trigger"])
    assert "autoApplyDisarm" in triggers and "autoSyncMerge" not in triggers
    with env["app"].test_request_context():
        assert routes._active_ctx().get("auto_apply") is None
    assert _live(env)["T1"] == 2e-5 and _live(env)["f_01"] == 5.2e9
    assert "1 value you applied" in c.get("/state/review").get_data(as_text=True)


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
    from quam_state_manager.web import routes
    # The note still describes unresolved losses; armed pulls now restore them.
    with env["app"].test_request_context():
        routes._active_ctx()["_lost_unacked"] = [
            {"path": "qubits.q1.T1", "applied": 3.3e-5, "now": 2e-5}]
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


@pytest.mark.parametrize("dirt", ["pending_edit", "typed_cell"])
@pytest.mark.parametrize("push", [True, False])
def test_merge_pull_restages_a_reverted_applied_value(env, dirt, push):
    """The pull tick with a typed cell or a pending edit signals the merge-pull,
    which presses reapply. That pull adopted the outside value and the applied
    one was gone without a word; it is re-staged as a pending change now -- an
    armed push session flushes it, a pull-only session leaves it for Apply."""
    from tests.test_autosync_merge import _arm
    from quam_state_manager.web import routes
    c = env["client"]
    _arm(env, pull=True, push=push)
    _apply_t1(env)
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "4.4e-5"})
    c.post("/state/apply-to-live")
    form = {"dom_dirty": "0"}
    if dirt == "pending_edit":
        c.post("/field/edit", data={"dot_path": "qubits.q1.T2ramsey", "value": "2.2e-5"})
    else:
        form = {"dom_dirty": "1", "dom_path": "qubits.q1.T2ramsey"}
    _outside(env, _st(f01=5.2e9))                    # T1 back to A, f_01 moved
    r = c.post("/auto-sync/pull", data=form)
    assert r.status_code == 204
    assert "autoSyncMergePull" in json.loads(r.headers["HX-Trigger"])
    r = c.post("/state/sync?mode=reapply")           # what the client presses
    body = r.get_json()
    assert body["status"] == "ok", body
    assert body["auto_restored_count"] == 1 and body["auto_restored_staged"] is True
    with env["app"].test_request_context():
        ctx = routes._active_ctx()
        staged = {e.dot_path: e.new_value for e in ctx["store"].change_log}
        assert staged["qubits.q1.T1"] == 4.4e-5, staged
        assert ctx["store"].merged["qubits"]["q1"]["f_01"] == 5.2e9
        assert ctx["auto_apply"]["pull"] and not ctx["auto_apply"].get("hold")
    assert _live(env)["T1"] == 2e-5                  # reapply wrote nothing
    tray = c.get("/state/tray").get_data(as_text=True)
    assert "conflict-tray" not in tray
    # the press the armed push session (or the person) makes next writes it
    r = c.post("/state/apply-to-live")
    assert r.status_code == 200, r.get_data(as_text=True)[:200]
    assert _live(env)["T1"] == 4.4e-5 and _live(env)["f_01"] == 5.2e9
    if dirt == "pending_edit":
        assert _live(env)["T2ramsey"] == 2.2e-5


def test_two_applied_leaves_under_a_dropped_parent_are_both_restored(tmp_path):
    from tests.test_autosync_merge import _arm, _write_chip
    from quam_state_manager.web.app import create_app
    live = tmp_path / "chips" / "live"
    base = _st()
    base["extras"] = {"a": 1, "b": 2}
    _write_chip(live, base)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    e = {"app": app, "client": c, "live": live}
    _arm(e, pull=True, push=True)
    c.post("/field/edit", data={"dot_path": "extras.a", "value": "10"})
    c.post("/field/edit", data={"dot_path": "extras.b", "value": "20"})
    assert c.post("/state/apply-to-live").status_code == 200
    _outside(e, _st(f01=5.2e9))                      # the outside save dropped extras
    r = c.post("/auto-sync/pull", data={"dom_dirty": "0"})
    assert r.status_code == 200
    assert json.loads(r.headers["HX-Trigger"])["autoSyncRestored"]["count"] == 2
    doc = json.loads((live / "state.json").read_text(encoding="utf-8"))
    assert doc["extras"] == {"a": 10, "b": 20}, doc.get("extras")
    assert doc["qubits"]["q1"]["f_01"] == 5.2e9


def test_a_refused_restore_hands_the_tray_back_never_json(env, monkeypatch):
    """The pull tick is an htmx swap into #pending-tray; the in-process merge
    may be refused (the chip moved again). The answer is the tray the door
    rendered, with its signal, not a JSON body as the tray's text."""
    from tests.test_autosync_merge import _arm
    from flask import jsonify
    from quam_state_manager.web import routes
    c = env["client"]
    _arm(env, pull=True, push=True)
    _apply_t1(env)
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "4.4e-5"})
    c.post("/state/apply-to-live")
    _outside(env, _st(f01=5.2e9))

    def refused(*_a, **_k):
        resp = jsonify({"status": "conflict", "mode": "apply",
                        "tray_html": '<div id="pending-tray" data-probe="conflict"></div>'})
        resp.headers["HX-Trigger"] = json.dumps({"autoSyncMerge": {"tries": 1, "chip": ""}})
        return resp
    monkeypatch.setattr(routes, "_sync_pull_apply_to_live", refused)
    r = c.post("/auto-sync/pull", data={"dom_dirty": "0"})
    assert r.status_code == 200
    assert not r.is_json and 'pending-tray-conflict' in r.get_data(as_text=True)
    assert 'retrying (1)' in r.get_data(as_text=True)
    assert "autoSyncMerge" in json.loads(r.headers["HX-Trigger"])


@pytest.mark.parametrize("kind", ["stale", "lock"])
def test_refused_restoration_uses_the_retry_policy(env, monkeypatch, kind):
    from tests.test_autosync_merge import _arm
    from quam_state_manager.web import routes
    c = env["client"]
    _arm(env, pull=True, push=True)
    _apply_t1(env)
    c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "4.4e-5"})
    c.post("/state/apply-to-live")
    _outside(env, _st(f01=5.2e9))
    def refuse(*args, **kwargs):
        if kind == "stale":
            raise routes.working_copy.StaleLiveError("live moved again")
        raise OSError("[WinError 32] sharing violation on replace")
    monkeypatch.setattr(routes.working_copy, "apply_to_live", refuse)
    r = c.post("/auto-sync/pull", data={"dom_dirty": "0"})
    assert r.status_code == 200 and not r.is_json
    event = json.loads(r.headers["HX-Trigger"])["autoSyncRetry"]
    assert event["class"] == "transient" and event["tries"] == 1
    assert "retrying (1)" in r.get_data(as_text=True)
