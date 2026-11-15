"""Document-local consent for journal undo and redo on scratch chips."""
import copy
import json

import pytest

from tests.test_undo_live import (env, _ctx, _edit, _apply, _live_off,
                                  _work_off, _trig, _sidecar, _set_setting)

A = {"X-SM-Window-ID": "window-a"}
B = {"X-SM-Window-ID": "window-b"}


def capture(env, value):
    c = env["client"]
    _edit(c, value)
    assert c.post("/state/apply-to-live", headers=A).status_code == 200


def snapshot(env):
    ctx = _ctx(env)
    return (copy.deepcopy(ctx["store"].merged), list(ctx["store"].change_log),
            ctx["undo_cursor"], _sidecar(env).read_bytes(),
            (env["live"] / "state.json").read_bytes(),
            (env["live"] / "wiring.json").read_bytes())


def test_capture_records_window_on_disk(env):
    capture(env, 0.1)
    assert _ctx(env)["undo_units"][-1]["meta"]["window_id"] == A["X-SM-Window-ID"]
    assert json.loads(_sidecar(env).read_text())["units"][-1]["meta"]["window_id"] == A["X-SM-Window-ID"]


@pytest.mark.parametrize("on", [True, False])
def test_same_window_has_no_new_question(env, on):
    _set_setting(env, on)
    capture(env, 0.1)
    t = _trig(env["client"].post("/undo", headers=A))
    assert "undoAskPrevSession" not in t
    assert t["cellsReverted"]["live"] is on
    assert _work_off(env) == 0.08
    assert _live_off(env) == (0.08 if on else 0.1)


@pytest.mark.parametrize("on", [True, False])
def test_other_window_no_is_inert_yes_is_once_and_client_owned(env, on):
    _set_setting(env, on)
    capture(env, 0.1)
    capture(env, 0.12)
    c = env["client"]
    before = snapshot(env)
    q = _trig(c.post("/undo", headers=B))["undoAskPrevSession"]
    assert snapshot(env) == before
    assert "another window" in q["message"] and "joint_offset" in q["message"]
    assert ("writes the chip" if on else "stages only") in q["message"]
    assert q["message"].isascii() and "\n" not in q["message"]
    # No sends nothing. A later unacknowledged request still asks, with no writes.
    assert "undoAskPrevSession" in _trig(c.post("/undo", headers=B))
    assert snapshot(env) == before
    yes = {"ack_prev_window": "1"}
    assert "cellsReverted" in _trig(c.post("/undo", headers=B, data=yes))
    assert _work_off(env) == 0.1
    after_yes = snapshot(env)
    # The server never remembers Yes, including for the same window id.
    assert "undoAskPrevSession" in _trig(c.post("/undo", headers=B))
    assert snapshot(env) == after_yes
    assert "cellsReverted" in _trig(c.post("/undo", headers=B, data=yes))
    assert _work_off(env) == 0.08
    assert _live_off(env) == (0.08 if on else 0.12)


def test_legacy_unit_is_another_window(env):
    capture(env, 0.1)
    _ctx(env)["undo_units"][-1]["meta"].pop("window_id")
    assert "undoAskPrevSession" in _trig(env["client"].post("/undo", headers=A))


@pytest.mark.parametrize("on", [True, False])
def test_redo_first_foreign_step_asks_before_any_mutation(env, on):
    capture(env, 0.1)
    c = env["client"]
    c.post("/undo", headers=A)
    if not on:
        # Create a staged journal step for the unstage redo path.
        c.post("/redo", headers=A)
        _set_setting(env, False)
        c.post("/undo", headers=A)
    before = snapshot(env)
    q = _trig(c.post("/redo", headers=B))["undoAskPrevSession"]
    assert q["direction"] == "/redo"
    assert snapshot(env) == before
    assert "cellsReverted" in _trig(c.post("/redo", headers=B, data={"ack_prev_window": "1"}))
    assert _work_off(env) == 0.1


def test_group_asks_once_then_rolls_back_on_live_refusal(env):
    c = env["client"]
    m = _ctx(env)["modifier"]
    m.set_value("qubits.qA1.z.joint_offset", 0.1, group_id="together")
    m.set_value("qubits.qA1.f_01", 6e9, group_id="together")
    c.post("/state/apply-to-live", headers=A)
    before = snapshot(env)
    q = _trig(c.post("/undo", headers=B))["undoAskPrevSession"]
    assert "2 changes" in q["message"]
    assert snapshot(env) == before
    live = env["live"] / "state.json"
    state = json.loads(live.read_text())
    state["qubits"]["qA1"]["f_01"] = 7e9
    live.write_text(json.dumps(state))
    before = snapshot(env)
    t = _trig(c.post("/undo", headers=B, data={"ack_prev_window": "1"}))
    assert t["cellsReverted"]["live"] is False
    assert snapshot(env) == before


def test_group_yes_reverts_every_entry(env):
    c = env["client"]
    m = _ctx(env)["modifier"]
    m.set_value("qubits.qA1.z.joint_offset", 0.1, group_id="together")
    m.set_value("qubits.qA1.f_01", 6e9, group_id="together")
    c.post("/state/apply-to-live", headers=A)
    assert "undoAskPrevSession" in _trig(c.post("/undo", headers=B))
    t = _trig(c.post("/undo", headers=B, data={"ack_prev_window": "1"}))
    assert t["cellsReverted"]["live"] is True
    assert _work_off(env) == 0.08
    assert _ctx(env)["store"].merged["qubits"]["qA1"]["f_01"] == 5e9


def test_unseen_edit_gate_precedes_question(env):
    capture(env, 0.1)
    r = env["client"].post("/undo", headers=B, data={"seen_edit_seq": "unseen"})
    assert r.status_code == 409
    assert r.headers["HX-Trigger"] == "undo-foreign"
    assert _live_off(env) == 0.1


def test_gate_delete_other_window_then_class_file_notice(env):
    from tests.test_class_file_left_by_undo import _publish, _gate, NOTE
    _inst, _scope, target = _publish(env)
    c = env["client"]
    _gate(env, "gate_one")
    c.post("/state/apply-to-live", headers=A)
    _ctx(env)["modifier"].delete_subtree("gate_one")
    c.post("/state/apply-to-live", headers=A)
    before = snapshot(env)
    q = _trig(c.post("/undo", headers=B))["undoAskPrevSession"]
    assert "gate_one restored" in q["message"]
    assert snapshot(env) == before
    yes = {"ack_prev_window": "1"}
    assert _trig(c.post("/undo", headers=B, data=yes))["cellsReverted"]["live"] is True
    assert "gate_one" in _ctx(env)["store"].merged
    t = _trig(c.post("/undo", headers=B, data=yes))
    assert t["cellsReverted"]["live"] is True
    assert NOTE in t["cellsReverted"]["message"] and target.is_file()
    assert "gate_one" not in _ctx(env)["store"].merged


def test_journal_remembers_editor_when_another_window_applies(env):
    c = env["client"]
    r = c.post("/field/edit-batch", headers=A, json={"updates": [
        {"dot_path": "qubits.qA1.z.joint_offset", "value": "0.1"}], "expect_chip": ""})
    assert r.get_json()["ok"]
    c.post("/state/apply-to-live", headers=B)
    assert _ctx(env)["undo_units"][-1]["meta"]["window_id"] == A["X-SM-Window-ID"]
    assert "undoAskPrevSession" in _trig(c.post("/undo", headers=B))
    assert "cellsReverted" in _trig(c.post("/undo", headers=A))


def test_wholesale_capture_records_window_and_asks(env):
    from tests.test_undo_live import TestWholesale
    c = env["client"]
    hm = env["app"].config["history_manager"]
    hm.check_and_snapshot(_ctx(env)["path"], "manual", force=True)
    ts = TestWholesale()._snapshot_ts(env)
    capture(env, 0.1)
    assert c.post(f"/state-history/{ts}/stage", headers=A).status_code == 200
    assert c.post("/state/apply-to-live", headers=A).status_code == 200
    unit = _ctx(env)["undo_units"][-1]
    assert unit["meta"]["wholesale"] and unit["meta"]["window_id"] == A["X-SM-Window-ID"]
    assert "undoAskPrevSession" in _trig(c.post("/undo", headers=B))
    assert _live_off(env) == 0.08
    assert "cellsReverted" in _trig(c.post("/undo", headers=A))
    assert _live_off(env) == 0.1


@pytest.mark.parametrize("direction", ["/undo", "/redo"])
@pytest.mark.parametrize("on", [True, False])
@pytest.mark.parametrize("consent", ["", "1"])
def test_headerless_person_asks_before_any_mutation(env, direction, on, consent):
    _set_setting(env, on)
    capture(env, 0.1)
    c = env["client"]
    if direction == "/redo":
        assert "cellsReverted" in _trig(c.post("/undo", headers=A))
    before = snapshot(env)
    c.environ_base.pop("HTTP_X_SM_WINDOW_ID", None)
    r = c.post(direction, data={"ack_prev_window": consent})
    assert r.status_code == 409
    q = _trig(r)["undoAskPrevSession"]
    assert q["direction"] == direction and "joint_offset" in q["message"]
    assert ("writes the chip" if on else "stages only") in q["message"]
    assert snapshot(env) == before


@pytest.mark.parametrize("direction", ["/undo", "/redo"])
@pytest.mark.parametrize("window", ["", "window-b"])
def test_agent_keeps_window_exemption_and_route_ownership_guard(env, direction, window):
    from quam_state_manager.web import routes
    capture(env, 0.1)
    c = env["client"]
    unit = _ctx(env)["undo_units"][-1]
    headers = {"X-SM-Agent": "mcp", "X-SM-Window-ID": window}
    # The route's ownership guard precedes the helper and refuses applied
    # history. Pin the helper too: dropping its exemption must fail here.
    with env["app"].test_request_context(direction, method="POST", headers=headers):
        assert routes._journal_window_ask(unit, direction) is None
    before = snapshot(env)
    r = c.post(direction, headers=headers)
    assert r.status_code == 409
    assert "undoAskPrevSession" not in _trig(r)
    assert snapshot(env) == before


def test_agent_headerless_age_rule_is_retained(env, monkeypatch):
    import time
    from quam_state_manager.web import routes
    now = time.time()
    monkeypatch.setattr(routes, "_PROCESS_STARTED_AT", now - 12 * 3600)
    with env["app"].test_request_context("/undo", method="POST", headers={"X-SM-Agent": "mcp"}):
        assert not routes._unit_needs_ask({"ts": now - 7 * 3600})
        assert routes._unit_needs_ask({"ts": now - 9 * 3600})
        assert routes._unit_needs_ask({"ts": now - 13 * 3600})
        assert routes._unit_needs_ask({})


@pytest.mark.parametrize("window", [A, B])
def test_mixed_window_group_asks_each_creator_before_reverting_whole_unit(env, window):
    c = env["client"]
    ctx = _ctx(env)
    for headers, path, value in ((A, "qubits.qA1.z.joint_offset", "0.1"),
                                  (B, "qubits.qA1.f_01", "6e9")):
        r = c.post("/field/edit-batch", headers=headers, json={"updates": [
            {"dot_path": path, "value": value}], "expect_chip": ""})
        assert r.get_json()["ok"]
        ctx["store"].change_log[-1].group_id = "together"
    assert c.post("/state/apply-to-live", headers=A).status_code == 200
    unit = ctx["undo_units"][-1]
    assert len(unit["entries"]) == 2
    assert unit["meta"]["window_id"] is None
    before = snapshot(env)
    assert "2 changes" in _trig(c.post("/undo", headers=window))["undoAskPrevSession"]["message"]
    assert snapshot(env) == before
    t = _trig(c.post("/undo", headers=window, data={"ack_prev_window": "1"}))
    assert t["cellsReverted"]["live"] is True
    assert _work_off(env) == _live_off(env) == 0.08
    assert ctx["store"].merged["qubits"]["qA1"]["f_01"] == 5e9
    assert "undoAskPrevSession" in _trig(c.post("/redo", headers=window))


def test_headerless_person_can_undo_and_redo_an_unapplied_edit(env):
    c = env["client"]
    _edit(c, 0.1)
    c.environ_base.pop("HTTP_X_SM_WINDOW_ID", None)
    assert "cellsReverted" in _trig(c.post("/undo"))
    assert _work_off(env) == 0.08
    assert "cellsReverted" in _trig(c.post("/redo"))
    assert _work_off(env) == 0.1
    assert _live_off(env) == 0.08
