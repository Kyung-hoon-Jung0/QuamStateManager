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


def test_ctrl_z_after_recreating_a_vanished_chip_never_empties_it(tmp_path):
    chip = Chip(tmp_path)
    shutil.rmtree(chip.live)
    r = chip.c.post("/state/apply-to-live", data={"force": "1"})
    assert r.status_code == 200 and (chip.live / "state.json").exists()
    before = (chip.log(), chip.journal(), chip.live_state())
    r = chip.c.post("/undo")
    # v1.3.0: delete the recreated document -> refuse, there is no state to restore.
    assert r.status_code == 409
    assert "the chip had no state before this write; nothing to restore" in r.get_data(as_text=True)
    assert (chip.log(), chip.journal(), chip.live_state()) == before
    doc = json.loads((chip.live / "state.json").read_text(encoding="utf-8"))
    assert doc.get("qubits"), f"the live state.json now holds {doc!r}"


@pytest.mark.parametrize("force", [False, True])
@pytest.mark.parametrize("door", ["apply", "walk", "core"])
def test_no_apply_door_can_write_an_empty_state(tmp_path, force, door):
    from quam_state_manager.core import hub, safe_io, working_copy
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    before = chip.live_state()
    ctx = chip.ctx
    ctx["store"].state.clear()
    if door == "core":
        safe_io.atomic_write_json(ctx["working_copy"].working_folder / "state.json", {})
        refused = False
        try:
            working_copy.apply_to_live(
                ctx["working_copy"], force=force,
                record=hub.Pending(tmp_path / "record", "sm_apply", "human", "probe", entries=[]))
        except working_copy.EmptyStateError:
            refused = True
        assert refused, "the core write accepted an empty state"
    elif door == "walk":
        with chip.app.test_request_context("/undo", method="POST"):
            response = chip.app.make_response(
                routes._sync_pull_apply_to_live(ctx, None, force=force, walk=True))
            assert response.status_code == 409
            assert "empty" in response.get_json()["message"]
    else:
        response = chip.c.post("/state/apply-to-live", data={"force": "1"} if force else {})
        assert response.status_code == 409
        assert "empty" in response.get_data(as_text=True)
    assert chip.live_state() == before
