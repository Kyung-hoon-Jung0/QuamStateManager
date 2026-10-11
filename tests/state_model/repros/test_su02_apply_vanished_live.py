"""SU-02: Apply while the live state.json (or the live folder) is gone answers HTTP 500.

Harness: OUTSIDE ``delete_state`` / ``delete_folder``, then APPLY. With an edit
pending the press first saves (the tray empties into the working copy, the
journal gains the units), then fails with 500 "Apply to live failed".
Contract: VPLAN A.3 (vanished folder: "copy served unchanged; no prompt") and
contract (3) "never a 500"; docs/255 -- a press that wrote nothing changes
nothing in SM (tray, working copy, journal) and says why.
"""
import json
import shutil

import pytest

from tests.state_model.repros._rig import Chip


def _press(chip):
    before = (chip.log(), chip.journal())
    r = chip.c.post("/state/apply-to-live")
    return before, r


@pytest.mark.xfail(strict=True, reason="SU-02: unforced Apply over a missing live file is a 500")
def test_apply_with_the_live_state_file_missing_refuses_and_changes_nothing(tmp_path):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    (chip.live / "state.json").unlink()
    (log0, jr0), r = _press(chip)
    assert r.status_code < 500, r.get_data(as_text=True)[-300:]
    assert chip.log() == log0, "the refused press emptied the tray"
    assert chip.journal() == jr0


@pytest.mark.xfail(strict=True, reason="SU-02: unforced Apply over a missing live folder is a 500")
def test_apply_with_the_live_folder_missing_is_not_a_500(tmp_path):
    chip = Chip(tmp_path)
    shutil.rmtree(chip.live)
    r = chip.c.post("/state/apply-to-live")
    assert r.status_code < 500, r.get_data(as_text=True)[-300:]
