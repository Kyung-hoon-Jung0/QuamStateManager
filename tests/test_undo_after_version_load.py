"""docs/301 (F24, F27): Ctrl+Z after loading a whole version says what to do.

A version loaded with "Load as working state" has no change-log entries, so
Ctrl+Z had nothing to undo and said nothing; the door that sets the version
aside is Take live. /undo now names it -- through the response's HX-Trigger
showToast, which the page did not listen for at all until now (Auto-Sync's
"which half was withheld" message was never shown either)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes as routes_mod
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def env(tmp_path):
    chip = tmp_path / "quam_state"
    chip.mkdir()
    (chip / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "T1": 1.0e-5}}, "active_qubit_names": ["q1"]}), encoding="utf-8")
    (chip / "wiring.json").write_text(json.dumps({"network": {}, "wiring": {"qubits": {}}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    assert c.post("/state-history/snapshot").status_code == 200
    with app.app_context():
        ts = routes_mod._history().list_snapshots(chip)[0].timestamp
    return {"client": c, "ts": ts, "chip": chip}


def _toast(resp) -> dict | None:
    trig = resp.headers.get("HX-Trigger")
    return json.loads(trig).get("showToast") if trig else None


def test_ctrl_z_after_a_version_load_names_take_live(env):
    c = env["client"]
    assert c.post(f"/state-history/{env['ts']}/stage").status_code == 200
    r = c.post("/undo")
    assert r.status_code == 200
    toast = _toast(r)
    assert toast and "Take live" in toast["message"] and "Nothing to undo" in toast["message"], toast


def test_ctrl_z_after_a_version_load_never_walks_into_an_older_apply(env):
    """The journal holds an earlier apply. The press used to walk into it and
    stage that apply's inverse on top of the loaded version (never live: the
    docs/160 C2 fix); it now stops at the load and names Take live."""
    c = env["client"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "3e-05"}).status_code == 200
    assert c.post("/state/apply-to-live", headers={"X-SM-Actor": "tester"}).status_code == 200
    live_before = (env["chip"] / "state.json").read_bytes()
    tray = c.get("/state/tray").get_data(as_text=True)
    assert 'data-jrn-what=' in tray, "the fixture reaches the state: the journal names the apply"
    assert c.post(f"/state-history/{env['ts']}/stage").status_code == 200
    tray = c.get("/state/tray").get_data(as_text=True)
    assert 'data-jrn-what=' not in tray, "the tray button does not advertise the older apply"
    r = c.post("/undo")
    assert r.status_code == 200 and "Take live" in (_toast(r) or {}).get("message", "")
    assert (env["chip"] / "state.json").read_bytes() == live_before, "the live chip is untouched"


def test_ctrl_z_with_nothing_loaded_stays_quiet(env):
    r = env["client"].post("/undo")
    assert r.status_code == 200 and _toast(r) is None


def _node_env():
    env = dict(os.environ)
    for cand in (ROOT / "node_modules", ROOT.parent / "statemanager" / "node_modules"):
        if (cand / "jsdom").is_dir():
            env["NODE_PATH"] = str(cand)
            return env
    return None


def test_the_page_shows_a_responses_toast_trigger():
    node_env = _node_env()
    if shutil.which("node") is None or node_env is None:
        pytest.skip("node + jsdom not installed")
    r = subprocess.run(["node", str(ROOT / "tests" / "server_toast_selfcheck.cjs")],
                       capture_output=True, text=True, env=node_env, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all passed" in r.stdout

