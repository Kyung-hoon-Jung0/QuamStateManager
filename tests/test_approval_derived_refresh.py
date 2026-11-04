"""An approved agent write must reach every derived view at once.

The approve door stages through ``Modifier.set_value(_defer_hooks=True)`` and
then presses the apply door; nothing on that path cleared the QueryEngine's
qubit / pair / topology caches, so Chip Status and Chip Components kept the
pre-approval values until a full state reload. The caches now validate
against the store's mutation counter, so every write door refreshes them.
"""
import json

import pytest

from quam_state_manager.web.app import create_app

HUMAN = {"X-SM-Actor": "pin"}


@pytest.fixture
def setup(tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    inst = tmp_path / "_inst"
    app = create_app(testing=True, instance_path=str(inst))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(d)}).status_code in (200, 302)
    return app, c, d, inst


def _engine(app):
    with app.app_context():
        from quam_state_manager.web import routes as r
        return r._active_ctx()["engine"]


def test_an_approved_write_reaches_qubit_and_topology_views(setup):
    from quam_state_manager.core import approvals as ap
    app, c, d, inst = setup
    eng = _engine(app)
    old = json.loads((d / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["T1"]
    # warm every derived cache the way Chip Status and the Qubits page do
    assert eng.get_qubit("qA1")["T1"] == old
    topo = eng.get_topology()
    assert c.get("/qubits").status_code == 200
    chip = c.get("/api/agent/chip").get_json()["chip_key"]
    a = ap.add(str(inst), chip, kind="writes", node="17_T1", targets=["qA1"],
               writes=[{"path": "qubits.qA1.T1", "old": old, "new": 3.25e-5}],
               reason="r", why_held="mode ask-writes", actor="by_claude", run_id=9001)
    res = c.post(f"/api/agent/approvals/{a['id']}/approve", json={}, headers=HUMAN).get_json()
    assert res["ok"] and res["stage"]["applied"] is True, res
    live = json.loads((d / "state.json").read_text(encoding="utf-8"))
    assert live["qubits"]["qA1"]["T1"] == 3.25e-5
    assert eng.get_qubit("qA1")["T1"] == 3.25e-5, "the qubit view kept the pre-approval value"
    assert eng.get_topology() is not topo, "the topology kept its pre-approval build"
    assert "3.25e-05" in c.get("/qubits").get_data(as_text=True) or \
        "32.5" in c.get("/qubits").get_data(as_text=True)


def test_any_write_door_refreshes_the_engine_without_an_explicit_invalidate(setup):
    """The root rule: a write that bumps mutation_seq is enough."""
    app, c, d, inst = setup
    eng = _engine(app)
    eng.get_qubit("qA1")
    eng.get_topology()
    with app.app_context():
        from quam_state_manager.web import routes as r
        mod = r._active_ctx()["modifier"]
        mod.set_value("qubits.qA1.T1", 4.4e-5, _defer_hooks=True)
    assert eng.get_qubit("qA1")["T1"] == 4.4e-5
