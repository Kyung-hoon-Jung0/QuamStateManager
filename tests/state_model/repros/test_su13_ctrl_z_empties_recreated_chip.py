"""SU-13: Keep mine onto a vanished live folder, then one Ctrl+Z writes an EMPTY chip.

Keep mine (force) recreates a deleted live folder from the working copy and
journals a wholesale ``force-overwrite`` unit that "created" every top-level
key. The next Ctrl+Z walks that unit live: every key is deleted and ``{}`` is
written to the live state.json (wiring too) -- the chip file is emptied.
Harness: OUTSIDE delete_folder, APPLY force, UNDO (live).
Contract: docs/160 (a live walk step writes only what the journal can put
back); I7/I11 -- a keypress never leaves the chip without its qubits.
"""
import json
import shutil

import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-13: the recreate is journaled as 'created every key'")
def test_ctrl_z_after_recreating_a_vanished_chip_never_empties_it(tmp_path):
    chip = Chip(tmp_path)
    shutil.rmtree(chip.live)
    r = chip.c.post("/state/apply-to-live", data={"force": "1"})
    assert r.status_code == 200 and (chip.live / "state.json").exists()
    chip.c.post("/undo")
    doc = json.loads((chip.live / "state.json").read_text(encoding="utf-8"))
    assert doc.get("qubits"), f"the live state.json now holds {doc!r}"
