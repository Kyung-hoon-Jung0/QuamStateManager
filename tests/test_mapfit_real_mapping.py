"""Synthetic pins for saved-record mappings found during archive validation."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from quam_state_manager.core.mapfit import api
from quam_state_manager.web import map_analysis
from quam_state_manager.core.mapfit.compare import saved_comparisons
from quam_state_manager.core.mapfit.models import chevron


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


def test_chevron_noise_rows_do_not_invent_a_second_branch():
    x, y = np.arange(16., 121., 4.), np.arange(-25., 26.)
    frequency = np.sqrt(.02**2 + (.005 * (y + 2))**2)
    contrast = .35 * np.exp(-((y + 2) / 5)**2)
    z = .8 + contrast[:, None] * np.cos(2 * np.pi * frequency[:, None] * x)
    z += np.random.default_rng(19).normal(0, .03, z.shape)
    out = chevron(z, x, y, {"time_units": "ns"})
    assert out["verdict"] == "ok"
    assert out["values"]["dv0"] == pytest.approx(-2, abs=.5)
    assert out["values"]["two_j_hz"] == pytest.approx(20e6, rel=.05)


def test_json_null_phase_cells_are_nan_before_cap_inference():
    x, y = np.arange(31.), np.arange(31.)
    phase = (180 + (y[:, None] - 15) * 3 + np.zeros((31, 31))).tolist()
    phase[2][2] = None
    try:
        out = api.analyze({"x": x, "y": y, "z": (x[None, :] - 15)**2 + (y[:, None] - 15)**2,
            "meta": {"x": {"label": "a_scan"}, "y": {"label": "b_over_a"}, "phase": phase, "circular": 360}})
    except TypeError:
        out = {}
    assert out.get("fit", {}).get("verdict") == "ok"
    assert out["caps_applied"]["cap_x"] == pytest.approx(5/3/4)


def test_ridge_default_does_not_blend_steep_neighboring_columns():
    x, y = np.arange(13.), np.arange(161.)
    center = 140 - (x - 5.3)**2
    z = np.exp(-((y[:, None] - center)/1.5)**2)
    request = {"x": x, "y": y, "z": z,
        "meta": {"x": {"label": "flux"}, "y": {"label": "frequency"}}}
    out = api.analyze(request)
    assert out["caps_applied"]["sigma_x"] == .5
    assert out["fit"]["verdict"] == "ok"
    assert out["fit"]["values"]["x"] == pytest.approx(5.3, abs=.1)
    assert out["fit"]["values"]["y"] == pytest.approx(140, abs=.5)
    capped=api.analyze({**request,"smooth":{"sigma_x":8,"sigma_y":8}})
    assert capped['fit']['verdict']=='ok'
    assert capped['caps_applied']['cap_x']==.5


@pytest.mark.parametrize("label,name", [("neg_offset_v (V)", "optimal_neg_offset_v"),
    ("t_phi (ns)", "optimal_t_phi")])
def test_landscape_stage_comparison_uses_the_varied_knob(tmp_path, label, name):
    x, y = np.arange(31.), np.arange(31.)
    z = ((x[None, :] - 15)/10)**2 + ((y[:, None] - 15)/10)**2
    phase = 180 + (y[:, None] - 15) * 3 + np.zeros_like(z)
    trace = {"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(), "z": z.tolist()}
    phase_trace = {**trace, "z": phase.tolist(), "colorbar": {"label": "phase (deg)"}}
    axis = {"title": "q0", "xlabel": "b_over_a", "ylabel": label, "traces": [trace]}
    sibling = {**axis, "traces": [phase_trace]}
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0": {
        "optimal_a_scan": 1., "optimal_b_over_a": 15., name: 15.}}}))
    out = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path), experiment_name="synthetic"),
        "run", {"axes": [axis], "map_sibling_axes": [sibling]}, {"data": [trace]}, {})
    assert out["classification"]["type"] == "landscape"
    assert out["compare"]["node_name"] == name
    assert out["compare"]["verdict"] == "agree"


def test_flat_contour_valley_is_unresolved_at_measured_population_noise():
    x,y = np.arange(31.),np.arange(31.)
    meta = {"x":{"label":"b_over_a"},"y":{"label":"a_scan"},
        "phase":180+(y[:,None]-15)*3+np.zeros((31,31)), "circular":360.,"objective_sigma":.01}
    out = api.analyze({"x":x,"y":y,"z":.001*(x[None,:]-15)**2+(y[:,None]-15)**2,"meta":meta})
    assert out["fit"]["verdict"] == "ambiguous"
    assert out["fit"]["values"] is None


@pytest.mark.parametrize("population_floor",[.01,0.])
def test_saved_population_noise_reaches_landscape_model(tmp_path,monkeypatch,population_floor):
    x,y=np.arange(31.),np.arange(31.)
    trace={"type":"heatmap","full_resolution":True,"x":x.tolist(),"y":y.tolist(),
        "z":(.001*(x[None,:]-15)**2+(y[:,None]-15)**2).tolist()}
    axis={"xlabel":"b_over_a","ylabel":"a_scan","title":"q0","traces":[trace]}
    phase={**trace,"z":(180+(y[:,None]-15)*3+np.zeros((31,31))).tolist(),"colorbar":{"label":"phase (deg)"}}
    (tmp_path/'data.json').write_text(json.dumps({"fit_results":{"q0":{"l1_at_pick":.01,"readout_f_floor":population_floor}}}))
    captured=[];original=api.analyze
    def capture(request):
        captured.append(request['meta'].copy())
        return original(request)
    monkeypatch.setattr(api,'analyze',capture)
    out=map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path),experiment_name='synthetic',
        parameters={"num_shots":100,"num_frame_rotations":8}),"run",
        {"axes":[axis],"map_sibling_axes":[{**axis,"traces":[phase]}]},{"data":[trace]}, {})
    p=population_floor or .5/801
    assert captured[0].get('objective_sigma') == pytest.approx(np.sqrt(p*(1-p)*2)/(2*np.sqrt(800)))


def test_missing_landscape_pick_is_not_a_comparison_to_nominal_amplitude():
    out=saved_comparisons('landscape',{'optimal_a_scan':1.,'optimal_b_over_a':None},
        {'verdict':'ok','values':{'x':.9,'y':.5}}, {'values':None}, .01,.05,
        landscape_fields=[('optimal_a_scan','x'),('optimal_b_over_a','y')])
    assert all(c['node_value'] is None and c['verdict']=='n/a' for c in out)


def test_low_power_asymptote_requires_a_low_power_feature():
    x,y=np.arange(61.),np.arange(201.)
    z=np.exp(-((y[:,None]-100)/4)**2)*np.where(x<20,0.,1.)[None,:]
    out=api.analyze({'x':x,'y':y,'z':z,'meta':{'x':{'label':'power'},'y':{'label':'frequency'},'summary':'asymptote'}})
    assert out['fit']['verdict']=='no feature'
    assert out['fit']['values'] is None


def test_incoherent_low_power_positions_are_not_a_frequency():
    x,y=np.arange(61.),np.arange(201.)
    centers=np.tile([40.,100.,160.],21)[:61]
    z=np.exp(-((y[:,None]-centers)/3)**2)
    out=api.analyze({'x':x,'y':y,'z':z,'meta':{'x':{'label':'power'},'y':{'label':'frequency'},'summary':'asymptote'},
        'smooth':{'sigma_x':0,'sigma_y':0}})
    assert out['fit']['verdict']=='no feature'
    assert out['fit']['values'] is None


def test_nominal_policy_is_not_compared_with_an_unrestricted_minimum():
    out=saved_comparisons('landscape',{'optimal_a_scan':1.,'optimal_b_over_a':.5,
        'pick_note':'kept the nominal amplitude'}, {'verdict':'ok','values':{'x':.9,'y':.8}},
        {'values':None},.01,.05,landscape_fields=[('optimal_a_scan','x'),('optimal_b_over_a','y')])
    assert all(c['verdict']=='n/a' and 'different quantity' in c['reason'] for c in out)


def test_lower_boundary_contour_minimum_is_not_replaced_by_an_interior_one():
    from quam_state_manager.core.mapfit.models import landscape
    x,y=np.arange(31.),np.arange(31.)
    phase=180+(y[:,None]-10)*(y[:,None]-25)+np.zeros((31,31))
    z=(x[None,:]-15)**2+(y[:,None]-25)**2
    # At the second branch the minimum is beyond the x boundary.
    z[24:27,:]=(x[None,:]+2)**2
    out=landscape(z,x,y,{'phase':phase,'circular':360.})
    assert out['verdict']=='outside the sweep'
    assert out['values'] is None
