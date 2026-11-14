"""A small chip with CZ gates built by the gate planner, for the status line,
Make active and the one-step gate delete tests."""
from __future__ import annotations

import copy
import json

from quam_state_manager.core import cz_gate_build as cgb

SQUARE = "quam.components.pulses.SquarePulse"


def _qubit(name):
    return {"id": name, "z": {"operations": {"const": {"amplitude": 0.1, "length": 100,
                                                        "__class__": SQUARE}}},
            "xy": {"operations": {}}}


def _pair(c, t, moving="control"):
    return {"id": f"{c}-{t}", "qubit_control": f"#/qubits/{c}",
            "qubit_target": f"#/qubits/{t}", "moving_qubit": moving, "macros": {},
            "coupler": {"id": "c", "operations": {}}}


def chip() -> dict:
    qs = ["q1", "q2", "q3"]
    return {"qubits": {q: _qubit(q) for q in qs},
            "qubit_pairs": {"q1-q2": _pair("q1", "q2"), "q2-q3": _pair("q2", "q3"),
                            "q2-q1": _pair("q2", "q1")}}


def square(length=48, amp=0.3, coupler=False):
    out = {"flux_pulse_qubit": {"class": SQUARE, "fields": {"length": length, "amplitude": amp}}}
    if coupler:
        out["coupler_flux_pulse"] = {"class": SQUARE,
                                     "fields": {"length": length, "amplitude": 0.05}}
    return out


def apply(state, creates):
    s = copy.deepcopy(state)
    for path, value in creates:
        node = s
        parts = path.split(".")
        for seg in parts[:-1]:
            node = node[seg]
        node[parts[-1]] = copy.deepcopy(value)
    return s


def add_gate(state, pair="q1-q2", name="cz_sq", **kw):
    kw.setdefault("gate_class", cgb.CZ_GATE)
    kw.setdefault("pulses", square())
    plan = cgb.plan(state, pair, gate_name=name, **kw)
    assert "creates" in plan, plan
    return apply(state, plan["creates"]), plan


def reverse(state):
    """Make every existing gate of the chip operation-owns-pulse so the next
    planned gate is built in the reverse direction."""
    s = copy.deepcopy(state)
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_old"] = dict(
        cgb._cz_skeleton(), flux_pulse_qubit="cz_old_pulse_q1_q2")
    s["qubits"]["q1"]["z"]["operations"]["cz_old_pulse_q1_q2"] = {
        "id": "cz_old_pulse_q1_q2", "length": 40, "amplitude": 0.1, "__class__": SQUARE}
    return s


def write(folder, state):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}),
                                        encoding="utf-8")
    return folder
