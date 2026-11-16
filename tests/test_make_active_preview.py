"""Make active and Re-check compare a gate already on the chip with SM's own
drawing of its pulses (core/gate_preview.py), so a gate of a class SM does not
build itself (a slot class) is fully checked, coupler pulse included."""
from __future__ import annotations

import pytest

from quam_state_manager.core import config_generator, gate_check, gate_preview, lab_waveform
from quam_state_manager.generator import run_pulse_waveform as runner_mod
from quam_state_manager.web.app import create_app
from tests.cz_gate_chip import add_gate, chip, square, write

GATE = "cz_sq"
MACRO = f"qubit_pairs.q1-q2.macros.{GATE}"
SLOT_CLASS = "lab_gates.slot_gates.SlotGateMade"
SCRIPT = ('from qm.qua import *\n\nwith program() as prog:\n'
          '    play("cz_sq_pulse_q1_q2", "q1.z")\n'
          '    play("cz_sq_coupler_pulse_q1_q2", "c")\n\nconfig = {}\n')


def _cfg(q_amp=0.3, c_amp=0.05, length=48):
    out = {"delay": 0, "sampling_rate": 1e9, "offset": 0.0, "output_mode": "direct"}
    el = {"singleInput": {"port": ["con1", 1, 1]}}
    return {"controllers": {"con1": {"fems": {1: {"type": "LF", "analog_outputs": {1: out}}}}},
            "elements": {"q1.z": dict(el, operations={"cz_sq_pulse_q1_q2": "pq"}),
                         "c": dict(el, operations={"cz_sq_coupler_pulse_q1_q2": "pc"})},
            "pulses": {"pq": {"length": length, "waveforms": {"single": "wq"}},
                       "pc": {"length": length, "waveforms": {"single": "wc"}}},
            "waveforms": {"wq": {"type": "constant", "sample": q_amp},
                          "wc": {"type": "constant", "sample": c_amp}}}


def _ran(cfg):
    ops = [runner_mod.op_data(cfg, el, op, runner_mod._MAX_SAMPLES)
           for el, op in runner_mod.played_ops(SCRIPT)]
    entry = {"script": SCRIPT, "error": None, "ops": ops}
    ok = {"load_error": None, "config_error": None}
    return {"status": "ok", "error": None, "data": {
        "ok": True, "before": ok, "after": ok,
        "gates": {gate_check.macro_path("q1-q2", GATE): entry}, "before_gates": {},
        "quam_config": {}}}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    s, _ = add_gate(chip(), pulses=square(coupler=True))
    s, _ = add_gate(s, name="cz_two")
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "#./cz_two"
    s["qubit_pairs"]["q1-q2"]["macros"][GATE]["__class__"] = SLOT_CLASS
    folder = write(tmp_path / "chip", s)
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda inst: "C:/env/python.exe")
    c.cfg = {"cfg": _cfg()}
    monkeypatch.setattr(lab_waveform, "run_gate_check",
                        lambda *a, **k: _ran(c.cfg["cfg"]))
    c._app = app
    yield c


def _merged(c):
    return next(iter(c._app.config["contexts"].values()))["store"].merged


def _ma(c):
    return c.post("/api/pulse/cz-gate/make-active", json={"pair": "q1-q2", "gate": GATE})


def test_the_coupler_element_follows_the_channel_name_rule():
    s = chip()
    assert gate_preview.coupler_element(s, "q1-q2") == "c"
    del s["qubit_pairs"]["q1-q2"]["coupler"]["id"]
    assert gate_preview.coupler_element(s, "q1-q2") == "q1-q2.coupler"
    del s["qubit_pairs"]["q1-q2"]["id"]
    assert gate_preview.coupler_element(s, "q1-q2") == "q1-q2.coupler"
    assert gate_preview.coupler_element(s, "nope") is None


def test_a_fully_checked_slot_gate_with_a_coupler_pulse_is_made_active(rig):
    r = _ma(rig)
    assert r.status_code == 200, r.data[:600]
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == f"#./{GATE}"


def test_a_coupler_pulse_that_differs_from_the_chip_is_still_refused(rig):
    rig.cfg["cfg"] = _cfg(c_amp=0.2)
    r = _ma(rig)
    assert r.status_code == 400
    assert "It was not made active." in r.get_json()["error"]
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_two"


def test_a_pulse_sm_cannot_draw_keeps_the_gate_refused(rig):
    ctx = next(iter(rig._app.config["contexts"].values()))
    ctx["modifier"].set_value("qubits.q1.z.operations.cz_sq_pulse_q1_q2.__class__",
                              "lab_pulses.MyPulse")
    r = _ma(rig)
    assert r.status_code == 400
    assert "not fully checked" in r.get_json()["error"]
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_two"


def test_a_planned_coupler_pulse_is_previewed_on_its_element():
    from quam_state_manager.web import cz_gate_routes as czr
    s = chip()
    _, plan = add_gate(s, pulses=square(coupler=True))
    pv, skipped = czr._previews(plan, s, "q1-q2")
    assert skipped == []
    assert pv[("c", "cz_sq_coupler_pulse_q1_q2")] == pytest.approx(0.05)
    assert pv[("q1.z", "cz_sq_pulse_q1_q2")] == pytest.approx(0.3)


def test_integer_ids_name_elements_as_quam_does():
    s = chip()
    s["qubit_pairs"]["q1-q2"]["coupler"]["id"] = 3
    assert gate_preview.coupler_element(s, "q1-q2") == "ch3"
    assert gate_preview.qubit_name("qA", {"id": 7}) == "q7"
    assert gate_preview.qubit_name("qA", {"id": "q1"}) == "q1"
    assert gate_preview.qubit_name("qA", {}) == "qA"


def test_a_pulse_sm_cannot_draw_is_named_with_its_reason():
    from quam_state_manager.web import gate_status_routes as gsr
    found = [{"element": "q1.z", "operation": "x"}]

    class Store:
        import threading
        _lock = threading.RLock()
        merged = {"qubits": {}}

    drawn = gsr._chip_previews(Store(), "q1-q2", GATE)[("q1-q2", GATE)](found)
    assert drawn[gate_check.SKIPPED] == ["x on q1.z: SM does not find this pulse on the chip."]
    msg = gate_check._preview_check(found, {}, False, drawn[gate_check.SKIPPED])["message"]
    assert "could not draw any pulse" in msg and "SM does not find this pulse on the chip." in msg
    assert "was given" not in msg
    assert gate_check._preview_check(found, {}, False)["message"] == "No preview was given to compare."


def test_a_known_class_gate_whose_pulses_match_is_still_made_active(rig):
    ctx = next(iter(rig._app.config["contexts"].values()))
    from quam_state_manager.core import cz_gate_build as cgb
    ctx["modifier"].set_value(f"{MACRO}.__class__", cgb.CZ_GATE)
    r = _ma(rig)
    assert r.status_code == 200, r.data[:600]
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == f"#./{GATE}"
