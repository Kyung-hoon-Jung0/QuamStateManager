"""v1.5.1 (r6 P2-7): one "Not done" expiry toast per two-press button.

A two-press button that ran out of time says "Not done: the confirm press
did not come within 5 s, so nothing was changed. Press twice to do it." --
and several identical ones piled up on screen at once. Now a new expiry of
a button replaces that button's previous toast (also when the panel
re-rendered the button), pressing the button again takes its toast down, and
toasts of different buttons may coexist. Pinned with the REAL sync-panel
markup (/state/review with an unapplied edit) and the SHIPPED app.js
(tests/v151_r6_expiry_toast_selfcheck.cjs).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_one_expiry_toast_per_button(tmp_path):
    live = tmp_path / "chip"
    live.mkdir()
    (live / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"id": "q1", "T1": 1e-5}}, "qubit_pairs": {},
        "active_qubit_names": ["q1"]}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "2e-05"}).get_json()["ok"]
    panel = c.get("/state/review", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert "sp-take sync-arm" in panel and "sp-discard-all" in panel, panel[:800]
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"panel": panel}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_r6_expiry_toast_selfcheck.cjs"), str(fx)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
