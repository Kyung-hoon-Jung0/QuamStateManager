"""SB-04: a second process's Save must never overwrite another process's saved draft.

Two SM processes on one instance dir share the working folder. Process A saves
a draft; process B, loaded before that save, edits and presses Save. B used to
write its store over A's draft, so A's edits vanished on A's next restart and A
was shown B's edit as its own staged version. B's Save must refuse honestly and
change nothing: its tray, the working files (A's draft) and the journal.
"""
import json

from tests.state_model.repros._rig import Chip


def _foreign_save(chip: Chip, path: str, value) -> bytes:
    """What another process's Save leaves in the shared working folder."""
    from quam_state_manager.core import safe_io
    wc = chip.ctx["working_copy"]
    state, wiring = safe_io.read_state_wiring(wc.working_folder)
    node = state
    *parents, leaf = path.split(".")
    for key in parents:
        node = node[key]
    node[leaf] = value
    safe_io.write_state_wiring(wc.working_folder, state, wiring)
    return (wc.working_folder / "state.json").read_bytes()


def test_a_save_over_another_process_draft_is_refused(tmp_path):
    chip = Chip(tmp_path)
    draft = _foreign_save(chip, "qubits.q1.T1", 2.222e-05)
    chip.edit("qubits.q2.T1", "3.333e-05")
    before = (chip.log(), chip.journal(), chip.live_state())
    r = chip.c.post("/save")
    assert r.status_code == 409, r.get_data(as_text=True)
    assert "window/process" in r.get_data(as_text=True)
    assert "Nothing was saved" in r.get_data(as_text=True)
    assert (chip.log(), chip.journal(), chip.live_state()) == before
    wc = chip.ctx["working_copy"]
    assert (wc.working_folder / "state.json").read_bytes() == draft
    assert chip.ctx["store"].change_log, "the refused save emptied the tray"


def test_a_copy_replaced_while_the_save_waits_keeps_the_tray(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    chip.edit("qubits.q2.T1", "3.333e-05")
    ctx = chip.ctx
    reapply0 = json.dumps(ctx.get("pending_reapply"), sort_keys=True, default=str)
    original = routes._active_wc_lock
    state = {}

    def replaced_after_first_check(c):
        # the other process saves after the up-front check, before the locked one
        if not state:
            state["draft"] = _foreign_save(chip, "qubits.q1.T1", 2.222e-05)
        return original(c)

    monkeypatch.setattr(routes, "_active_wc_lock", replaced_after_first_check)
    before = (chip.log(), chip.journal(), chip.live_state())
    r = chip.c.post("/save")
    assert r.status_code == 409, r.get_data(as_text=True)
    assert "window/process" in r.get_data(as_text=True)
    assert (chip.log(), chip.journal(), chip.live_state()) == before
    assert (ctx["working_copy"].working_folder / "state.json").read_bytes() == state["draft"]
    assert json.dumps(ctx.get("pending_reapply"), sort_keys=True, default=str) == reapply0


def test_an_own_save_then_another_save_still_works(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q2.T1", "3.333e-05")
    assert chip.c.post("/save").status_code == 200
    chip.edit("qubits.q3.T1", "4.444e-05")
    assert chip.c.post("/save").status_code == 200
    assert not chip.ctx["store"].change_log
