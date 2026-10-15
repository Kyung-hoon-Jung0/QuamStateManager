"""The chip frequency map (CZ gate plan): every qubit's idle frequency and stored population-loss
data, and one operation per pair at the frequency each member reaches."""
import json

import pytest

from quam_state_manager.core import cz_gate_plan as C


def qubit(f, point="joint", **extras):
    return {"f_01": f, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
            "z": {"flux_point": point, "operations": {"flux": {"amplitude": .1, "length": 40}}}, "extras": extras}


def chip():
    defects = json.dumps([{"frequency_hz": 4.62e9, "fwhm_hz": 4e6, "peak_loss": .3}])
    safe = json.dumps([{"frequency_lo_hz": 4.70e9, "frequency_hi_hz": 4.95e9}])
    return {
        "qubits": {"q10": qubit(5.0e9, population_loss_defects_json=defects, population_loss_safe_intervals_json=safe),
                   "q2": qubit(4.0e9, "min"), "q3": qubit(4.5e9, at_sweet_spot=False)},
        "qubit_pairs": {
            "q10-2": {"qubit_control": "#/qubits/q10", "qubit_target": "#/qubits/q2", "moving_qubit": "control",
                      "extras": {"cz_plan": {"omega_int_hz": 4.6e9, "leakage_qubit": "control"}},
                      "macros": {"cz_a": {"__class__": "x.CZGate", "flux_pulse_qubit": "#/qubits/q10/z/operations/flux"},
                                 "cz_b": {"__class__": "x.CZGate", "flux_pulse_qubit": {"amplitude": .05, "length": 20}},
                                 "cz": "#./cz_b"}},
            "q3-2": {"qubit_control": "#/qubits/q3", "qubit_target": "#/qubits/q2", "moving_qubit": "control",
                     "macros": {"cz_a": {"__class__": "x.CZGate", "flux_pulse_qubit": {"amplitude": .1, "length": 20}}}},
        }}


def the_map(op=None):
    rows, convs = C.operations(chip())
    return C.chip_map(rows, convs, op)


def test_columns_follow_natural_qubit_order_with_idle_and_loss_data():
    m = the_map()
    assert [q["name"] for q in m["qubits"]] == ["q2", "q3", "q10"]
    q10 = m["qubits"][2]
    assert q10["f01_hz"] == 5e9 and q10["loss_data"]
    assert q10["defects"] == [{"lo_hz": 4.618e9, "hi_hz": 4.622e9, "peak_loss": .3}]
    assert q10["safe"] == [{"lo_hz": 4.70e9, "hi_hz": 4.95e9}]
    assert not m["qubits"][0]["loss_data"]


def test_each_pair_shows_its_cz_by_default_else_its_first_operation():
    m = the_map()
    shown = {(s["pair"], s["operation"]) for s in m["segments"]}
    assert ("q10-2", "cz") in shown and ("q10-2", "cz_a") not in shown       # the alias "cz" -> cz_b
    seg = next(s for s in m["segments"] if s["pair"] == "q10-2" and s["qubit"] == "q10")
    assert seg["frequency_hz"] == pytest.approx(5e9 - 8e9 * .05 ** 2) and seg["moves"]
    still = next(s for s in m["segments"] if s["pair"] == "q10-2" and s["qubit"] == "q2")
    assert still["frequency_hz"] == 4e9 and not still["moves"]                # the partner stays at idle


def test_a_refused_frequency_is_listed_never_drawn():
    m = the_map()
    assert not any(s["pair"] == "q3-2" and s["qubit"] == "q3" for s in m["segments"])
    r = next(r for r in m["refused"] if r["qubit"] == "q3")
    assert r["pair"] == "q3-2" and "at_sweet_spot" in r["reason"]


def test_an_operation_name_is_drawn_on_every_pair_that_has_it():
    m = the_map("cz_a")
    assert {s["operation"] for s in m["segments"]} == {"cz_a"} and m["missing"] == []
    seg = next(s for s in m["segments"] if s["pair"] == "q10-2" and s["qubit"] == "q10")
    assert seg["frequency_hz"] == pytest.approx(5e9 - 8e9 * .1 ** 2)
    m = the_map("cz_b")
    assert m["missing"] == ["q3-2"] and all(s["pair"] == "q10-2" for s in m["segments"])
    assert m["operations"] == ["cz", "cz_a", "cz_b"]


def test_the_planned_point_sits_in_the_carrier_column():
    p = the_map()["planned"]
    assert p == [{"pair": "q10-2", "qubit": "q10", "qubit_path": "qubits.q10", "frequency_hz": 4.6e9,
                  "what": "planned interaction frequency"}]
