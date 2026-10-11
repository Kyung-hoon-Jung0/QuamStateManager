"""SU-15: a door outside the mutator lock stages an edit that the plan's next write carries to the chip.

While an Auto Calibrate plan (or the agent / the Experiment Runner) holds the
chip, ``_scheduler_lock_guard`` 409s the doors in ``_SCHEDULER_MUTATOR_ENDPOINTS``.
Six doors are not in that set and still stage into the shared change log
(the harness's lock probe): pair switch-moving, pulse copy, type-fix apply,
applied-log revert (x), Bring back, chip-profile set. The autofit writer
(``core/autofit/writer.apply_rows``) runs on the SAME store and saves it whole
(``saver.save()``) before ``apply_to_live`` -- so whatever those doors staged
reaches the live chip under the plan's write, with no Apply press.
Contract: docs/117 (a direct live write needs one explicit act); the lock-set
comment itself ("un-reviewed extras must not ride an autofit plan's next
apply"); I1/I9.
"""
import threading

import pytest

from tests.state_model.repros._rig import Chip


def test_an_edit_staged_through_an_unlocked_door_does_not_ride_a_plan_write(tmp_path):
    from quam_state_manager.core.autofit.writer import ChipHandle, apply_rows
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    ctx = chip.ctx
    chip.app.config["agent_edit_lock"] = {"path": ctx["path"], "node": "plan"}   # the chip is held
    assert chip.c.post("/field/edit", data={"dot_path": "qubits.q3.T1", "value": "5e-05"}).status_code == 409
    # a door outside the lock set still stages (the type-fix of a text leaf)
    r = chip.c.post("/type-fix/apply", json={"paths": ["twpas.twpa1.spectroscopy_amplitude"]})
    # v1.3.0: stage while held -> refuse unchanged, no edit may ride the plan write.
    assert r.status_code == 409 and not chip.log(), "the held chip accepted a staged edit"
    with chip.app.app_context():
        handle = ChipHandle(store=ctx["store"], modifier=ctx["modifier"], saver=ctx["saver"],
                            wc=ctx["working_copy"], build_lock=threading.RLock(),
                            live_path=str(ctx["path"]),
                            hub_dir=routes._hub_chip_dir(ctx["path"]), hub_plan="autofit:probe")
        out = apply_rows(handle, [{"path": "qubits.q1.T1", "value": 3.3e-05}],
                         apply_live=True, label="probe")
    assert out.ok, out
    live = chip.live_state()
    assert live["qubits"]["q1"]["T1"] == 3.3e-05                     # the plan's own write landed
    assert live["twpas"]["twpa1"]["spectroscopy_amplitude"] == "0.12", \
        "the person's staged (unapplied) type-fix reached the chip under the plan's write"
