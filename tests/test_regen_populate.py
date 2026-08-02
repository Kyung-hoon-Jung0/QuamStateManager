"""Populate-protect for Re-generate (core/regen_populate.py, docs/72).

The wizard ships its hydration-time populate snapshot back with the build;
changed cells expand to state dot-paths whose NEW (build-applied) value must
beat the tier-1 carry in merge_states — without this every Populate edit was
silently reverted by the value merge (r16 report ⓪).
"""
from __future__ import annotations

import copy

from quam_state_manager.core import regen_populate as rp
from quam_state_manager.core import regen_merge


# --- changed_fields ---------------------------------------------------------

def test_changed_iff_differs():
    spec = {"qubit": {"q1": {"RF_freq": 5.1e9, "anharmonicity": -2e8}}}
    base = {"qubit": {"q1": {"RF_freq": 5.0e9, "anharmonicity": -2e8}}}
    assert rp.changed_fields(spec, base) == [("qubit", "q1", "RF_freq")]


def test_float_tolerance_no_false_positive():
    spec = {"qubit": {"q1": {"RF_freq": 5.0e9 + 1e-4}}}
    base = {"qubit": {"q1": {"RF_freq": 5.0e9}}}
    assert rp.changed_fields(spec, base) == []


def test_cleared_cell_is_not_changed():
    # "clear = don't re-seed": tier-1 keeps the calibration.
    spec = {"qubit": {"q1": {}}}
    base = {"qubit": {"q1": {"RF_freq": 5.0e9}}}
    assert rp.changed_fields(spec, base) == []


def test_touched_protects_even_when_equal_or_baseline_blind():
    spec = {"qubit": {"q1": {"RF_freq": 5.0e9, "band": 3}}}
    base = {"qubit": {"q1": {"RF_freq": 5.0e9}}}     # band never extracted
    got = rp.changed_fields(spec, base,
                            touched=[["qubit", "q1", "band"],
                                     ["qubit", "q1", "RF_freq"]])
    assert ("qubit", "q1", "band") in got
    assert ("qubit", "q1", "RF_freq") in got          # touched ∧ present


def test_touched_but_cleared_not_protected():
    spec = {"qubit": {"q1": {}}}
    base = {"qubit": {}}
    assert rp.changed_fields(spec, base,
                             touched=[["qubit", "q1", "RF_freq"]]) == []


def test_string_and_non_dict_shapes_tolerated():
    assert rp.changed_fields(None, None) == []
    assert rp.changed_fields({"qubit": "junk"}, {}) == []
    spec = {"flux": {"q1": {"flux_point": "independent"}}}
    base = {"flux": {"q1": {"flux_point": "joint"}}}
    assert rp.changed_fields(spec, base) == [("flux", "q1", "flux_point")]


# --- protect_paths fanout ---------------------------------------------------

def _mini_chip():
    state = {
        "qubits": {
            "q1": {
                "f_01": 5.1e9,
                "anharmonicity": -2e8,
                "grid_location": "0,0",
                "xy": {
                    "RF_frequency": 5.1e9,
                    "opx_output": "#/wiring/qubits/q1/xy/opx_output",
                    "operations": {
                        "x180_DragCosine": {"length": 32, "amplitude": 0.25,
                                            "alpha": 0.1, "detuning": 0.0},
                        "y90_DragCosine": {"length": 32, "amplitude": 0.125,
                                           "alpha": 0.1, "detuning": 0.0},
                        "saturation": {"length": 10000, "amplitude": 0.1},
                    },
                },
                "z": {"opx_output": "#/wiring/qubits/q1/z/opx_output",
                      "independent_offset": 0.01},
                "resonator": {
                    "f_01": 7.2e9, "RF_frequency": 7.2e9,
                    "depletion_time": 1000,
                    "opx_output": "#/wiring/qubits/q1/rr/opx_output",
                    "operations": {"readout": {"length": 1500,
                                               "amplitude": 0.05}},
                },
            },
        },
        "qubit_pairs": {
            "q1-q2": {
                "qubit_control": "#/qubits/q1",
                "qubit_target": "#/qubits/q2",
                "moving_qubit": "control",
                "macros": {
                    "cz_unipolar": {"flux_pulse_qubit": {"length": 100,
                                                         "amplitude": 0.11}},
                },
            },
        },
        "ports": {
            "mw_outputs": {"con1": {"1": {
                "2": {"upconverter_frequency": 5.0e9, "band": 2,
                      "full_scale_power_dbm": 4},
                "8": {"upconverter_frequency": 7.0e9, "band": 3,
                      "full_scale_power_dbm": 0},
            }}},
            "analog_outputs": {"con1": {"5": {"1": {"delay": 161,
                                                    "output_mode": "direct"}}}},
        },
    }
    wiring = {"wiring": {"qubits": {"q1": {
        "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"},
        "z": {"opx_output": "#/ports/analog_outputs/con1/5/1"},
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/8"},
    }}}}
    return state, wiring


def test_rf_freq_two_paths():
    state, wiring = _mini_chip()
    prot, _ = rp.protect_paths([("qubit", "q1", "RF_freq")], {},
                               state, wiring, state, wiring)
    assert "qubits.q1.f_01" in prot
    assert "qubits.q1.xy.RF_frequency" in prot


def test_lo_chain_resolves_through_wiring_pointer():
    state, wiring = _mini_chip()
    prot, _ = rp.protect_paths([("qubit", "q1", "LO_frequency")], {},
                               state, wiring, state, wiring)
    assert "ports.mw_outputs.con1.1.2.upconverter_frequency" in prot
    assert "ports.mw_outputs.con1.1.2.band" in prot


def test_drag_family_fanout_excludes_saturation():
    state, wiring = _mini_chip()
    prot, _ = rp.protect_paths([("pulses", "q1", "x180_amplitude")], {},
                               state, wiring, state, wiring)
    assert "qubits.q1.xy.operations.x180_DragCosine.amplitude" in prot
    assert "qubits.q1.xy.operations.y90_DragCosine.amplitude" in prot
    assert "qubits.q1.xy.operations.saturation.amplitude" not in prot


def test_pair_paths_use_membership_not_id_split():
    # The wizard keys pairs canonically ("q1-q2"); the merged tree keys by
    # the OLD state id. A short-form OLD id ("q1-2") must still resolve.
    state, wiring = _mini_chip()
    old = copy.deepcopy(state)
    old["qubit_pairs"] = {"q1-2": old["qubit_pairs"].pop("q1-q2")}
    prot, _ = rp.protect_paths([("pairs", "q1-q2", "cz_amplitude")], {},
                               old, wiring, state, wiring)
    # merge walks post-reconcile keys == the OLD id
    assert any(p.startswith("qubit_pairs.q1-2.") and p.endswith(".amplitude")
               for p in prot)


def test_band_crossing_protects_table_delay():
    state, wiring = _mini_chip()
    new = copy.deepcopy(state)
    new["ports"]["mw_outputs"]["con1"]["1"]["2"].update(
        {"upconverter_frequency": 6.9e9, "band": 3})
    new["ports"]["analog_outputs"]["con1"]["5"]["1"]["delay"] = 141
    prot, conf = rp.protect_paths([("qubit", "q1", "LO_frequency")], {},
                                  state, wiring, new, wiring)
    # old delay 161 == the band-2 table value → never hand-tuned → protect
    assert "ports.analog_outputs.con1.5.1.delay" in prot
    assert conf == []


def test_band_crossing_keeps_hand_tuned_delay_with_conflict():
    state, wiring = _mini_chip()
    old = copy.deepcopy(state)
    old["ports"]["analog_outputs"]["con1"]["5"]["1"]["delay"] = 155  # hand-tuned
    new = copy.deepcopy(state)
    new["ports"]["mw_outputs"]["con1"]["1"]["2"].update(
        {"upconverter_frequency": 6.9e9, "band": 3})
    prot, conf = rp.protect_paths([("qubit", "q1", "LO_frequency")], {},
                                  old, wiring, new, wiring)
    assert "ports.analog_outputs.con1.5.1.delay" not in prot
    assert len(conf) == 1 and "hand-tuned" in conf[0]


def test_band_only_edit_protects_port_band():
    state, wiring = _mini_chip()
    prot, _ = rp.protect_paths([("resonator", "q1", "band")], {},
                               state, wiring, state, wiring)
    assert "ports.mw_outputs.con1.1.8.band" in prot


def test_absent_paths_not_protected():
    # Paths the NEW state doesn't carry stay out (harmless either way, but
    # the set stays tight for the report).
    state, wiring = _mini_chip()
    prot, _ = rp.protect_paths([("flux", "q9", "independent_offset")], {},
                               state, wiring, state, wiring)
    assert prot == set()


def test_band_delay_table_in_sync_with_run_build():
    from quam_state_manager.generator import run_build
    assert rp._BAND_TO_DELAY_NS == run_build._BAND_TO_DELAY_NS


# --- merge integration ------------------------------------------------------

def test_protect_beats_tier1_in_merge():
    old = {"qubits": {"q1": {"f_01": 5.0e9, "anharmonicity": -2e8}}}
    new = {"qubits": {"q1": {"f_01": 5.1e9, "anharmonicity": -2.1e8}}}
    res = regen_merge.merge_states(old, new,
                                   protect_paths={"qubits.q1.f_01"})
    assert res.merged["qubits"]["q1"]["f_01"] == 5.1e9          # protected → NEW
    assert res.merged["qubits"]["q1"]["anharmonicity"] == -2e8  # tier-1 → OLD
    assert res.stats.populate_protected == ["qubits.q1.f_01"]
    assert res.stats.carried == 1


def test_pointer_still_beats_protect():
    old = {"qubits": {"q1": {"xy": {"RF_frequency": 5.0e9}}}}
    new = {"qubits": {"q1": {"xy": {"RF_frequency": "#../f_01"}}}}
    res = regen_merge.merge_states(
        old, new, protect_paths={"qubits.q1.xy.RF_frequency"})
    assert res.merged["qubits"]["q1"]["xy"]["RF_frequency"] == "#../f_01"
    assert res.stats.populate_protected == []


def test_merge_without_kwarg_unchanged():
    old = {"qubits": {"q1": {"f_01": 5.0e9}}}
    new = {"qubits": {"q1": {"f_01": 5.1e9}}}
    res = regen_merge.merge_states(old, new)
    assert res.merged["qubits"]["q1"]["f_01"] == 5.0e9
    assert res.stats.populate_protected == []
