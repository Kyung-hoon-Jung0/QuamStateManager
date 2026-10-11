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
import copy

import pytest

from tests.state_model.repros._rig import Chip


def _press(chip):
    before = _facts(chip)
    r = chip.c.post("/state/apply-to-live")
    return before, r


def _facts(chip):
    ctx = chip.ctx
    wc = ctx["working_copy"]
    files = [wc.working_folder / name for name in ("state.json", "wiring.json")]
    return (chip.log(), chip.journal(),
            [(p.read_bytes(), p.stat().st_mtime_ns) for p in files],
            wc.meta_path().read_bytes(),
            copy.deepcopy({k: ctx.get(k) for k in ("working_dirty", "pending_reapply",
                                                   "pending_reapply_orig", "last_apply")}))


@pytest.fixture
def saves(monkeypatch):
    from quam_state_manager.web import routes
    calls = []
    original = routes._press_save

    def track(ctx, press):
        calls.append(True)
        return original(ctx, press)

    monkeypatch.setattr(routes, "_press_save", track)
    return calls


def test_apply_with_the_live_state_file_missing_refuses_and_changes_nothing(tmp_path, saves):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    (chip.live / "state.json").unlink()
    before, r = _press(chip)
    # v1.3.0: save then 500 -> refuse 409 before save, a missing live file changes nothing.
    assert r.status_code == 409, r.get_data(as_text=True)[-300:]
    assert "live chip state file is missing" in r.get_data(as_text=True)
    assert _facts(chip) == before
    assert not saves, "a known missing live file must refuse before saving"


def test_apply_with_the_live_folder_missing_is_not_a_500(tmp_path, saves):
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    shutil.rmtree(chip.live)
    before, r = _press(chip)
    # v1.3.0: 500 after a save -> refuse 409 unchanged, the live folder is absent.
    assert r.status_code == 409, r.get_data(as_text=True)[-300:]
    assert "live chip folder is missing" in r.get_data(as_text=True)
    assert _facts(chip) == before
    assert not saves, "a known missing live folder must refuse before saving"


def test_a_live_file_disappearing_after_save_restores_the_press(tmp_path, monkeypatch):
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    original = routes._press_save

    def save_then_remove(ctx, press):
        original(ctx, press)
        (chip.live / "state.json").unlink()

    monkeypatch.setattr(routes, "_press_save", save_then_remove)
    before, response = _press(chip)
    assert response.status_code == 409
    assert _facts(chip) == before
