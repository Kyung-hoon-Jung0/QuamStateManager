import json
import pytest
from tests.test_autosync_merge import env, _arm, _ctx, _state, _edit
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


@pytest.mark.parametrize("fork", [False, True], ids=["intact-redo", "forked-redo"])
def test_restore_marks_only_an_intact_redo_timeline(env, fork):
    _arm(env, pull=True, push=True)
    _apply_name(env)
    # Keep T1 applied while undoing a separate edit. The outside save below
    # must actually restore T1, advancing the sequence after the pull.
    assert _edit(env).status_code == 200
    assert env["client"].post("/undo").status_code == 200
    ctx = _ctx(env)
    assert ctx["redo_stack"]
    assert ctx["redo_seq"] == ctx["store"].mutation_seq
    if fork:
        assert _edit(env, path="network.host", value="2.2.2.2").status_code == 200
        assert ctx["redo_seq"] != ctx["store"].mutation_seq
    before = undo_journal.load_state(_sidecar(env))
    response = _outside_save_and_pull(env, _state())
    if fork:
        # The background pull leaves pending edits alone; the user's merge
        # replays the new edit and restores the lost applied value together.
        assert response.status_code == 204
        response = env["client"].post("/state/sync", data={"mode": "apply"})
    assert response.status_code == 200
    assert json.loads(response.headers["HX-Trigger"])["autoSyncRestored"]["count"] == 1
    after = undo_journal.load_state(_sidecar(env))
    if not fork:
        assert after == before
    response = env["client"].post("/redo")
    assert response.status_code == 200, response.get_data(as_text=True)
    redo = json.loads(response.headers["HX-Trigger"])["cellsReverted"]
    assert redo["consumed"] == (0 if fork else 1)
    assert redo["stopped"] == ("forked" if fork else None)
    live = json.loads((env["live"] / "state.json").read_text(encoding="utf-8"))
    assert live["qubits"]["qA1"]["T1"] == 3.3e-5
    assert ctx["store"].state["qubits"]["qA1"]["f_01"] == (5.0e9 if fork else 5.1e9)
    if fork:
        assert not ctx["redo_stack"]
        assert ctx["store"].wiring["network"]["host"] == "2.2.2.2"
