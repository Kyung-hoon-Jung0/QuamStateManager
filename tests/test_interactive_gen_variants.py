"""Interactive figures for node generations that name their variables differently.

* A chevron run that names its qubits by ROLE (``state_moving`` /
  ``state_stationary`` over amplitude x time, or ``state_mover`` /
  ``state_static`` over ``flux_dv`` x ``pulse_duration`` with a ``prep`` axis)
  draws both heatmaps instead of "Not drawn: no state_target".
* A CZ phase-compensation run numbered 34a is routed to the raw-and-fit figure
  (not the conditional-phase one that "lacks phase_diff"), and a run that saved
  raw quadratures draws the ``I_<role>`` entry of its ``control_target`` axis
  the fit belongs to.
* The Interactive tab says nothing about a QM environment while SM draws a
  figure of its own; when nothing is drawn it says what the user gets without one.

Shapes copy the real runs' variable names and dimensions only; every value
here is synthetic and every name generic.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

h5py = pytest.importorskip("h5py")

PAIR = "qA1-qA2"


def _write_h5(path: Path, arrays: dict, coords: dict):
    with h5py.File(path, "w") as f:
        scales = {}
        for cname, values in coords.items():
            d = f.create_dataset(cname, data=np.asarray(values))
            d.attrs["CLASS"] = np.bytes_("DIMENSION_SCALE")
            scales[cname] = d
        for vname, (dims, data) in arrays.items():
            ds = f.create_dataset(vname, data=np.asarray(data))
            for ax, dim in enumerate(dims):
                if dim in scales:
                    ds.dims[ax].attach_scale(scales[dim])


def _run(tmp_path, node, fit, coords, fit_results, figures, raw=None):
    folder = tmp_path / "2026-07-03" / f"#900_{node}_120000"
    folder.mkdir(parents=True)
    (folder / "node.json").write_text(json.dumps({
        "id": 900, "parents": [], "created_at": "2026-07-03T12:00:00",
        "metadata": {"name": node, "status": "successful",
                     "run_start": "2026-07-03T12:00:00",
                     "run_end": "2026-07-03T12:00:30"},
        "data": {"parameters": {"model": {"qubit_pairs": [PAIR]}}, "outcomes": {}},
    }))
    (folder / "data.json").write_text(json.dumps({
        "fit_results": {PAIR: fit_results},
        "figures": {k: f"./figures.{k}.png" for k in figures}}))
    _write_h5(folder / "ds_fit.h5", fit, coords)
    _write_h5(folder / "ds_raw.h5", raw if raw is not None else fit, coords)
    from quam_state_manager.core.dataset import DatasetStore
    store = DatasetStore(tmp_path)
    return next(iter(store.runs.values()))


def _menu(run):
    from quam_state_manager.core.interactive_plots import list_interactive_figures
    return {m["key"]: m for m in list_interactive_figures(run)}


def _build(run, key):
    from quam_state_manager.core.interactive_plots import build_interactive_figure
    return build_interactive_figure(run, key)


# ----- chevron, role-named --------------------------------------------------

def test_role_named_chevron_draws_moving_and_stationary(tmp_path):
    na, nt = 6, 9
    amp = np.linspace(0.9, 1.1, na)
    t = np.arange(16, 16 + nt)
    mov = np.random.default_rng(1).uniform(0, 1, (1, na, nt))
    run = _run(tmp_path, "31_chevron_11_20",
               {"state_moving": (("qubit_pair", "amplitude", "time"), mov),
                "state_stationary": (("qubit_pair", "amplitude", "time"), 1 - mov),
                "amp_full": (("qubit_pair", "amplitude"), (amp * 0.15).reshape(1, -1)),
                "cz_len": (("qubit_pair",), [20]), "cz_amp": (("qubit_pair",), [0.15])},
               {"qubit_pair": [PAIR.encode()], "amplitude": amp, "time": t},
               {"success": True, "cz_len": 20, "cz_amp": 0.15},
               ["moving_qubit", "stationary_qubit"])
    menu = _menu(run)
    mk, sk = f"moving_qubit::{PAIR}", f"stationary_qubit::{PAIR}"
    assert menu[mk]["available"] and menu[sk]["available"]
    # the saved PNGs of the same names are covered by the drawn tiles
    assert not any(m["static"] for m in menu.values())
    fig = _build(run, mk)
    hm = fig["data"][0]
    assert hm["type"] == "heatmap" and np.array(hm["z"]).shape == (na, nt)
    assert np.allclose(np.array(hm["z"], dtype=float), mov[0])
    assert np.allclose(hm["y"], amp * 0.15)          # amp_full [V]
    assert fig["layout"]["yaxis"]["title"]["text"] == "Flux pulse amplitude [V]"
    assert any(t.get("name") == "fitted" for t in fig["data"])
    # view-only: the first generation's macro paths are not proven for this one
    assert fig.get("clickable") is None
    assert np.allclose(np.array(_build(run, sk)["data"][0]["z"], dtype=float), 1 - mov[0])


def test_role_named_chevron_failed_fit_has_no_star_and_says_so(tmp_path):
    amp = np.linspace(0.9, 1.1, 4)
    t = np.arange(16, 21)
    run = _run(tmp_path, "31_chevron_11_20",
               {"state_moving": (("qubit_pair", "amplitude", "time"), np.zeros((1, 4, 5))),
                "state_stationary": (("qubit_pair", "amplitude", "time"), np.zeros((1, 4, 5))),
                "cz_len": (("qubit_pair",), [18]), "cz_amp": (("qubit_pair",), [1.0])},
               {"qubit_pair": [PAIR.encode()], "amplitude": amp, "time": t},
               {"success": False}, ["moving_qubit"])
    fig = _build(run, f"moving_qubit::{PAIR}")
    assert not any(t.get("name") == "fitted" for t in fig["data"])
    assert "failed" in fig["layout"]["annotations"][0]["text"]


def test_mover_static_generation_slices_prep_and_uses_flux_dv(tmp_path):
    nv, nd = 5, 7
    dv = np.linspace(0.1, 0.2, nv)
    dur = np.arange(10, 10 + nd)
    mover = np.random.default_rng(2).uniform(0, 1, (1, 1, nv, nd))
    run = _run(tmp_path, "31_chevron_11_20",
               {"state_mover": (("qubit_pair", "prep", "flux_dv", "pulse_duration"), mover),
                "state_static": (("qubit_pair", "prep", "flux_dv", "pulse_duration"), 1 - mover)},
               {"qubit_pair": [PAIR.encode()], "prep": [b"11"], "flux_dv": dv,
                "pulse_duration": dur},
               {"success": True}, ["raw"])
    menu = _menu(run)
    assert menu[f"moving_qubit::{PAIR}"]["available"]
    fig = _build(run, f"moving_qubit::{PAIR}")
    hm = fig["data"][0]
    assert np.array(hm["z"]).shape == (nv, nd)
    assert np.allclose(np.array(hm["z"], dtype=float), mover[0, 0])
    assert np.allclose(hm["y"], dv) and np.allclose(hm["x"], dur)
    assert fig["layout"]["yaxis"]["title"]["text"] == "Mover flux excursion [V]"


def test_chevron_with_neither_generation_names_every_variable_it_looked_for():
    from quam_state_manager.core.interactive_plots.recipes import chevron
    from quam_state_manager.core.interactive_plots.recipes.base import Bundle
    b = Bundle(run=None, node_meta={}, fit_results={PAIR: {}},
               fit_vars={"amp_full"}, raw_vars=set())
    reasons = {s.key.split("::")[0]: s.reason for s in chevron.menu(b)}
    assert reasons["amplitude"] == "no state_target / state_moving / state_mover"
    assert reasons["state_control"] == "no state_control / state_stationary / state_static"


# ----- CZ phase compensation, raw quadratures ---------------------------------

def _comp_run(tmp_path, node="34a_cz_phase_compensation", labels=(b"c", b"t")):
    n = 17
    frame = np.linspace(0, 1, n, endpoint=False)
    rng = np.random.default_rng(3)
    fc = 1e-4 * np.cos(2 * np.pi * frame)
    ft = 1e-4 * np.sin(2 * np.pi * frame) - 5e-4
    # the fitted entry's noise has zero mean: a least-squares fit with a free
    # offset has its points' mean (what the runtime check compares)
    nc, nt = rng.normal(0, 1e-5, n), rng.normal(0, 1e-5, n)
    ic = np.stack([fc + nc - nc.mean(), 1.1 * fc + 3e-6], axis=-1)
    it = np.stack([1.1 * ft + 2e-6, ft + nt - nt.mean()], axis=-1)
    dims3 = ("qubit_pair", "frame", "control_target")
    fit = {"I_control": (dims3, ic[None]), "Q_control": (dims3, (ic * 0 + 6e-4)[None]),
           "I_target": (dims3, it[None]), "Q_target": (dims3, (it * 0 - 1e-4)[None]),
           "fitted_control": (("qubit_pair", "frame"), fc[None]),
           "fitted_target": (("qubit_pair", "frame"), ft[None])}
    coords = {"qubit_pair": [PAIR.encode()], "frame": frame,
              "control_target": list(labels)}
    run = _run(tmp_path, node, fit, coords, {"success": True}, ["raw_and_fit"])
    return run, ic, it, fc, ft


def test_34a_is_phase_compensation_and_draws_the_fitted_entry(tmp_path):
    run, ic, it, fc, ft = _comp_run(tmp_path)
    menu = _menu(run)
    key = f"raw_and_fit::{PAIR}"
    assert key in menu and menu[key]["available"], menu
    assert not any("phase_diff" in (m["reason"] or "") for m in menu.values())
    fig = _build(run, key)
    by = {t["name"]: np.asarray(t["y"], dtype=float) for t in fig["data"]}
    assert np.allclose(by["control"], ic[:, 0])     # entry "c" of I_control
    assert np.allclose(by["target"], it[:, 1])      # entry "t" of I_target
    assert np.allclose(by["control fit"], fc) and np.allclose(by["target fit"], ft)
    assert fig["layout"]["yaxis"]["title"]["text"] == "I quadrature [V]"


def test_raw_quadratures_without_role_labels_are_not_guessed(tmp_path):
    run, *_ = _comp_run(tmp_path, labels=(b"x", b"y"))
    fig = _build(run, f"raw_and_fit::{PAIR}")
    assert fig is None or not fig.get("data")


def test_an_entry_whose_mean_is_not_its_fits_is_not_drawn(tmp_path, monkeypatch):
    """Labels swapped: the 'c' entry is not the points fitted_control was made
    to, so no measured points are drawn, and the reason says why."""
    from quam_state_manager.core.interactive_plots.recipes import cz_phase
    specs, real = [], cz_phase.build
    monkeypatch.setattr(cz_phase, "build", lambda b, k: specs.append(real(b, k)) or specs[-1])
    run, *_ = _comp_run(tmp_path, labels=(b"t", b"c"))
    fig = _build(run, f"raw_and_fit::{PAIR}")
    assert fig is None or not fig.get("data")
    assert specs and not specs[-1].available
    assert "not the points its fit was made to" in specs[-1].reason


def test_error_amplified_compensation_name_keeps_its_own_figure():
    from quam_state_manager.core.interactive_plots.recipes import cz_phase
    assert cz_phase._figure_for("36_cz_phase_compensation_error_amp")[0] == "phase_vs_operations"
    assert cz_phase._figure_for("2Q_21_cz_phase_compensation")[0] == "raw_and_fit"
    assert cz_phase._figure_for("32a_cz_conditional_phase")[0] == "phase_figure"


# ----- the environment line ---------------------------------------------------

def test_env_line_only_when_nothing_is_drawn():
    from quam_state_manager.core.interactive_plots.replot import reproduction_hint
    cap = {"available": False, "reason": "No QM environment selected.", "env": None}
    drawn = [{"key": "a", "available": True, "static": False}]
    static_only = [{"key": "figures.x", "available": True, "static": True},
                   {"key": "b", "available": False, "static": False}]
    assert reproduction_hint(cap, drawn) == ""
    hint = reproduction_hint(cap, static_only)
    assert hint.startswith("Without a QM environment SM shows this run's saved images only")
    assert "Generate Config" in hint
    assert reproduction_hint({"available": True}, static_only) == ""
    other = {"available": False, "reason": "Run has no quam_state/ snapshot.", "env": "py"}
    assert reproduction_hint(other, static_only) == "Run has no quam_state/ snapshot."
    assert reproduction_hint(other, drawn) == ""


def _comp_run_with_phases(tmp_path, control_phase, target_phase):
    n = 17
    frame = np.linspace(0, 1, n, endpoint=False)
    fc = 1e-4 * np.cos(2 * np.pi * frame)               # peaks at frame 0
    ft = 1e-4 * np.sin(2 * np.pi * frame) - 5e-4        # peaks at frame 0.25
    fit = {"state_control": (("qubit_pair", "frame"), fc[None]),
           "state_target": (("qubit_pair", "frame"), ft[None]),
           "fitted_control": (("qubit_pair", "frame"), fc[None]),
           "fitted_target": (("qubit_pair", "frame"), ft[None]),
           "fitted_control_phase": (("qubit_pair",), [control_phase]),
           "fitted_target_phase": (("qubit_pair",), [target_phase])}
    coords = {"qubit_pair": [PAIR.encode()], "frame": frame}
    return _run(tmp_path, "34a_cz_phase_compensation", fit, coords, {"success": True}, ["raw_and_fit"])


def test_compensation_figure_has_a_legend_and_marks_the_fitted_phase_shift(tmp_path):
    """Audit recheck: the four control / target traces had no legend, and the
    fitted phase was not marked. A cosine fit peaks at (1 - fitted phase) mod 1."""
    run = _comp_run_with_phases(tmp_path, control_phase=1.0, target_phase=0.75)
    fig = _build(run, f"raw_and_fit::{PAIR}")
    assert fig["layout"]["showlegend"] is True
    xs = sorted(s["x0"] for s in fig["layout"]["shapes"])
    assert xs == pytest.approx([0.0, 0.25])
    texts = [a["text"] for a in fig["layout"]["annotations"]]
    assert texts == ["control fit phase shift 0.000", "target fit phase shift 0.250"]


def test_a_phase_that_does_not_match_its_own_fit_is_not_marked(tmp_path):
    run = _comp_run_with_phases(tmp_path, control_phase=0.5, target_phase=0.75)
    fig = _build(run, f"raw_and_fit::{PAIR}")
    assert [a["text"] for a in fig["layout"]["annotations"]] == ["target fit phase shift 0.250"]


# ----- variable-driven routing and the honest "nothing mapped" line ----------

def _cond_run(tmp_path, node="32a_cz_conditional_phase", with_phase_diff=False):
    n = 17
    frame = np.linspace(0, 1, n, endpoint=False)
    fc = 1e-4 * np.cos(2 * np.pi * frame)
    ft = 1e-4 * np.sin(2 * np.pi * frame) - 5e-4
    fit = {"state_control": (("qubit_pair", "frame"), fc[None]),
           "state_target": (("qubit_pair", "frame"), ft[None]),
           "fitted_control": (("qubit_pair", "frame"), fc[None]),
           "fitted_target": (("qubit_pair", "frame"), ft[None])}
    if with_phase_diff:
        fit["phase_diff"] = (("qubit_pair", "frame"), (fc - ft)[None])
    coords = {"qubit_pair": [PAIR.encode()], "frame": frame}
    run = _run(tmp_path, node, fit, coords, {"success": True}, [])
    return run, fc, ft


def test_conditional_phase_run_without_phase_diff_draws_the_fitted_curves(tmp_path):
    run, fc, ft = _cond_run(tmp_path)
    key = f"raw_and_fit::{PAIR}"
    menu = _menu(run)
    assert menu[key]["available"], menu
    assert not any("phase_diff" in (m["reason"] or "") for m in menu.values())
    by = {t["name"]: np.asarray(t["y"], dtype=float) for t in _build(run, key)["data"]}
    assert np.allclose(by["control fit"], fc) and np.allclose(by["target fit"], ft)


def test_run_with_phase_diff_and_fitted_curves_keeps_the_existing_figure(tmp_path):
    run, fc, ft = _cond_run(tmp_path, with_phase_diff=True)
    menu = _menu(run)
    assert menu[f"phase_figure::{PAIR}"]["available"]
    assert f"raw_and_fit::{PAIR}" not in menu          # nothing drawn twice


def test_phase_routing_needs_both_fitted_curves():
    from quam_state_manager.core.interactive_plots.recipes import cz_phase
    name = "32a_cz_conditional_phase"
    assert cz_phase._figure_for(name, {"fitted_control"})[0] == "phase_figure"
    assert cz_phase._figure_for(name, {"fitted_target"})[0] == "phase_figure"
    assert cz_phase._figure_for(name, set())[0] == "phase_figure"
    assert cz_phase._figure_for(name, {"fitted_control", "fitted_target"})[0] == "raw_and_fit"
    assert cz_phase._figure_for(
        name, {"fitted_control", "fitted_target", "phase_diff"})[0] == "phase_figure"


def _chevron_run(tmp_path, moving=True, stationary=True, extra=None, nan=False):
    na, nt = 5, 7
    amp, tm = np.linspace(0.1, 0.5, na), np.linspace(10, 70, nt)
    rng = np.random.default_rng(5)
    z1 = rng.random((1, na, nt))
    z2 = rng.random((1, na, nt))
    if nan:
        z1[:] = np.nan
    dims = ("qubit_pair", "amplitude", "time")
    fit = {}
    if moving:
        fit["state_moving"] = (dims, z1)
    if stationary:
        fit["state_stationary"] = (dims, z2)
    fit.update(extra or {})
    coords = {"qubit_pair": [PAIR.encode()], "amplitude": amp, "time": tm}
    return _run(tmp_path, "31_chevron", fit, coords, {"success": True}, []), z1, z2


def _z_of(fig):
    return np.asarray(fig["data"][0]["z"], dtype=float)


def test_chevron_state_moving_and_stationary_values_match_the_dataset(tmp_path):
    run, z1, z2 = _chevron_run(tmp_path)
    for base, z in (("moving_qubit", z1), ("stationary_qubit", z2)):
        fig = _build(run, f"{base}::{PAIR}")
        assert np.array_equal(_z_of(fig), z[0])
        assert base.split("_")[0] in fig["title"].lower()


def test_chevron_with_only_one_role_draws_it_and_says_what_is_missing(tmp_path):
    run, z1, _z2 = _chevron_run(tmp_path, stationary=False)
    menu = _menu(run)
    assert menu[f"moving_qubit::{PAIR}"]["available"]
    gone = menu[f"stationary_qubit::{PAIR}"]
    assert not gone["available"] and "state_stationary" in gone["reason"]
    assert np.array_equal(_z_of(_build(run, f"moving_qubit::{PAIR}")), z1[0])


def test_chevron_all_nan_is_said_not_drawn(tmp_path):
    run, *_ = _chevron_run(tmp_path, nan=True)
    fig = _build(run, f"moving_qubit::{PAIR}")
    assert fig is None or not fig.get("data")
    from quam_state_manager.core.interactive_plots.registry import figure_unavailable_reason
    assert "no finite values" in figure_unavailable_reason(run, f"moving_qubit::{PAIR}")
    # the other role of the same run still draws
    assert _build(run, f"stationary_qubit::{PAIR}")["data"]


def test_chevron_unexpected_extra_dimension_is_sliced_and_said(tmp_path):
    na, nt = 5, 7
    z = np.random.default_rng(1).random((1, 3, na, nt))
    fit = {"state_moving": (("qubit_pair", "prep", "amplitude", "time"), z)}
    coords = {"qubit_pair": [PAIR.encode()], "prep": [0, 1, 2],
              "amplitude": np.linspace(0.1, 0.5, na), "time": np.linspace(10, 70, nt)}
    run = _run(tmp_path, "31_chevron", fit, coords, {"success": True}, [])
    fig = _build(run, f"moving_qubit::{PAIR}")
    assert np.array_equal(_z_of(fig), z[0, 0])
    assert "first of 3" in json.dumps(fig["layout"])


def test_chevron_partial_variable_name_is_not_a_match(tmp_path):
    run, *_ = _chevron_run(tmp_path, moving=False, stationary=False,
                           extra={"state_moving_extra": (("qubit_pair", "amplitude", "time"),
                                                         np.zeros((1, 5, 7)))})
    assert not any(m["available"] for m in _menu(run).values())


def test_chevron_variable_without_amplitude_and_time_is_not_drawn(tmp_path):
    fit = {"state_moving": (("qubit_pair", "other"), np.zeros((1, 4)))}
    coords = {"qubit_pair": [PAIR.encode()], "other": np.arange(4.0)}
    run = _run(tmp_path, "31_chevron", fit, coords, {"success": True}, [])
    fig = _build(run, f"moving_qubit::{PAIR}")
    assert fig is None or not fig.get("data")
    from quam_state_manager.core.interactive_plots.registry import figure_unavailable_reason
    assert "not amplitude x time" in figure_unavailable_reason(run, f"moving_qubit::{PAIR}")


def test_nothing_mapped_run_names_the_variables_it_found(tmp_path):
    from quam_state_manager.core.interactive_plots import list_interactive_figures
    from quam_state_manager.core.interactive_plots.registry import unmapped_variables_line
    fit = {"mystery_a": (("qubit_pair", "x"), np.zeros((1, 3))),
           "mystery_b": (("qubit_pair", "x"), np.ones((1, 3)))}
    coords = {"qubit_pair": [PAIR.encode()], "x": np.arange(3.0)}
    run = _run(tmp_path, "99_unknown_node", fit, coords, {"success": True}, [])
    line = unmapped_variables_line(run, list_interactive_figures(run))
    assert "mystery_a (fit)" in line and "mystery_b (fit)" in line
    assert " -- " not in line


def test_mapped_run_has_no_unmapped_line(tmp_path):
    from quam_state_manager.core.interactive_plots import list_interactive_figures
    from quam_state_manager.core.interactive_plots.registry import unmapped_variables_line
    run, *_ = _chevron_run(tmp_path)
    assert unmapped_variables_line(run, list_interactive_figures(run)) == ""


# ----- the 404 text of the two plot routes ----------------------------------

def _client(tmp_path, monkeypatch, run):
    from types import SimpleNamespace
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    store = SimpleNamespace(runs={1: run})
    monkeypatch.setattr(routes, "_resolve_run", lambda uid: (store, 1, ""))
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    return app.test_client()


def test_interactive_route_404_names_why_the_figure_is_not_drawn(tmp_path, monkeypatch):
    run, *_ = _chevron_run(tmp_path, nan=True)
    resp = _client(tmp_path, monkeypatch, run).get(
        f"/dataset/x/interactive/plot?fig=moving_qubit::{PAIR}")
    assert resp.status_code == 404
    assert resp.get_json()["error"].startswith("Not drawn: ")
    assert "no finite values" in resp.get_json()["error"]


def test_replot_route_404_reason_comes_from_the_replot_result(tmp_path, monkeypatch):
    from quam_state_manager.core.interactive_plots import replot
    run, *_ = _chevron_run(tmp_path)
    monkeypatch.setattr(replot, "replot_capability", lambda r, ip: {"available": True})
    result = {"figures": [], "errors": [
        {"stage": "plot:amp_map", "trace": "Traceback\n  ...\nValueError: replot-only failure"}]}
    monkeypatch.setattr(replot, "replot_run", lambda r, ip: result)
    client = _client(tmp_path, monkeypatch, run)
    # a key the interactive recipe knows too: the reason must still be the replot one
    for key, expect in (("amp_map", "Not drawn: ValueError: replot-only failure"),
                        (f"moving_qubit::{PAIR}", "no figure named")):
        resp = client.get(f"/dataset/x/replot/plot?fig={key}")
        assert resp.status_code == 404
        assert expect in resp.get_json()["error"]
    assert "Not drawn" not in resp.get_json()["error"]


def test_build_interactive_figure_reads_the_bundle_inputs_once(tmp_path, monkeypatch):
    from quam_state_manager.core.interactive_plots import registry
    run, *_ = _chevron_run(tmp_path)
    calls = []
    real = registry._bundle_inputs
    monkeypatch.setattr(registry, "_bundle_inputs", lambda r: calls.append(1) or real(r))
    registry.build_interactive_figure(run, f"moving_qubit::{PAIR}")
    assert len(calls) == 1


def _reason_for(trace_last_line):
    from quam_state_manager.core.interactive_plots.replot import replot_unavailable_reason
    result = {"errors": [{"stage": "plot:amp_map", "trace": "Traceback\n  ...\n" + trace_last_line}]}
    return replot_unavailable_reason(result, "amp_map")


def test_unavailable_reason_is_cut_at_200_characters():
    why = _reason_for("ValueError: " + "x" * 300)
    assert len(why) == 203 and why.endswith("...")
    assert _reason_for("ValueError: short") == "ValueError: short"


def test_unavailable_reason_keeps_only_the_base_name_of_a_path():
    assert _reason_for(r"FileNotFoundError: C:\Users\Some One\my data\run 1\ds.h5") == "FileNotFoundError: ds.h5"
    assert _reason_for("OSError: C:/lab/data/run/ds.nc") == "OSError: ds.nc"
    assert _reason_for("OSError: /home/u/data/ds.nc") == "OSError: ds.nc"
    assert _reason_for("ValueError: a and/or b, 1/2") == "ValueError: a and/or b, 1/2"


_BS = "\\"


@pytest.mark.parametrize("line, expected", [
    ("see https://example.com/a/b.html now", "see https://example.com/a/b.html now"),
    (f"ValueError: cannot read D:{_BS}a{_BS}b.h5 because 3/4 of points are NaN",
     "ValueError: cannot read b.h5 because 3/4 of points are NaN"),
    (f"OSError: {_BS}{_BS}server{_BS}share{_BS}f.h5 missing", "OSError: f.h5 missing"),
    (f"OSError: {_BS}server{_BS}share{_BS}f.h5 missing", "OSError: f.h5 missing"),
    ("KeyError: '/qubits/q1/xy'", "KeyError: '/qubits/q1/xy'"),
    (f'IOError: "C:{_BS}x y{_BS}z.h5" locked', 'IOError: "z.h5" locked'),
    ("IOError: 'D:/a b/c.h5'", "IOError: 'c.h5'"),
    (f"E:{_BS}\ub370\uc774\ud130{_BS}f.h5 gone", "f.h5 gone"),
    (f"x /a/b.h5 and D:{_BS}c{_BS}d.nc, end", "x b.h5 and d.nc, end"),
    ("/home/u/run/data.h5", "data.h5"),
    (f"D:{_BS}a{_BS}b.h5 failed, see /x/c.h5", "b.h5 failed, see c.h5"),
    (f"OSError: C:{_BS}Some One{_BS}run 1{_BS}ds.h5 not found", "OSError: ds.h5 not found"),
    ("", ""),
    ("a and/or b, 1/2", "a and/or b, 1/2"),
    ("x" * 500, "x" * 200 + "..."),
    (None, ""),
    (5, "5"),
    ("KeyError: /qubits/qA.z.joint_offset", "KeyError: /qubits/qA.z.joint_offset"),
    ("ratio /0.5 too low", "ratio /0.5 too low"),
    ("KeyError: '#/qubits/x/y'", "KeyError: '#/qubits/x/y'"),
    ("OSError: /srv/lab/node.py failed", "OSError: node.py failed"),
    ("OSError: /srv/lab/st.json failed", "OSError: st.json failed"),
    # suffix rule: digit-only tail after a suffix, long suffix, underscore rejects
    ("OSError: /usr/lib/libx.so.6", "OSError: libx.so.6"),
    ("OSError: /tmp/data.backup gone", "OSError: data.backup gone"),
    ("OSError: /srv/node.journal", "OSError: node.journal"),
    ("KeyError: /qubits/q1.xy_detuning", "KeyError: /qubits/q1.xy_detuning"),
    ("KeyError: /a/b.c_d.7", "KeyError: /a/b.c_d.7"),
    ("x.tar.gz at /d/x.tar.gz,", "x.tar.gz at x.tar.gz,"),
    ("(/d/ds.h5.bak)", "(ds.h5.bak)"),
    ("/d/f.nc4 and '/d/c.json5'.", "f.nc4 and 'c.json5'."),
    ("numpy 1.26.4 and 0.5.", "numpy 1.26.4 and 0.5."),
    ("see the file.", "see the file."),
    # space extension: POSIX and single-backslash roots, stop rules
    ("OSError: /home/u/My Data/ds.h5 gone", "OSError: ds.h5 gone"),
    (f"OSError: {_BS}srv{_BS}a b{_BS}x.nc4", "OSError: x.nc4"),
    (f"C:{_BS}a b{_BS}f.h5 x{_BS}1.5 ok", "f.h5 x\\1.5 ok"),
    ("/a/b c/d.h5 /e/f.h5", "d.h5 f.h5"),
    ("/qubits/q1 and/or y", "/qubits/q1 and/or y"),
    ("/a/b see https://x.org/c.h5", "/a/b see https://x.org/c.h5"),
    ("/a/b https://x.org/c.h5", "/a/b https://x.org/c.h5"),
    ("x / y/z.h5", "x / y/z.h5"),
    ("/a/b c/d gone", "/a/b c/d gone"),
])
def test_bounded_reason_shortens_only_file_paths(line, expected):
    from quam_state_manager.core.interactive_plots.replot import _bounded_reason
    assert _bounded_reason(line) == expected


@pytest.mark.parametrize("reason, expected", [
    ("no finite values", "Not drawn: no finite values."),
    ("already ends.", "Not drawn: already ends."),
    ("a question?", "Not drawn: a question?"),
    ("loud!", "Not drawn: loud!"),
    ("", "Figure not available."),
    ("   ", "Figure not available."),
    (None, "Figure not available."),
])
def test_not_drawn_line_ends_with_exactly_one_period(reason, expected):
    from quam_state_manager.core.interactive_plots.replot import not_drawn_line
    assert not_drawn_line(reason) == expected


def test_interactive_route_404_texts_end_with_one_period(tmp_path, monkeypatch):
    from quam_state_manager.core.interactive_plots import registry
    run, *_ = _chevron_run(tmp_path, nan=True)
    client = _client(tmp_path, monkeypatch, run)
    err = client.get(f"/dataset/x/interactive/plot?fig=moving_qubit::{PAIR}").get_json()["error"]
    assert err.endswith(".") and not err.endswith("..")
    monkeypatch.setattr(registry, "figure_unavailable_reason", lambda r, k: "")
    err = client.get("/dataset/x/interactive/plot?fig=no_such_fig").get_json()["error"]
    assert err == "Figure not available."


def test_unavailable_tile_line_has_one_period(tmp_path):
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    tpl = app.jinja_env.from_string("{{ r | not_drawn }}")
    assert tpl.render(r="x holds no finite values.") == "Not drawn: x holds no finite values."
    assert tpl.render(r="after slicing") == "Not drawn: after slicing."


def test_unavailable_tile_template_renders_the_reason_through_the_one_helper():
    from pathlib import Path
    import quam_state_manager.web as web
    src = (Path(web.__file__).parent / "templates" / "_dataset_interactive.html").read_text(encoding="utf-8")
    assert "{{ fig.reason | not_drawn }}" in src
    assert "Not drawn: {{" not in src


def test_template_empty_reason_shows_the_fallback_line():
    from quam_state_manager.web.app import create_app
    app = create_app()
    html = app.jinja_env.from_string("{{ r | not_drawn }}").render(r="")
    assert html == "Figure not available."
