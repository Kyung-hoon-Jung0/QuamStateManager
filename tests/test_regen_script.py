"""Tests for the single-file build-recipe emitter (core/regen_script.py).

The emitter turns a reconstructed spec into standalone editable Python (QM's
generate + populate combined). These pin: the emitted source is valid Python,
the wiring/populate/pairs blocks are present and faithful to the spec, and a
real chip round-trips through reconstruct -> emit -> compile with no loss of
structure. (An end-to-end *execution* of the emitted script needs the QM stack +
a calibration repo; it was verified manually against the LabA chip in the LabB
env: 21 qubits, 31 pairs, generate_config() -> 63 elements.)
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from quam_state_manager.core import regen_script, regen_spec


def _mini_spec() -> dict:
    return {
        "network": {"host": "10.0.0.1", "cluster_name": "clu", "port": None},
        "instruments": {"controllers": [
            {"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}
        ], "opx_plus": [], "octaves": []},
        "qubits": ["qA1", "qA2"],
        "qubit_pairs": [["qA2", "qA1"]],
        "twpas": [],
        "lines": [
            {"element": "qA1", "line": "resonator", "group": "feedline1",
             "channel": {"kind": "mw_fem", "con": 1, "slot": 1, "in_port": 1, "out_port": 1}},
            {"element": "qA2", "line": "resonator", "group": "feedline1",
             "channel": {"kind": "mw_fem", "con": 1, "slot": 1, "in_port": 1, "out_port": 1}},
            {"element": "qA1", "line": "drive",
             "channel": {"kind": "mw_fem", "con": 1, "slot": 1, "out_port": 2}},
            {"element": "qA1", "line": "flux",
             "channel": {"kind": "lf_fem", "con": 1, "slot": 5, "out_port": 1}},
        ],
        "pair_gate": "cz_fixed",
        "populate": {
            "qubit": {"qA1": {"RF_freq": 5.1e9, "LO_frequency": 5.0e9,
                              "anharmonicity": 2.0e8, "grid_location": "0,0"}},
            "resonator": {"qA1": {"RF_freq": 7.1e9, "LO_frequency": 7.4e9,
                                  "readout_length": 800}},
            "flux": {"qA1": {"joint_offset": 0.02, "flux_point": "joint"}},
            "pulses": {"qA1": {"x180_amplitude": 0.2, "x180_length": 40}},
        },
    }


def test_emitted_source_is_valid_python():
    src = regen_script.emit_build_script(_mini_spec(), chip_name="mini")
    ast.parse(src)                                   # raises SyntaxError on bad emit
    assert compile(src, "build_mini.py", "exec")     # also bytecode-compiles
    assert src.lstrip().startswith("#!/usr/bin/env python")


def test_wiring_calls_are_present_and_pinned():
    src = regen_script.emit_build_script(_mini_spec(), chip_name="mini")
    # resonator group collapses the two qubits onto one multiplexed line
    assert "add_resonator_line(qubits=['A1', 'A2']" in src
    assert "mw_fem_spec(con=1, slot=1, in_port=1, out_port=1)" in src
    assert "add_qubit_drive_lines(qubits='A1'" in src
    assert "add_qubit_flux_lines(qubits='A1'" in src
    assert "lf_fem_spec(con=1, out_slot=5, out_port=1)" in src


def test_q_prefix_stripped_in_wiring():
    # wiring uses stripped indices (A1), QubitReference re-adds the q -> no qqA1
    src = regen_script.emit_build_script(_mini_spec(), chip_name="mini")
    assert "'qA1'" not in src.split("EDIT: populate")[0]   # not in the wiring half
    assert "'qA1'" in src.split("EDIT: populate")[1]        # but keyed by full id in populate


def test_populate_blocks_carry_spec_values():
    src = regen_script.emit_build_script(_mini_spec(), chip_name="mini")
    assert "QUBIT = {" in src and "'RF_freq': 5100000000.0" in src
    assert "RESONATOR = {" in src and "'readout_length': 800" in src
    assert "FLUX = {" in src and "'joint_offset': 0.02" in src
    assert "PULSES = {" in src and "'x180_amplitude': 0.2" in src


def test_pairs_block_maps_gate_family():
    src = regen_script.emit_build_script(_mini_spec(), chip_name="mini")
    assert "PAIRS = {" in src
    assert "'qA2-qA1': 'CZ'" in src            # cz_fixed -> "CZ"
    # CR chip maps to "CR"
    cr = _mini_spec(); cr["pair_gate"] = "cr"
    assert "'qA2-qA1': 'CR'" in regen_script.emit_build_script(cr, chip_name="mini")


def test_band_extracted_never_hardcoded():
    spec = _mini_spec()
    spec["populate"]["qubit"]["qA1"]["band"] = 3
    spec["populate"]["resonator"]["qA1"]["band"] = 3
    src = regen_script.emit_build_script(spec, chip_name="mini")
    ast.parse(src)
    assert "band = 2" not in src                     # never hardcoded
    assert 'v.get("band", get_band(lo))' in src       # xy: real band, get_band fallback
    assert 'r.get("band"' in src                      # resonator: real band, fallback


def test_tunable_coupler_pairs_branch():
    spec = _mini_spec()
    spec["pair_gate"] = "cz_tunable"
    spec["lines"].append({"element": "qA2-qA1", "line": "coupler",
                          "channel": {"kind": "lf_fem", "con": 1, "slot": 6, "out_port": 1}})
    src = regen_script.emit_build_script(spec, chip_name="mini")
    ast.parse(src)
    assert "add_qubit_pair_flux_lines(qubit_pairs=[('A2', 'A1')]" in src
    assert "if machine.qubit_pairs:" in src           # runtime branch: tunable
    assert "pg.add_gates(machine, pair, gate, coupler=cpl)" in src
    assert 'cpl.flux_point = "off"' in src            # coupler parking
    assert "DEFAULT_PAIR_GATE = 'CZ'" in src


def test_twpa_pump_emitted_as_add_twpa_lines():
    spec = _mini_spec()
    spec["twpas"] = ["twpaA"]
    spec["lines"].append({"element": "twpaA", "line": "twpa_pump",
                          "channel": {"kind": "mw_fem", "con": 1, "slot": 1, "out_port": 8}})
    src = regen_script.emit_build_script(spec, chip_name="mini")
    ast.parse(src)
    assert "add_twpa_lines(twpas=['twpaA'], pump_constraints=mw_fem_spec(con=1, slot=1, out_port=8))" in src


def test_empty_populate_still_valid():
    spec = _mini_spec(); spec["populate"] = {}
    src = regen_script.emit_build_script(spec, chip_name="mini")
    ast.parse(src)
    assert "QUBIT = {}" in src


def test_no_pairs_omits_pair_import():
    spec = _mini_spec(); spec["qubit_pairs"] = []
    src = regen_script.emit_build_script(spec, chip_name="mini")
    ast.parse(src)
    assert "PAIRS = {}" in src


# --- real-chip round-trip (auto-skip when absent) ---------------------------
_LabA = Path("<quam-states>/example_lab")


@pytest.mark.skipif(not (_LabA / "state.json").exists(),
                    reason="real LabA chip folder not present")
def test_real_chip_reconstruct_emit_compiles():
    state = json.loads((_LabA / "state.json").read_text())
    wiring = json.loads((_LabA / "wiring.json").read_text())
    rec = regen_spec.reconstruct_spec(state, wiring)
    src = regen_script.emit_build_script(rec.spec, chip_name="LabA")
    ast.parse(src)                                   # valid Python
    # every qubit + every pair represented
    for q in rec.spec["qubits"]:
        assert repr(q) in src
    assert src.count("add_qubit_flux_lines") == 21   # LabA flux lines
    assert "PAIRS = {" in src
    assert compile(src, "build_LabA.py", "exec")     # bytecode-compiles
