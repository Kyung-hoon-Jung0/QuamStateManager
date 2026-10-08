"""Missing target fits never borrow another target's parameters or model."""
from types import SimpleNamespace

import numpy as np
import pytest

from quam_state_manager.core.interactive_plots import models
from quam_state_manager.core.interactive_plots.recipes import ramsey, two_qubit_rb
from quam_state_manager.core.interactive_plots.recipes.base import Bundle


def _fits(figure):
    return [t for t in figure["data"] if t.get("name", "").startswith("fit")]


def _notes(figure):
    return " ".join(a["text"].replace("<br>", " ") for a in figure["layout"].get("annotations", [])
                    if a.get("name") == "fit-note")


def test_missing_pair_does_not_borrow_the_first_pairs_fit():
    depths = np.array([0, 10, 20])
    fit = {"coords": {"qubit_pair": ["q0-q1"], "circuit_depth": depths},
           "vars": {"fit_amplitude": [0.75], "fit_alpha": [0.98], "fit_offset": [0.25],
                    "success": [1], "fitted_curve": np.array([0.75 * 0.98 ** depths + 0.25])},
           "dim_order": {v: ["qubit_pair"] for v in
                         ("fit_amplitude", "fit_alpha", "fit_offset", "success")}}
    fit["dim_order"]["fitted_curve"] = ["qubit_pair", "circuit_depth"]
    raw = {"coords": {"qubit_pair": ["q0-q1", "q2-q3"], "circuit_depth": depths},
           "vars": {"state": np.array([[0, 0, 0], [1, 1, 1]])},
           "dim_order": {"state": ["qubit_pair", "circuit_depth"]}}
    results = {"q0-q1": {"alpha": 0.98, "success": True},
               "q2-q3": {"alpha": 0.7, "success": False}}
    bundle = Bundle(run=SimpleNamespace(parameters={}), raw=raw, fit=fit, fit_results=results)
    present = two_qubit_rb.build(bundle, "fig_q0-q1::q0-q1").figure
    assert len(_fits(present)) == 1
    absent = two_qubit_rb.build(bundle, "fig_q2-q3::q2-q3").figure
    assert not _fits(absent)
    assert "saved no fit for this pair" in _notes(absent)
    assert np.allclose(absent["data"][0]["y"], 0)
    assert two_qubit_rb._node_params(bundle, "q2-q3") is None


def _ramsey_bundle(fit_signs=(1,)):
    t = np.linspace(0, 100, 51)
    negative = [1.0, 0.01, 0.0, 0.0, 0.01]
    positive = [1.0, 0.03, 0.0, 0.0, 0.01]
    params = {-1: negative, 1: positive}
    y = np.stack([models.oscillation_decay_exp(t, *negative),
                  models.oscillation_decay_exp(t, *positive)], axis=-1)
    raw = {"coords": {"qubit": ["q0"], "idle_time": t, "detuning_signs": [-1, 1]},
           "vars": {"I": y[None]},
           "dim_order": {"I": ["qubit", "idle_time", "detuning_signs"]}}
    fit = {"coords": {"qubit": ["q0"], "fit_vals": ["a", "f", "phi", "offset", "decay"],
                      "detuning_signs": list(fit_signs), "idle_time": t},
           "vars": {"fit": np.array([[params[s] for s in fit_signs]])},
           "dim_order": {"fit": ["qubit", "detuning_signs", "fit_vals"]}}
    return Bundle(run=None, raw=raw, fit=fit)


@pytest.mark.parametrize("fit_signs", [(1,), (-1,)])
def test_partial_sign_fit_matches_by_value(fit_signs):
    bundle = _ramsey_bundle(fit_signs)
    figure = ramsey.build(bundle, "amplitude::q0").figure
    fits = _fits(figure)
    assert [t["name"] for t in fits] == [f"fit Δ={fit_signs[0]:+g}"]
    params = bundle.fit["vars"]["fit"][0, 0]
    assert np.allclose(fits[0]["y"], models.oscillation_decay_exp(np.array(fits[0]["x"]), *params) * 1e3)
    assert f"detuning sign {-fit_signs[0]:+g}" in _notes(figure)
    assert "saved no fit row" in _notes(figure)


@pytest.mark.parametrize("fault", ["missing", "shape", "nan", "inf"])
def test_mixed_model_requires_a_usable_saved_curve(fault):
    bundle = _ramsey_bundle((-1, 1))
    fit = bundle.fit
    fit["vars"]["used_mixed"] = np.array([True])
    fit["dim_order"]["used_mixed"] = ["qubit"]
    t = fit["coords"]["idle_time"]
    saved = bundle.raw["vars"]["I"].transpose(0, 2, 1) * np.exp(-(t / 40) ** 2)
    fit["vars"]["fit_curve"] = saved.copy()
    fit["dim_order"]["fit_curve"] = ["qubit", "detuning_signs", "idle_time"]
    bundle.raw["vars"]["I"] = saved.transpose(0, 2, 1)
    fit["vars"]["fit_r2"] = np.array([1.0])
    fit["dim_order"]["fit_r2"] = ["qubit"]
    # Remove the metric to ensure an exponential substitution cannot be hidden
    # by a separate agreement gate during the absence check.
    fit["vars"].pop("fit_r2")
    if fault == "missing":
        fit["vars"].pop("fit_curve")
    elif fault == "shape":
        fit["vars"]["fit_curve"] = np.zeros((1, 2, 3))
    else:
        fit["vars"]["fit_curve"][0, :, 0] = float(fault)
    absent = ramsey.build(bundle, "amplitude::q0").figure
    assert not _fits(absent)
    assert "node used the mixed model" in _notes(absent)
    assert "not saved or usable" in _notes(absent)
    fit["vars"]["fit_curve"] = saved
    fit["vars"]["fit_r2"] = np.array([1.0])
    valid = ramsey.build(bundle, "amplitude::q0").figure
    assert len(_fits(valid)) == 2
    for i, trace in enumerate(_fits(valid)):
        assert np.allclose(trace["y"], saved[0, i] * 1e3)


def test_mixed_model_keeps_only_its_usable_sign_curve():
    bundle = _ramsey_bundle((1, -1))
    fit = bundle.fit
    saved = bundle.raw["vars"]["I"].transpose(0, 2, 1)[:, ::-1].copy()
    saved[0, 1, 0] = np.nan
    fit["vars"].update(used_mixed=np.array([True]), fit_curve=saved)
    fit["dim_order"].update(used_mixed=["qubit"],
                            fit_curve=["qubit", "detuning_signs", "idle_time"])
    figure = ramsey.build(bundle, "amplitude::q0").figure
    assert [t["name"] for t in _fits(figure)] == ["fit Δ=+1"]
    assert np.allclose(_fits(figure)[0]["y"], saved[0, 0] * 1e3)
    assert "Detuning sign -1" in _notes(figure)
    assert "node used the mixed model" in _notes(figure)
