"""SU-08: a refused Apply's status comes back after later writes return the chip to that state.

``ctx["apply_refused"]`` is never cleared; it counts while its hash equals the
sync point. A chip that goes A -> B -> A (a Keep mine, a Take live, a revert)
shows "refused" (your Apply wrote nothing) again on the next edit, though the
last press LANDED.
Harness: APPLY refused (stale) ... a landed write that returns live to the
refused sync point ... EDIT -> status "refused", model "mine".
Contract: sync-ux 2026-09-25 -- "refused" is "the most recent thing the user did".
"""
import pytest

from tests.state_model.repros._rig import Chip


def test_a_landed_write_ends_an_earlier_refusal(tmp_path):
    chip = Chip(tmp_path)
    orig = chip.live_state()["qubits"]["q2"]["chi"]
    chip.set_live("qubits.q2.chi", 1300000.0)               # an experiment writes
    chip.edit("qubits.q1.T1", "3.1e-05")
    r = chip.c.post("/state/apply-to-live")                  # refused: live moved
    assert "pending-tray-conflict" in r.get_data(as_text=True)
    chip.c.post("/discard_all")
    h = chip.c.get("/state/overwrite-live/preflight").get_json()["live_hash"]
    r = chip.c.post("/state/apply-to-live", data={"force": "1", "expect_live_hash": h})
    assert r.status_code == 200 and chip.live_state()["qubits"]["q2"]["chi"] == orig   # Keep mine landed
    chip.edit("qubits.q3.T1", "4.1e-05")
    assert chip.status() == "mine"


def test_a_take_live_ends_an_earlier_refusal(tmp_path):
    chip = Chip(tmp_path)
    orig = chip.live_state()["qubits"]["q2"]["chi"]
    chip.set_live("qubits.q2.chi", 1300000.0)
    chip.edit("qubits.q1.T1", "3.1e-05")
    r = chip.c.post("/state/apply-to-live")                  # refused at the old sync point
    assert "pending-tray-conflict" in r.get_data(as_text=True)
    chip.c.post("/discard_all")
    assert chip.c.post("/state/sync", data={"mode": "discard", "force": "1"}).status_code == 200
    chip.set_live("qubits.q2.chi", orig)                     # the outside writer goes back
    assert chip.c.post("/state/sync", data={"mode": "discard", "force": "1"}).status_code == 200
    chip.edit("qubits.q3.T1", "4.1e-05")
    assert chip.status() == "mine"
