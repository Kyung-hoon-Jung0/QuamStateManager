"""G4 extremum rule: a fitted vertex must lie inside the swept axis, and the
proposed move must not exceed the sweep half-span.

The synthetic case mirrors a Ramsey-versus-flux run whose sweep covered
-5 .. 5 mV while the parabola vertex sat at 6.6 mV: across the window the
frequency is nearly linear and the measured detuning folds where it crosses
the Ramsey detuning, so the fit extrapolates. The span check alone reports
"signal present" and passed it.
"""
from __future__ import annotations

import dataclasses

import h5py
import numpy as np
import pytest

from quam_state_manager.core.autofit import families, gates

NODE = "1Q_09b_ramsey_vs_flux_calibration"
HALF = 0.005          # V, the sweep half-span
RAMSEY_DET = 2e6      # Hz


def _write_raw(folder, *, flux=None, units="V", with_axis=True):
    folder.mkdir(parents=True, exist_ok=True)
    t = np.linspace(0, 2e-6, 100)
    flux = np.linspace(-HALF, HALF, 21) if flux is None else np.asarray(flux)
    vertex, quad = 0.0066, -4e10          # Hz / V^2
    f = quad * (flux - vertex) ** 2       # nearly linear across the window
    det = np.abs(RAMSEY_DET + f)          # folds where it crosses zero
    rng = np.random.default_rng(3)
    i_data = 0.5 * np.cos(2 * np.pi * det[:, None] * t[None, :]) \
        + 0.01 * rng.standard_normal((flux.size, t.size))
    with h5py.File(folder / "ds_raw.h5", "w") as h:
        q = h.create_dataset("qubit", data=np.array([b"qA1"]))
        q.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
        scales = [q]
        if with_axis:
            fb = h.create_dataset("flux_bias", data=flux)
            fb.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            if units:
                fb.attrs["units"] = units
            scales.append(fb)
        it = h.create_dataset("idle_time", data=t)
        it.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
        scales.append(it)
        ds = h.create_dataset("I", data=i_data[None, :, :])
        for ax, sc in enumerate([scales[0]] + ([scales[1]] if with_axis else [None]) + [it]):
            if sc is not None:
                ds.dims[ax].attach_scale(sc)
    return folder


def _evaluate(folder, flux_offset, fam=None):
    fam = fam or families.family_for(NODE)
    run = {"fit_results": {"qA1": {"success": True, "flux_offset": flux_offset,
                                   "freq_offset": 1e5, "quad_term": -4e10}},
           "outcomes": {"qA1": "successful"},
           "folder_path": str(folder) if folder else None}
    return gates.evaluate_target(run, fam, "qA1",
                                 current_value_of=lambda p: None)


def test_the_node_resolves_its_own_family_with_the_rule():
    fam = families.family_for(NODE)
    assert fam.key == "ramsey_vs_flux"
    assert fam.extremum is not None and fam.extremum.key == "flux_offset"
    # the plain Ramsey family did not opt in: no behaviour change there
    assert families.family_for("1Q_09_ramsey").extremum is None


def test_reported_shape_vertex_outside_is_not_passed(tmp_path):
    v = _evaluate(_write_raw(tmp_path / "r"), 0.0066)
    assert v.verdict == "fail"
    assert v.checks["G4_extremum"] == "fail"
    assert any("fit vertex at 6.60 mV is outside the swept range "
               "-5.00 .. 5.00 mV" in r for r in v.reasons), v.reasons


def test_without_the_rule_the_same_run_passes(tmp_path):
    """The removal check: the run that the rule rejects passes otherwise."""
    fam = dataclasses.replace(families.family_for(NODE), extremum=None)
    v = _evaluate(_write_raw(tmp_path / "r"), 0.0066, fam=fam)
    assert v.verdict == "pass", v.reasons
    assert any("signal present" in r for r in v.reasons)


def test_vertex_inside_small_move_passes_as_before(tmp_path):
    folder = _write_raw(tmp_path / "r")
    v = _evaluate(folder, 0.001)            # 20 % of the half-span
    base = _evaluate(folder, 0.001,
                     fam=dataclasses.replace(families.family_for(NODE),
                                             extremum=None))
    assert v.verdict == "pass"
    assert v.checks["G4_extremum"] == "ok"
    assert v.reasons == base.reasons


@pytest.mark.parametrize("edge", [-HALF, HALF])
def test_vertex_exactly_at_the_edge_counts_as_inside(tmp_path, edge):
    v = _evaluate(_write_raw(tmp_path / "r"), edge)
    assert v.checks["G4_extremum"] == "ok"
    assert v.verdict == "pass"


def test_move_larger_than_half_span_inside_an_off_centre_sweep(tmp_path):
    """Descending, off-centre sweep 8 .. -2 mV: range is min..max, half-span
    5 mV; a vertex at 6 mV is inside but moves 6 mV."""
    flux = np.linspace(0.008, -0.002, 21)
    v = _evaluate(_write_raw(tmp_path / "r", flux=flux), 0.006)
    assert v.verdict == "fail"
    assert any("proposed move 6.00 mV is larger than the sweep half-span "
               "5.00 mV" in r for r in v.reasons), v.reasons


def test_unit_comes_from_the_data(tmp_path):
    v = _evaluate(_write_raw(tmp_path / "r", units="mV",
                             flux=np.linspace(-5, 5, 21)), 6.6)
    assert any("fit vertex at 6.60 mV is outside the swept range "
               "-5.00 .. 5.00 mV" in r for r in v.reasons), v.reasons


def test_missing_axis_is_not_a_pass_and_not_a_guessed_fail(tmp_path):
    """The proposed value is the extremum itself: an unread range is a
    suspect (unverifiable) with the reason, never a pass."""
    v = _evaluate(_write_raw(tmp_path / "r", with_axis=False), 0.0066)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert v.verdict == "suspect" and v.failure_mode == "unverifiable"
    assert any("extremum check not evaluated" in r and "flux_bias" in r
               for r in v.reasons)


def test_missing_vertex_is_reported_not_evaluated(tmp_path):
    v = _evaluate(_write_raw(tmp_path / "r"), None)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert any("does not record flux_offset" in r for r in v.reasons)
    assert v.verdict == "suspect"


def test_nan_vertex_is_named_and_not_passed(tmp_path):
    v = _evaluate(_write_raw(tmp_path / "r"), float("nan"))
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert v.verdict == "suspect"
    assert any("flux_offset is not a finite number" in r for r in v.reasons)


def test_one_point_sweep_is_not_passed(tmp_path):
    v = _evaluate(_write_raw(tmp_path / "r", flux=[0.0]), 0.0)
    assert v.verdict == "suspect"
    assert any("fewer than 2 values" in r for r in v.reasons), v.reasons


@pytest.mark.parametrize("vertex,inside", [(0.00499, True), (0.00501, False),
                                           (-0.00501, False)])
def test_just_inside_and_just_outside(tmp_path, vertex, inside):
    v = _evaluate(_write_raw(tmp_path / "r"), vertex)
    assert v.checks["G4_extremum"] == ("ok" if inside else "fail")
    assert (v.verdict == "pass") is inside


def test_descending_sweep_outside_fails(tmp_path):
    v = _evaluate(_write_raw(tmp_path / "r",
                             flux=np.linspace(HALF, -HALF, 21)), 0.0066)
    assert v.checks["G4_extremum"] == "fail"
    assert any("-5.00 .. 5.00 mV" in r for r in v.reasons)


def _curv_family(expect):
    fam = families.family_for(NODE)
    rule = dataclasses.replace(fam.extremum, curvature_key="quad_term",
                               expect=expect)
    return dataclasses.replace(fam, extremum=rule)


def test_wrong_curvature_sign_is_not_a_pass(tmp_path):
    folder = _write_raw(tmp_path / "r")
    # fixture quad_term is negative: a maximum
    ok = _evaluate(folder, 0.001, fam=_curv_family("max"))
    assert ok.verdict == "pass" and ok.checks["G4_extremum"] == "ok"
    bad = _evaluate(folder, 0.001, fam=_curv_family("min"))
    assert bad.verdict == "fail"
    assert any("makes the vertex a maximum, but this calibration expects "
               "a minimum" in r for r in bad.reasons), bad.reasons


def _evaluate_with(folder, fam, *, quad=-4e10, anchor=None, drop_quad=False):
    entry = {"success": True, "flux_offset": 0.001, "freq_offset": 1e5,
             "quad_term": quad}
    if drop_quad:
        del entry["quad_term"]
    run = {"fit_results": {"qA1": entry}, "outcomes": {"qA1": "successful"},
           "folder_path": str(folder)}
    return gates.evaluate_target(run, fam, "qA1",
                                 current_value_of=lambda p: anchor)


def test_an_unknown_expectation_is_not_read_as_a_minimum(tmp_path):
    folder = _write_raw(tmp_path / "r")
    # a positive coefficient would satisfy a silent "anything else is min"
    v = _evaluate_with(folder, _curv_family("maximum"), quad=4e10)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert v.verdict != "pass"
    assert any("neither 'max' nor 'min'" in r for r in v.reasons), v.reasons


@pytest.mark.parametrize("quad, said", [
    (float("nan"), "is not a finite number"),
    (float("inf"), "is not a finite number"),
    ("steep", "is not a finite number"),
    (0.0, "has no extremum"),
])
def test_an_unreadable_curvature_is_not_a_pass(tmp_path, quad, said):
    folder = _write_raw(tmp_path / "r")
    v = _evaluate_with(folder, _curv_family("max"), quad=quad)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert v.verdict != "pass"
    assert any("quad_term" in r and said in r for r in v.reasons), v.reasons


def test_a_missing_curvature_is_not_a_pass(tmp_path):
    folder = _write_raw(tmp_path / "r")
    v = _evaluate_with(folder, _curv_family("max"), drop_quad=True)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert v.verdict != "pass"
    assert any("does not record quad_term" in r for r in v.reasons), v.reasons


def _absolute_family(state_path="qubits.{q}.z.joint_offset"):
    fam = families.family_for(NODE)
    rule = dataclasses.replace(fam.extremum, move="absolute",
                               state_path=state_path)
    return dataclasses.replace(fam, extremum=rule)


@pytest.mark.parametrize("anchor", [None, float("nan"), "0.001", True])
def test_an_absolute_move_without_its_anchor_is_not_a_pass(tmp_path, anchor):
    folder = _write_raw(tmp_path / "r")
    v = _evaluate_with(folder, _absolute_family(), anchor=anchor)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert v.verdict != "pass"
    assert any("the size of the move is unknown" in r
               and "qubits.qA1.z.joint_offset" in r for r in v.reasons), v.reasons


def test_an_absolute_move_without_a_state_path_is_not_a_pass(tmp_path):
    folder = _write_raw(tmp_path / "r")
    v = _evaluate_with(folder, _absolute_family(state_path=None), anchor=0.0)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert any("names no state path" in r for r in v.reasons), v.reasons


def test_an_absolute_move_is_measured_from_the_anchor(tmp_path):
    folder = _write_raw(tmp_path / "r")
    # vertex 1 mV, half-span 5 mV
    near = _evaluate_with(folder, _absolute_family(), anchor=0.0005)
    assert near.verdict == "pass" and near.checks["G4_extremum"] == "ok"
    far = _evaluate_with(folder, _absolute_family(), anchor=-0.0045)
    assert far.verdict == "fail"
    assert any("larger than the sweep half-span" in r for r in far.reasons)


def test_the_shipped_family_does_not_judge_the_curvature_sign():
    """The stored quadratic term's sign convention differs between setups."""
    assert families.family_for(NODE).extremum.curvature_key is None


def test_only_the_declared_families_opt_in():
    opted = sorted(f.key for f in families.FAMILIES.values()
                   if getattr(f, "extremum", None) is not None)
    assert opted == ["ramsey_vs_flux"]


def test_several_qubits_get_their_own_verdicts(tmp_path):
    folder = _write_raw(tmp_path / "r")
    fam = families.family_for(NODE)
    fit = {"success": True, "freq_offset": 1e5, "quad_term": -4e10}
    run = {"fit_results": {"qA1": dict(fit, flux_offset=0.001),
                           "qA2": dict(fit, flux_offset=0.0066)},
           "outcomes": {"qA1": "successful", "qA2": "successful"},
           "folder_path": str(folder)}
    out = {t: gates.evaluate_target(run, fam, t,
                                    current_value_of=lambda p: None)
           for t in ("qA1", "qA2")}
    assert out["qA2"].verdict == "fail"
    assert out["qA2"].checks["G4_extremum"] == "fail"
    assert out["qA1"].checks["G4_extremum"] == "ok"


def _vertex(a, b):
    return -b / (2 * a)          # [derived]


def test_vertex_formula_matches_brute_force_argmax():
    rng = np.random.default_rng(11)
    for _ in range(3):
        a = -rng.uniform(0.5, 5.0)
        b, c = rng.uniform(-3, 3), rng.uniform(-1, 1)
        x = np.linspace(-10, 10, 2_000_001)
        assert abs(x[np.argmax(a * x * x + b * x + c)] - _vertex(a, b)) < 2e-5
