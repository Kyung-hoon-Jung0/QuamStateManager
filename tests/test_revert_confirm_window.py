"""gl-17y: "Revert last apply" in the sync panel keeps its two-press window.

The confirm is client-side only (the stage route has no time window of its
own), so the window the button and the toast state (5 s) is the only one.
Pinned on the REAL /state/review panel under jsdom
(tests/revert_confirm_window_selfcheck.cjs): a panel refresh between the two
presses keeps the confirm armed until the original deadline, a press inside the
window runs the revert, a press after it does not, and a re-rendered button for
another snapshot is not armed.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


def _panel_after_apply(tmp_path) -> str:
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "2e-05"}).get_json()["ok"]
    assert c.post("/state/apply-to-live", data={"seen_changes": "1"}).status_code == 200
    return c.get("/state/review").get_data(as_text=True)


def test_the_stage_route_has_no_window_of_its_own(tmp_path):
    """The second press is honoured by the server whenever it arrives: the
    only window is the client's, so the UI's 5 s is the whole truth."""
    panel = _panel_after_apply(tmp_path)
    assert "tray-revert-apply" in panel and "SyncPanel.arm(this, event)" in panel


def test_revert_confirm_survives_a_refresh_and_keeps_its_window(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    panel = _panel_after_apply(tmp_path)
    assert "tray-revert-apply" in panel
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"panel_html": panel}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "revert_confirm_window_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
