"""SU-05: unapplied tray rows survive a restart even if the live chip moved.

Harness: EDIT, OUTSIDE, RESTART. Replay the rows onto their verified working
base, warn of possible collisions, and leave both live and working files alone.
Contract: A-22 (pending rows survive a new app); the user never dropped them.
"""
import pytest

from tests.state_model.repros._rig import Chip


# v1.3.0: xfail -> pass, live drift keeps rows and names possible collisions.
@pytest.mark.parametrize("change", ["different", "same", "torn"])
def test_rows_survive_a_restart_while_an_experiment_wrote_the_chip(tmp_path, change):
    from dataclasses import asdict
    from quam_state_manager.core import pending_tray
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    chip = Chip(tmp_path)
    chip.edit("qubits.q1.T1", "3.1e-05")
    originals = [asdict(e) for e in chip.ctx["store"].change_log]
    if change == "different":
        chip.set_live("qubits.q2.chi", 1300000.0)
    elif change == "same":
        chip.set_live("qubits.q1.T1", 4.1e-05)
    else:
        p = chip.live / "state.json"
        p.write_bytes(p.read_bytes()[:40])
    live_bytes = (chip.live / "state.json").read_bytes()
    working_bytes = (chip.ctx["working_copy"].working_folder / "state.json").read_bytes()
    pending_tray.flush_all()
    routes._quam_cache.clear()                            # the process ends
    app = create_app(testing=True, instance_path=str(chip.inst))
    c = app.test_client()
    page = c.post("/load", data={"folder": str(chip.live)}, follow_redirects=True).get_data(as_text=True)
    ctx = next(v for v in app.config["contexts"].values() if v.get("type") == "quam")
    rows = [e.dot_path for e in ctx["store"].change_log]
    bring = c.post("/state/take-live/restore")
    assert rows == ["qubits.q1.T1"], (rows, "could not be recovered" in page, bring.headers.get("HX-Trigger"))
    assert [asdict(e) for e in ctx["store"].change_log] == originals
    assert ctx["store"].get_value("qubits.q1.T1") == 3.1e-05
    assert ctx["live_diverged"] is True
    assert "may collide" in page
    assert (chip.live / "state.json").read_bytes() == live_bytes
    assert (ctx["working_copy"].working_folder / "state.json").read_bytes() == working_bytes
    assert c.post("/undo").status_code == 200
    assert not ctx["store"].change_log
    assert (chip.live / "state.json").read_bytes() == live_bytes
