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
from tests.state_model.repros._rig import Chip


# v1.3.0: xfail -> pass, a refused pull cannot publish dropped-edit backups.
def test_bring_back_after_a_refused_take_live_restores_nothing(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    versions = set((chip.inst / "history").glob("*/*/meta.json"))
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
    assert chip.ctx.get("take_live_backup") is None
    assert set((chip.inst / "history").glob("*/*/meta.json")) == versions


def test_a_refused_take_live_preserves_an_earlier_successful_backup(tmp_path):
    import copy
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    r = chip.c.post("/state/sync", data={"mode": "discard", "force": "1"})
    assert r.status_code == 200
    backup = copy.deepcopy(chip.ctx["take_live_backup"])
    versions = set((chip.inst / "history").glob("*/*/meta.json"))
    chip.edit("qubits.q2.T1", "4.1e-05")
    p = chip.live / "state.json"
    p.write_bytes(p.read_bytes()[:40])
    r = chip.c.post("/state/sync", data={"mode": "discard", "force": "1"})
    assert r.status_code == 409
    assert chip.ctx["take_live_backup"] == backup
    assert set((chip.inst / "history").glob("*/*/meta.json")) == versions
    assert chip.log() == [("qubits.q2.T1", 4.1e-05)]
