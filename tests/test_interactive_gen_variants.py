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
    ic = np.stack([fc + rng.normal(0, 1e-5, n), 1.1 * fc + 3e-6], axis=-1)
    it = np.stack([1.1 * ft + 2e-6, ft + rng.normal(0, 1e-5, n)], axis=-1)
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
