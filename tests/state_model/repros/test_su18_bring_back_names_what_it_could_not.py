"""SU-18: Bring back counts the edits it could not re-stage but does not name them.

Take live drops an edit inside a pulse; the pulse is deleted afterwards; Bring
back re-stages the rest and says "...; 1 could not be re-applied." -- which edit
(and its value) is said nowhere, so the value is lost without a name.
Harness: EDIT inside a pulse, TAKE_LIVE, PULSE delete, BRING_BACK (seed 1686).
Contract: I7 -- every value entered is on live, in the working copy, in the
tray, in the backup, or NAMED in a refusal/toast.
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-18: the Bring back toast counts, it does not name")
def test_bring_back_names_the_edit_it_could_not_restage(tmp_path):
    chip = Chip(tmp_path)
    path = "qubits.q1.xy.operations.saturation.amplitude"
    chip.edit(path, "0.21")
    assert chip.c.post("/state/sync", data={"mode": "discard", "force": "1"}).status_code == 200
    r = chip.c.post("/api/pulse/delete", data={"path": "qubits.q1.xy.operations.saturation", "force": "1"})
    assert r.status_code == 200
    r = chip.c.post("/state/take-live/restore")
    msg = (chip.trigger(r).get("syncToast") or {}).get("message", "")
    assert "could not" in msg, msg
    assert path in msg or "saturation" in msg, msg
