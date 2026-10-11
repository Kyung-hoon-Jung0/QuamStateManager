"""SU-04: a wholesale journal unit re-records edits a Save already journaled.

Harness: EDIT, SAVE, then Keep mine (force + the fresh live hash) -- with or
without a live move -- or Apply after a stage: the press adds a wholesale unit
(``force-overwrite`` / ``apply-staged``) computed against the SAVED working
copy, so the saved edit is in the journal twice, the wholesale unit on top.
Ctrl+Z then undoes it on the chip, and the next Ctrl+Z meets the saved unit
whose value already went back: "staged only: the value had moved since".
Contract: docs/160 B (one unit per change; the walk undoes edits first) and
docs/107 (the journal is a straight line of what is in effect).
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-04: the force-overwrite unit repeats the saved unit")
def test_keep_mine_after_a_save_journals_the_edit_once(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    assert chip.c.post("/save").status_code == 200
    h = chip.c.get("/state/overwrite-live/preflight").get_json()["live_hash"]
    r = chip.c.post("/state/apply-to-live", data={"force": "1", "expect_live_hash": h})
    assert r.status_code == 200
    assert chip.live_state()["qubits"]["q1"]["T1"] == 3.1e-05
    units, _ = chip.journal()
    with_t1 = [u for u in units if any(e["path"] == "qubits.q1.T1" for e in u["entries"])]
    assert len(with_t1) == 1, [u.get("meta") for u in with_t1]


@pytest.mark.xfail(strict=True, reason="SU-04: the second Ctrl+Z meets the repeated unit")
def test_two_ctrl_z_after_keep_mine_walk_the_chip_back_once(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    chip.c.post("/save")
    h = chip.c.get("/state/overwrite-live/preflight").get_json()["live_hash"]
    chip.c.post("/state/apply-to-live", data={"force": "1", "expect_live_hash": h})
    chip.edit("qubits.q2.T1", "4.1e-05")
    chip.c.post("/state/apply-to-live")
    msgs = []
    for _ in range(3):
        r = chip.c.post("/undo")
        msgs.append((chip.trigger(r).get("cellsReverted") or {}).get("message"))
    # three presses: q2 back, q1 back, nothing left -- never a phantom staged step
    assert not any("moved since" in (m or "") for m in msgs), msgs
