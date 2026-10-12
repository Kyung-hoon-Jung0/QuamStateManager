"""Synthetic pins for saved-record mappings found during archive validation."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from quam_state_manager.core.mapfit import api
from quam_state_manager.web import map_analysis
from quam_state_manager.core.mapfit.compare import saved_comparisons


def test_unresolved_control_minimum_is_ambiguous():
    x, y = np.linspace(-1, 1, 31), np.linspace(-1, 1, 31)
    out = api.analyze({"x": x, "y": y, "z": x[None, :] ** 2 + y[:, None] ** 2,
        "meta": {"x": {"label": "a_scan"}, "y": {"label": "b_over_a"}, "b_resolved": False}})
    assert out["fit"]["verdict"] == "ambiguous"
    assert out["fit"]["values"] is None


def test_saved_unresolved_control_reaches_model_and_blocks_updates(tmp_path):
    x, y = np.linspace(-1, 1, 31), np.linspace(-1, 1, 31)
    trace = {"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(),
        "z": (x[None, :] ** 2 + y[:, None] ** 2).tolist()}
    figure = {"axes": [{"title": "q0-q1: leakage", "xlabel": "a_scan", "ylabel": "b_over_a", "traces": [trace]}]}
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0-q1": {
        "optimal_a_scan": .1, "optimal_b_over_a": .2, "b_resolved": False}}}))
    out = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path), experiment_name="synthetic"), "run",
        figure, {"data": [trace], "clickable": {"targets": [{"axis": "x", "path": "qubits.q0.amplitude"}]}}, {})
    assert out["fit"]["verdict"] == "ambiguous"
    assert out["compare"]["verdict"] == "n/a"
    assert out["updates"] == []


@pytest.mark.parametrize("lobe,center,expected", [("+", .23, .228), ("-", -.23, -.228)])
def test_chevron_saved_voltage_coupling_and_full_period(lobe, center, expected):
    name = "v_res_plus_v" if lobe == "+" else "v_res_minus_v"
    record = {name: expected, "lobes": {lobe: {"centre_v": center, "two_j_hz": 20e6,
        "two_j_err_hz": 1e5, "t_lim_ns": 50}}}
    fitted = {"verdict": "ok", "values": {"dv0": -2., "two_j_hz": 20e6, "t_pi": 25.}}
    out = saved_comparisons("chevron", record, fitted, fitted, 4., 1.,
        time_scale=1e-9, control_scale=.001, lobe=lobe)
    assert [c["verdict"] for c in out] == ["agree"] * 3
    assert out[0]["sm_value"] == pytest.approx(expected)
    assert out[0]["tol"] == .0005
    assert out[1]["tol"] == 3e5
    assert out[2]["sm_value"] == pytest.approx(50.) and out[2]["tol"] == pytest.approx(4.)


def test_ridge_saved_frequency_and_offset_use_the_same_units():
    fitted = {"verdict": "ok", "values": {"x": .01, "y": 5.}}
    out = saved_comparisons("ridge", {"qubit_frequency": 5e9, "idle_offset": .01},
        fitted, fitted, .002, .001, frequency_scale=1e9)
    assert [c["verdict"] for c in out] == ["agree", "agree"]
    assert out[0]["sm_value"] == 5e9 and out[0]["tol"] == 1e6
    assert out[1]["sm_value"] == .01 and out[1]["tol"] == .002


def test_frequency_scale_requires_matching_source_coordinate(tmp_path):
    import h5py
    values = np.linspace(4.9, 5.1, 21)
    run = SimpleNamespace(folder_path=str(tmp_path))
    assert map_analysis._frequency_scale(run, values, "RF frequency [Hz]") == 1.
    with h5py.File(tmp_path / "ds_raw.h5", "w") as stream:
        stream.create_dataset("full_freq", data=values[None, :] * 1e9)
    assert map_analysis._frequency_scale(run, values, "RF frequency [Hz]") == 1e9
    assert map_analysis._frequency_scale(run, values + .1, "RF frequency [Hz]") == 1.
    assert map_analysis._frequency_scale(run, values, "RF frequency [GHz]") == 1e9


@pytest.mark.parametrize("kind", ["chevron", "erroramp", "ridge"])
def test_saved_units_and_operation_are_bound_before_modeling(tmp_path, monkeypatch, kind):
    from tests.mapfit_synthetic import sample
    z, x, y, meta, truth = sample(kind, noise=0)
    labels = {"chevron": ("pulse duration (ns)", "scan dV (mV)"),
        "erroramp": ("amplitude prefactor", "Number of pulses"), "ridge": ("Flux (V)", "RF frequency [GHz]")}
    record = {"chevron": {"v_res_plus_v": .23, "lobes": {"+": {"centre_v": .23}}},
        "erroramp": {"operation": "x90", "opt_amp_prefactor": 1.},
        "ridge": {"qubit_frequency": 5e9}}[kind]
    axis = {"title": "q0 lobe +", "xlabel": labels[kind][0], "ylabel": labels[kind][1]}
    trace = {"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(), "z": z.tolist()}
    axis["traces"] = [trace]
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0": record}}))
    captured = []
    original = api.analyze
    def capture(request):
        captured.append(request["meta"].copy())
        return original(request)
    monkeypatch.setattr(api, "analyze", capture)
    out = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path), experiment_name="synthetic"),
        "run", {"axes": [axis]}, {"data": [trace]}, {})
    if kind == "chevron":
        assert all(m.get("time_units") == "ns" for m in captured)
        assert out["compare"]["node_name"] == "v_res_plus_v"
        assert out["compare"]["tol"] == .0005
        assert len(out["comparisons"]) == 3
    elif kind == "erroramp":
        assert all(m.get("theta") == np.pi / 2 for m in captured)
    else:
        assert out["compare"]["node_name"] == "qubit_frequency"
        assert out["compare"]["node_value"] == 5e9
