"""Auto Calibrate plan bar: the targets a real plan runs on, and its env.

* An empty target selection on the real backend is refused with the count
  until the request confirms ``all_targets`` (it used to mean every qubit of
  the chip, silently).
* With no env in the runner settings, a real plan's items run with the env
  the project resolves (the agent's rule, ``project_env.run_env``), and the
  readiness strip and the Experiment Runner field show it.
* The board/plan-bar client behaviour is executed by
  ``autofit_board_selfcheck.cjs``.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import project_env, scheduler
from quam_state_manager.core.autofit import realbackend
from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent


def _chip(folder: Path):
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({
        "qubits": {q: {"id": q, "T1": 1e-5} for q in ("qA1", "qA2", "qB1")},
        "qubit_pairs": {}, "active_qubit_names": ["qA1", "qA2", "qB1"]}),
        encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}),
        encoding="utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    live = tmp_path / "chip"
    _chip(live)
    py = tmp_path / "envs" / "lab" / "python.exe"
    py.parent.mkdir(parents=True)
    py.write_text("", encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = app.config["contexts"][app.config["active_context"]]
    ctx["qualibrate_project"] = "proj"
    project_env.remember(app.instance_path, "proj", str(py))
    return {"app": app, "client": c, "py": str(py)}


def test_an_empty_selection_on_hardware_needs_a_confirmation(env):
    r = env["client"].post("/autofit/start", json={
        "preset": "1q_bringup", "backend": "real", "force": True})
    assert r.status_code == 409
    d = r.get_json()
    assert d["reason"] == "confirm_all_targets" and d["count"] == 3


def test_a_confirmed_all_passes_the_target_gate(env):
    r = env["client"].post("/autofit/start", json={
        "preset": "1q_bringup", "backend": "real", "force": True,
        "all_targets": True})
    assert (r.get_json() or {}).get("reason") != "confirm_all_targets"


def test_readiness_and_the_runner_field_use_the_project_env(env):
    with env["app"].test_request_context("/"):
        rd = routes._autofit_readiness()
    assert rd["env"] == {"ok": True, "value": env["py"], "source": "project"}
    page = env["client"].get("/scheduler").get_data(as_text=True)
    assert env["py"].replace("\\", "\\\\") in page or env["py"] in page


def test_runner_settings_env_still_wins(env):
    other = Path(env["py"]).parent / "other.exe"
    other.write_text("", encoding="utf-8")
    with env["app"].test_request_context("/"):
        scheduler.save_settings(routes._sched_inst(), {"env_python": str(other)})
        assert routes._autofit_env() == (str(other), "runner")


def test_a_real_item_carries_the_env(monkeypatch, tmp_path):
    seen = {}

    def add_item(inst, info, targets=None):
        seen.update(info)
        raise RuntimeError("stop here")

    monkeypatch.setattr(scheduler, "add_item", add_item)
    node = tmp_path / "n.py"
    node.write_text("# node\n", encoding="utf-8")
    monkeypatch.setattr(realbackend.node_scan, "scan_file",
                        lambda p: type("I", (), {"error": None, "name": "n",
                                                 "kind": "node", "has_hook": False,
                                                 "targets_name": "qubits"})())
    ad = realbackend.RealAdapter(instance_path=str(tmp_path), reconcile=lambda: None,
                                 rescan_and_list_runs=lambda: [],
                                 env_python="X:/lab/python.exe")
    be = realbackend.RealBackend(ad, {"s": str(node)})
    from quam_state_manager.core.autofit.plan import Step
    import threading
    with pytest.raises(RuntimeError):
        be.run_step(Step(id="s", family="ramsey"), ["qA1"], {}, 0,
                    threading.Event())
    assert seen["env_python"] == "X:/lab/python.exe"


def test_the_board_selfcheck():
    envn = dict(os.environ)
    r = subprocess.run(["node", str(ROOT / "tests" / "autofit_board_selfcheck.cjs")],
                       capture_output=True, text=True, env=envn, timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
