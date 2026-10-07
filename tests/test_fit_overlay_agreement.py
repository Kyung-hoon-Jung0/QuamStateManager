"""The Interactive tab's fit overlays draw the node's own curve -- or say why not (docs/300).

Fixtures under ``tests/fixtures/fit_overlays/`` are single-target slices of real
archived runs (renamed to ``q0`` / ``q0-q1``, root attributes and free-text
results stripped). Each test builds the figure through SM's own registry, the
same path the Datasets > Interactive tab takes, and scores every drawn fit
trace against the data trace it overlays.

Two layers are pinned:

* agreement -- wherever the node accepted its fit, the drawn curve explains the
  data (R^2 above a per-family floor measured on the real run), and a growing,
  inverted or phase-shifted curve fails that check (``TestTheCheckBites``);
* honest absence -- where the curve cannot be the node's (parameters the node
  did not save, a fit the node rejected, a reconstruction that does not
  reproduce a number the node stored), no curve is drawn and the figure says
  why.
"""
from __future__ import annotations

import json
import math
import types
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.interactive_plots import fitcheck, models, registry
from quam_state_manager.core.scanner import node_parameters

FIX = Path(__file__).parent / "fixtures" / "fit_overlays"


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------

def _run(name):
    d = FIX / name
    node = json.loads((d / "node.json").read_text(encoding="utf-8"))
    fr = json.loads((d / "data.json").read_text(encoding="utf-8")).get("fit_results") or {}
    return types.SimpleNamespace(folder_path=str(d), experiment_name=node["metadata"]["name"],
                                 run_id=1, fit_results=fr,
                                 parameters=node_parameters(node.get("data", {})))


def _figures(name, base=None):
    run = _run(name)
    registry._bundle_cache.clear()
    out = {}
    for m in registry.list_interactive_figures(run):
        if m.get("static") or not m.get("available"):
            continue
        if base and not m["key"].startswith(base):
            continue
        out[m["key"]] = registry.build_interactive_figure(run, m["key"])
    assert out, f"{name}: no interactive figure built"
    return out


def _arr(v):
    return np.array([np.nan if e is None else e for e in (v or [])], dtype=float)


def _is_fit(t):
    n = str(t.get("name") or "")
    return (n.startswith("fit") or n.startswith("circle fit")) and "extrapolated" not in n


def _fit_traces(fig):
    return [t for t in fig["data"] if _is_fit(t)]


def _notes(fig):
    return [a["text"].replace("<br>", " ")
            for a in (fig["layout"].get("annotations") or []) if a.get("name") == "fit-note"]


def _r2(y, f, wrap=False):
    m = np.isfinite(y) & np.isfinite(f)
    y, f = y[m], f[m]
    if y.size < 5:
        return float("nan")
    res = (y - f + 0.5) % 1.0 - 0.5 if wrap else (y - f)
    return 1.0 - float(np.sum(res ** 2)) / float(np.sum((y - y.mean()) ** 2))


def agreement(fig):
    """``[(fit name, R^2 against its data trace)]`` for every drawn fit trace.

    Pairing: a fit named ``fit <tail>`` goes with the data trace ending in
    ``<tail>``, else any data trace on the same axes. The fit is compared on
    the data points it covers: the same grid, a sub-window of it (curves drawn
    on the fit window only), or interpolated from its own dense grid.
    Conditional phases (y in units of 2 pi) are compared modulo one turn.
    """
    ytitle = str(((fig["layout"].get("yaxis") or {}).get("title") or {}).get("text") or "")
    wrap = "2π" in ytitle
    out = []
    for ft in _fit_traces(fig):
        fx, fy = _arr(ft.get("x")), _arr(ft.get("y"))
        axes = (ft.get("xaxis") or "x", ft.get("yaxis") or "y")
        name = str(ft.get("name"))
        tail = name[3:].strip() if name.startswith("fit") else ""
        cands = [t for t in fig["data"] if not _is_fit(t)
                 and "extrapolated" not in str(t.get("name") or "")
                 and (t.get("xaxis") or "x", t.get("yaxis") or "y") == axes
                 and (t.get("line") or {}).get("color") != "rgba(0,0,0,0)"
                 and t.get("y") is not None]
        named = [t for t in cands if tail and str(t.get("name") or "").endswith(tail)]
        score = float("nan")
        for t in (named or cands):
            dx, dy = _arr(t.get("x")), _arr(t.get("y"))
            if dx.size == fx.size and np.allclose(dx, fx, equal_nan=True):
                score = _r2(dy, fy, wrap)
                break
            idx = {float(v): i for i, v in enumerate(dx) if np.isfinite(v)}
            if fx.size >= 5 and all(float(v) in idx for v in fx if np.isfinite(v)):
                sel = [idx[float(v)] for v in fx if np.isfinite(v)]
                score = _r2(dy[sel], fy[np.isfinite(fx)], wrap)
                break
            ok = np.isfinite(fx) & np.isfinite(fy)
            if ok.sum() >= 20 and np.all(np.diff(fx[ok]) > 0):
                inside = np.isfinite(dx) & (dx >= fx[ok][0]) & (dx <= fx[ok][-1])
                if inside.sum() >= 5:
                    score = _r2(dy[inside], np.interp(dx[inside], fx[ok], fy[ok]), wrap)
                    break
        out.append((name, score))
    return out


def assert_agrees(fig, floor):
    scores = agreement(fig)
    assert scores, "no fit curve drawn"
    for name, s in scores:
        assert np.isfinite(s) and s >= floor, f"{name}: R^2 {s:.3f} < {floor}"
    return scores


# ----------------------------------------------------------------------------
# agreement where the node accepted its fit (floors measured on the real runs)
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("fixture,floor", [
    ("ramsey_checked", 0.95),            # stored fit_r2 + t2_star reproduced exactly
    ("ramsey_state_unchecked", 0.65),    # state readout, older generation (no stored check)
    ("qspec_popt", 0.85),                # peak on the node's own fit window
    ("qspec_popt_no_window", 0.85),      # window not saved -> the node figure's fallback window
    ("rabi_line_snr", 0.9),              # cosine; stored osc_amp_snr reproduced
    ("rabi_line_no_snr", 0.9),
    ("rb1q_decay_exp", 0.95),            # fit_vals a/offset/decay by label
    ("rb1q_power_law", 0.9),             # param p/A/B by label
    ("rb2q_stored_fit", 0.95),           # the node's stored A, alpha, B
    ("cz_phase_accepted", 0.85),         # stored curve, compared modulo one turn
    ("flux_long_qubitspec", 0.95),       # plain multi-exponential
    ("flux_long_ramsey_global", 0.95),   # finite-pulse multi-exponential
])
def test_the_drawn_fit_explains_the_data(fixture, floor):
    figs = _figures(fixture)
    with_fit = {k: f for k, f in figs.items() if _fit_traces(f)}
    assert with_fit, f"{fixture}: no figure draws the node's fit"
    for key, fig in with_fit.items():
        assert_agrees(fig, floor)
    for key, fig in figs.items():
        assert not any("withheld" in n for n in _notes(fig)), key


def test_resonator_draws_the_saved_circle_fit_and_no_lorentzian_curve():
    fig = _figures("resonator_circle_fit", "amplitude::")["amplitude::q0"]
    names = [t.get("name") for t in fig["data"]]
    assert "fit" not in names                       # the Lorentzian curve is not redrawn
    assert "circle fit" in names and "circle fit (extrapolated)" in names
    for name, s in agreement(fig):
        assert s >= 0.99, (name, s)                 # the node's model, stored verbatim
    assert any("Lorentzian" in n for n in _notes(fig))
    # f0 marker + FWHM band stay: they are exact
    kinds = {s["type"] for s in fig["layout"]["shapes"]}
    assert {"line", "rect"} <= kinds


def test_a_runaway_branch_is_drawn_but_the_axis_stays_on_the_data():
    fig = _figures("ramsey_runaway_branch")["amplitude::q0"]
    fits = _fit_traces(fig)
    assert len(fits) == 2                           # the node's curves, both branches
    assert any("decay rate ≤ 0" in n for n in _notes(fig))
    data = [_arr(t["y"]) for t in fig["data"] if not _is_fit(t)]
    lo = min(np.nanmin(d) for d in data)
    hi = max(np.nanmax(d) for d in data)
    yr = fig["layout"]["yaxis"]["range"]
    assert yr[0] < lo and yr[1] > hi and (yr[1] - yr[0]) < 1.5 * (hi - lo)
    good = [s for _, s in agreement(fig) if s > 0.5]
    assert good and good[0] > 0.95                  # the decaying branch still matches


def test_legacy_peak_finder_overlay_is_the_legacy_figures_curve():
    """No least-squares fit exists in this generation: the overlay is exactly the
    legacy figure's peak estimate (baseline mean + amplitude, half width = width/2)."""
    run = _run("qspec_peak_finder")
    fig = _figures("qspec_peak_finder")["amplitude::q0"]
    (fit,) = _fit_traces(fig)
    assert fit["name"] == "fit (peak estimate)"
    import h5py
    with h5py.File(FIX / "qspec_peak_finder" / "ds_fit.h5", "r") as h:
        amp, pos, wid = (float(np.ravel(h[v][()])[0]) for v in ("amplitude", "position", "width"))
        base = float(np.nanmean(h["base_line"][()]))
    with h5py.File(FIX / "qspec_peak_finder" / "ds_raw.h5", "r") as h:
        det = np.asarray(h["detuning"][()], dtype=float)
    expected = models.lorentzian_peak(det, amp, pos, wid / 2.0, base) * 1e3
    assert np.allclose(_arr(fit["y"]), expected)
    y = _arr(fit["y"])
    assert det[int(np.nanargmax(y))] == pytest.approx(pos, abs=abs(det[1] - det[0]))   # a peak at the position
    assert run.experiment_name


# ----------------------------------------------------------------------------
# honest absence
# ----------------------------------------------------------------------------

@pytest.mark.parametrize("fixture,key,phrase", [
    ("qspec_peak_finder_dip", "amplitude::q0", "located a dip"),
    ("rb2q_alpha_only", "survival::q0-q1", "only α"),
    ("cz_phase_rejected", None, "marked this fit as failed"),
])
def test_no_curve_and_a_reason_where_the_curve_cannot_be_the_nodes(fixture, key, phrase):
    figs = _figures(fixture)
    fig = figs[key] if key else next(f for f in figs.values() if _notes(f))
    assert not _fit_traces(fig)
    assert any(phrase in n for n in _notes(fig)), _notes(fig)


def test_an_alpha_only_rb_tile_keeps_the_nodes_saved_figure_visible():
    keys = [m["key"] for m in registry.list_interactive_figures(_run("rb2q_alpha_only"))]
    assert "survival::q0-q1" in keys and not any(k.startswith("fig_") and "::" in k for k in keys)


def test_legacy_long_ramsey_fitter_gets_the_plain_model():
    """The sequential fitter (results carry ``optimized_fractions``) fitted the plain
    step model; the stored RMS error is reproduced by it, not by the finite-pulse one."""
    run = _run("flux_long_ramsey_legacy")
    res = run.fit_results["q0"]
    assert "optimized_fractions" in res
    fig = _figures("flux_long_ramsey_legacy", "fitted_data")["fitted_data::q0"]
    fits = _fit_traces(fig)
    assert len(fits) == 2                           # linear and log-time panels
    import h5py
    with h5py.File(FIX / "flux_long_ramsey_legacy" / "ds_fit.h5", "r") as h:
        t = np.asarray(h["time"][()], dtype=float)
    comps = [tuple(map(float, c)) for c in res["a_tau_tuple"]]
    plain = models.multiexp_decay(t, res["a_dc"], comps)
    for fit in fits:
        assert np.allclose(_arr(fit["y"]), plain)
    assert not _notes(fig)


# ----------------------------------------------------------------------------
# the check is not vacuous: wrong curves fail it, and the per-run gates fire
# ----------------------------------------------------------------------------

class TestTheCheckBites:
    def _ramsey_params(self):
        import h5py
        with h5py.File(FIX / "ramsey_checked" / "ds_fit.h5", "r") as h:
            labels = [x.decode() if isinstance(x, bytes) else str(x) for x in h["fit_vals"][()]]
            arr = np.asarray(h["fit"][()], dtype=float)[0]           # [sign, fit_vals]
        return [dict(zip(labels, row)) for row in arr]

    def test_a_growing_ramsey_envelope_fails(self):
        fig = _figures("ramsey_checked")["amplitude::q0"]
        fits = _fit_traces(fig)
        params = self._ramsey_params()
        assert {p["decay"] > 0 for p in params} == {True}
        t = _arr(fits[0]["x"])
        for ft in fits:
            sgn = ft["name"].split("=")[-1]
            p = params[0] if sgn.startswith("-") else params[1]
            ft["y"] = list(1e3 * (p["a"] * np.exp(+t * p["decay"])
                                  * np.cos(2 * np.pi * p["f"] * t + p["phi"]) + p["offset"]))
        with pytest.raises(AssertionError):
            assert_agrees(fig, 0.95)

    def test_an_inverted_qubit_spectroscopy_peak_fails(self):
        fig = _figures("qspec_popt")["amplitude::q0"]
        (fit,) = _fit_traces(fig)
        y = _arr(fit["y"])
        base = np.median(y)
        fit["y"] = list(2 * base - y)               # the same Lorentzian drawn as a dip
        with pytest.raises(AssertionError):
            assert_agrees(fig, 0.85)

    def test_a_quarter_period_rabi_shift_fails(self):
        fig = _figures("rabi_line_snr")["amplitude::q0"]
        (fit,) = _fit_traces(fig)
        y = _arr(fit["y"])
        mid = 0.5 * (np.nanmax(y) + np.nanmin(y))
        n = y.size
        fit["y"] = list(np.roll(y - mid, n // 4) + mid)
        with pytest.raises(AssertionError):
            assert_agrees(fig, 0.9)

    def test_the_stored_r2_gate_withholds_a_wrong_ramsey_convention(self, monkeypatch):
        def growing(t, a, f, phi, offset, decay):
            t = np.asarray(t, dtype=float)
            return a * np.exp(t * decay) * np.cos(2 * np.pi * f * t + phi) + offset
        monkeypatch.setattr(models, "oscillation_decay_exp", growing)
        fig = _figures("ramsey_checked")["amplitude::q0"]
        assert not _fit_traces(fig)
        assert any("withheld" in n for n in _notes(fig))

    def test_the_stored_snr_gate_withholds_a_sine(self, monkeypatch):
        def sine(t, a, f, phi, offset):
            return a * np.sin(2 * np.pi * f * np.asarray(t, dtype=float) + phi) + offset
        monkeypatch.setattr(models, "oscillation", sine)
        fig = _figures("rabi_line_snr")["amplitude::q0"]
        assert not _fit_traces(fig)
        assert any("withheld" in n for n in _notes(fig))

    def test_the_stored_rms_gate_withholds_the_wrong_long_distortion_model(self, monkeypatch):
        real = models.multiexp_finite_pulse
        monkeypatch.setattr(models, "multiexp_decay",
                            lambda t, a_dc, comps: real(t, a_dc, comps, 50.0))
        fig = _figures("flux_long_ramsey_legacy", "fitted_data")["fitted_data::q0"]
        assert not _fit_traces(fig)
        assert any("withheld" in n for n in _notes(fig))


# ----------------------------------------------------------------------------
# synthetic edges no archived run reaches
# ----------------------------------------------------------------------------

def _ramsey_bundle(fit_signs, extra_fit=None):
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    t = np.linspace(0, 4000, 81)
    signs = [-1, 1]
    p = {-1: dict(a=1e-3, f=1e-3, phi=0.0, offset=2e-3, decay=5e-4),
         1: dict(a=1e-3, f=2e-3, phi=0.5, offset=2e-3, decay=2.5e-4)}
    y = np.stack([models.oscillation_decay_exp(t, **p[s]) for s in signs], axis=-1)   # [t, sign]
    labels = ["a", "f", "phi", "offset", "decay"]
    farr = np.array([[p[s][k] for k in labels] for s in fit_signs])[None]
    raw = {"vars": {"I": y[None]}, "coords": {"qubit": ["q0"], "idle_time": list(t),
                                               "detuning_signs": signs},
           "dim_order": {"I": ["qubit", "idle_time", "detuning_signs"]}}
    fit = {"vars": {"fit": farr}, "coords": {"qubit": ["q0"], "detuning_signs": fit_signs,
                                              "fit_vals": labels},
           "dim_order": {"fit": ["qubit", "detuning_signs", "fit_vals"]}}
    for k, (arr, dims) in (extra_fit or {}).items():
        fit["vars"][k] = arr
        fit["dim_order"][k] = dims
    return Bundle(run=None, raw=raw, fit=fit, raw_vars={"I"}, fit_vars=set(fit["vars"])), t, y


def test_ramsey_pairs_fit_rows_with_data_by_sign_not_position():
    from quam_state_manager.core.interactive_plots.recipes import ramsey
    bundle, _t, _y = _ramsey_bundle(fit_signs=[1, -1])        # ds_fit rows in the other order
    spec = ramsey.build(bundle, "amplitude::q0")
    for name, s in agreement(spec.figure):
        assert s > 0.999, (name, s)


def test_ramsey_mixed_envelope_draws_the_stored_curve():
    from quam_state_manager.core.interactive_plots.recipes import ramsey
    t = np.linspace(0, 4000, 81)
    stored = np.full((1, 2, t.size), 2e-3)                  # a recognisable flat "stored" curve
    bundle, _t, _y = _ramsey_bundle(
        fit_signs=[-1, 1],
        extra_fit={"fit_curve": (stored, ["qubit", "detuning_signs", "idle_time"]),
                   "used_mixed": (np.array([True]), ["qubit"])})
    spec = ramsey.build(bundle, "amplitude::q0")
    fits = _fit_traces(spec.figure)
    assert fits and all(np.allclose(_arr(f["y"]), 2.0) for f in fits)
    assert any("mixed-envelope" in n for n in _notes(spec.figure))


def test_fitcheck_tolerances_scale_for_runaway_r2():
    assert fitcheck.agrees_abs(-1.3e17 * (1 + 1e-12), -1.3e17, fitcheck.R2_ABS_TOL)
    assert not fitcheck.agrees_abs(0.9, 0.95, fitcheck.R2_ABS_TOL)
    assert fitcheck.agrees_rel(1.0 + 1e-6, 1.0, fitcheck.T2_REL_TOL)
    assert not fitcheck.agrees_rel(float("nan"), 1.0, fitcheck.T2_REL_TOL)
    assert math.isnan(fitcheck.r2([1, 2], [1, 2]))


def test_fixtures_carry_only_generic_target_names():
    import h5py
    for d in FIX.iterdir():
        for f in d.glob("*.h5"):
            with h5py.File(f, "r") as h:
                for dim in ("qubit", "qubit_pair"):
                    if dim in h:
                        names = {x.decode() if isinstance(x, bytes) else str(x) for x in h[dim][()]}
                        assert names <= {"q0", "q0-q1"}, (f, names)
        fr = json.loads((d / "data.json").read_text(encoding="utf-8")).get("fit_results") or {}
        assert set(fr) <= {"q0", "q0-q1"}, d


def test_qubit_spectroscopy_withholds_a_peak_curve_over_a_dip():
    """If a run's popt describes a peak but its data is a dip, the reconstruction
    cannot be that run's fit (a future dip-model generation): withhold, never flip."""
    from quam_state_manager.core.interactive_plots.recipes import qubit_spectroscopy as qs
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    det = np.linspace(-10e6, 10e6, 201)
    popt = np.array([0.0, 2e6, 1e-3, 5e-3, 0.0])
    peak = models.lorentzian_peak_linbg(det, *popt)
    dip = 2 * popt[3] - peak
    raw = {"vars": {"full_freq": (5e9 + det)[None]}, "coords": {"qubit": ["q0"], "detuning": list(det)},
           "dim_order": {"full_freq": ["qubit", "detuning"]}}

    def bundle(y):
        fit = {"vars": {"I_rot": y[None], "popt": popt[None], "f0": np.array([0.0]),
                        "fit_window_half_hz": np.array([8e6])},
               "coords": {"qubit": ["q0"], "detuning": list(det), "param": [0, 1, 2, 3, 4]},
               "dim_order": {"I_rot": ["qubit", "detuning"], "popt": ["qubit", "param"],
                             "f0": ["qubit"], "fit_window_half_hz": ["qubit"]}}
        return Bundle(run=None, node_meta={"metadata": {"name": "08_qubit_spectroscopy"}},
                      raw=raw, fit=fit, raw_vars={"full_freq"}, fit_vars=set(fit["vars"]))

    good = qs.build(bundle(peak), "amplitude::q0")
    assert [s for _, s in agreement(good.figure)][0] > 0.999
    bad = qs.build(bundle(dip), "amplitude::q0")
    assert not _fit_traces(bad.figure)
    assert any("dip" in n for n in _notes(bad.figure))


def test_two_qubit_rb_withholds_parameters_that_do_not_reproduce_the_stored_curve():
    from quam_state_manager.core.interactive_plots.recipes import two_qubit_rb as rb
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    depths = np.array([0, 1, 2, 4, 8, 16, 32], dtype=float)
    A, alpha, B = 0.7, 0.9, 0.25
    state = np.zeros((1, 10, depths.size, 50), dtype=int)
    raw = {"vars": {"state": state}, "coords": {"qubit_pair": ["q0-q1"], "sequence": list(range(10)),
                                                "circuit_depth": list(depths), "shots": list(range(50))},
           "dim_order": {"state": ["qubit_pair", "sequence", "circuit_depth", "shots"]}}

    def bundle(stored_curve):
        fit = {"vars": {"fit_amplitude": np.array([A]), "fit_alpha": np.array([alpha]),
                        "fit_offset": np.array([B]), "success": np.array([True]),
                        "fitted_curve": stored_curve[None]},
               "coords": {"qubit_pair": ["q0-q1"], "circuit_depth": list(depths)},
               "dim_order": {"fit_amplitude": ["qubit_pair"], "fit_alpha": ["qubit_pair"],
                             "fit_offset": ["qubit_pair"], "success": ["qubit_pair"],
                             "fitted_curve": ["qubit_pair", "circuit_depth"]}}
        return Bundle(run=None, raw=raw, fit=fit, raw_vars={"state"}, fit_vars=set(fit["vars"]))

    ok = rb.build(bundle(A * alpha ** depths + B), "fig_q0-q1::q0-q1")
    assert _fit_traces(ok.figure)
    off = rb.build(bundle(A * alpha ** depths + B + 0.05), "fig_q0-q1::q0-q1")
    assert not _fit_traces(off.figure)
    assert any("withheld" in n for n in _notes(off.figure))


def test_a_circle_fit_for_another_qubit_is_never_drawn():
    from quam_state_manager.core.interactive_plots.recipes import resonator
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    f = np.linspace(7e9, 7.01e9, 50)
    port = {"vars": {"z_fit_real": np.ones((1, 50)), "z_fit_imag": np.zeros((1, 50)),
                     "freq_hz": f[None]},
            "coords": {"qubit": ["q9"], "freq_index": list(range(50))},
            "dim_order": {"z_fit_real": ["qubit", "freq_index"], "z_fit_imag": ["qubit", "freq_index"],
                          "freq_hz": ["qubit", "freq_index"]}}
    assert resonator._circle_fit_traces(Bundle(run=None, port_fit=port), "q0") == []
    assert resonator._circle_fit_traces(Bundle(run=None, port_fit=port), "q9")


def test_qubit_spectroscopy_background_is_referenced_to_the_fit_window():
    """popt's linear background is referenced to the mean of the window the node
    fitted on. Evaluated with the whole sweep's mean instead, an off-centre line
    with a sloped background is shifted by bg1 * (window mean - sweep mean)."""
    from quam_state_manager.core.interactive_plots.recipes import qubit_spectroscopy as qs
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    det = np.linspace(-20e6, 20e6, 401)
    f0, half = 12e6, 5e6
    popt = np.array([f0, 1e6, 1e-3, 5e-3, 2e-10])           # 2e-10 V/Hz * 12 MHz = 2.4 mV shift
    win = np.abs(det - f0) <= half
    y = np.full(det.shape, np.nan)
    y[win] = models.lorentzian_peak_linbg(det[win], *popt)  # the node's curve on its window
    y[~win] = popt[3] + popt[4] * (det[~win] - det[win].mean())
    raw = {"vars": {"full_freq": (5e9 + det)[None]}, "coords": {"qubit": ["q0"], "detuning": list(det)},
           "dim_order": {"full_freq": ["qubit", "detuning"]}}
    fit = {"vars": {"I_rot": y[None], "popt": popt[None], "f0": np.array([f0]),
                    "fit_window_half_hz": np.array([half])},
           "coords": {"qubit": ["q0"], "detuning": list(det), "param": [0, 1, 2, 3, 4]},
           "dim_order": {"I_rot": ["qubit", "detuning"], "popt": ["qubit", "param"],
                         "f0": ["qubit"], "fit_window_half_hz": ["qubit"]}}
    spec = qs.build(Bundle(run=None, node_meta={"metadata": {"name": "08_qubit_spectroscopy"}},
                           raw=raw, fit=fit, raw_vars={"full_freq"}, fit_vars=set(fit["vars"])),
                    "amplitude::q0")
    (drawn,) = _fit_traces(spec.figure)
    assert np.allclose(_arr(drawn["y"]), y[win] * 1e3)
    assert np.allclose(_arr(drawn["x"]), (5e9 + det[win]) / 1e9)


def test_a_runaway_fit_keeps_the_axis_on_the_data_rb_and_flux():
    from quam_state_manager.core.interactive_plots.recipes import flux_common as fc
    from quam_state_manager.core.interactive_plots.recipes import rb
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    depths = np.array([1, 2, 4, 8, 16, 32, 64, 128, 256], dtype=float)
    y = 0.5 + 0.45 * 0.99 ** depths
    fit = {"vars": {"data_mean": y[None], "fit_data": np.array([[1.2, 0.45, 0.5]])},
           "coords": {"qubit": ["q0"], "depths": list(depths), "param": ["p", "A", "B"]},
           "dim_order": {"data_mean": ["qubit", "depths"], "fit_data": ["qubit", "param"]}}
    spec = rb.build(Bundle(run=None, fit=fit, fit_vars=set(fit["vars"])), "amplitude::q0")
    assert _fit_traces(spec.figure)                      # still the node's curve
    rng = spec.figure["layout"]["yaxis"]["range"]
    assert rng[0] < y.min() and rng[1] > y.max() and rng[1] - rng[0] < 2 * (y.max() - y.min())
    assert any("runs away" in n for n in _notes(spec.figure))
    t = np.arange(1.0, 50.0)
    fig = fc.fitted_two_panel(t, np.exp(-t / 10), 1e6 * np.exp(-t))
    assert fig["layout"]["yaxis"]["range"][1] < 2 and fig["layout"]["yaxis2"]["range"][1] < 2
    calm = fc.fitted_two_panel(t, np.exp(-t / 10), np.exp(-t / 10))
    assert "range" not in calm["layout"]["yaxis"]


def test_ramsey_fit_is_drawn_finer_than_the_data_so_it_can_be_seen():
    """docs/301: on the data's own points (about six per period) the fit was a
    polyline through the same places as the data line -- same colour, same
    width -- and could not be told apart. The data are points; the fit is a
    curve on a fine grid that still agrees with them."""
    from quam_state_manager.core.interactive_plots.recipes import ramsey
    bundle, t, _y = _ramsey_bundle(fit_signs=[-1, 1])
    spec = ramsey.build(bundle, "amplitude::q0")
    data = [tr for tr in spec.figure["data"] if not _is_fit(tr)]
    fits = _fit_traces(spec.figure)
    assert data and all(tr["mode"] == "markers" for tr in data)
    assert fits and all(len(f["x"]) >= 10 * t.size for f in fits)
    for name, s in agreement(spec.figure):
        assert s > 0.999, (name, s)
