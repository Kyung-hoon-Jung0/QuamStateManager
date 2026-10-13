"""Map routes use full source arrays and preserve verified write contracts."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from quam_state_manager.core.interactive_plots import replot
from quam_state_manager.web import map_analysis


@pytest.fixture
def rig(tmp_path, monkeypatch):
    x, y = np.linspace(-2, 2, 501), np.linspace(-2, 2, 31)
    z = (x[None, :] - .2) ** 2 + (y[:, None] + .3) ** 2
    figure = {"key": "map", "axes": [{"xlabel": "Amplitude a_scan", "ylabel": "Amplitude b_over_a",
        "title": "q0", "traces": [{"type": "heatmap", "full_resolution": True,
                                  "x": x.tolist(), "y": y.tolist(), "z": z.tolist()}]}],
        # v1.4.1: unproven map targets -> accepted-write evidence is required.
        "clickable": {"evidence_tier": "applied", "targets": [{"axis": "x", "plotly_axis": "x", "path": "qubits.q0.amplitude",
                                  "scale": 2., "offset": 0., "verified_on": "run"}]},
        "status_chip": "current code"}
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0": {"optimal_a_scan": .2}}}))
    # v1.4.1: declared tier -> actual accepted target value, so comparisons prove the written quantity.
    (tmp_path / "node.json").write_text(json.dumps({"patches": [
        {"op": "replace", "path": "/quam/qubits/q0/amplitude", "old": .3, "value": .4}]}))
    run = SimpleNamespace(folder_path=str(tmp_path), experiment_name="synthetic", id=1)
    monkeypatch.setattr(replot, "replot_capability", lambda *_: {"available": True})
    monkeypatch.setattr(replot, "replot_run", lambda *_: {"figures": [figure]})
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes
    monkeypatch.setattr(routes, "_resolve_run", lambda _: (SimpleNamespace(runs={1: run}), 1, ""))
    app = create_app(instance_path=str(tmp_path / "instance"))
    app.config["TESTING"] = True
    client = app.test_client()
    return client, figure


def test_full_array_deterministic_preview_compare_provenance_and_replay(rig, monkeypatch):
    client, figure = rig
    calls = []
    original = map_analysis.api.analyze
    def capture(request):
        calls.append(len(request["z"][0]))
        return original(request)
    monkeypatch.setattr(map_analysis.api, "analyze", capture)
    body = {"fig": "map", "fit": {"model": "extremum"}, "smooth": {"sigma_x": 0, "sigma_y": 0},
            "contour": {"on": True}}
    response = client.post("/dataset/run/map/analyze", json=body)
    assert response.status_code == 200
    out = response.get_json()
    again = client.post("/dataset/run/map/analyze", json=body).get_json()
    assert out == again and calls == [501, 501]
    assert len(out["smoothed"]["x"]) <= 400 and out["contours"]
    assert out["compare"]["verdict"] == "agree" and abs(out["compare"]["delta"]) < 1e-6
    assert len(out["updates"]) == 1 and out["updates"][0]["value"] == pytest.approx(.4)
    assert out["updates"][0]["provenance"]["request_hash"] == out["request_hash"]
    assert "derived from run #1" in out["provenance_text"]
    assert client.get("/dataset/run/map/analyze/" + out["request_hash"]).get_json() == out
    assert client.get("/dataset/other/map/analyze/" + out["request_hash"]).status_code == 404


@pytest.mark.parametrize("body", [[], "bad", {"fig": []}, {"panel": True}, {"panel": -1},
    {"smooth": []}, {"smooth": {"sigma_x": "1"}}, {"smooth": {"sigma_y": float("nan")}},
    {"smooth": {"sigma_x": 9}}, {"smooth": {"override_cap": "yes"}}, {"contour": {"on": 1}},
    {"contour": {"mode": "bad"}}, {"fit": {"model": []}}, {"z": [[1]]}, {"source": "bad"}])
def test_malformed_map_request_is_400(rig, body):
    client, _ = rig
    if isinstance(body, dict):
        body = {"fig": "map", **body}
    response = client.post("/dataset/run/map/analyze", json=body)
    assert response.status_code == 400 and response.get_json()["error"]


def test_display_only_and_panel_gate(rig):
    client, figure = rig
    figure["clickable"] = None
    out = client.post("/dataset/run/map/analyze", json={"fig": "map"}).get_json()
    assert out.get("updates") == [] and out.get("display_only_reason")
    assert client.post("/dataset/run/map/analyze", json={"fig": "map", "panel": 99}).status_code == 400
    assert client.post("/dataset/run/map/analyze", json={"fig": "map", "fit": {"model": "ridge"}}).status_code == 400
    figure["axes"][0]["traces"][0]["full_resolution"] = False
    assert client.post("/dataset/run/map/analyze", json={"fig": "map"}).status_code == 400


@pytest.mark.parametrize("tier", ["proposed", None])
def test_proposed_or_unverified_map_never_offers_staging(rig, tier):
    client, figure = rig
    figure["clickable"]["evidence_tier"] = tier
    result = client.post("/dataset/run/map/analyze", json={"fig": "map"}).get_json()
    assert result["fit"]["values"] and result["updates"] == []
    assert result["display_only_reason"]


def test_replay_identity_includes_the_verified_write_transform(rig):
    client, figure = rig
    first = client.post("/dataset/run/map/analyze", json={"fig": "map"}).get_json()
    figure["clickable"]["targets"][0]["scale"] = 3
    second = client.post("/dataset/run/map/analyze", json={"fig": "map"}).get_json()
    assert first["request_hash"] != second["request_hash"]
    assert client.get("/dataset/run/map/analyze/" + first["request_hash"]).get_json() == first


@pytest.mark.parametrize("own_record", [False, True])
def test_saved_result_uses_exact_entity_tokens(tmp_path, own_record):
    x = np.linspace(-2, 2, 41)
    trace = {"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": x.tolist(),
             "z": ((x[None, :] - .2)**2 + (x[:, None] + .3)**2).tolist()}
    records = {"q1": {"optimal_a_scan": .8}}
    if own_record: records["q10"] = {"optimal_a_scan": .2}
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": records}))
    figure = {"axes": [{"entity": "q10", "title": "q10", "xlabel": "a_scan", "ylabel": "b_over_a", "traces": [trace]}]}
    result = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "entity-run", figure,
                                 {"data": [trace]}, {"smooth": {"sigma_x": 0, "sigma_y": 0}})
    assert result["compare"]["node_value"] == (.2 if own_record else None)


@pytest.mark.parametrize("error", [ArithmeticError, OverflowError, FloatingPointError, ZeroDivisionError])
def test_map_numerical_failures_remain_request_errors(rig, monkeypatch, error):
    client, _ = rig

    def fail(*args):
        raise error("Invalid map calculation")

    monkeypatch.setattr(map_analysis, "analyze", fail)
    response = None
    try:
        response = client.post("/dataset/run/map/analyze", json={"fig": "map"})
    except error:
        pass
    assert response is not None, "Map numerical failures must not escape the request boundary"
    assert response.status_code == 400 and response.get_json()["error"] == "Invalid map calculation"


def test_interactive_defaults_to_reproduced_maps(rig, monkeypatch):
    from quam_state_manager.core import interactive_plots
    client, _ = rig
    monkeypatch.setattr(interactive_plots, "list_interactive_figures", lambda _: [{
        "key": "legacy::q0", "title": "Distinct recipe", "static": False, "available": True}])
    response = client.get("/dataset/run/interactive")
    assert response.status_code == 200
    assert 'data-endpoint="replot/plot"' in response.text
    assert "Distinct recipe" in response.text


@pytest.mark.parametrize("label", ["Number of pulses", "Number of gates", "Nb of pulses"])
def test_spaced_count_labels_select_erroramp_and_lock_counts(label):
    from quam_state_manager.core.mapfit.classify import classify
    out = classify([.8, 1., 1.2], [1, 3, 5], {"x": {"label": "Pulse amplitude [mV]"},
        "y": {"label": label}})
    assert out["type"] == "erroramp" and out["feature_axis"] == "y"


@pytest.mark.parametrize("label", ["scan dV from lobe centre", "control (mV)"])
def test_voltage_difference_labels_select_chevron(label):
    from quam_state_manager.core.mapfit.classify import classify
    out = classify([0, 4, 8], [-1, 0, 1], {"x": {"label": "Pulse duration (ns)"}, "y": {"label": label}})
    assert out["type"] == "chevron" and out["feature_axis"] == "x"


def test_zero_count_baseline_and_secondary_prefactor_are_bound_before_fit(tmp_path):
    from tests.mapfit_synthetic import sample
    z, x, y, meta, truth = sample("erroramp", noise=0)
    z = np.vstack([np.zeros(len(x)), z])
    y = np.r_[0, y]
    axis = {"xlabel": "Pulse amplitude [mV]", "ylabel": "Number of pulses", "title": "q0",
        "secondary_axes": [{"axis": "x", "label": "amplitude prefactor", "map": {
            "parent": [800, 1000, 1200], "forward": [.8, 1., 1.2]}}],
        "traces": [{"type": "heatmap", "x": (x * 1000).tolist(), "y": y.tolist(), "z": z.tolist(), "full_resolution": True}]}
    figure = {"axes": [axis]}
    # v1.4.1: declared tier -> accepted node patch, to keep the conversion pin applying a verified target.
    (tmp_path / "node.json").write_text(json.dumps({"patches": [
        {"op": "replace", "path": "/quam/qubits/q0/amplitude", "old": .2, "value": .2 * truth["a_pi"]}]}))
    converted = {"data": [axis["traces"][0]], "clickable": {"evidence_tier": "applied", "targets": [{"axis": "x", "path": "qubits.q0.amplitude", "transform": {
        "type": "prefactor", "old": .2, "coordinate_scale": .001, "coordinate_offset": 0}}]}}
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0": {"opt_amp_prefactor": truth["a_pi"]}}}))
    run = SimpleNamespace(folder_path=str(tmp_path), experiment_name="synthetic")
    try:
        result = map_analysis.analyze(run, "bound-run", figure, converted, {"smooth": {"sigma_x": 0, "sigma_y": 0}})
    except ValueError:
        result = {}
    assert result.get("compare", {}).get("verdict") == "agree"
    assert result["updates"][0]["value"] == pytest.approx(.2 * truth["a_pi"], abs=.0001)
    assert result["smoothed"]["x"][0] == pytest.approx(x[0] * 1000)
    assert result["smoothed"]["y"][0] > 0 and "nonpositive-count baseline" in result["fit"]["verdict_line"]
    assert result["caps_applied"]["cap_x"] < 8
    overridden = map_analysis.analyze(run, "bound-run", figure, converted, {
        "smooth": {"sigma_x": 8, "sigma_y": 0, "override_cap": True}})
    assert overridden["caps_applied"]["override"] and "cap overridden" in overridden["provenance_text"]
    no_levels = map_analysis.analyze(run, "bound-run", figure, converted, {
        "smooth": {"sigma_x": 0, "sigma_y": 0}, "contour": {"on": True}})
    assert "contour: no automatic levels" in no_levels["provenance_text"]


def test_raw_map_full_plane_uses_only_existing_candidates(tmp_path):
    import h5py
    x, y = np.linspace(4e9, 5e9, 501), np.linspace(-1, 1, 17)
    with h5py.File(tmp_path / "ds_raw.h5", "w") as f:
        q = f.create_dataset("qubit", data=np.array([b"q0"]))
        q.make_scale("qubit")
        xx = f.create_dataset("RF_frequency", data=x); xx.make_scale("RF_frequency")
        yy = f.create_dataset("flux", data=y); yy.make_scale("flux")
        ds = f.create_dataset("signal", data=((x[None, :] - 4.5e9) ** 2 / 1e18 + y[:, None] ** 2)[None, :, :])
        for dim, scale in zip(ds.dims, (q, yy, xx)): dim.attach_scale(scale)
    run = SimpleNamespace(folder_path=str(tmp_path), experiment_name="synthetic")
    body = {"source": "ndview", "which": "ds_raw.h5", "var": "signal", "x_dim": "RF_frequency", "y_dim": "flux", "selection": {"qubit": 0}, "fit": {"model": "extremum"}, "smooth": {"sigma_x": 0, "sigma_y": 0}}
    candidates = [{"dim": "RF_frequency", "path": "qubits.{q}.f_01"}]
    figure, converted = map_analysis.raw_figure(run, body, candidates)
    assert len(figure["axes"][0]["traces"][0]["x"]) == 501
    # v1.4.1: heuristic raw map writes -> display only, because coordinates prove no write formula.
    assert converted["clickable"] is None
    out = map_analysis.analyze(run, "raw-run", figure, converted, body)
    assert out["updates"] == []
    assert map_analysis._coordinate(out, "x", "extremum") == pytest.approx(4.5e9)
    assert out["compare"]["node_value"] is None and "no node fit" in out["display_only_reason"]
    body["selection"]["qubit"] = 99
    with pytest.raises(ValueError, match="outside"):
        map_analysis.raw_figure(run, body, candidates)


@pytest.mark.parametrize("transposed", [False, True])
def test_erroramp_tightest_cap_override_contours_and_figure_axis_provenance(tmp_path, transposed, monkeypatch):
    from tests.mapfit_synthetic import sample
    from quam_state_manager.core.mapfit.caps import erroramp_caps
    z, amplitude, counts, _, truth = sample("erroramp", noise=0)
    parent = amplitude * 1000 + 7
    x, y, data = (counts, parent, z.T) if transposed else (parent, counts, z)
    amp_axis = "y" if transposed else "x"
    axis = {"xlabel": "Number of pulses" if transposed else "Pulse amplitude [mV]",
        "ylabel": "Pulse amplitude [mV]" if transposed else "Number of pulses", "title": "q0",
        "secondary_axes": [{"axis": amp_axis, "label": "amplitude prefactor", "map": {
            "parent": [807, 1007, 1207], "forward": [.8, 1., 1.2]}}],
        "traces": [{"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(), "z": data.tolist()}]}
    # v1.4.1: declared tier -> accepted node patch, to keep the cap pin applying a verified target.
    (tmp_path / "node.json").write_text(json.dumps({"patches": [
        {"op": "replace", "path": "/quam/qubits/q0/amplitude", "old": .2, "value": .2 * truth["a_pi"]}]}))
    converted = {"data": [axis["traces"][0]], "clickable": {"evidence_tier": "applied", "targets": [{"axis": amp_axis,
        "path": "qubits.q0.amplitude", "transform": {"type": "prefactor", "old": .2,
        "coordinate_scale": .001, "coordinate_offset": -.007}}]}}
    run = SimpleNamespace(folder_path=str(tmp_path), experiment_name="synthetic", id=2)
    body = {"smooth": {"sigma_x": 0 if transposed else 8, "sigma_y": 8 if transposed else 0},
            "contour": {"on": True, "mode": "custom", "level": .5}}
    figure = {"axes": [axis], "revision": {"rung": "current", "sha": "123456789"}}
    scoped_requests = []
    original_hash = map_analysis.api.request_hash
    def capture_hash(request):
        if "analysis" in request:
            scoped_requests.append(request)
        return original_hash(request)
    monkeypatch.setattr(map_analysis.api, "request_hash", capture_hash)
    result = map_analysis.analyze(run, "cap-run", figure, converted, body)
    assert scoped_requests[-1]["panel"] == body.get("panel", 0)
    expected = float(np.min(erroramp_caps(counts, result["raw_fit"]["diagnostics"]["a_pi"], .005)))
    assert result["caps_applied"]["cap_x"] == pytest.approx(expected)
    assert result["caps_applied"]["sigma_x"] <= expected and result["caps_applied"]["sigma_y"] == 0
    assert result["contours"] and all(800 < p[1 if transposed else 0] < 1210
        for contour in result["contours"] for line in contour["lines"] for p in line)
    assert result["updates"][0]["value"] == pytest.approx(.2 * truth["a_pi"], abs=.0001)
    assert f"sigma {'x=0.0 pt, y=' if transposed else 'x='}" in result["provenance_text"]
    assert "code current/1234567" in result["provenance_text"]
    # v1.4.1: missing generic node value -> accepted target value, so displayed provenance describes Apply.
    assert "node value 0.1966" in result["provenance_text"] and "target qubits.q0.amplitude" in result["provenance_text"]
    # Requests that clamp to the same strength still retain their own scoped replay identity.
    second = map_analysis.analyze(run, "cap-run", figure, converted, {**body, "smooth": {
        "sigma_x": 0 if transposed else 7, "sigma_y": 7 if transposed else 0}})
    assert second["request_hash"] != result["request_hash"]
    overridden = map_analysis.analyze(run, "cap-run", figure, converted, {**body, "smooth": {
        "sigma_x": expected + .1 if not transposed else 0,
        "sigma_y": expected + .1 if transposed else 0, "override_cap": True}})
    assert overridden["caps_applied"]["override"] and overridden["caps_applied"]["warning"] == "cap overridden"
    assert "cap overridden" in overridden["fit"]["verdict_line"] and "cap overridden" in overridden["provenance_text"]
    assert len(overridden["provenance_text"]) <= 300


def test_landscape_compares_node_amplitude_on_its_figure_axis(tmp_path):
    x, y = np.linspace(-2, 2, 31), np.linspace(-2, 2, 41)
    trace = {"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(),
        "z": ((x[None, :] - .2) ** 2 + (y[:, None] + .3) ** 2).tolist()}
    axis = {"xlabel": "b_over_a", "ylabel": "scan amplitude (relative)", "title": "q0", "traces": [trace]}
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0": {"optimal_a_scan": -.3}}}))
    result = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "landscape-axis", {"axes": [axis]},
        {"data": [trace]}, {"smooth": {"sigma_x": 0, "sigma_y": 0}})
    assert result["compare"]["sm_value"] == pytest.approx(-.3) and result["compare"]["verdict"] == "agree"


def test_descending_chevron_cap_inference_uses_sorted_model_axes(tmp_path, monkeypatch):
    from tests.mapfit_synthetic import sample
    z, x, y, _, _ = sample("chevron", noise=0)
    trace = {"type": "heatmap", "full_resolution": True, "x": x[::-1].tolist(), "y": y[::-1].tolist(), "z": z[::-1, ::-1].tolist()}
    axis = {"xlabel": "time", "ylabel": "detuning", "traces": [trace]}
    observed = []
    original = map_analysis.api._cap_info
    def capture(kind, array, xc, yc, meta, fit):
        observed.append((xc.copy(), yc.copy()))
        return original(kind, array, xc, yc, meta, fit)
    monkeypatch.setattr(map_analysis.api, "_cap_info", capture)
    map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "descending-cap", {"axes": [axis]},
        {"data": [trace]}, {"smooth": {"sigma_x": 0, "sigma_y": 0}})
    assert observed and all(np.all(np.diff(xc) > 0) and np.all(np.diff(yc) > 0) for xc, yc in observed)


@pytest.mark.parametrize("declared", [False, True])
def test_phase_error_objective_uses_only_its_entity_and_declared_zero_target(tmp_path, declared):
    x, y = np.linspace(-2, 2, 41), np.linspace(-2, 2, 41)
    xx, yy = np.meshgrid(x, y)
    def axis(entity, signal, z, zero=False):
        trace = {"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(),
            "z": z.tolist(), "colorbar": {"label": signal, "units": "deg" if "phase" in signal else ""}}
        return {"title": entity + ": " + signal, "xlabel": "a_scan", "ylabel": "b_over_a", "traces":
            [trace] + ([{"type": "contour", "level": 0}] if zero else [])}
    own = axis("q0", "phase error per gate", 20 * (yy + .3), declared)
    foreign = axis("q1", "phase error per gate", 20 * (yy - 1.2), True)
    objective = axis("q0", "leakage", (xx - .2) ** 2 + (yy + .3) ** 2)
    figure = {"axes": [own, foreign, objective]}
    # v1.4.1: declared tier -> accepted node patch, to keep the entity pin applying a verified target.
    (tmp_path / "node.json").write_text(json.dumps({"patches": [
        {"op": "replace", "path": "/quam/qubits/q0/amplitude", "old": .1, "value": .2}]}))
    converted = {"data": [a["traces"][0] for a in figure["axes"]], "clickable": {"evidence_tier": "applied", "targets": [{
        "path": "qubits.q0.amplitude", "axis": "x", "plotly_axis": "x", "scale": 1}]}}
    result = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "phase-target", figure, converted,
        {"panel": 2, "smooth": {"sigma_x": 0, "sigma_y": 0}})
    if declared:
        assert result["fit"]["verdict"] == "ok" and result["updates"]
        assert result["fit"]["values"]["x"] == pytest.approx(.2, abs=.01)
        assert result["fit"]["values"]["y"] == pytest.approx(-.3, abs=.01)
    else:
        assert result["fit"]["values"] is None and not result["updates"]


def test_objective_binds_sibling_radian_units_for_target_contour(tmp_path):
    x, y = np.linspace(-2, 2, 41), np.linspace(-2, 2, 41)
    xx, yy = np.meshgrid(x, y)
    def axis(label, z):
        return {"title": "q0: " + label, "xlabel": "a_scan", "ylabel": "b_over_a", "traces": [{
            "type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(), "z": z.tolist(),
            "colorbar": {"label": label, "units": "rad" if label == "phase" else ""}}]}
    phase = axis("phase", np.pi + yy + .3)
    objective = axis("leakage", (xx - .2) ** 2 + (yy + .3) ** 2)
    result = map_analysis.analyze(SimpleNamespace(folder_path=str(tmp_path)), "radian-sibling",
        {"axes": [phase, objective]}, {"data": [phase["traces"][0], objective["traces"][0]]},
        {"panel": 1, "smooth": {"sigma_x": 0, "sigma_y": 0}})
    assert result["fit"]["verdict"] == "ok"
    assert result["fit"]["values"]["x"] == pytest.approx(.2, abs=.01)
    assert result["fit"]["values"]["y"] == pytest.approx(-.3, abs=.01)


def test_separate_figure_phase_binds_contour_fit_without_phase_levels_on_objective(rig, monkeypatch):
    client, figure = rig
    axis = figure["axes"][0]
    x, y = np.asarray(axis["traces"][0]["x"]), np.asarray(axis["traces"][0]["y"])
    axis["title"] = "q0 leakage"
    axis["traces"][0]["z"] = ((x[None, :] - .2) ** 2 + (y[:, None] + 1) ** 2).tolist()
    phase = {"key": "phase", "axes": [{"title": "q0 phase", "xlabel": axis["xlabel"], "ylabel": axis["ylabel"],
        "traces": [{"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(),
            "z": np.broadcast_to(180 + 20 * (y[:, None] + .3), (len(y), len(x))).tolist(),
            "colorbar": {"label": "phi_2Q", "units": "deg"}}]}]}
    monkeypatch.setattr(replot, "replot_run", lambda *_: {"figures": [figure, phase]})
    response = client.post("/dataset/run/map/analyze", json={"fig": "map",
        "smooth": {"sigma_x": 0, "sigma_y": 0}, "contour": {"on": True}})
    assert response.status_code == 200
    out = response.get_json()
    assert out["fit"]["verdict"] == "ok" and out["updates"]
    assert out["fit"]["values"]["y"] == pytest.approx(-.3, abs=.01)
    assert out["contours"] and all(0 < item["level"] < 15 and item["lines"] for item in out["contours"])
    assert len(client.get("/dataset/run/map/figures?fig=map").get_json()["panels"]) == 1
