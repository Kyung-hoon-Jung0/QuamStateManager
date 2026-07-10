"""Unit tests for spec reconstruction (core/regen_spec.py)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.regen_spec import reconstruct_spec
from quam_state_manager.core import config_generator


def _tiny():
    state = {
        "qubits": {"q1": {}, "q2": {}},
        "qubit_pairs": {"q1-2": {"coupler": {"x": 1}, "macros": {"cz_unipolar": {}}}},
    }
    wiring = {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "wiring": {
            "qubits": {
                "q1": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1",
                              "opx_output": "#/ports/mw_outputs/con1/1/1"},
                       "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
                       "z": {"opx_output": "#/ports/analog_outputs/con1/4/1"}},
                "q2": {"rr": {"opx_input": "#/ports/mw_inputs/con1/1/1",
                              "opx_output": "#/ports/mw_outputs/con1/1/1"},
                       "xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"},
                       "z": {"opx_output": "#/ports/analog_outputs/con1/4/2"}},
            },
            "qubit_pairs": {"q1-2": {"c": {"control_qubit": "#/qubits/q1",
                                            "target_qubit": "#/qubits/q2",
                                            "opx_output": "#/ports/analog_outputs/con1/4/7"}}},
        },
    }
    return state, wiring


def test_twpa_lines_extracted_and_buildable():
    # modern quam_builder builds TWPAs (Connectivity.add_twpa_lines), so the
    # reconstruct must PIN each pump line from wiring.twpas, not drop them.
    state = {"qubits": {"q1": {}}, "twpas": {"twpaA": {"pump": {}}}}
    wiring = {"network": {"host": "1.2.3.4", "cluster_name": "C"}, "wiring": {
        "qubits": {"q1": {"rr": {"opx_output": "#/ports/mw_outputs/con1/1/1",
                                 "opx_input": "#/ports/mw_inputs/con1/1/1"}}},
        "twpas": {"twpaA": {"pump": {"opx_output": "#/ports/mw_outputs/con1/1/8"},
                            "pump_": {"opx_output": "#/ports/mw_outputs/con1/1/8"}}}}}
    rec = reconstruct_spec(state, wiring)
    assert rec.spec["twpas"] == ["twpaA"]
    pump = [ln for ln in rec.spec["lines"] if ln["line"] == "twpa_pump"]
    assert len(pump) == 1
    assert pump[0]["element"] == "twpaA"
    assert pump[0]["channel"] == {"kind": "mw_fem", "con": 1, "slot": 1, "out_port": 8}
    assert config_generator.validate_spec(rec.spec) == []   # string-id TWPAs accepted


def test_reconstructs_structure():
    state, wiring = _tiny()
    r = reconstruct_spec(state, wiring)
    s = r.spec
    assert s["qubits"] == ["q1", "q2"]
    assert s["qubit_pairs"] == [["q1", "q2"]]
    assert s["network"]["host"] == "1.2.3.4"
    assert s["pair_gate"] == "cz_tunable"          # coupler + cz macro
    # multiplexed resonator: both qubits share one feedline group
    res = [l for l in s["lines"] if l["line"] == "resonator"]
    assert {l["group"] for l in res} == {"feedline1"}
    # instruments inferred: one MW-FEM (slot 1) + one LF-FEM (slot 4) on con1
    fems = {(f["slot"], f["fem"]) for f in s["instruments"]["controllers"][0]["fems"]}
    assert (1, "mw") in fems and (4, "lf") in fems


def test_reconstructed_spec_is_valid():
    state, wiring = _tiny()
    r = reconstruct_spec(state, wiring)
    assert config_generator.validate_spec(r.spec) == []


def test_mixed_gates_flagged():
    state = {"qubit_pairs": {
        "a": {"coupler": {"x": 1}, "macros": {"cz_unipolar": {}}},
        "b": {"cross_resonance": {"x": 1}, "macros": {"cr_drive": {}}},
    }}
    r = reconstruct_spec(state, {"wiring": {}})
    assert r.mixed_gates is True


# --- real calibrated chip (auto-skip when absent) ---------------------------
_CHIP = Path("<quam-states>/gen_2x3_cz_tunable")


@pytest.mark.skipif(not _CHIP.exists(), reason="real chip folder not present")
def test_real_chip_reconstructs_to_valid_buildable_spec():
    state = json.loads((_CHIP / "state.json").read_text())
    wiring = json.loads((_CHIP / "wiring.json").read_text())
    r = reconstruct_spec(state, wiring)
    assert len(r.spec["qubits"]) == 6
    assert len(r.spec["qubit_pairs"]) == 7
    assert r.spec["pair_gate"] == "cz_tunable"
    assert config_generator.validate_spec(r.spec) == []   # buildable
