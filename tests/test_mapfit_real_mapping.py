"""Synthetic pins for saved-record mappings found during archive validation."""
import json
from types import SimpleNamespace

import numpy as np

from quam_state_manager.core.mapfit import api
from quam_state_manager.web import map_analysis


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
