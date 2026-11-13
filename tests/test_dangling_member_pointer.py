"""A pointer to a member of a collection that does not exist is a real dangling
pointer, and the CZ plan names it instead of blaming a missing f_01.

A pair whose qubit_target pointed at a qubit absent from `qubits` was classed
"soft" (an optional field at its default) because its parent, `#/qubits`,
resolves; Diagnostics hid it as info and the CZ plan said "f_01 is missing".
"""
import json

from quam_state_manager.core import cz_gate_plan as C
from quam_state_manager.core.loader import QuamStore


def _state(target="#/qubits/q9"):
    return {
        "qubits": {
            "q1": {"id": "q1", "f_01": 5.0e9, "anharmonicity": 200e6,
                   "xy": {"operations": {"x180": {"__class__": "quam.components.pulses.DragCosinePulse",
                                                   "amplitude": 0.1, "length": 40,
                                                   "detuning": "#../x90/detuning"},
                                          "x90": {"__class__": "quam.components.pulses.DragCosinePulse",
                                                  "amplitude": 0.05, "length": 40}}}},
            "q2": {"id": "q2", "f_01": 4.8e9, "anharmonicity": 200e6},
        },
        "qubit_pairs": {"q1-q9": {"qubit_control": "#/qubits/q1", "qubit_target": target,
                                  "moving_qubit": "control",
                                  "macros": {"cz": {"__class__": "x.CZGate",
                                                    "flux_pulse_qubit": {"amplitude": 0.1, "length": 40}}}}},
    }


def _store(tmp_path, state):
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
    return QuamStore(str(tmp_path))


def test_a_missing_collection_member_is_not_soft(tmp_path):
    ws = {w.dot_path: w for w in _store(tmp_path, _state()).validate_pointers()}
    assert ws["qubit_pairs.q1-q9.qubit_target"].soft is False


def test_an_omitted_optional_field_stays_soft(tmp_path):
    ws = {w.dot_path: w for w in _store(tmp_path, _state()).validate_pointers()}
    assert ws["qubits.q1.xy.operations.x180.detuning"].soft is True


def test_the_cz_plan_names_the_pointer():
    rows, _ = C.operations(_state())
    reason = rows[0]["lines"][0]["point"]["reason"]
    assert "qubit_target points at #/qubits/q9, which names no qubit" in reason
    assert "f_01" not in reason
