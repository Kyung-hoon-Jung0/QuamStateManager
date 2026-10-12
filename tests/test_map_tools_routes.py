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
        "clickable": {"targets": [{"axis": "x", "plotly_axis": "x", "path": "qubits.q0.amplitude",
                                  "scale": 2., "offset": 0., "verified_on": "run"}]},
        "status_chip": "current code"}
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0": {"optimal_a_scan": .2}}}))
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
