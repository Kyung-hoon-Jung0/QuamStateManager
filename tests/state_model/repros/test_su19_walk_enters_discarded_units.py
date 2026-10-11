"""SU-19: Ctrl+Z walks into journal units the working copy no longer holds.

A Save commits journal units; a later Take live (or stage / restore) replaces
the working copy and marks those units ``meta.discarded`` -- "no longer in
effect anywhere" (docs/271 review P2-6). The walk ignores the mark: Ctrl+Z
stages the inverse of a change that is not there. For a created subtree the
inverse delete fails, the press answers 409 "Undo (staged) failed, nothing
changed", and every later Ctrl+Z meets the same unit -- the journal below it is
unreachable.
Harness: PULSE duplicate, SAVE, TAKE_LIVE, UNDO (seed 1761).
Contract: docs/107/160 (the walk restores what each unit wrote; a unit no
longer in effect is not re-applied backwards).
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-19: the walk does not skip discarded units")
def test_ctrl_z_after_take_live_skips_the_dropped_save(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q2.T1", "3.3e-05")
    assert chip.c.post("/state/apply-to-live").status_code == 200        # a unit in effect
    r = chip.c.post("/api/pulse/duplicate", data={"path": "qubits.q1.z.operations.const", "new_name": "dupx"})
    assert r.status_code == 200
    assert chip.c.post("/save").status_code == 200                        # saved, never applied
    r = chip.c.post("/state/sync", data={"mode": "discard", "force": "1"})
    assert r.status_code == 200                                           # Take live drops it
    units, _ = chip.journal()
    assert (units[-1].get("meta") or {}).get("discarded"), units[-1].get("meta")
    r = chip.c.post("/undo")
    assert r.status_code == 200, r.get_data(as_text=True)[-300:]
    assert chip.live_state()["qubits"]["q2"]["T1"] != 3.3e-05, "Ctrl+Z did not reach the applied unit"
