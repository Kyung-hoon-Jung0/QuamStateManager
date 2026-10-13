"""Adversarial requests must not turn map previews into arbitrary writes."""
import json
from types import SimpleNamespace

import numpy as np
import pytest

from tests.state_model.repros._rig import Chip
from quam_state_manager.web import routes

PATH = "qubits.q1.T1"


@pytest.fixture
def map_chip(tmp_path, monkeypatch):
    chip = Chip(tmp_path)
    folder = tmp_path / "run"
    folder.mkdir()
    (folder / "node.json").write_text("{}")
    x = np.linspace(-2, 2, 41)
    trace = {"type": "heatmap", "full_resolution": True, "x": x.tolist(), "y": x.tolist(),
             "z": ((x[None, :] - .2)**2 + (x[:, None] + .3)**2).tolist()}
    fig = {"key": "map", "axes": [{"xlabel": "a_scan", "ylabel": "b_over_a", "traces": [trace]}]}
    converted = {"data": [trace], "clickable": {"targets": [{"path": PATH, "axis": "x", "scale": .0002}]}}
    run = SimpleNamespace(folder_path=str(folder), id=7, experiment_name="synthetic")
    monkeypatch.setattr(routes, "_map_figure", lambda *a: (run, fig, converted))
    out = chip.c.post("/dataset/run-7/map/analyze", json={"fig": "map", "smooth": {"sigma_x": 0, "sigma_y": 0}})
    assert out.status_code == 200, out.data
    return chip, out.get_json(), folder


def post(chip, row, batch):
    if batch:
        return chip.c.post("/field/edit-batch", json={"updates": [row]})
    return chip.c.post("/field/edit", data={**row, "provenance": json.dumps(row["provenance"])})


@pytest.mark.parametrize("batch", [False, True])
@pytest.mark.parametrize("tamper", ["value", "path", "text", "hash", "run", "expired", "inputs", "chip", "code"])
def test_map_tampering_is_refused_before_staging(map_chip, batch, tamper):
    from quam_state_manager.web import map_analysis
    chip, result, folder = map_chip
    row = json.loads(json.dumps(result["updates"][0]))
    if tamper == "value": row["value"] = .00009
    if tamper == "path": row["dot_path"] = "qubits.q2.T1"
    if tamper == "text": row["provenance"]["text"] = "invented proof"
    if tamper == "hash": row["provenance"]["request_hash"] = "other-figure"
    if tamper == "run": row["provenance"]["run_uid"] = "other-run"
    if tamper == "expired": map_analysis._RESULTS.clear()
    if tamper == "inputs": (folder / "node.json").write_text('{"changed":true}')
    if tamper == "chip":
        chip.app.extensions["map_apply_grants"][("run-7", result["request_hash"])]["ctx"] = {}
    if tamper == "code":
        from quam_state_manager.generator.iplot_sources import snapshot
        source = folder / "plotting.py"
        source.write_text("before")
        grant = chip.app.extensions["map_apply_grants"][("run-7", result["request_hash"])]
        grant["files"] = [str(source)]
        grant["code_stats"] = snapshot(grant["files"], [])
        source.write_text("after")
    before = chip.live_state(), chip.log(), chip.journal()
    response = post(chip, row, batch)
    assert response.status_code == 400, response.data
    assert (chip.live_state(), chip.log(), chip.journal()) == before


@pytest.mark.parametrize("batch", [False, True])
def test_authorized_map_stages_exact_value_and_undo_cleans_tray(map_chip, batch):
    from quam_state_manager.core import pending_tray
    chip, result, _ = map_chip
    row = result["updates"][0]
    before = chip.live_state()
    response = post(chip, row, batch)
    assert response.status_code == 200 and response.get_json()["ok"], response.data
    assert chip.log() == [(PATH, row["value"])]
    assert chip.ctx["store"].change_log[0].origin == row["provenance"]
    assert chip.live_state() == before
    assert chip.c.post("/undo").status_code == 200
    assert chip.log() == []
    pending_tray.flush_all()
    sidecar = pending_tray.sidecar_path(chip.ctx["working_copy"])
    assert not sidecar.exists() or not json.loads(sidecar.read_text())["entries"]


def test_late_forged_row_refuses_whole_batch(map_chip):
    chip, result, _ = map_chip
    row = result["updates"][0]
    response = chip.c.post("/field/edit-batch", json={"updates": [row, {**row, "value": .1}]})
    assert response.status_code == 400 and chip.log() == []


def test_map_duplicate_and_changed_target_are_refused(map_chip):
    chip, result, _ = map_chip
    row = result["updates"][0]
    assert post(chip, row, False).status_code == 200
    count = len(chip.log())
    assert post(chip, row, False).status_code == 400
    assert len(chip.log()) == count


@pytest.mark.parametrize("batch", [False, True])
def test_type_coercion_cannot_change_map_number(map_chip, batch, monkeypatch):
    chip, result, _ = map_chip
    monkeypatch.setattr(chip.ctx["modifier"], "_checked_value", lambda *a, **kw: 0)
    response = post(chip, result["updates"][0], batch)
    assert response.status_code == 400 and chip.log() == []
