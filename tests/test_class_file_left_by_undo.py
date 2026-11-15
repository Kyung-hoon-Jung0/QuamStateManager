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
