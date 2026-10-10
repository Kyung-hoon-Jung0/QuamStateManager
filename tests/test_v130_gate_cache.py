"""Durable gate verdicts follow run files across app and ledger restarts."""

import json
import os
from pathlib import Path
import threading

import pytest

from quam_state_manager.core import story
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app
from tests.test_calibration_log_hub import DAY, world  # noqa: F401
from tests.test_s10_walk_report_log_speed import _runs_with_folders, _section, _stub_gates


def test_report_and_log_reuse_verdicts_in_a_new_app(world, monkeypatch):
    _runs_with_folders(world)
    calls = _stub_gates(monkeypatch)
    assert _section(world).count("gate: pass - stub gate") == 3
    kept = list((Path(world["inst"]) / "story_cache" / "gates").rglob("*.json"))
    assert len(kept) == 3, "each verdict is persisted under the instance"
    story._HUB_GATES.clear()
    app = create_app(testing=True, instance_path=str(world["inst"]))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(world["chip"])}).status_code in (200, 302)
    monkeypatch.setattr(routes, "REPORT_GATE_BUDGET_S", -1.0)
    body = _section(dict(world, client=client))
    assert body.count("gate: pass - stub gate") == 3 and "not checked yet" not in body
    assert len(calls) == 3, "the new app reads disk before checking the budget"
    story._HUB_GATES.clear()
    log = client.get(f"/journal/day?day={DAY}").get_data(as_text=True)
    assert log.count("gate pass</span>") == 3 and "Checking" not in log
    assert len(calls) == 3


def _run(tmp_path):
    folder = tmp_path / "run"
    (folder / "quam_state").mkdir(parents=True)
    for name in ("node.json", "data.json", "quam_state/state.json", "state.json", "ds_raw.h5"):
        (folder / name).write_text("{}", encoding="utf-8")
    return {"run_id": 7, "folder_path": str(folder), "experiment_name": "scan",
            "parameters": {}, "outcomes": {}, "_hub": {"eid": 1, "state_hash": "a"}}


def test_background_verdict_is_persisted_before_a_later_read(tmp_path, monkeypatch):
    run, calls = _run(tmp_path), _stub_gates(monkeypatch)
    inst = tmp_path / "inst"
    stored, save = threading.Event(), story._gate_store
    def saved(token, path, value):
        result = save(token, path, value)
        if path.is_relative_to(inst):
            stored.set()
        return result
    monkeypatch.setattr(story, "_gate_store", saved)
    assert story._hub_gate(inst, run, "a", None, None, False)["verdict"] == "pending"
    story._start_gates()
    assert stored.wait(15), "the background worker completed"
    assert list((inst / "story_cache" / "gates").rglob("*.json"))
    story._HUB_GATES.clear()
    assert story._hub_gate(inst, run, "a", None, None, False)["verdict"] == "pass"
    assert calls == [7]


@pytest.mark.parametrize("name", ["node.json", "data.json", "quam_state/state.json", "state.json", "ds_raw.h5"])
@pytest.mark.parametrize("change", ["mtime", "size"])
def test_changed_gate_input_recomputes_even_with_a_warm_cache(tmp_path, monkeypatch, name, change):
    run, calls = _run(tmp_path), _stub_gates(monkeypatch)
    inst = tmp_path / "inst"
    assert story._hub_gate(inst, run, "a", None, None, True)["verdict"] == "pass"
    story._HUB_GATES.clear()
    assert story._hub_gate(inst, run, "b", None, None, False)["verdict"] == "pass"
    assert calls == [7], "a rebuilt ledger reuses the run's disk verdict"
    path = Path(run["folder_path"]) / name
    stat = path.stat()
    if change == "mtime":
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    else:
        path.write_text("{ }", encoding="utf-8")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert story._hub_gate(inst, run, "b", None, None, True)["verdict"] == "pass"
    assert calls == [7, 7], "a changed mtime or size invalidates both disk and RAM"
    story._HUB_GATES.clear()
    assert story._hub_gate(inst, run, "b", None, None, False)["verdict"] == "pass"
    assert calls == [7, 7], "the new verdict is durable too"


def test_changed_data_uses_current_fit_results_instead_of_an_old_index(tmp_path, monkeypatch):
    run = _run(tmp_path)
    run["_from_ds"] = True
    class Index:
        def get_run(self, run_id):
            return {"fit_results": {"value": 1}}
    path = Path(run["folder_path"]) / "data.json"
    path.write_text(json.dumps({"fit_results": {"value": 2}}), encoding="utf-8")
    monkeypatch.setattr(story, "_compute_gate", lambda r: {
        "verdict": "pass", "reason": str(r["fit_results"]["value"]), "anchor": "run"})
    inst = tmp_path / "inst"
    assert story._hub_gate(inst, run, "a", None, Index(), True)["reason"] == "2"
    path.write_text(json.dumps({"fit_results": {"value": 300}}), encoding="utf-8")
    assert story._hub_gate(inst, run, "a", None, Index(), True)["reason"] == "300"


def test_legacy_and_hub_read_the_same_durable_verdict(tmp_path, monkeypatch):
    run, calls = _run(tmp_path), _stub_gates(monkeypatch)
    inst = tmp_path / "inst"
    assert story.gate_for_run(inst, run, folder_key="a")["verdict"] == "pass"
    story._HUB_GATES.clear()
    assert story._hub_gate(inst, run, "b", None, None, False)["verdict"] == "pass"
    assert calls == [7], "both readers use the same run files and durable key"
    (Path(run["folder_path"]) / "data.json").write_text("{ }", encoding="utf-8")
    assert story.gate_for_run(inst, run, folder_key="a")["verdict"] == "pass"
    assert story._hub_gate(inst, run, "b", None, None, False)["verdict"] == "pass"
    assert calls == [7, 7], "the legacy reader invalidates edited inputs too"
