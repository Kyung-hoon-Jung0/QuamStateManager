"""SU-05 (needs adjudication): a restart drops every unapplied tray row when the live chip moved meanwhile.

Harness: EDIT, OUTSIDE (any field), RESTART -> the tray is empty; a one-shot
notice ("Staged edits could not be recovered ... the live chip changed") names
the paths and the rows survive only in ``<key>.pending_tray.unrecovered-*.json``.
Nothing in the app brings them back (Bring back is empty).
Contract question: A-22 (pending rows survive a new app) vs the restore's own
gate. I7 holds by the letter (the notice names the values), but a routine
event -- an experiment writing the chip while SM was closed -- discards edits
the user never chose to drop.
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-05: rows are not restored once live moved")
def test_rows_survive_a_restart_while_an_experiment_wrote_the_chip(tmp_path):
    from quam_state_manager.core import pending_tray
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    chip.set_live("qubits.q2.chi", 1300000.0)            # an experiment, while SM runs
    pending_tray.flush_all()
    routes._quam_cache.clear()                            # the process ends
    app = create_app(testing=True, instance_path=str(chip.inst))
    c = app.test_client()
    page = c.post("/load", data={"folder": str(chip.live)}, follow_redirects=True).get_data(as_text=True)
    ctx = next(v for v in app.config["contexts"].values() if v.get("type") == "quam")
    rows = [e.dot_path for e in ctx["store"].change_log]
    bring = c.post("/state/take-live/restore")
    assert rows == ["qubits.q1.T1"], (rows, "could not be recovered" in page, bring.headers.get("HX-Trigger"))
