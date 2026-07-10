"""Tests for the interactive-plot recipes (faithful Plotly reproductions).

Pure-unit tests of the model functions + JSON sanitization always run. The
recipe/route tests use the real experiment-data folder and auto-skip when it
is absent (mirroring the repo's ExampleChip real-data gating).
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.interactive_plots import models, plotbuild

# --- real-data gate ----------------------------------------------------
_DATA_ROOT = Path("<dataset-root>/example_lab")
_HAS_DATA = _DATA_ROOT.is_dir()
skip_no_data = pytest.mark.skipif(not _HAS_DATA, reason="experiment data folder not found")


# ======================================================================
# Pure-unit tests (no data) — never skipped
# ======================================================================

def test_lorentzian_dip_linbg_at_center():
    # At f == f0 (single point), fc == 0 → bg0 - amp.
    val = models.lorentzian_dip_linbg(np.array([5.0]), 5.0, 1.0, 2.0, 10.0, 0.0)
    assert val[0] == pytest.approx(8.0)


def test_sin_osc():
    assert models.sin_osc(np.array([0.0]), 1.0, 2.0, 0.25, 0.0)[0] == pytest.approx(1.0)


def test_multiexp_decay_at_zero():
    assert models.multiexp_decay(np.array([0.0]), 1.0, [(2.0, 5.0)])[0] == pytest.approx(3.0)


def test_multiexp_finite_pulse():
    y = models.multiexp_finite_pulse(np.array([0.0]), 1.0, [(2.0, 5.0)], 10.0)[0]
    assert y == pytest.approx(1.0 + 2.0 * (1.0 - math.exp(-2.0)))


def test_detrend_phase_poly_removes_linear_trend():
    axis = np.arange(10, dtype=float)
    phase = 3.0 * axis + 1.0
    residual = models.detrend_phase_poly(phase, axis, center=0.0, halfwidth=0.0, deg=1)
    assert np.max(np.abs(residual)) < 1e-6


def test_jsonable_replaces_non_finite():
    out = plotbuild.jsonable(np.array([1.0, np.nan, np.inf, -np.inf]))
    assert out == [1.0, None, None, None]


def test_jsonable_recurses_dicts_and_numpy():
    out = plotbuild.jsonable({"a": np.float64(2.0), "b": [np.int64(3), np.bool_(True)]})
    assert out == {"a": 2.0, "b": [3, True]}
    # Result must be strict-JSON serializable (no NaN/Inf tokens).
    json.dumps(out, allow_nan=False)


# ----- GEF (g/e/f) readout-opt reconstruction (synthetic, no data) ------

def _gef_fit(n, *, freq=False, smooth=True, abs_amp=True):
    """Minimal synthetic ds_fit dict for a GEF readout-opt run."""
    import numpy as np
    if freq:
        coord = np.linspace(-1e6, 1e6, n)
        dist = -(((coord + 2.3e5) / 1e6) ** 2) + 0.004
        vars_ = {"Distance": dist.reshape(1, n), "optimal_detuning": np.array([-2.3e5])}
        coords = {"qubit": ["qA2"], "frequency": coord.tolist()}
        cdim = "frequency"
    else:
        pref = np.linspace(0.8, 1.4, n)
        dist = -((pref - 1.25) ** 2) + 0.012
        vars_ = {"Distance": dist.reshape(1, n),
                 "optimal_amplitude": np.array([0.0628]),
                 "optimal_amp_prefactor": np.array([1.25])}
        if abs_amp:  # absolute readout amplitude [V] mapping present
            vars_["readout_amplitude"] = (pref * 0.05).reshape(1, n)
        if smooth:
            vars_["Distance_smooth"] = (dist + 1e-4).reshape(1, n)
        coords = {"qubit": ["qA2"], "amp_prefactor": pref.tolist()}
        cdim = "amp_prefactor"
    for v in ("Dge", "Def", "Dgf"):
        vars_[v] = dist.reshape(1, n)
    dim_order = {k: (["qubit"] if np.asarray(a).size == 1 else ["qubit", cdim])
                 for k, a in vars_.items()}
    return {"vars": vars_, "coords": coords, "dim_order": dim_order}


def _gef_bundle(fit, name, op="readout_GEF"):
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    meta = {"metadata": {"name": name},
            "data": {"parameters": {"model": {"operation": op}}}}
    return Bundle(run=None, node_meta=meta, fit=fit,
                  fit_vars=set(fit["vars"]), qubit_names=["qA2"])


def test_gef_power_fitted_distances_synthetic():
    """1Q_30a: raw + 3-pt-smoothed overlay, prefactor twin axis, amplitude click."""
    from quam_state_manager.core.interactive_plots.recipes import readout_opt as ro
    bundle = _gef_bundle(_gef_fit(12), "1Q_30a_gef_readout_power_optimization")
    spec = ro.build(bundle, "fitted_distances::qA2")
    assert spec.available and spec.figure is not None
    assert {"raw", "smoothed (3pt)"} <= {t.get("name") for t in spec.figure["data"]}
    assert spec.figure["layout"]["shapes"]          # optimal-amplitude vline
    assert "xaxis2" in spec.figure["layout"]        # amplitude-prefactor twin axis
    assert spec.clickable["axis"] == "x" and spec.clickable["qubit"] == "qA2"
    assert spec.clickable["targets"] == [
        {"path": "qubits.{q}.resonator.operations.readout_GEF.amplitude", "scale": 1}]
    json.dumps({"data": spec.figure["data"], "layout": spec.figure["layout"]},
               allow_nan=False)


def test_gef_power_click_uses_node_operation_param():
    """The click target follows the node's ``operation`` param, not a hardcode."""
    from quam_state_manager.core.interactive_plots.recipes import readout_opt as ro
    bundle = _gef_bundle(_gef_fit(10), "1Q_30a_gef_readout_power_optimization",
                         op="readout_custom")
    spec = ro.build(bundle, "fitted_distances::qA2")
    assert spec.clickable["targets"][0]["path"] == \
        "qubits.{q}.resonator.operations.readout_custom.amplitude"


def test_gef_power_degrades_without_smoothed_overlay():
    """No Distance_smooth → only the raw curve, still clickable."""
    from quam_state_manager.core.interactive_plots.recipes import readout_opt as ro
    bundle = _gef_bundle(_gef_fit(10, smooth=False), "1Q_30a_gef_readout_power_optimization")
    spec = ro.build(bundle, "fitted_distances::qA2")
    names = {t.get("name") for t in spec.figure["data"]}
    assert "raw" in names and "smoothed (3pt)" not in names
    assert spec.clickable is not None


def test_gef_power_without_volt_axis_is_view_only_prefactor():
    """No `readout_amplitude` V mapping → plot the dimensionless prefactor sweep,
    honestly labeled and VIEW-ONLY (never offer a unit-wrong amplitude write)."""
    from quam_state_manager.core.interactive_plots.recipes import readout_opt as ro
    bundle = _gef_bundle(_gef_fit(12, abs_amp=False),
                         "1Q_30a_gef_readout_power_optimization")
    spec = ro.build(bundle, "fitted_distances::qA2")
    assert spec.available and spec.figure is not None
    assert spec.figure["layout"]["xaxis"]["title"]["text"] == "Amplitude prefactor"
    assert "xaxis2" not in spec.figure["layout"]        # x already IS the prefactor
    assert spec.clickable is None                       # no V axis → no amplitude write
    # optimum marked from the prefactor fit, on the prefactor axis (not a stray V vline)
    assert abs(spec.figure["layout"]["shapes"][0]["x0"] - 1.25) < 1e-9


def test_gef_freq_fitted_distances_synthetic():
    """1Q_30: distance vs MHz shift, view-only, no prefactor axis, vline at detuning."""
    from quam_state_manager.core.interactive_plots.recipes import readout_opt as ro
    bundle = _gef_bundle(_gef_fit(20, freq=True), "1Q_30_gef_readout_frequency_optimization")
    spec = ro.build(bundle, "fitted_distances::qA2")
    assert spec.available and spec.figure is not None
    assert any(t.get("name") == "raw" for t in spec.figure["data"])
    assert "xaxis2" not in spec.figure["layout"]    # no prefactor sweep axis
    assert spec.clickable is None                   # detuning is added, not set → view-only
    assert abs(spec.figure["layout"]["shapes"][0]["x0"] - (-0.23)) < 1e-9  # -230 kHz → MHz
    json.dumps(spec.figure, allow_nan=False)


def test_gef_pairwise_distances_is_view_only():
    """gef_distances shows d_ge/d_ef/d_gf (+ min) and is never clickable."""
    from quam_state_manager.core.interactive_plots.recipes import readout_opt as ro
    bundle = _gef_bundle(_gef_fit(12), "1Q_30a_gef_readout_power_optimization")
    spec = ro.build(bundle, "gef_distances::qA2")
    names = {t.get("name") for t in spec.figure["data"]}
    assert {"d(g,e)", "d(e,f)", "d(g,f)", "min"} <= names
    assert spec.clickable is None


def test_gef_menu_marks_distance_figs_unavailable_without_vars():
    """Menu capability-detects: no Distance/pairwise vars → greyed (not crashing)."""
    from quam_state_manager.core.interactive_plots.recipes import readout_opt as ro
    bundle = _gef_bundle({"vars": {}, "coords": {}, "dim_order": {}},
                         "1Q_30a_gef_readout_power_optimization")
    bundle.fit_vars = set()
    specs = {s.key.split("::")[0]: s for s in ro.menu(bundle)}
    assert not specs["fitted_distances"].available
    assert not specs["gef_distances"].available


# ----- GEF (g/e/f) IQ blobs / confusion matrix (1Q_30b, synthetic) ------

def _iq_gef_fit(n=60, *, with_matrix=True):
    """Synthetic 1Q_30b ds_fit: 3 tight g/e/f clouds around separated centroids."""
    cents = np.array([[0.0, 0.0], [0.010, 0.0], [0.0, 0.010]])  # g, e, f (V)
    t = np.linspace(0, 1, n, endpoint=False)
    cloud = lambda c: (c[0] + 2e-4 * np.cos(2 * np.pi * t),
                       c[1] + 2e-4 * np.sin(2 * np.pi * t))
    (ig, qg), (ie, qe), (iff, qf) = cloud(cents[0]), cloud(cents[1]), cloud(cents[2])
    vars_ = {"Ig": ig.reshape(1, n), "Qg": qg.reshape(1, n), "Ie": ie.reshape(1, n),
             "Qe": qe.reshape(1, n), "If": iff.reshape(1, n), "Qf": qf.reshape(1, n)}
    dim = {k: ["qubit", "n_runs"] for k in vars_}
    if with_matrix:
        vars_["center_matrix"] = cents.reshape(1, 3, 2)
        dim["center_matrix"] = ["qubit", "I", "Q"]
    else:
        for i, s in enumerate(("g", "e", "f")):
            vars_[f"I_{s}_center"] = np.array([cents[i, 0]]); dim[f"I_{s}_center"] = ["qubit"]
            vars_[f"Q_{s}_center"] = np.array([cents[i, 1]]); dim[f"Q_{s}_center"] = ["qubit"]
    return {"vars": vars_, "coords": {"qubit": ["qA2"]}, "dim_order": dim}


def _iq_gef_bundle(fit):
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    return Bundle(run=None, node_meta={"metadata": {"name": "1Q_30b_iq_blobs_gef"}},
                  fit=fit, fit_vars=set(fit["vars"]), qubit_names=["qA2"])


def test_iq_blobs_gef_scatter_and_confusion_synthetic():
    """1Q_30b: g/e/f scatter + centroids, and a nearest-centroid 3x3 confusion."""
    from quam_state_manager.core.interactive_plots.recipes import iq_blobs_gef as gb
    bundle = _iq_gef_bundle(_iq_gef_fit())
    blobs = gb.build(bundle, "iq_blobs::qA2")
    assert [t.get("name") for t in blobs.figure["data"]] == ["g", "e", "f", "centroids"]
    assert blobs.clickable is None and blobs.kind == "2d"
    conf = gb.build(bundle, "confusion_matrix::qA2")
    z = conf.figure["data"][0]["z"]
    # tight, well-separated clouds → perfect nearest-centroid assignment (identity)
    assert z[0][0] == 1.0 and z[1][1] == 1.0 and z[2][2] == 1.0
    assert conf.clickable is None
    json.dumps({"a": blobs.figure, "b": conf.figure}, allow_nan=False)


def test_iq_blobs_gef_centroids_from_per_state_scalars():
    """Centroids fall back to I_<s>_center/Q_<s>_center when center_matrix is absent."""
    from quam_state_manager.core.interactive_plots.recipes import iq_blobs_gef as gb
    conf = gb.build(_iq_gef_bundle(_iq_gef_fit(with_matrix=False)), "confusion_matrix::qA2")
    z = conf.figure["data"][0]["z"]
    assert z[0][0] == 1.0 and z[1][1] == 1.0 and z[2][2] == 1.0


def test_iq_blobs_gef_resolves_not_fallback():
    """The 1Q_30b node name must route to the iq_blobs_gef recipe, not fallback."""
    from quam_state_manager.core.interactive_plots.registry import _resolve
    from quam_state_manager.core.interactive_plots.recipes import iq_blobs_gef
    assert _resolve("1Q_30b_iq_blobs_gef") is iq_blobs_gef


# ----- 2Q CPhase chevron (2Q_19, synthetic) ----------------------------

def _chevron_fit(na=8, nt=10):
    """Synthetic 2Q_19 ds_fit: a chevron over (amplitude, time) keyed by pair."""
    amp = np.linspace(0.9, 1.1, na)
    amp_full = np.linspace(0.19, 0.23, na)
    t = np.arange(1, nt + 1)
    z = np.outer(np.cos(amp), np.sin(t)) * 0.5 + 0.5  # (amplitude, time)
    vars_ = {"state_target": z.reshape(1, na, nt),
             "state_control": (1 - z).reshape(1, na, nt),
             "amp_full": amp_full.reshape(1, na),
             "cz_len": np.array([5]), "cz_amp": np.array([0.21])}
    dim = {"state_target": ["qubit_pair", "amplitude", "time"],
           "state_control": ["qubit_pair", "amplitude", "time"],
           "amp_full": ["qubit_pair", "amplitude"],
           "cz_len": ["qubit_pair"], "cz_amp": ["qubit_pair"]}
    return {"vars": vars_, "dim_order": dim,
            "coords": {"qubit_pair": ["qA2-qA1"], "amplitude": amp.tolist(), "time": t.tolist()}}


def _chevron_bundle(fit):
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    return Bundle(run=None, node_meta={"metadata": {"name": "2Q_19_chevron_1102"}},
                  fit=fit, fit_vars=set(fit["vars"]), fit_results={"qA2-qA1": {"success": True}})


def test_chevron_heatmap_and_fitted_star():
    """2Q_19: target-state chevron heatmap (keyed `amplitude` to dedup the PNG),
    y = flux amplitude in V, fitted star at (cz_len, cz_amp), 5-field click."""
    from quam_state_manager.core.interactive_plots.recipes import chevron
    bundle = _chevron_bundle(_chevron_fit())
    bases = {s.key.split("::")[0] for s in chevron.menu(bundle)}
    assert {"amplitude", "state_control"} <= bases
    spec = chevron.build(bundle, "amplitude::qA2-qA1")
    assert spec.available and spec.kind == "2d"
    # the fixture carries amp_full (absolute axis) → the 5-field click contract
    # MUST be offered; a regression that kills it has to fail, not pass silently.
    assert spec.clickable is not None
    paths = [t["path"] for t in spec.clickable["targets"]]
    assert sum(pth.endswith(".amplitude") for pth in paths) == 2
    assert sum(pth.endswith((".length", ".flat_length")) for pth in paths) == 3
    hm = next(t for t in spec.figure["data"] if t["type"] == "heatmap")
    assert np.array(hm["z"]).shape == (8, 10)              # (amplitude, time)
    assert len(hm["x"]) == 10 and len(hm["y"]) == 8
    assert abs(min(hm["y"]) - 0.19) < 1e-9 and abs(max(hm["y"]) - 0.23) < 1e-9  # amp_full [V]
    star = next(t for t in spec.figure["data"] if t.get("name") == "fitted")
    assert star["x"] == [5] and star["y"] == [0.21]        # (cz_len, cz_amp)
    json.dumps(spec.figure, allow_nan=False)


def test_chevron_resolves_and_slices_pair_axis():
    """Node routes to the chevron recipe; control-state figure slices the same
    qubit_pair axis and carries the same 5-field click contract."""
    from quam_state_manager.core.interactive_plots.registry import _resolve
    from quam_state_manager.core.interactive_plots.recipes import chevron
    assert _resolve("2Q_19_chevron_1102") is chevron
    spec = chevron.build(_chevron_bundle(_chevron_fit()), "state_control::qA2-qA1")
    assert spec.available and spec.title.endswith("control state")
    # amp_full present in the fixture → the same 5-field contract rides the
    # control-state companion figure too.
    assert spec.clickable is not None
    assert len(spec.clickable["targets"]) == 5


# ----- two-qubit RB (2Q_37/37b, synthetic) -----------------------------

def _rb_bundle(name="2Q_37_two_qubit_standard_rb", alpha=0.9, fidelity=0.95):
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    depths = np.array([1, 2, 4, 8, 16, 32, 64]); repeat, avg = 3, 200
    state = np.empty((1, repeat, len(depths), avg), dtype=int)
    for di, d in enumerate(depths):
        n0 = int(round((0.25 + 0.7 * alpha ** d) * avg))   # survival = P(|00>)
        row = np.ones(avg, dtype=int); row[:n0] = 0; row[n0:] = np.arange(avg - n0) % 3 + 1
        state[0, :, di, :] = row
    raw = {"vars": {"state": state},
           "coords": {"qubit_pair": ["qA2-qA1"], "repeat": list(range(repeat)),
                      "circuit_depth": depths.tolist(), "average": list(range(avg))},
           "dim_order": {"state": ["qubit_pair", "repeat", "circuit_depth", "average"]}}
    return Bundle(run=None, node_meta={"metadata": {"name": name}}, raw=raw,
                  raw_vars=set(raw["vars"]),
                  fit_results={"qA2-qA1": {"alpha": alpha, "fidelity": fidelity}})


def test_two_qubit_rb_decay_fit_and_replaces_static():
    """2Q RB: survival P(|00>) decay (log-x) + alpha overlay + fidelity title; the
    figure is keyed to the node's auto-named fig_<pair> so it replaces the PNG."""
    from quam_state_manager.core.interactive_plots.recipes import two_qubit_rb as rb
    bundle = _rb_bundle()
    assert [s.key for s in rb.menu(bundle)] == ["fig_qA2-qA1::qA2-qA1"]
    spec = rb.build(bundle, "fig_qA2-qA1::qA2-qA1")
    assert spec.available
    # RB fidelity is fit-derived, never a clicked coordinate — stays view-only.
    assert spec.clickable is None
    names = [t.get("name") for t in spec.figure["data"]]
    assert "P(|00⟩)" in names and "fit" in names
    surv = next(t for t in spec.figure["data"] if t.get("name") == "P(|00⟩)")
    assert surv["y"][0] > surv["y"][-1]                       # decays
    assert spec.figure["layout"]["xaxis"]["type"] == "log"
    assert "fidelity 0.9500" in spec.title
    json.dumps(spec.figure, allow_nan=False)


def test_two_qubit_rb_family_and_naming_variant():
    from quam_state_manager.core.interactive_plots.registry import _resolve
    from quam_state_manager.core.interactive_plots.recipes import two_qubit_rb
    for n in ("2Q_37_two_qubit_standard_rb", "2Q_37b_two_qubit_interleaved_cz_rb",
              "37_two_qubit_standard_rb"):
        assert _resolve(n) is two_qubit_rb


# ----- CZ phase calibrations (2Q_20/20b/21/21b, synthetic) --------------

def _cz_bundle(name, fit_vars, coords, dim_order):
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    return Bundle(run=None, node_meta={"metadata": {"name": name}},
                  fit={"vars": fit_vars, "coords": coords, "dim_order": dim_order},
                  fit_vars=set(fit_vars), fit_results={"qA2-qA1": {}})


def test_cz_21_raw_and_fit():
    from quam_state_manager.core.interactive_plots.recipes import cz_phase
    n = 17; frame = np.linspace(0, 1, n)
    fv = {"state_control": np.cos(2 * np.pi * frame).reshape(1, n),
          "state_target": np.sin(2 * np.pi * frame).reshape(1, n),
          "fitted_control": np.cos(2 * np.pi * frame).reshape(1, n),
          "fitted_target": np.sin(2 * np.pi * frame).reshape(1, n)}
    dim = {k: ["qubit_pair", "frame"] for k in fv}
    b = _cz_bundle("2Q_21_cz_phase_compensation", fv,
                   {"qubit_pair": ["qA2-qA1"], "frame": frame.tolist()}, dim)
    assert [s.key for s in cz_phase.menu(b)] == ["raw_and_fit::qA2-qA1"]
    spec = cz_phase.build(b, "raw_and_fit::qA2-qA1")
    assert [t.get("name") for t in spec.figure["data"]] == \
        ["control", "control fit", "target", "target fit"]
    assert spec.clickable is None
    json.dumps(spec.figure, allow_nan=False)


def test_cz_21b_phase_vs_operations():
    from quam_state_manager.core.interactive_plots.recipes import cz_phase
    n = 17; frame = np.linspace(-0.1, 0.09, n)
    fv = {"control_mean_vs_frame": np.cos(frame * 10).reshape(1, n),
          "target_mean_vs_frame": np.sin(frame * 10).reshape(1, n),
          "fitted_control_phase": np.array([-0.006]), "fitted_target_phase": np.array([0.006]),
          "control_mean_at_peak": np.array([0.92]), "target_mean_at_peak": np.array([0.96])}
    dim = {"control_mean_vs_frame": ["qubit_pair", "frame"],
           "target_mean_vs_frame": ["qubit_pair", "frame"],
           "fitted_control_phase": ["qubit_pair"], "fitted_target_phase": ["qubit_pair"],
           "control_mean_at_peak": ["qubit_pair"], "target_mean_at_peak": ["qubit_pair"]}
    b = _cz_bundle("2Q_21b_cz_phase_compensation_error_amp", fv,
                   {"qubit_pair": ["qA2-qA1"], "frame": frame.tolist()}, dim)
    spec = cz_phase.build(b, "phase_vs_operations::qA2-qA1")
    assert {"control", "target", "control peak", "target peak"} <= \
        {t.get("name") for t in spec.figure["data"]}
    assert len(spec.figure["layout"]["shapes"]) == 2     # two residual-phase vlines
    json.dumps(spec.figure, allow_nan=False)


def test_cz_20_and_20b_conditional_phase():
    from quam_state_manager.core.interactive_plots.recipes import cz_phase
    # 2Q_20: single conditional-phase curve + fit + optimal vline, keyed phase_figure
    n = 61; amp = np.linspace(0.18, 0.24, n)
    fv = {"phase_diff": np.linspace(0, 3, n).reshape(1, n),
          "fitted_curve": np.linspace(0, 3, n).reshape(1, n),
          "amp_full": amp.reshape(1, n), "optimal_amplitude": np.array([0.209])}
    dim = {"phase_diff": ["qubit_pair", "amp"], "fitted_curve": ["qubit_pair", "amp"],
           "amp_full": ["qubit_pair", "amp"], "optimal_amplitude": ["qubit_pair"]}
    b = _cz_bundle("2Q_20_cz_conditional_phase", fv,
                   {"qubit_pair": ["qA2-qA1"], "amp": amp.tolist()}, dim)
    assert [s.key for s in cz_phase.menu(b)] == ["phase_figure::qA2-qA1"]
    spec = cz_phase.build(b, "phase_figure::qA2-qA1")
    assert {"conditional phase", "fit"} <= {t.get("name") for t in spec.figure["data"]}
    assert len(spec.figure["layout"]["shapes"]) == 1     # optimal-amplitude vline
    json.dumps(spec.figure, allow_nan=False)
    # 2Q_20b: phase_diff over (number_of_operations, qubit_pair, amp) → one curve per ops
    no, m = 4, 33; amp2 = np.linspace(0.18, 0.24, m)
    fv2 = {"phase_diff": np.linspace(0, 3, no * m).reshape(no, 1, m),
           "amp_full": amp2.reshape(1, m), "optimal_amplitude": np.array([0.209])}
    dim2 = {"phase_diff": ["number_of_operations", "qubit_pair", "amp"],
            "amp_full": ["qubit_pair", "amp"], "optimal_amplitude": ["qubit_pair"]}
    b2 = _cz_bundle("2Q_20b_cz_conditional_phase_error_amp", fv2,
                    {"qubit_pair": ["qA2-qA1"], "number_of_operations": [1, 2, 3, 4],
                     "amp": amp2.tolist()}, dim2)
    spec2 = cz_phase.build(b2, "phase_figure::qA2-qA1")
    assert [t.get("name") for t in spec2.figure["data"]] == ["1 ops", "2 ops", "3 ops", "4 ops"]
    json.dumps(spec2.figure, allow_nan=False)


# ======================================================================
# Recipe + route tests (real data)
# ======================================================================

@pytest.fixture(scope="module")
def store():
    from quam_state_manager.core.dataset import DatasetStore
    return DatasetStore(str(_DATA_ROOT))


def _run_for(store, prefix):
    for rid in sorted(store.runs):
        if store.runs[rid].experiment_name.startswith(prefix):
            return store.runs[rid]
    return None


@skip_no_data
def test_resonator_menu_and_figures(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_03_resonator_spectroscopy_single")
    if run is None:
        pytest.skip("no resonator run")
    menu = list_interactive_figures(run)
    bases = {m["key"].split("::")[0] for m in menu}
    assert {"amplitude", "phase", "detrended_phase", "iq_circle"} <= bases
    for m in menu:
        if not m["available"] or m.get("static"):
            continue
        fig = build_interactive_figure(run, m["key"])
        assert fig is not None and fig["data"]
        json.dumps(fig, allow_nan=False)  # strict: no NaN/Inf leaked


@skip_no_data
def test_resonator_amplitude_clickable_targets(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_03_resonator_spectroscopy_single")
    if run is None:
        pytest.skip("no resonator run")
    key = next(m["key"] for m in list_interactive_figures(run)
               if m["key"].startswith("amplitude") and m["available"])
    fig = build_interactive_figure(run, key)
    clk = fig["clickable"]
    assert clk["axis"] == "x"
    paths = [t["path"] for t in clk["targets"]]
    assert paths == ["qubits.{q}.resonator.f_01", "qubits.{q}.resonator.RF_frequency"]
    assert all(t["scale"] == 1e9 for t in clk["targets"])
    assert clk["qubit"]  # the figure's qubit is embedded


@skip_no_data
def test_resonator_degrades_without_fit(store):
    """Amplitude still renders (no overlay) and stays clickable when popt absent."""
    from quam_state_manager.core.interactive_plots import h5reader
    from quam_state_manager.core.interactive_plots.recipes import resonator
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    run = _run_for(store, "1Q_03_resonator_spectroscopy_single")
    if run is None:
        pytest.skip("no resonator run")
    raw = h5reader.load_dataset(run, "ds_raw")
    qname = next(iter(run.qubits), "q")
    bundle = Bundle(run=run, raw=raw, fit=None,
                    raw_vars=set(raw["vars"]), fit_vars=set())
    spec = resonator.build(bundle, f"amplitude::{qname}")
    assert spec.available and spec.figure is not None
    assert not any(t.get("name") == "fit" for t in spec.figure["data"])  # no overlay
    assert spec.clickable is not None  # still clickable


@skip_no_data
def test_power_rabi_heatmap_and_clickable(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_11_power_rabi")
    if run is None:
        pytest.skip("no power_rabi run")
    key = next(m["key"] for m in list_interactive_figures(run) if m["available"])
    fig = build_interactive_figure(run, key)
    assert any(t.get("type") == "heatmap" for t in fig["data"])
    json.dumps(fig, allow_nan=False)
    paths = {t["path"]: t["scale"] for t in fig["clickable"]["targets"]}
    assert paths.get("qubits.{q}.xy.operations.x180.amplitude") == 1e-3
    assert paths.get("qubits.{q}.xy.operations.x90.amplitude") == 5e-4


@skip_no_data
def test_flux_qubitspec_figures_and_not_clickable(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_19a_qubit_flux_long_distortion_qubitspec")
    if run is None:
        pytest.skip("no 19a run")
    menu = list_interactive_figures(run)
    bases = {m["key"].split("::")[0] for m in menu}
    assert {"iq_abs_linear", "phase", "flux_response_linear", "fitted_data"} <= bases
    any_built = False
    for m in menu:
        if not m["available"] or m.get("static"):
            continue
        fig = build_interactive_figure(run, m["key"])
        assert fig is not None
        assert fig["clickable"] is None  # distortion figures are never clickable
        json.dumps(fig, allow_nan=False)
        any_built = True
    assert any_built


@skip_no_data
def test_flux_ramsey_figures(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_19b_qubit_flux_long_distortion_ramsey")
    if run is None:
        pytest.skip("no 19b run")
    menu = list_interactive_figures(run)
    bases = {m["key"].split("::")[0] for m in menu}
    assert {"raw_data_linear", "flux_response_linear"} <= bases
    for m in menu:
        if not m["available"] or m.get("static"):
            continue
        fig = build_interactive_figure(run, m["key"])
        assert fig is not None and fig["clickable"] is None
        json.dumps(fig, allow_nan=False)


def test_unsupported_experiment_resolves_to_fallback():
    # An unknown experiment name routes to the fallback recipe (empty menu).
    from quam_state_manager.core.interactive_plots.recipes import fallback
    from quam_state_manager.core.interactive_plots.registry import _resolve
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    assert _resolve("ZZ_totally_unknown_node") is fallback
    assert fallback.menu(Bundle(run=None)) == []
    assert fallback.build(Bundle(run=None), "anything") is None


@skip_no_data
def test_routes(store):
    from quam_state_manager.web.app import create_app
    run = _run_for(store, "1Q_03_resonator_spectroscopy_single")
    if run is None:
        pytest.skip("no resonator run")
    app = create_app()
    app.config["dataset_store"] = store
    client = app.test_client()
    from quam_state_manager.web import routes as _r
    uid = _r._dataset_uid(_r._folder_key(store.folder_path), run.run_id)

    r = client.get(f"/dataset/{uid}/interactive")
    assert r.status_code == 200
    assert b"ds-interactive-plot" in r.data

    key = next(m["key"] for m in
               __import__("quam_state_manager.core.interactive_plots", fromlist=["x"])
               .list_interactive_figures(run) if m["available"])
    r2 = client.get(f"/dataset/{uid}/interactive/plot", query_string={"fig": key})
    assert r2.status_code == 200
    payload = r2.get_json()
    assert set(payload) >= {"data", "layout", "kind"}

    r3 = client.get(f"/dataset/{uid}/interactive/plot", query_string={"fig": "bogus::q"})
    assert r3.status_code == 404


# ======================================================================
# Round 2: all-experiments coverage + new click transforms
# ======================================================================

def test_osc_decay_and_lorentzian_peak_models():
    assert models.osc_decay(np.array([0.0]), 2.0, 0.1, 0.0, 1.0, -0.01)[0] == pytest.approx(3.0)
    # peak at f0 (single point): base + amp
    v = models.lorentzian_peak_linbg(np.array([5.0]), 5.0, 1.0, 2.0, np.array([10.0]))
    assert v[0] == pytest.approx(12.0)


def test_plotbuild_new_helpers():
    # SVG scatter by default (renders without WebGL); GL is opt-in.
    assert plotbuild.scatter([1, 2], [3, 4], name="g")["type"] == "scatter"
    assert plotbuild.scatter([1, 2], [3, 4], webgl=True)["type"] == "scattergl"
    assert plotbuild.bar(["a", "b"], [1, 2])["type"] == "bar"
    tr, ann = plotbuild.confusion_matrix([[0.9, 0.1], [0.2, 0.8]])
    assert tr["type"] == "heatmap" and len(ann) == 4
    assert tr["colorscale"] == "Viridis"  # matches the saved figures; no white holes
    # Viridis-aware contrast: black text on bright (high-P) cells, white on dark (low-P)
    assert [a["font"]["color"] for a in ann] == ["#000", "#fff", "#fff", "#000"]
    json.dumps({"data": [tr], "ann": ann}, allow_nan=False)


def test_scatter_downsamples_large_point_clouds():
    """IQ blobs have thousands of shots — scatter() caps points so SVG stays fast."""
    import numpy as np
    n = 50_000
    tr = plotbuild.scatter(np.arange(n), np.arange(n))
    assert len(tr["x"]) <= plotbuild._SCATTER_MAX_POINTS
    assert len(tr["x"]) == len(tr["y"])
    # Small inputs are untouched.
    small = plotbuild.scatter([1, 2, 3], [4, 5, 6])
    assert len(small["x"]) == 3


_NEW_EXPERIMENT_PREFIXES = [
    "1Q_01_time_of_flight", "1Q_05_resonator_spectroscopy_vs_power",
    "1Q_06_resonator_spectroscopy_vs_flux", "1Q_08_qubit_spectroscopy",
    "1Q_09_qubit_spectroscopy_vs_flux", "1Q_12_ramsey", "1Q_13_drag_calibration",
    "1Q_15a_readout_frequency_optimization", "1Q_15b_readout_power_optimization",
    "1Q_16_iq_blobs", "1Q_17_xyz_delay", "1Q_20_qubit_flux_short_distortion",
    "1Q_22b_all_xy", "1Q_23_ramsey_vs_flux_calibration",
    "1Q_27_single_qubit_randomized_benchmarking", "1Q_28_Qubit_Spectroscopy_E_to_F",
    "1Q_29_power_rabi_ef", "1Q_03_resonator_spectroscopy_wide",
    "1Q_30_gef_readout_frequency_optimization", "1Q_30a_gef_readout_power_optimization",
    "1Q_30b_iq_blobs_gef", "2Q_19_chevron",
    "2Q_37_two_qubit_standard_rb", "2Q_37b_two_qubit_interleaved_cz_rb",
    "2Q_21_cz_phase_compensation", "2Q_21b_cz_phase_compensation_error_amp",
    "2Q_20_cz_conditional_phase", "2Q_20b_cz_conditional_phase_error_amp",
]


@skip_no_data
def test_all_new_experiments_build(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    missing = []
    for prefix in _NEW_EXPERIMENT_PREFIXES:
        run = _run_for(store, prefix)
        if run is None:
            missing.append(prefix)
            continue
        menu = list_interactive_figures(run)
        avail = [m for m in menu if m["available"] and not m.get("static")]
        assert avail, f"{prefix}: no available figures"
        for m in avail:
            fig = build_interactive_figure(run, m["key"])
            assert fig is not None and fig["data"], f"{prefix}:{m['key']} empty"
            json.dumps(fig, allow_nan=False)  # strict JSON (no NaN/Inf)
    if missing:
        pytest.skip(f"no data for: {missing}")


@skip_no_data
def test_qubit_spectroscopy_clickable(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_08_qubit_spectroscopy")
    if run is None:
        pytest.skip("no qubit_spectroscopy run")
    key = next(m["key"] for m in list_interactive_figures(run) if m["available"])
    clk = build_interactive_figure(run, key)["clickable"]
    paths = [t["path"] for t in clk["targets"]]
    assert paths == ["qubits.{q}.f_01", "qubits.{q}.xy.RF_frequency"]
    assert all(t["scale"] == 1e9 for t in clk["targets"])
    # e→f spectroscopy is view-only (updates anharmonicity).
    ef = _run_for(store, "1Q_28_Qubit_Spectroscopy_E_to_F")
    if ef is not None:
        k = next(m["key"] for m in list_interactive_figures(ef) if m["available"])
        assert build_interactive_figure(ef, k)["clickable"] is None


@skip_no_data
def test_vs_power_dbm_to_amp_transform(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_05_resonator_spectroscopy_vs_power")
    if run is None:
        pytest.skip("no vs_power run")
    fig = build_interactive_figure(run, next(m["key"] for m in list_interactive_figures(run) if m["available"]))
    clk = fig["clickable"]
    if clk is None:
        pytest.skip("vs_power run lacks quam_state full_scale_power_dbm")
    # amplitude target (dbm_to_amp, y-axis) + optional 05b INCREMENT-semantics
    # frequency targets on x (f_01/RF shift baked from the run's own RF_at_run).
    amp_targets = [t for t in clk["targets"] if "amplitude" in t["path"]]
    freq_targets = [t for t in clk["targets"]
                    if t["path"].endswith(("f_01", "RF_frequency"))]
    assert len(amp_targets) == 1
    for ft in freq_targets:
        assert ft["axis"] == "x" and ft["scale"] == 1e9
        assert isinstance(ft["offset"], float)       # frozen_target − RF_at_run
        assert ft.get("provenance", {}).get("formula")
    t = amp_targets[0]
    assert t["path"] == "qubits.{q}.resonator.operations.readout.amplitude"
    assert t["transform"]["type"] == "dbm_to_amp"
    assert clk["axis"] == "y"
    assert "yaxis2" in fig["layout"]  # twin amplitude axis
    assert fig["layout"]["yaxis2"].get("type") == "log"  # log axis aligns with dBm
    # read-only dBm context row (clicked power shown, not written)
    assert clk["context"] and clk["context"][0]["unit"] == "dBm"
    assert clk["context"][0]["axis"] == "y"


@skip_no_data
@pytest.mark.parametrize("prefix,freq_path", [
    ("1Q_06_resonator_spectroscopy_vs_flux", "qubits.{q}.resonator.f_01"),
    ("1Q_09_qubit_spectroscopy_vs_flux", "qubits.{q}.f_01"),
])
def test_vs_flux_two_value_click(store, prefix, freq_path):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, prefix)
    if run is None:
        pytest.skip(f"no {prefix} run")
    clk = build_interactive_figure(run, next(m["key"] for m in list_interactive_figures(run) if m["available"]))["clickable"]
    # CONTRACT-FAITHFUL targets (upgraded): flux is flux_point-routed with a
    # CONCRETE qubit path; node 06 assigns the ABSOLUTE swept offset (offset=0)
    # while node 09's delta axis carries the baked PRE-update offset (float).
    flux_ts = [t for t in clk["targets"] if t["path"].endswith("_offset")]
    assert flux_ts and flux_ts[0]["axis"] == "y"
    assert isinstance(flux_ts[0].get("offset", 0), (int, float))
    freq_leaf = freq_path.rsplit(".", 1)[-1]           # f_01
    f01_ts = [t for t in clk["targets"]
              if t["path"].endswith(f".{freq_leaf}") or t["path"] == freq_path]
    rf_ts = [t for t in clk["targets"] if t["path"].endswith("RF_frequency")]
    assert rf_ts and rf_ts[0]["axis"] == "x" and rf_ts[0]["scale"] == 1e9
    for ft in f01_ts:
        assert ft["axis"] == "x" and ft["scale"] == 1e9
        # 06: increment → float offset baked; 09: absolute assign → offset 0/absent
        assert isinstance(ft.get("offset", 0.0), (int, float))


@skip_no_data
def test_iq_blobs_threshold_click(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_16_iq_blobs")
    if run is None:
        pytest.skip("no iq_blobs run")
    hist_key = next(m["key"] for m in list_interactive_figures(run)
                    if m["available"] and m["key"].startswith("histograms"))
    clk = build_interactive_figure(run, hist_key)["clickable"]
    if clk is None:
        pytest.skip("no readout length in quam_state")
    paths = {t["path"] for t in clk["targets"]}
    assert paths == {"qubits.{q}.resonator.operations.readout.threshold",
                     "qubits.{q}.resonator.operations.readout.rus_exit_threshold"}
    assert all(t["scale"] > 0 for t in clk["targets"])  # length/(4096*1e3)
    # blob/confusion figures are not clickable
    for m in list_interactive_figures(run):
        if m["available"] and not m["key"].startswith("histograms"):
            assert build_interactive_figure(run, m["key"])["clickable"] is None


@skip_no_data
def test_xyz_delay_and_drag_click(store):
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    xy = _run_for(store, "1Q_17_xyz_delay")
    if xy is not None:
        clk = build_interactive_figure(xy, next(m["key"] for m in list_interactive_figures(xy) if m["available"]))["clickable"]
        assert clk["targets"][0]["path"] == "qubits.{q}.z.opx_output.delay"
    dr = _run_for(store, "1Q_13_drag_calibration")
    if dr is not None:
        k = next(m["key"] for m in list_interactive_figures(dr) if m["available"])
        clk = build_interactive_figure(dr, k)["clickable"]
        assert clk["targets"][0]["path"].endswith(".alpha")


# ======================================================================
# Round 3: vs-power amplitude conversion + robust heatmap (pure-unit, no data)
# ======================================================================

import types  # noqa: E402

from quam_state_manager.core.interactive_plots.recipes import resonator_2d  # noqa: E402
from quam_state_manager.core.interactive_plots.recipes.base import Bundle  # noqa: E402


def _dbm_to_amp(dbm, ref_dbm, scale):
    """Mirror the JS dbm_to_amp transform (app.js _attachInteractivePlotClickHandler)."""
    return scale * 10 ** ((dbm - ref_dbm) / 20)


def test_num_unwraps_single_element_arrays_and_scalars():
    _num = resonator_2d._num
    assert _num([-25]) == -25.0            # 1-element list (HDF5 round-trip shape)
    assert _num(np.array([0.1])) == pytest.approx(0.1)
    assert _num(np.float64(3.0)) == 3.0
    assert _num(-25) == -25.0
    assert _num([1, 2]) is None            # multi-element → ambiguous
    assert _num([]) is None
    assert _num("nope") is None
    assert _num(None) is None
    assert _num([float("nan")]) is None    # non-finite rejected
    assert _num(True) is None              # bool is not a measurement


def test_amp_conversion_source_order():
    # 1) root_attrs (1-element arrays, as stored on disk) win and are unwrapped.
    b = Bundle(run=None, raw={"root_attrs": {"max_power_dbm": [-25], "max_amp": [0.1]}})
    assert resonator_2d._amp_conversion(b, "q0") == (-25.0, 0.1)

    # 2) run.parameters fallback when root_attrs are absent/empty.
    run = types.SimpleNamespace(parameters={"max_power_dbm": -20, "max_amp": 0.2})
    b = Bundle(run=run, raw={"root_attrs": {}})
    assert resonator_2d._amp_conversion(b, "q0") == (-20.0, 0.2)

    # 3) quam_state full_scale_power_dbm fallback (scale = 1 ⇒ node's amp formula).
    qs = {"qubits": {"q0": {"resonator": {"opx_output": {"full_scale_power_dbm": -30}}}}}
    b = Bundle(run=None, raw={"root_attrs": {}}, quam_state=qs)
    assert resonator_2d._amp_conversion(b, "q0") == (-30.0, 1.0)

    # None when no source resolves.
    assert resonator_2d._amp_conversion(Bundle(run=None, raw={"root_attrs": {}}), "q0") is None


def _synthetic_vs_power_bundle(with_norm=True):
    n_pow, n_det = 4, 3
    iq = np.arange(1.0, n_pow * n_det + 1.0).reshape(n_pow, n_det)
    vars_ = {"IQ_abs": iq}
    dim_order = {"IQ_abs": ["power", "detuning"]}
    if with_norm:
        vars_["IQ_abs_norm"] = iq / iq.max()
        dim_order["IQ_abs_norm"] = ["power", "detuning"]
    raw = {
        "vars": vars_, "dim_order": dim_order,
        "coords": {"power": np.array([-50.0, -40.0, -30.0, -25.0]),
                   "detuning": np.array([-1e6, 0.0, 1e6])},
        "root_attrs": {"max_power_dbm": [-25], "max_amp": [0.1]},
    }
    return Bundle(run=None, raw=raw, fit=None, quam_state=None)


def test_vs_power_log_axis_amplitude_target_and_context_row():
    bundle = _synthetic_vs_power_bundle(with_norm=True)
    x_ghz = np.array([7.0, 7.001, 7.002])
    spec = resonator_2d._vs_power(bundle, "amplitude::q0", "q0", 0, x_ghz)

    # log twin amplitude axis (aligns with the linear dBm axis + popup value)
    assert spec.figure["layout"]["yaxis2"]["type"] == "log"

    clk = spec.clickable
    assert clk["axis"] == "y"
    assert len(clk["targets"]) == 1                      # amplitude only
    t = clk["targets"][0]
    assert t["path"] == "qubits.{q}.resonator.operations.readout.amplitude"
    assert t["transform"] == {"type": "dbm_to_amp", "ref_dbm": -25.0, "scale": 0.1}

    # read-only dBm context row
    ctx = clk["context"][0]
    assert ctx["label"] == "Readout power" and ctx["axis"] == "y" and ctx["unit"] == "dBm"

    # heatmap renders IQ_abs_norm with robust clipping → finite zmin < zmax
    hm = spec.figure["data"][0]
    assert hm["type"] == "heatmap"
    assert hm["zmin"] < hm["zmax"]


def test_vs_power_falls_back_to_iq_abs_without_norm():
    spec = resonator_2d._vs_power(_synthetic_vs_power_bundle(with_norm=False),
                                  "amplitude::q0", "q0", 0, np.array([7.0, 7.001, 7.002]))
    # Still builds a heatmap (from IQ_abs) and stays clickable.
    assert spec.figure["data"][0]["type"] == "heatmap"
    assert spec.clickable is not None


def test_vs_power_dbm_to_amp_numeric():
    # ref_dbm = -25, scale = 0.1 (= max_amp): dBm == ref ⇒ amp == max_amp.
    assert _dbm_to_amp(-25, -25.0, 0.1) == pytest.approx(0.1)
    assert _dbm_to_amp(-50, -25.0, 0.1) == pytest.approx(0.0056234, abs=1e-6)


def test_heatmap_robust_percentile_clip():
    z = np.arange(100.0).reshape(10, 10)         # 0 .. 99
    tr = plotbuild.heatmap([0, 1], [0, 1], z, robust=True)
    lo, hi = np.percentile(z, [2, 98])
    assert tr["zmin"] == pytest.approx(lo)
    assert tr["zmax"] == pytest.approx(hi)

    # explicit zmin/zmax take precedence over robust (not recomputed)
    tr2 = plotbuild.heatmap([0, 1], [0, 1], z, robust=True, zmin=0, zmax=99)
    assert tr2["zmin"] == 0 and tr2["zmax"] == 99

    # all-equal input → no clip keys (hi == lo)
    tr3 = plotbuild.heatmap([0, 1], [0, 1], np.full((4, 4), 5.0), robust=True)
    assert "zmin" not in tr3 and "zmax" not in tr3

    # all-NaN input → no clip keys, no crash
    tr4 = plotbuild.heatmap([0, 1], [0, 1], np.full((4, 4), np.nan), robust=True)
    assert "zmin" not in tr4 and "zmax" not in tr4

    # robust=False (default) → never clips
    assert "zmin" not in plotbuild.heatmap([0, 1], [0, 1], z)


# ======================================================================
# Round 4: static-PNG superset + the recipe-coverage regression guard
# ======================================================================

# Saved-figure base names the Interactive tab legitimately shows only as static
# PNGs because their data is NOT in the run's HDF5 (loaded from another run, or
# matplotlib-only). Anything else appearing as static means a recipe silently
# dropped a reconstructable figure — the bug class this guard exists to catch.
def _allowlisted_static_base(base: str) -> bool:
    # The cz_2d_maps recipe rebuilds these saved figures' content under its
    # unified "map" key — the static PNG stays as the node's exact-styling copy.
    _CZ_MAP_COVERED = {"jazz_n_amplitude", "jazz2_n_amplitude",
                       "jazz2_n_snz_scan", "snz_b_over_a", "snz_conditional_phase",
                       "coupling_figure", "decay_time_figure", "oscillation_figure",
                       "raw_fit", "leakage_figure"}
    return (base.startswith("fir_")
            or base in {"ramsey_curve", "spectroscopy_curve"}
            or base in _CZ_MAP_COVERED)


def test_saved_base_normalization():
    from quam_state_manager.core.interactive_plots.registry import _saved_base
    assert _saved_base("figures.raw_data", ["qA1"]) == "raw_data"        # drop container
    assert _saved_base("raw_qA1", ["qA1", "qA2"]) == "raw"               # drop _<qubit>
    assert _saved_base("figures.fir_corrected_qA2", ["qA1", "qA2"]) == "fir_corrected"
    assert _saved_base("figure_flux", []) == "figure_flux"               # top-level, no qubit


def test_merge_static_dedups_and_falls_back():
    from quam_state_manager.core.interactive_plots.registry import _merge_static
    from quam_state_manager.core.interactive_plots.recipes.base import FigureSpec
    specs = [
        FigureSpec(key="raw_data::qA1", title="Raw", available=True),
        FigureSpec(key="parabola_fit::qA1", title="Parabola", available=False, reason="no fit"),
    ]
    saved = ["figures.raw_data", "figures.parabola_fit", "figures.ramsey_curve"]
    out = {s.key: s.kind for s in _merge_static(specs, saved, ["qA1"])}
    assert out.get("raw_data::qA1") != "static"          # interactive kept
    assert "figures.raw_data" not in out                 # its PNG deduped away
    assert "parabola_fit::qA1" not in out                # unavailable + has PNG → replaced
    assert out.get("figures.parabola_fit") == "static"   # ...by its static PNG
    assert out.get("figures.ramsey_curve") == "static"   # no recipe spec → static


def test_merge_static_keeps_greyed_stub_without_png():
    from quam_state_manager.core.interactive_plots.registry import _merge_static
    from quam_state_manager.core.interactive_plots.recipes.base import FigureSpec
    specs = [FigureSpec(key="qubit_freq_vs_flux::qA1", title="x", available=False, reason="no fit")]
    out = _merge_static(specs, [], ["qA1"])  # no saved PNG to fall back to
    assert len(out) == 1 and out[0].kind != "static" and not out[0].available


@skip_no_data
def test_ramsey_vs_flux_parabola_fit(store):
    """The reported bug: parabola_fit was missing from the Interactive tab."""
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_23_ramsey_vs_flux_calibration")
    if run is None:
        pytest.skip("no ramsey_vs_flux run")
    menu = list_interactive_figures(run)
    bases = {m["key"].split("::")[0] for m in menu if not m.get("static")}
    assert "parabola_fit" in bases
    key = next(m["key"] for m in menu
               if m["key"].startswith("parabola_fit") and m["available"])
    fig = build_interactive_figure(run, key)
    assert fig is not None and fig["data"]
    names = {t.get("name") for t in fig["data"]}
    assert {"unfolded freq", "parabola fit"} <= names
    assert fig["layout"].get("shapes")          # flux_offset / freq_offset / Nyquist guides
    # Contract-faithful clickable (upgraded from view-only): x = flux DELTA
    # around the parked offset → the target carries the PRE-update offset baked
    # in (offset_new = offset_pre + clicked; patches-aware provenance).
    clk = fig.get("clickable")
    if clk is not None:
        t = clk["targets"][0]
        assert t["path"].endswith(("joint_offset", "independent_offset"))
        assert t["axis"] == "x" and t["scale"] == 1.0
        assert isinstance(t["offset"], float)       # baked pre-update offset
        assert t.get("provenance", {}).get("formula")
    json.dumps(fig, allow_nan=False)


@skip_no_data
def test_resonator_wide_amplitude_local_and_detrended(store):
    """Wide variants: amplitude_local was missing; detrended_phase was wrongly
    unavailable (it stores RF_frequency, not a detuning coord)."""
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_03_resonator_spectroscopy_wide")
    if run is None:
        pytest.skip("no wide resonator run")
    menu = list_interactive_figures(run)
    avail = {m["key"].split("::")[0] for m in menu if m["available"] and not m.get("static")}
    assert {"amplitude_local", "detrended_phase"} <= avail
    for base in ("amplitude_local", "detrended_phase"):
        key = next(m["key"] for m in menu if m["key"].startswith(base) and m["available"])
        fig = build_interactive_figure(run, key)
        assert fig is not None and fig["data"]
        json.dumps(fig, allow_nan=False)


@skip_no_data
def test_readout_power_opt_blobs_from_ds_iq_blobs(store):
    """1Q_15b persists IQ-blob/confusion data to ds_iq_blobs.h5 (not ds_fit)."""
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    run = _run_for(store, "1Q_15b_readout_power_optimization")
    if run is None:
        pytest.skip("no 15b run")
    menu = list_interactive_figures(run)
    avail = {m["key"].split("::")[0] for m in menu if m["available"] and not m.get("static")}
    assert {"iq_blobs", "confusion_matrix"} <= avail
    for base in ("iq_blobs", "confusion_matrix"):
        key = next(m["key"] for m in menu if m["key"].startswith(base) and m["available"])
        fig = build_interactive_figure(run, key)
        assert fig is not None and fig["data"]
        json.dumps(fig, allow_nan=False)


@skip_no_data
@pytest.mark.parametrize("prefix,swept,clickable", [
    ("1Q_30a_gef_readout_power_optimization", "amplitude", True),
    ("1Q_30_gef_readout_frequency_optimization", "frequency", False),
])
def test_gef_readout_opt_replaces_static_png(store, prefix, swept, clickable):
    """The reported bug: GEF readout-opt showed only the static fitted_distances
    PNG. Now both variants reconstruct it interactively (so the PNG is deduped),
    power is clickable → the readout-operation amplitude, frequency is view-only."""
    from quam_state_manager.core.interactive_plots import (
        build_interactive_figure, list_interactive_figures)
    from quam_state_manager.core.interactive_plots.registry import _saved_figures
    run = _run_for(store, prefix)
    if run is None:
        pytest.skip(f"no {prefix} run")
    assert "figures.fitted_distances" in _saved_figures(run)  # it IS a saved figure
    menu = list_interactive_figures(run)
    avail = {m["key"].split("::")[0] for m in menu if m["available"] and not m.get("static")}
    assert {"fitted_distances", "gef_distances"} <= avail
    # ...and the saved PNG is reproduced interactively, not shown as a static tile.
    assert "figures.fitted_distances" not in {m["key"] for m in menu if m.get("static")}

    fd = build_interactive_figure(run, next(m["key"] for m in menu
                                            if m["key"].startswith("fitted_distances")))
    assert fd is not None and fd["data"]
    json.dumps(fd, allow_nan=False)                 # strict JSON (no NaN/Inf)
    assert any(t.get("name") == "raw" for t in fd["data"])
    assert (swept == "amplitude") == ("xaxis2" in fd["layout"])  # prefactor axis: power only
    if clickable:
        paths = [t["path"] for t in fd["clickable"]["targets"]]
        assert paths == ["qubits.{q}.resonator.operations.readout_GEF.amplitude"]
    else:
        assert fd.get("clickable") is None


@skip_no_data
def test_qubit_spectroscopy_ef_resolves_not_fallback(store):
    """The 'ef' node-name spelling must resolve to a real recipe (was a
    case-sensitivity typo, E_to_F, that fell through to the empty fallback)."""
    from quam_state_manager.core.interactive_plots import list_interactive_figures
    run = _run_for(store, "1Q_28_Qubit_Spectroscopy_ef")
    if run is None:
        pytest.skip("no ef run")
    avail = [m for m in list_interactive_figures(run)
             if m["available"] and not m.get("static")]
    assert any(m["key"].startswith("amplitude") for m in avail)


@skip_no_data
def test_interactive_tab_is_superset_of_figures_tab(store):
    """Regression guard. For every node type on disk:

    (1) every saved figure appears in the Interactive tab (interactive or static) —
        a hard requirement for ALL node types, recipe or not; a saved figure that
        vanishes entirely is a real `_merge_static` bug.
    (2) for node types that HAVE an interactive recipe, no saved figure is shown
        ONLY as a static PNG when the recipe could reconstruct it — i.e. a recipe
        that silently drops a reconstructable figure fails this test.

    Node types with NO recipe yet resolve to the empty ``fallback`` and legitimately
    render every figure as a static PNG. Those are reported as a non-fatal warning
    (add a recipe to make them interactive), NOT a failure — so the live, ever-
    growing LabA dataset can sprout new experiment types without turning this guard
    red. The moment a recipe is added for such a type, clause (2) starts enforcing it.
    """
    import warnings

    from quam_state_manager.core.interactive_plots import list_interactive_figures
    from quam_state_manager.core.interactive_plots.recipes import fallback
    from quam_state_manager.core.interactive_plots.registry import (
        _resolve, _saved_base, _saved_figures)

    one_per_type = {}
    for rid in sorted(store.runs):
        run = store.runs[rid]
        one_per_type.setdefault(run.experiment_name, run)

    missing, dropped, uncovered = [], [], []
    for name, run in one_per_type.items():
        has_recipe = _resolve(name) is not fallback
        menu = list_interactive_figures(run)
        qnames = [str(q) for q in (run.qubits or [])]
        avail_bases = {m["key"].split("::")[0] for m in menu
                       if m["available"] and not m.get("static")}
        static_keys = {m["key"] for m in menu if m.get("static")}
        for fig in _saved_figures(run):
            base = _saved_base(fig, qnames)
            if base not in avail_bases and fig not in static_keys:
                missing.append(f"{name}:{fig}")
            elif fig in static_keys and not _allowlisted_static_base(base):
                # A recipe-covered type leaving a figure static == a dropped
                # reconstructable figure (the bug this guards). No recipe == merely
                # uncovered (expected) → warn instead of fail.
                (dropped if has_recipe else uncovered).append(f"{name}:{fig} (base={base})")
    assert not missing, "Saved figures absent from the Interactive tab: " + ", ".join(missing)
    assert not dropped, (
        "Recipe-covered node types show a reconstructable figure only as a static PNG "
        "(a recipe dropped it, or it needs an allowlist entry): " + ", ".join(dropped))
    if uncovered:
        types = sorted({u.split(":", 1)[0] for u in uncovered})
        warnings.warn(
            f"{len(uncovered)} saved figure(s) across {len(types)} node type(s) with no "
            f"interactive recipe are shown as static PNGs (expected — add a recipe to make "
            f"them interactive): {', '.join(types)}", stacklevel=2)


@skip_no_data
def test_dataset_detail_stamps_run_chip_token(store):
    """Regression: get_run() returns a DICT, so the old attribute-style
    ``getattr(run, "has_quam_state", False)`` was always False and every run's
    data-chip-token rendered empty — silently disabling the cross-chip 409
    gate for Interactive/Results applies (found by real-browser E2E)."""
    from quam_state_manager.web.app import create_app
    run = next((store.runs[rid] for rid in sorted(store.runs)
                if store.runs[rid].has_quam_state), None)
    if run is None:
        pytest.skip("no run with quam_state")
    app = create_app()
    app.config["dataset_store"] = store
    client = app.test_client()
    from quam_state_manager.web import routes as _r
    uid = _r._dataset_uid(_r._folder_key(store.folder_path), run.run_id)
    r = client.get(f"/dataset/{uid}", headers={"HX-Request": "true"})
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    import re as _re
    m = _re.search(r'data-chip-token="([^"]*)"', html)
    assert m, "detail page lost the data-chip-token attribute"
    assert m.group(1), "run has quam_state but data-chip-token rendered empty"
