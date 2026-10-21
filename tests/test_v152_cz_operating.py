"""Played CZ fields and honest operating-frequency claims on synthetic states."""
import pytest
from tests.test_cz_gate_plan import state, model_qubit
from quam_state_manager.core import cz_gate_plan as C


def registered():
    s = state(cz={"__class__": "g.CZGate", "flux_pulse_qubit": {
        "__class__": "p._CosineBipolarPulse", "id": "flux", "amplitude": .209}})
    s["shared"] = {"played": .1, "pulse": {"__class__": "p._CosineBipolarPulse",
                                          "amplitude": "#/shared/played"}}
    s["qubits"]["q0"]["z"]["operations"] = {"flux": "#/shared/pulse"}
    return s


def test_f1_registered_id_uses_played_amplitude_and_edit_target():
    s = registered()
    row = C.operations(s)[0][0]
    line = row["lines"][0]
    assert line["amplitude_v"] == .1
    assert line["point"]["frequency_hz"] == pytest.approx(4.92e9)
    assert C.read(s, line["amplitude_path"])["holder_path"] == "shared.played"
    assert "the macro's copy says 0.209 V; the qubit plays 0.1 V" in " ".join(row["notes"])
    s["shared"]["played"] = .12
    assert C.operations(s)[0][0]["lines"][0]["amplitude_v"] == .12


def test_f1_registered_amplitude_outside_accepted_model_is_refused():
    s = registered()
    s["qubits"]["q0"].update(model_qubit())
    s["qubits"]["q0"]["z"]["operations"] = {"flux": "#/shared/pulse"}
    s["shared"]["played"] = .8
    row = C.operations(s)[0][0]
    assert row["lines"][0]["amplitude_v"] == .8
    assert row["lines"][0]["point"]["frequency_hz"] is None
    assert "FluxConversionRangeError" in row["lines"][0]["point"]["reason"]
    assert row["nearest"] is None
