"""Replay evidence, payload and client transform integration pins."""
import json
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

from quam_state_manager.core.interactive_plots import replot, replot_cache


def setup(tmp_path):
    (tmp_path / "node.json").write_text(json.dumps({"patches": [{"op": "replace",
        "path": "/quam/qubits/q0/xy/amplitude", "old": 0.23, "value": 0.253}]}))
    (tmp_path / "data.json").write_text(json.dumps({"fit_results": {"q0": {"opt": 1.1}}}))
    run = SimpleNamespace(folder_path=str(tmp_path), uid="run-1")
    payload = {"figures": [{"key": "fit", "title": "Fit", "axes": [{"title": "q0", "traces": [
        {"type": "line", "role": "vline", "x": [1.1, 1.1], "y": [0, 1]}]}]}]}
    return run, payload


def test_replay_contract_and_reason_reach_plotly_without_mutating_cache(tmp_path):
    run, payload = setup(tmp_path)
    annotated = replot._with_click_contracts(payload, run)
    figure = replot.replot_figure(annotated, "fit")
    assert figure and figure.get("clickable") and figure["clickable"]["n_verified"] == 1
    target = figure["clickable"]["targets"][0]
    assert target["provenance"]["result_names"] == ["q0.opt"]
    assert "1 state writes; the fit marker reproduces them exactly" in target["provenance"]["formula"]
    assert "clickable" not in payload["figures"][0]
    (tmp_path / "node.json").write_text("{}")
    display = replot.replot_figure(replot._with_click_contracts(payload, run), "fit")
    assert display["clickable"] is None
    assert "wrote no state" in display["display_only_reason"]
    assert display["display_only_reason"] in display["notices"]


def test_real_driver_attaches_proof_on_fresh_ram_and_disk_returns(tmp_path, monkeypatch):
    from quam_state_manager.core import config_generator
    from quam_state_manager.core.interactive_plots import replot_revision
    run, payload = setup(tmp_path)
    monkeypatch.setattr(config_generator, "get_selected_env", lambda _: "python")
    monkeypatch.setattr(config_generator, "_env_signature", lambda _: "signature")
    monkeypatch.setattr(replot_revision, "reproduce", lambda *args: payload)
    monkeypatch.setattr(replot_cache, "valid", lambda *_: True)
    monkeypatch.setattr(replot_cache.DiskCache, "store", lambda *args: ({}, 1))
    replot._CACHE.clear()
    instance = tmp_path / "instance"
    fresh = replot.replot_run(run, instance, force=True)
    assert fresh["figures"][0].get("clickable")
    ram = replot.replot_run(run, instance)
    assert ram["figures"][0].get("clickable")
    replot._CACHE.clear()
    monkeypatch.setattr(replot_cache.DiskCache, "load", lambda *args: ({}, payload, 1))
    disk = replot.replot_run(run, instance)
    assert disk["figures"][0].get("clickable")
    replot._CACHE.clear()


def test_proposal_file_is_part_of_replay_input_fingerprint(tmp_path):
    before = replot._fingerprint(tmp_path)
    (tmp_path / "state_updates.json").write_text('{"operations": []}')
    assert replot._fingerprint(tmp_path) != before


def test_replot_route_uses_existing_rename_translation(tmp_path, monkeypatch):
    from tests.test_interactive_replot import _app_client
    from quam_state_manager.web import routes
    from quam_state_manager.core import run_names
    run, payload = setup(tmp_path)
    run.experiment_name = "synthetic"
    payload = replot._with_click_contracts(payload, run)
    monkeypatch.setattr(replot, "replot_capability", lambda *_: {"available": True})
    monkeypatch.setattr(replot, "replot_run", lambda *_: payload)
    monkeypatch.setattr(routes, "_dataset_run_names", lambda _: SimpleNamespace(identity=False))
    calls = []
    def translate(clickable, *_):
        calls.append(clickable)
        return {**clickable, "translated": True}
    monkeypatch.setattr(run_names, "translate_clickable", translate)
    client = _app_client(monkeypatch, run)
    response = client.get("/dataset/anyuid/replot/plot", query_string={"fig": "fit"})
    assert response.status_code == 200
    assert response.get_json()["clickable"].get("translated") and len(calls) == 1


def test_client_prefactor_and_panel_selection():
    script = Path(__file__).with_name("learn_click_selfcheck.cjs")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True,
                            env={**os.environ, "NODE_PATH": "D:/work/statemanager/node_modules"}, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
