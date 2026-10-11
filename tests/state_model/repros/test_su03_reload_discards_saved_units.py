"""SU-03: App reload marks saved-but-unapplied journal units "discarded".

Harness: SAVE, then APP_RELOAD (nothing pending in the tray) -> every unit the
save committed is stamped ``meta.discarded`` although the reload re-read the
very working copy that still holds them (working_dirty is carried across).
Contract: docs/271 review P2-6 -- the mark is for a working copy REPLACED
wholesale (pull / stage / restore); a reload replaces nothing. A later Apply
of that content must name those units as what it wrote.
"""
import pytest

from tests.state_model.repros._rig import Chip



def test_a_reload_keeps_saved_units_claimable(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    assert chip.c.post("/save").status_code == 200
    units, _ = chip.journal()
    saved = units[-1]["id"]
    r = chip.c.post("/app/reload", data={"mode": "state"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert chip.ctx.get("working_dirty"), "the saved content is still waiting"
    units, _ = chip.journal()
    meta = next(u for u in units if u["id"] == saved).get("meta") or {}
    assert not meta.get("discarded"), meta
    assert chip.c.post("/state/apply-to-live").status_code == 200
    units, _ = chip.journal()
    meta = next(u for u in units if u["id"] == saved).get("meta") or {}
    assert meta.get("hub"), f"the Apply that wrote the saved unit does not name it: {meta}"
