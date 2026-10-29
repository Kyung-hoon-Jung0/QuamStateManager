"""The exported recipe of a QDAC chip wires the same trigger outputs the wizard built.

The QDAC trigger outputs are allocated in a pass of their own, outside the main
Connectivity, so the build's ``allocation`` never carried them and the recipe
emitted ``QDAC_PINS = {}``: every QDAC qubit came out with no trigger output.
"""
import importlib.util
from pathlib import Path

from quam_state_manager.core import script_emitter

_RB = Path(__file__).resolve().parents[1] / "quam_state_manager" / "generator" / "run_build.py"


def _run_build():
    spec = importlib.util.spec_from_file_location("run_build_for_qdac_pin_test", _RB)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _spec():
    return {"qubits": ["q1", "q2"], "qubit_pairs": [], "lines": [], "pair_gate": "",
            "network": {}, "instruments": {}, "populate": {},
            "qdac": {"ip_address": "10.0.0.2", "qubits": {"q1": {"channel": 1}, "q2": {"channel": 2}}}}


def test_build_allocation_carries_the_trigger_pins():
    rb = _run_build()
    alloc = rb._with_qdac_pins({"q1": {"drive": [{"con": 1, "slot": 1, "port": 2}]}},
                               {"q1": (1, 2, 3), "q2": (1, 2, 4)})
    assert alloc["q1"]["qt"] == [{"con": 1, "slot": 2, "port": 3}]
    assert alloc["q1"]["drive"]  # existing lines kept
    assert alloc["q2"]["qt"] == [{"con": 1, "slot": 2, "port": 4}]


def test_build_result_uses_the_pinned_allocation():
    src = _RB.read_text(encoding="utf-8")
    assert '"allocation": _with_qdac_pins(read_allocation(connectivity), qdac_pins)' in src


def test_recipe_emits_the_pins_from_the_build_allocation():
    rb = _run_build()
    alloc = rb._with_qdac_pins({}, {"q1": (1, 2, 3), "q2": (1, 2, 4)})
    two = script_emitter.emit_bundle(_spec(), alloc, {}, chip_name="c", stamp="2026-01-01")["02_build_machine.py"]
    assert "QDAC_PINS = {}" not in two
    assert "'q1': [1, 2, 3]" in two and "'q2': [1, 2, 4]" in two


def test_recipe_names_the_qdac_instrument():
    two = script_emitter.emit_bundle(_spec(), {}, {}, chip_name="c", stamp="2026-01-01")["02_build_machine.py"]
    assert '_qdac_inst["id"] = _qdac_inst.get("id") or "qdac"' in two
    assert 'setdefault("id", "qdac")' not in two
