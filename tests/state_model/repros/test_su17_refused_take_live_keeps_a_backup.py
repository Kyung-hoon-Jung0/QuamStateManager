"""SU-17: a Take live refused over an unreadable live file still records a Take-live backup.

Take live writes its labelled backup (the edits it is about to drop) BEFORE it
reads the live chip; when that read fails (a torn file: 409 transient) nothing
is dropped -- the tray keeps every edit -- but the backup stays. A later
"Bring back" then re-stages those edits on top of the tray, with their values
from the refused press: a field edited again since goes back to the older value.
Harness: EDIT, OUTSIDE torn, TAKE_LIVE (refused), OUTSIDE repair, EDIT (same
field), BRING_BACK -> "Brought back", the newer value replaced.
Contract: I7 (Bring back re-stages exactly the dropped edits); docs/255 (a
refused press changes nothing).
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-17: the backup is written before the refused pull")
def test_bring_back_after_a_refused_take_live_restores_nothing(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    raw = (chip.live / "state.json").read_bytes()
    (chip.live / "state.json").write_bytes(raw[: len(raw) // 2])        # a save in progress
    r = chip.c.post("/state/sync", data={"mode": "discard", "force": "1"})
    assert r.status_code >= 400, r.get_data(as_text=True)[:200]           # refused: unreadable
    assert chip.log() == [("qubits.q1.T1", 3.1e-05)]                     # nothing was dropped
    (chip.live / "state.json").write_bytes(raw)
    chip.edit("qubits.q1.T1", "3.3e-05")                                 # the user moves on
    r = chip.c.post("/state/take-live/restore")
    msg = (chip.trigger(r).get("syncToast") or {}).get("message", "")
    assert chip.ctx["store"].merged["qubits"]["q1"]["T1"] == 3.3e-05, (msg, chip.log())
    assert not msg.startswith("Brought back"), msg
