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


def test_the_window_for_an_undone_value_runs_from_the_undo(env, monkeypatch):  # noqa: F811
    """Applied 5h50m ago, undone now, outside save 20 min later: still a loss."""
    import time as _time
    _apply_name(env)
    t_apply = _time.time()
    monkeypatch.setattr(routes_mod.time, "time", lambda: t_apply + 5 * 3600 + 50 * 60)
    from quam_state_manager.core import undo_journal
    monkeypatch.setattr(undo_journal.time, "time", lambda: t_apply + 5 * 3600 + 50 * 60)
    _undo(env)
    monkeypatch.setattr(routes_mod.time, "time", lambda: t_apply + 6 * 3600 + 10 * 60)
    with env["app"].test_request_context():
        held = routes_mod._applied_values_held(routes_mod._active_ctx())
        assert held.get("qubits.qA1.T1") == 2e-05
        assert routes_mod._applied_value_lost(held, "qubits.qA1.T1", 3.3e-05)


def _sidecar(env):
    from quam_state_manager.core import undo_journal
    with env["app"].test_request_context():
        ctx = routes_mod._active_ctx()
        return undo_journal.sidecar_path(env["app"].instance_path, ctx["path"])


def test_a_redo_takes_the_undo_stamp_off_the_unit(tmp_path):
    """save_cursor stamps meta.undone_ts on every unit an undo walks past; a
    redo that moves the cursor forward over a unit takes the stamp off, so a
    unit in effect never reads as undone to another window or the next
    session. A unit still past the cursor keeps its stamp."""
    from quam_state_manager.core import undo_journal
    p = tmp_path / "j.undo_journal.json"

    def _u(i):
        return {"id": f"u{i}", "ts": 1.0, "entries": [
            {"path": "a.b", "old": i, "new": i + 1, "source_file": "state",
             "created": False, "deleted": False}], "meta": {}}

    undo_journal.append_units(p, [_u(1), _u(2), _u(3)])
    undo_journal.save_cursor(p, 1)                      # two units undone live
    units, cur = undo_journal.load_state(p)
    assert cur == 1 and "undone_ts" not in units[0]["meta"]
    assert all(isinstance(u["meta"].get("undone_ts"), float) for u in units[1:]), units
    undo_journal.save_cursor(p, 2)                      # one redo
    units, cur = undo_journal.load_state(p)
    assert cur == 2
    assert "undone_ts" not in units[1]["meta"], "the redone unit still reads as undone"
    assert "undone_ts" in units[2]["meta"], "the unit still undone lost its stamp"


def test_a_live_redo_leaves_no_undo_stamp_on_the_sidecar(env):  # noqa: F811
    """Through the real doors: Ctrl+Z then Ctrl+Shift+Z, both live."""
    from quam_state_manager.core import undo_journal
    _apply_name(env)
    _undo(env)
    units, cur = undo_journal.load_state(_sidecar(env))
    assert cur == len(units) - 1 and "undone_ts" in (units[-1].get("meta") or {}), units
    r = env["client"].post("/redo")
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _live_t1(env) == 3.3e-05
    units, cur = undo_journal.load_state(_sidecar(env))
    assert cur == len(units) and "undone_ts" not in (units[-1].get("meta") or {}), units
