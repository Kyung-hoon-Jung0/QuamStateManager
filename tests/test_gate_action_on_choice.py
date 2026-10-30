"""C7: automatic ownership never silently chooses a missing role."""
from copy import deepcopy

import pytest

from quam_state_manager.core.gate_action import resolve


def action(role="moving", choice="auto"):
    return {"gate": {"name": "cz_bm", "macro_class": "pkg.Gate"},
            "items": [{"role": role, "on_choice": choice, "pulse_class": "pkg.Pulse",
                       "fields": {"length": 40, "amplitude": 1e-9}}]}


def test_moving_is_per_pair_and_input_untouched():
    spec = {"qubit_pairs": [["q1", "q2"], ["q2", "q3"]],
            "populate": {"pairs": {"q1-q2": {"moving_qubit": "target"},
                                    "q2-q3": {"moving_qubit": "control"}}}}
    before = deepcopy(spec)
    for pair in spec["qubit_pairs"]:
        got = resolve(spec, pair, action())
        assert got["choices"]["moving"] == "q2"
        assert got["additions"][0]["target"] == "qubits.q2.z.operations"
        assert type(got["additions"][0]["fields"]["length"]) is int
        assert got["additions"][0]["fields"]["amplitude"] == 1e-9
    assert spec == before


@pytest.mark.parametrize("role", ["moving", "high", "low"])
def test_unknown_auto_needs_choice(role):
    got = resolve({"qubit_pairs": [["q1", "q2"]]}, ["q1", "q2"], action(role))
    assert not got["ok"]
    assert "needs a look: choose" in "; ".join(got["errors"])


@pytest.mark.parametrize("choice,qubit", [("control", "q1"), ("target", "q2")])
def test_explicit_choice(choice, qubit):
    assert resolve({}, ["q1", "q2"], action("high", choice))["choices"]["high"] == qubit


def test_equal_frequency_uses_control():
    doc = {"populate": {"qubit": {"q1": {"RF_freq": 5e9}, "q2": {"RF_freq": 5e9}}}}
    assert resolve(doc, ["q1", "q2"], action("high"))["choices"]["high"] == "q1"
    assert resolve(doc, ["q1", "q2"], action("low"))["choices"]["low"] == "q2"
