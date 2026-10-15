"""CZ gate plan: flux -> frequency tiers, the sign rule, the measured quad range, the |11>
resonances, every operation's pulse-to-qubit mapping, the waveform trajectory, and when each
operation was last set."""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core import cz_gate_plan as C

GOLDEN = json.loads((Path(__file__).parent / "golden" / "cz_flux_model.json").read_text())


def qubit(**changes):
    q = {"f_01": 5e9, "anharmonicity": -200e6, "freq_vs_flux_01_quad_term": 8e9,
         "z": {"flux_point": "joint"}, "extras": {}}
    q.update(changes)
    return q


def model_qubit(case=0, f01_shift=0.0, **extra):
    m = GOLDEN["cases"][case]["model"]
    return qubit(f_01=m["f_01_at_fit_hz"] + f01_shift, extras={"lffem_flux_model": dict(m), **extra},
                 z={"flux_point": "joint" if C.spot_kind(m["phi_park"]) == "upper" else "min"})


# ------------------------------------------------------------- transcription
@pytest.mark.parametrize("case", GOLDEN["cases"], ids=lambda c: f"phi{c['model']['phi_park']}")
def test_the_lab_evaluator_is_transcribed_exactly(case):
    m, a = case["model"], np.array(case["amplitudes_v"])
    np.testing.assert_allclose(C.fitted_frequency(m, m["phi_park"] + a / m["phi0_v"]),
                               case["frequency_hz"], rtol=1e-13, atol=1e-4)
    mine = C.fitted_shift(m, a)
    lab = np.array([np.nan if v is None else v for v in case["shift_hz"]])
    assert (np.isnan(mine) == np.isnan(lab)).all()            # same measured-range guard
    np.testing.assert_allclose(mine[~np.isnan(lab)], lab[~np.isnan(lab)], rtol=1e-13, atol=1e-4)
    assert C.sweet_spot_phi(m["phi_park"]) == case["sweet_spot_phi"]


@pytest.mark.parametrize("rule", GOLDEN["rules"], ids=lambda r: r["reason"][:40])
def test_the_lab_acceptance_rule_is_transcribed(rule):
    m = C.model_of({"extras": {"lffem_flux_model": {k: v for k, v in rule["model"].items() if v is not None}}})
    assert (C.lab_rejection(m, rule["f_01"]) is None) == (rule["source"] == "model")


# ------------------------------------------------------------- the sign
@pytest.mark.parametrize("coefficient", [-8e9, 8e9])
def test_the_sign_comes_from_evidence_never_from_the_coefficient(coefficient):
    up = C.Conversion(qubit(freq_vs_flux_01_quad_term=coefficient, z={"flux_point": "min"}))
    down = C.Conversion(qubit(freq_vs_flux_01_quad_term=coefficient))
    assert up.point(.1)["frequency_hz"] == pytest.approx(5e9 + 80e6)
    assert down.point(.1)["frequency_hz"] == pytest.approx(5e9 - 80e6)
    assert up.point(.1)["kind"] == C.QUAD_LABEL


def test_conflicting_evidence_shows_no_frequency():
    q = qubit(extras={"lower_sweet_spot_frequency": 5e9})          # joint says down, this says up
    p = C.Conversion(q).point(.1)
    assert p["frequency_hz"] is None and p["reason"] == "sign convention unclear"
    assert C.Conversion(qubit(z={})).point(.1)["reason"] == "no sweet-spot evidence"
    off = C.Conversion(qubit(extras={"at_sweet_spot": False})).point(.1)
    assert off["frequency_hz"] is None and "at_sweet_spot" in off["reason"]


def test_a_qubit_moved_to_the_other_sweet_spot_flips_the_model_evidence():
    m = GOLDEN["cases"][0]["model"]                                # fitted at an upper spot
    other = C.model_spots(m)["other"]
    q = qubit(f_01=other["f_hz"], z={"flux_point": "min"}, extras={
        "lffem_flux_model": dict(m),
        # recorded from the old park, it names the spot the qubit sits at now
        "opposite_sweet_spot_qubit_frequency": other["f_hz"] + 1e6})
    conv = C.Conversion(q)
    assert conv.sign == 1 and other["kind"] == "lower"
    assert any("not used" in n for n in conv.notes)
    assert conv.model_kind == C.OTHER_SPOT_LABEL and conv.rejection     # the lab would reject the fit


# ------------------------------------------------------------- tiers
def test_the_lab_model_is_the_main_curve_and_the_quad_estimate_stays_beside_it():
    conv = C.Conversion(model_qubit(0))
    assert conv.rejection is None and conv.model_kind == C.MODEL_LABEL
    p = conv.point(.3)
    m = GOLDEN["cases"][0]["model"]
    assert p["kind"] == C.MODEL_LABEL
    assert p["frequency_hz"] == pytest.approx(conv.f01 + float(C.fitted_shift(m, .3)))
    assert p["quad_hz"] is not None and p["error_hz"] == pytest.approx(abs(p["quad_hz"] - p["model_hz"]))


def test_outside_the_measured_flux_the_quad_estimate_answers_with_the_reason():
    conv = C.Conversion(model_qubit(1))                            # measured on [-0.6, 0.9] V
    p = conv.point(-0.8)
    assert p["kind"] == C.QUAD_LABEL and "outside the flux range" in p["reason"]


def test_without_any_model_there_is_no_frequency_and_a_reason():
    p = C.Conversion(qubit(freq_vs_flux_01_quad_term=None)).point(.2)
    assert p["frequency_hz"] is None and "freq_vs_flux_01_quad_term" in p["reason"]
    assert C.Conversion(qubit()).point(0)["frequency_hz"] == 5e9


def test_a_stale_quad_term_from_the_other_sweet_spot_is_named():
    m = GOLDEN["cases"][0]["model"]
    conv = C.Conversion(model_qubit(0))
    other_k = conv.curvature["other_hz_per_v2"]
    stale = C.Conversion(model_qubit(0) | {"freq_vs_flux_01_quad_term": abs(other_k)})
    assert any("OTHER sweet spot" in n for n in stale.notes)
    assert m["phi_park"] == 0.0


# ------------------------------------------------------------- validity
def test_the_quad_range_is_measured_against_the_fitted_arc_on_both_lobes():
    conv = C.Conversion(model_qubit(0) | {"freq_vs_flux_01_quad_term": 1.0})
    k = abs(conv.curvature["here_hz_per_v2"])
    good = C.Conversion(model_qubit(0) | {"freq_vs_flux_01_quad_term": k})
    band = good.range
    assert len(band["branches"]) == 2 and 100e6 < band["limit_hz"] < 1e9
    # v1.4.1: generic wording -> measured wording, this arc supplies the checked span.
    assert good.point(.05)["validity"] == "within the measured quadratic range"
    assert good.point(.7)["validity"] == "outside the quadratic range"
    assert conv.range["limit_hz"] < band["limit_hz"]               # a wrong coefficient fails sooner


def test_without_a_fitted_arc_the_measured_generic_limit_depends_on_the_sweet_spot():
    upper, lower = C.Conversion(qubit()), C.Conversion(qubit(z={"flux_point": "min"}))
    assert upper.range["limit_hz"] == 190e6 and lower.range["limit_hz"] == 120e6
    # 8e9 Hz/V^2: 0.15 V -> 180 MHz (inside the upper limit), 0.16 V -> 205 MHz (outside)
    assert upper.point(.15)["validity"] == "within the quadratic range"
    assert upper.point(.16)["validity"] == "outside the quadratic range"
    assert lower.point(.15)["validity"] == "outside the quadratic range"


def test_the_generic_limits_hold_on_synthetic_arcs_at_their_sweet_spots():
    # the [derived, measured] constants re-checked on arcs that are not device data
    for case in GOLDEN["cases"]:
        m = case["model"]
        if abs(m["phi_park"] - C.sweet_spot_phi(m["phi_park"])) > 1e-3:
            continue
        conv = C.Conversion(model_qubit(GOLDEN["cases"].index(case)))
        exact = C.Conversion(model_qubit(GOLDEN["cases"].index(case))
                             | {"freq_vs_flux_01_quad_term": abs(conv.curvature["here_hz_per_v2"])})
        assert exact.range["limit_hz"] >= C.GENERIC_LIMIT_HZ[exact.frame["spot"]] * 0.6, case["model"]


# ------------------------------------------------------------- resonances
def test_the_three_resonances_of_the_11_manifold():
    r = C.resonances(4.2e9, 4e9, -200e6, 300e6)
    assert [x["name"] for x in r] == ["|11>-|20>", "|11>-|02>", "iSWAP"]
    assert [x["distance_hz"] for x in r] == [0, 500e6, 200e6]
    assert C.resonances(3.7e9, 4e9, 200e6, -300e6)[1]["distance_hz"] == 0
    assert C.resonances(None, 4e9, 200e6, 300e6)[0]["distance_hz"] is None
    # |11>: f_c + f_t ; |20>: 2 f_c - |a_c| -> equal exactly at Delta = +|a_c| (brute force)
    fc, ft, ac = 4.7e9, 4.5e9, 200e6
    assert math.isclose((2 * fc - ac) - (fc + ft), 0) == (C.resonances(fc, ft, -ac, -1)[0]["distance_hz"] == 0)


# ------------------------------------------------------------- operations
def state(**macros):
    return {"qubits": {"q0": qubit(), "q1": qubit(f_01=4e9, z={"flux_point": "min"})},
            "qubit_pairs": {"p0": {"qubit_control": "#/qubits/q0", "qubit_target": "#/qubits/q1",
                                   "moving_qubit": "control", "macros": macros}}}


def test_every_operation_and_alias_with_its_pulse_on_the_right_qubit():
    s = state(cz_a={"__class__": "gates.CZGate", "flux_pulse_qubit": "#/qubits/q0/z/operations/flux",
                    "fidelity": {"InterleavedRB": .99, "InterleavedRB_load_id": 7}},
              cz="#./cz_a", other={"__class__": "gates.Measure"})
    s["qubits"]["q0"]["z"]["operations"] = {"flux": {"amplitude": .1, "length": 40}}
    rows, convs = C.operations(s)
    assert [r["operation"] for r in rows] == ["cz_a", "cz"] and rows[1]["alias_of"]
    for r in rows:
        assert [ln["amplitude_v"] for ln in r["lines"]] == [.1, 0.0]
        assert r["lines"][0]["point"]["frequency_hz"] == pytest.approx(5e9 - 80e6)
        assert r["lines"][1]["point"]["frequency_hz"] == 4e9
    assert rows[0]["fidelity"]["InterleavedRB"] == {"value": .99, "load_id": 7}
    assert rows[0]["detuning_hz"] == pytest.approx(5e9 - 80e6 - 4e9)


def test_two_flux_and_both_movers_and_a_spectator_member():
    s = state(two={"__class__": "x.CZGateTwoFlux", "flux_pulse_qubit": {"amplitude": .1},
                   "flux_pulse_target": {"amplitude": .2}},
              both={"__class__": "x.CZGateBothMovers", "flux_pulse_high": {"amplitude": .3},
                    "flux_pulse_low": {"amplitude": .05}, "interaction_frequency": 4.5e9},
              spec={"__class__": "x.CZGate", "flux_pulse_qubit": {"amplitude": .1},
                    "spectator_qubits": {"q1": "#/qubits/q1"},
                    "spectator_qubits_control": {"q1": {"amplitude": .07}}})
    rows = {r["operation"]: r for r in C.operations(s)[0]}
    assert [ln["amplitude_v"] for ln in rows["two"]["lines"]] == [.1, .2]
    assert rows["two"]["lines"][1]["point"]["frequency_hz"] == pytest.approx(4e9 + 8e9 * .04)
    # the control (5 GHz) is the higher qubit: it plays the high pulse
    assert [ln["amplitude_v"] for ln in rows["both"]["lines"]] == [.3, .05]
    assert rows["both"]["interaction_frequency_hz"] == 4.5e9
    assert rows["spec"]["lines"][1]["moves"] and rows["spec"]["lines"][1]["amplitude_v"] == .07


def test_the_high_qubit_tie_goes_to_the_control_like_the_gate():
    s = state(both={"__class__": "x.CZGateBothMovers", "flux_pulse_high": {"amplitude": .3},
                    "flux_pulse_low": {"amplitude": .05}})
    s["qubits"]["q1"]["f_01"] = 5e9
    row = C.operations(s)[0][0]
    assert row["lines"][0]["pulse_path"].endswith("flux_pulse_high")


def test_plan_beside_what_the_operation_plays():
    s = state(cz={"__class__": "x.CZGateTwoFlux", "flux_pulse_qubit": {"amplitude": .1},
                  "flux_pulse_target": {"amplitude": .2}})
    s["qubit_pairs"]["p0"]["extras"] = {"cz_plan": {"omega_int_hz": 4.6e9, "v_control_v": .09, "v_target_v": .2,
                                                    "path_ok": True, "reasons_json": "[\"a\"]"}}
    plan = C.operations(s)[0][0]["plan"]
    assert plan["omega_int_hz"] == 4.6e9 and plan["reasons"] == ["a"]
    assert plan["roles"]["control"]["difference_v"] == pytest.approx(.01)
    assert plan["roles"]["target"]["difference_v"] == pytest.approx(0)


# ------------------------------------------------------------- trajectory
def test_a_bipolar_waveform_maps_both_lobes_and_idles_at_f01():
    conv = C.Conversion(qubit())
    t = conv.trajectory([0, .1, .2, 0, -.2, -.1, 0])
    assert t["frequency_hz"] == pytest.approx([5e9, 4.92e9, 4.68e9, 5e9, 4.68e9, 4.92e9, 5e9])
    blind = C.Conversion(qubit(freq_vs_flux_01_quad_term=None)).trajectory([0, .1])
    assert blind["frequency_hz"][0] == 5e9 and blind["frequency_hz"][1] is None


def test_the_trajectory_follows_the_lab_model_where_it_has_one():
    conv = C.Conversion(model_qubit(0))
    t = conv.trajectory([0, .3])
    assert t["frequency_hz"][1] == pytest.approx(conv.point(.3)["model_hz"])
    assert t["quad_hz"][1] == pytest.approx(conv.point(.3)["quad_hz"])


# ------------------------------------------------------------- when set
def pt(t_us, run=1, proven=True):
    return {"t": f"T{t_us}", "t_us": t_us, "run_id": run, "proven": proven,
            "provenance": "run_proven" if proven else "run_saved"}


def test_last_set_is_the_newest_amplitude_and_a_later_dependency_is_stale():
    row = {"amplitudes": ["a", "b"], "dependencies": ["f", "m", "o"]}
    h = {"mode": "ledger", "rows": {"a": {"effective": [pt(10_000_000)]}, "b": {"effective": [pt(30_000_000, 3)]},
                                     "f": {"effective": [pt(20_000_000, 2)]}, "m": {"effective": [pt(9_000_000)]}}}
    c = C.calibration(row, h)
    assert c["last_set"]["run_id"] == 3 and c["last_calibrated"]["run_id"] == 3
    assert [w["path"] for w in c["warnings"]] == ["f"]           # f changed after amplitude a was set
    assert c["fields"]["o"]["t_us"] is None and not c["unknown"]


def test_unproven_is_not_called_calibrated_and_missing_history_is_unknown():
    row = {"amplitudes": ["a"], "dependencies": []}
    c = C.calibration(row, {"mode": "ledger", "rows": {"a": {"effective": [pt(5, proven=False)]}}})
    assert c["last_set"]["run_id"] == 1 and c["last_calibrated"] == {}
    gone = C.calibration(row, {"mode": "unavailable", "rows": {}, "reason": "no_ledger"})
    assert gone["unknown"] and gone["last_set"] == {} and gone["status"] == "unavailable"


@pytest.mark.parametrize("evidence", [{"lower_sweet_spot_frequency": 5.04e9}, {"at_sweet_spot": False}])
def test_refused_conversion_keeps_only_idle_in_curves_and_trajectories(evidence):
    conv = C.Conversion(model_qubit(0, **evidence))
    assert conv.point(.1)["frequency_hz"] is None
    for key in ("quad_hz", "model_hz"):
        assert conv.curve(.1, 3)[key] == [None, conv.f01, None]
        assert conv.trajectory([.1, 0, -.1])[key] == [None, conv.f01, None]
    assert conv.trajectory([.1, 0, -.1])["frequency_hz"] == [None, conv.f01, None]


@pytest.mark.parametrize("bad", [{"model_r2": .1}, {"a_half_v": None}, {"phi0_v": float("inf")}])
@pytest.mark.parametrize("other_spot", [False, True])
def test_quality_rejection_never_draws_the_fit_at_either_anchor(bad, other_spot):
    q = model_qubit(0)
    if other_spot:
        q["f_01"] = C.model_spots(q["extras"]["lffem_flux_model"])["other"]["f_hz"]
        q["z"]["flux_point"] = "min"
    q["extras"]["lffem_flux_model"].update(bad)
    conv = C.Conversion(q)
    assert conv.anchor is None and conv.model_kind is None
    assert conv.curve(.1)["model_hz"] is None
    assert conv.trajectory([0, .1])["model_hz"] is None
    assert conv.point(.1)["kind"] == C.QUAD_LABEL
    assert any("not drawn" in n for n in conv.notes)


def test_reanchored_model_has_numeric_points_curves_and_trajectory():
    q = model_qubit(0)
    m = q["extras"]["lffem_flux_model"]
    q["f_01"] = C.model_spots(m)["other"]["f_hz"]
    q["z"]["flux_point"] = "min"
    conv = C.Conversion(q)
    expected = q["f_01"] + ((5.2e9 * (np.cos(np.pi * .6)**2 + .25 * np.sin(np.pi * .6)**2)**.25)
                              - 5.2e9 * .25**.25)
    assert math.isfinite(expected) and expected > q["f_01"]
    assert conv.point(.1)["model_hz"] == pytest.approx(expected)
    assert conv.point(.1)["frequency_hz"] == pytest.approx(expected)
    assert conv.curve(.1, 3)["model_hz"] == pytest.approx([expected, q["f_01"], expected])
    assert conv.trajectory([0, .1])["frequency_hz"] == pytest.approx([q["f_01"], expected])


def test_checked_window_boundaries_and_each_lobe_are_finite():
    q = model_qubit(0)
    q["extras"]["lffem_flux_model"].update(amp_min_v=-.01, amp_max_v=.02)
    q["freq_vs_flux_01_quad_term"] = abs(C.Conversion(q).curvature["here_hz_per_v2"])
    conv = C.Conversion(q)
    for a in (.019, -.009):
        assert conv.point(a)["validity"] == "within the measured quadratic range"
    for a in (.021, -.011, .4):
        assert conv.point(a)["validity"] == "beyond the fitted arc's measured range"
    # The fitted model's 1% guard itself is inclusive on both measured boundaries.
    lo, hi = conv._model_window()
    assert math.isfinite(float(conv.model_shift(lo)))
    assert math.isfinite(float(conv.model_shift(hi)))
    assert math.isnan(float(conv.model_shift(lo - 1e-6)))
    assert math.isnan(float(conv.model_shift(hi + 1e-6)))


def test_reanchored_measured_window_is_periodic_with_numeric_endpoints():
    q = model_qubit(0)
    m = q["extras"]["lffem_flux_model"]
    m.update(amp_min_v=-.02, amp_max_v=.02)
    q.update(f_01=C.model_spots(m)["other"]["f_hz"], z={"flux_point": "min"})
    conv = C.Conversion(q)
    expected = m["f_01_at_fit_hz"]
    assert conv.point(.5)["model_hz"] == pytest.approx(expected)
    assert conv.point(-.5)["model_hz"] == pytest.approx(expected)
    assert conv.point(.01)["model_hz"] is None
    assert math.isfinite(float(conv.model_shift(.5201)))
    assert math.isnan(float(conv.model_shift(.5203)))
    assert math.isfinite(float(conv.model_shift(-.5201)))
    assert math.isnan(float(conv.model_shift(-.5203)))
