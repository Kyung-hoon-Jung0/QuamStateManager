import json
from tests.test_autosync_merge import env, _arm, _ctx, _state
from tests.test_autosync_lost_applied import _apply_name, _outside_save_and_pull
from tests.test_undo_tail_protect import _sidecar
from quam_state_manager.core import undo_journal


def test_restore_preserves_redo_and_cursor(env):
    _arm(env, pull=True, push=True)
    _apply_name(env)
    assert env["client"].post("/undo").status_code == 200
    before = undo_journal.load_state(_sidecar(env))
    response = _outside_save_and_pull(env, _state(t1=3.3e-5))
    assert response.status_code == 200
    after = undo_journal.load_state(_sidecar(env))
    assert after == before
    response = env["client"].post("/redo")
    assert response.status_code == 200, response.get_data(as_text=True)
    live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert live["qubits"]["qA1"]["T1"] == 3.3e-5
