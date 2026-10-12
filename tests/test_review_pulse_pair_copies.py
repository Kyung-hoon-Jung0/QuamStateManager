"""Identical pulse templates on a different pair cannot verify this slot."""
import copy

import pytest

from quam_state_manager.web.routes import _slot_config_match, _slot_copies

SLOT = "qubit_pairs.p12.macros.cz.flux_pulse"


@pytest.mark.parametrize("chained", [False, True])
def test_slot_copies_belong_to_the_resolved_pair_endpoints(chained):
    pulse = {"__class__": "quam.components.pulses.SquarePulse",
             "id": "cz", "length": 40, "amplitude": 0.1}
    state = {
        "qubits": {q: {"z": {"operations": {"cz": copy.deepcopy(pulse)}}}
                   for q in ("q1", "q2", "q3", "q4")},
        "qubit_pairs": {
            "p12": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                    "macros": {"cz": {"flux_pulse": copy.deepcopy(pulse)}}},
            "p34": {"qubit_control": "#/qubits/q3", "qubit_target": "#/qubits/q4"},
        },
        "wiring": {"control": "#/qubits/q1"},
    }
    if chained:
        state["qubit_pairs"]["p12"]["qubit_control"] = "#/wiring/control"
    state["qubits"]["q2"]["z"]["operations"]["cz"] = "#/" + SLOT.replace(".", "/")
    expected = ["qubits.q1.z.operations.cz", "qubits.q2.z.operations.cz"]
    assert _slot_copies(state, SLOT) == expected
    config = {"elements": {"q3.z": {"operations": {"cz": "pulse"}}}}
    match = _slot_config_match(config, SLOT, state)
    assert match["element"] is None and match["operation"] is None
    assert match["copies"] == expected
    config["elements"]["q2.z"] = {"operations": {"cz": "pulse"}}
    match = _slot_config_match(config, SLOT, state)
    assert (match["element"], match["operation"]) == ("q2.z", "cz")
    assert match["mirror"] == expected[1]
