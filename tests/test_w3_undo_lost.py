"""An undo that reached the chip is an apply of the old value: an outside
save (a node's machine.save from its in-memory copy) that writes the undone
value back is a loss, restored by an armed pull and flagged otherwise."""
from __future__ import annotations

import json

import quam_state_manager.web.routes as routes_mod
from tests.test_autosync_lost_applied import _apply_name, _outside_save_and_pull
from tests.test_autosync_merge import _arm, _state, env  # noqa: F401


def _live_t1(env):
    return json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["T1"]


def _undo(env):
    r = env["client"].post("/undo")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _live_t1(env) == 2e-05, "Ctrl+Z must have reached the chip"


def test_an_undo_a_node_save_reverted_is_restored(env):  # noqa: F811
    _arm(env, pull=True, push=True)
    _apply_name(env)
    _undo(env)
    r = _outside_save_and_pull(env, _state(f01=5.2e9, t1=3.3e-05))  # pre-undo value back
    assert r.status_code == 200
    trig = json.loads(r.headers["HX-Trigger"])
    assert trig["autoSyncRestored"]["count"] == 1, trig
    assert _live_t1(env) == 2e-05
    doc = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert doc["qubits"]["qA1"]["f_01"] == 5.2e9, "the node's own write is kept"


def test_a_redo_a_node_save_reverted_is_restored(env):  # noqa: F811
    _arm(env, pull=True, push=True)
    _apply_name(env)
    _undo(env)
    r = env["client"].post("/redo")
    assert r.status_code == 200
    assert _live_t1(env) == 3.3e-05
    r = _outside_save_and_pull(env, _state(f01=5.2e9))           # T1 back to 2e-5
    assert json.loads(r.headers["HX-Trigger"])["autoSyncRestored"]["count"] == 1
    assert _live_t1(env) == 3.3e-05


def test_an_undo_reverted_with_auto_sync_off_is_flagged(env):  # noqa: F811
    _apply_name(env)
    _undo(env)
    with env["app"].test_request_context():
        ctx = routes_mod._active_ctx()
        held = routes_mod._applied_values_held(ctx)
        assert held.get("qubits.qA1.T1") == 2e-05
        assert routes_mod._applied_value_lost(held, "qubits.qA1.T1", 3.3e-05)
        assert not routes_mod._applied_value_lost(held, "qubits.qA1.T1", 2e-05)


def test_an_unrelated_outside_value_is_not_a_loss(env):  # noqa: F811
    _apply_name(env)
    _undo(env)
    with env["app"].test_request_context():
        held = routes_mod._applied_values_held(routes_mod._active_ctx())
        assert not routes_mod._applied_value_lost(held, "qubits.qA1.T1", 7e-05)
