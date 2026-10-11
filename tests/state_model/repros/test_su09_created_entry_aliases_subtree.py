"""SU-09: a created subtree's change-log entry aliases the live subtree.

Duplicate (or create) a pulse, then edit a field inside it: the create entry's
``new_value`` IS the subtree, so it changes with the edit, and the journal unit
saved from it records the EDITED value as what the create wrote. After Apply,
Ctrl+Z #1 takes the field back on the chip; Ctrl+Z #2 should remove the pulse
on the chip but compares the chip (old field value) with the aliased record
(new value), calls it drift, and only stages: "the value had moved since".
Harness: PULSE duplicate/rename + EDIT inside the new subtree + APPLY + UNDO.
Contract: docs/107/160 (the journal records what each action wrote; the walk
writes the chip when nothing moved).
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-09: ChangeEntry.new_value aliases the created subtree")
def test_the_create_entry_keeps_the_value_it_created(tmp_path):
    chip = Chip(tmp_path)
    r = chip.c.post("/api/pulse/duplicate", data={"path": "qubits.q1.z.operations.const", "new_name": "dupx"})
    assert r.status_code == 200
    chip.edit("qubits.q1.z.operations.dupx.amplitude", "0.333")
    created = chip.ctx["store"].change_log[0]
    assert created.created and created.new_value["amplitude"] == 0.1


@pytest.mark.xfail(strict=True, reason="SU-09: the second Ctrl+Z calls the aliased record drift")
def test_two_ctrl_z_after_apply_remove_the_new_pulse_from_the_chip(tmp_path):
    chip = Chip(tmp_path)
    chip.c.post("/api/pulse/duplicate", data={"path": "qubits.q1.z.operations.const", "new_name": "dupx"})
    chip.edit("qubits.q1.z.operations.dupx.amplitude", "0.333")
    assert chip.c.post("/state/apply-to-live").status_code == 200
    msgs = [(chip.trigger(chip.c.post("/undo")).get("cellsReverted") or {}).get("message")
            for _ in range(2)]
    ops = chip.live_state()["qubits"]["q1"]["z"]["operations"]
    assert "dupx" not in ops, msgs
