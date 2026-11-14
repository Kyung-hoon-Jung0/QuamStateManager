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


def test_missing_axis_is_reported_not_evaluated_and_verdict_unchanged(tmp_path):
    """Convention of the other G4 rules: a missing input never changes the
    verdict; this rule also says it could not run."""
    v = _evaluate(_write_raw(tmp_path / "r", with_axis=False), 0.0066)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert v.verdict == "pass"
    assert any("extremum check not evaluated" in r and "flux_bias" in r
               for r in v.reasons)


def test_missing_vertex_is_reported_not_evaluated(tmp_path):
    v = _evaluate(_write_raw(tmp_path / "r"), None)
    assert v.checks["G4_extremum"] == "not_evaluated"
    assert any("does not record flux_offset" in r for r in v.reasons)
