"""The comparison and Apply must name the same accepted write quantity."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from quam_state_manager.web import map_analysis


@pytest.fixture
def ridge_run(tmp_path):
    x = np.linspace(.10, .24, 71)
    y = np.linspace(6.827, 6.831, 101)
    ridge = 6.82973 - .08 * (x - .170545) ** 2
    z = np.exp(-.5 * ((y[:, None] - ridge[None, :]) / .00015) ** 2)
    trace = {"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": y.tolist(), "z": z.tolist()}
    figure = {"key": "amplitude", "axes": [{"title": "q0", "xlabel": "Flux (V)",
               "ylabel": "RF frequency [GHz]", "traces": [trace]}]}
    paths = ["qubits.q0.z.independent_offset", "qubits.q0.z.min_offset", "qubits.q0.resonator.f_01"]
    values = [.161408, -.341632, 6.828050e9]
    patches = [{"op": "replace", "path": "/quam/" + p.replace(".", "/"), "old": v * .99, "value": v}
               for p, v in zip(paths, values)]
    (tmp_path / "node.json").write_text(json.dumps({"patches": patches}))
    targets = [{"path": p, "axis": "y" if p.endswith("f_01") else "x",
                "scale": 1e9 if p.endswith("f_01") else 1., "evidence_tier": "applied"} for p in paths]
    converted = {"data": [trace], "clickable": {"targets": targets}}
    return SimpleNamespace(folder_path=str(tmp_path), id=499), figure, converted, paths


def test_ridge_apply_compares_each_written_target_and_refuses_lower_offset(ridge_run):
    run, figure, converted, paths = ridge_run
    result = map_analysis.analyze(run, "run", figure, converted, {})
    assert [row["dot_path"] for row in result["updates"]] == [paths[0], paths[2]]
    assert "lower sweet spot" in result["display_only_reason"]
    c = result["compare"]
    assert c["node_name"] == paths[0] and c["node_value"] == .161408
    assert c["sm_value"] == pytest.approx(.170545, abs=.001)
    assert c["delta"] > .008 and c["verdict"] == "disagree"
    assert c["tol"] == pytest.approx(.002)
    for row, c in zip(result["updates"], result["comparisons"]):
        assert c["node_name"] == row["dot_path"] and c["sm_value"] == row["value"]
        assert row["provenance"]["map"]["comparison"] == c
        assert "tolerance" in row["provenance"]["text"]
    assert result["comparisons"][1]["node_value"] == 6.828050e9
    assert "node value 0.161408" in result["provenance_text"] and paths[0] in result["provenance_text"]


def test_claimed_contract_without_accepted_target_value_has_no_apply(ridge_run):
    run, figure, converted, _ = ridge_run
    from pathlib import Path
    (Path(run.folder_path) / "node.json").write_text("{}")
    result = map_analysis.analyze(run, "run", figure, converted, {})
    assert result["updates"] == []
    assert "node value for this target is unavailable" in result["display_only_reason"]


def test_proposals_do_not_enable_map_apply(ridge_run):
    run, figure, converted, _ = ridge_run
    converted["clickable"]["targets"][0]["evidence_tier"] = "proposed"
    result = map_analysis.analyze(run, "run", figure, converted, {})
    assert all(not row["dot_path"].endswith("independent_offset") for row in result["updates"])


def test_pulse_amplitude_is_a_scale_and_flux_offset_is_volts(ridge_run):
    from pathlib import Path
    run, figure, converted, paths = ridge_run
    node = Path(run.folder_path) / "node.json"
    amplitude = "qubits.q0.xy.operations.x180.amplitude"
    saved = json.loads(node.read_text())
    saved["patches"][0]["path"] = "/quam/" + amplitude.replace(".", "/")
    node.write_text(json.dumps(saved))
    converted["clickable"]["targets"][0]["path"] = amplitude
    # v1.4.1: "(scale)" by path shape -> by the chip's port (an LF pulse amplitude is volts).
    result = map_analysis.analyze(run, "run", figure, converted, {}, amplitude_kind=lambda p: "mw")
    c = result["compare"]
    assert c["node_name"] == amplitude and c["quantity"].startswith("(scale);")
    assert "(scale)" in result["updates"][0]["provenance"]["text"]
    lf = map_analysis.analyze(run, "run", figure, converted, {}, amplitude_kind=lambda p: "lf")
    assert lf["compare"]["quantity"].startswith("V;")
    for unknown in (None, lambda p: None):
        c = map_analysis.analyze(run, "run", figure, converted, {}, amplitude_kind=unknown)["compare"]
        assert c["quantity"].startswith("target units;")
    converted["clickable"]["targets"][0]["path"] = paths[0]
    saved["patches"][0]["path"] = "/quam/" + paths[0].replace(".", "/")
    node.write_text(json.dumps(saved))
    assert map_analysis.analyze(run, "run", figure, converted, {})["compare"]["quantity"].startswith("V;")
