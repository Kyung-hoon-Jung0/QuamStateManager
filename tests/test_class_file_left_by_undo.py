"""An undo keeps the class file; the notice and the class list say it is not used."""
from __future__ import annotations

import json

import quam_state_manager.web.routes as routes_mod
from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.core import quam_config_save as Q
from tests.test_autosync_merge import env  # noqa: F401

CLS = "FlatPulse"
QCLASS = f"quam_config.{CLS}.{CLS}"


def _scope(env):
    with env["app"].app_context():
        return routes_mod._ctx_chip_key(routes_mod._active_ctx()) or "no-chip"


def _publish(env):
    inst, scope = env["app"].instance_path, _scope(env)
    D.save_draft(inst, scope, CLS, "class FlatPulse: pass\n")
    target = env["tmp"] / "quam_config" / f"{CLS}.py"
    target.parent.mkdir(exist_ok=True)
    target.write_text("# class file\n")
    rec = D.draft_path(inst, scope, CLS).parent / W.PUBLISHED_FILE
    rec.write_text(json.dumps({"path": str(target), "qclass": QCLASS}))
    return inst, scope, target


def _gate(env, key):
    with env["app"].app_context():
        m = routes_mod._active_ctx()["modifier"]
        m.create_subtree(key, {"__class__": QCLASS, "amplitude": 0.1}, group_id=f"g-{key}")


def _undo_message(env):
    r = env["client"].post("/undo")
    assert r.status_code == 200
    return json.loads(r.headers["HX-Trigger"])["cellsReverted"]["message"]


def _merged(env):
    with env["app"].app_context():
        return routes_mod._active_ctx()["store"].merged


def test_undo_of_the_last_gate_names_the_file_left_on_disk(env):  # noqa: F811
    inst, scope, target = _publish(env)
    _gate(env, "gate_one")
    assert Q.unused_files(inst, scope, _merged(env)) == []
    msg = _undo_message(env)
    assert target.is_file()
    assert f"quam_config/{CLS}.py stays on disk; no gate uses it" in msg
    assert Q.unused_files(inst, scope, _merged(env)) == [CLS]


def test_undo_of_one_of_two_gates_on_a_class_keeps_it_in_use(env):  # noqa: F811
    inst, scope, target = _publish(env)
    _gate(env, "gate_one")
    _gate(env, "gate_two")
    msg = _undo_message(env)
    assert "stays on disk" not in msg
    assert target.is_file()
    assert Q.unused_files(inst, scope, _merged(env)) == []


def test_the_class_list_marks_an_unused_file_only(env):  # noqa: F811
    inst, scope, _target = _publish(env)
    _gate(env, "gate_one")
    c = env["client"]
    assert "not used" not in c.get("/pulse-class").get_data(as_text=True)
    _undo_message(env)
    html = c.get("/pulse-class").get_data(as_text=True)
    assert f"{CLS}</a> <span class=\"ui-note\">not used</span>" in html


AGENT = {"X-SM-Agent": "claude"}
NOTE = f"quam_config/{CLS}.py stays on disk; no gate uses it"


def _modifier(env):
    with env["app"].app_context():
        return routes_mod._active_ctx()["modifier"]


def _class_list(env):
    return env["client"].get("/pulse-class").get_data(as_text=True)


def test_a_pulse_naming_the_class_keeps_it_in_use(env):  # noqa: F811
    inst, scope, target = _publish(env)
    _modifier(env).create_subtree("qubits.qA1.xy_pulse", {"__class__": QCLASS, "length": 40}, group_id="g-pulse")
    _gate(env, "gate_one")
    msg = _undo_message(env)
    assert "stays on disk" not in msg
    assert target.is_file()
    assert Q.unused_files(inst, scope, _merged(env)) == []
    assert "not used" not in _class_list(env)


def test_a_file_published_for_another_chip_is_not_judged_here(env):  # noqa: F811
    inst, scope = env["app"].instance_path, "another-chip"
    D.save_draft(inst, scope, CLS, "class FlatPulse: pass\n")
    target = env["tmp"] / "quam_config" / f"{CLS}.py"
    target.parent.mkdir(exist_ok=True)
    target.write_text("# class file\n")
    rec = D.draft_path(inst, scope, CLS).parent / W.PUBLISHED_FILE
    rec.write_text(json.dumps({"path": str(target), "qclass": QCLASS}))
    _gate(env, "gate_one")
    msg = _undo_message(env)
    assert "stays on disk" not in msg
    assert Q.unused_files(inst, _scope(env), _merged(env)) == []
    assert Q.unused_files(inst, scope, _merged(env)) == [CLS]
    assert "not used" not in _class_list(env)


def test_a_grouped_undo_names_the_file_whatever_its_place(env):  # noqa: F811
    _inst, _scope_key, target = _publish(env)
    m = _modifier(env)
    m.create_subtree("gate_one", {"__class__": QCLASS, "amplitude": 0.1}, group_id="g-both")
    m.create_subtree("gate_note", {"amplitude": 0.2}, group_id="g-both")
    msg = _undo_message(env)
    assert NOTE in msg
    assert target.is_file()


def test_undoing_a_class_leaf_edit_names_the_file(env):  # noqa: F811
    inst, scope, target = _publish(env)
    m = _modifier(env)
    m.create_subtree("gate_one", {"__class__": "quam.components.pulses.SquarePulse", "amplitude": 0.1},
                     group_id="g-made")
    m.set_value("gate_one.__class__", QCLASS, group_id="g-leaf")
    assert Q.unused_files(inst, scope, _merged(env)) == []
    msg = _undo_message(env)
    assert NOTE in msg
    assert target.is_file()
    assert Q.unused_files(inst, scope, _merged(env)) == [CLS]


def test_the_journal_step_undo_names_the_file(env):  # noqa: F811
    inst, scope, target = _publish(env)
    c = env["client"]
    assert c.post("/settings/undo-live", data={"enabled": "1"}).get_json()["enabled"] is True
    _gate(env, "gate_one")
    assert c.post("/state/apply-to-live").status_code == 200
    with env["app"].app_context():
        assert not routes_mod._active_ctx()["store"].change_log
    r = c.post("/undo")
    assert r.status_code == 200
    t = json.loads(r.headers["HX-Trigger"])["cellsReverted"]
    assert t["live"] is True
    assert NOTE in t["message"]
    assert target.is_file()
    assert Q.unused_files(inst, scope, _merged(env)) == [CLS]


def test_a_redo_that_restores_the_use_gives_no_notice(env):  # noqa: F811
    inst, scope, target = _publish(env)
    _gate(env, "gate_one")
    assert NOTE in _undo_message(env)
    r = env["client"].post("/redo")
    assert r.status_code == 200
    assert "stays on disk" not in (r.headers.get("HX-Trigger") or "")
    assert "stays on disk" not in r.get_data(as_text=True)
    assert Q.unused_files(inst, scope, _merged(env)) == []
    assert target.is_file()


def test_the_agent_undo_of_its_own_gate_names_the_file(env):  # noqa: F811
    inst, scope, target = _publish(env)
    m = _modifier(env)
    e = m.create_subtree("gate_one", {"__class__": QCLASS, "amplitude": 0.1}, group_id="g-agent")
    e.actor = "by_claude"
    d = env["client"].post("/api/agent/undo-mine", json={}, headers=AGENT).get_json()
    assert d["ok"] and d["reverted"] == ["gate_one"]
    assert d["notice"] == NOTE
    assert target.is_file()
    assert Q.unused_files(inst, scope, _merged(env)) == [CLS]


def test_the_agent_undo_keeps_quiet_while_a_gate_still_uses_the_class(env):  # noqa: F811
    _publish(env)
    _gate(env, "gate_one")
    e = _modifier(env).create_subtree("gate_two", {"__class__": QCLASS, "amplitude": 0.1}, group_id="g-agent")
    e.actor = "by_claude"
    d = env["client"].post("/api/agent/undo-mine", json={}, headers=AGENT).get_json()
    assert d["ok"] and d["reverted"] == ["gate_two"]
    assert d["notice"] == ""
