"""v1.5.1 R3 M6 / MINOR 3 / MINOR 7: two-press confirms.

A double click on "Keep mine -- overwrite live" overwrote live before its
armed label could be read; a held Enter confirmed any two-press button; a
detached button's countdown toasted "nothing was changed" after a write
landed; the seconds left were read as part of the button's name and the
expiry was not said to a screen reader. Pinned on the REAL /state/review
panel under jsdom (tests/v151_r3_two_press_selfcheck.cjs).
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


def test_two_press_confirms_need_a_readable_pause(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
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
    panel = c.get("/state/review").get_data(as_text=True)
    assert "sp-keep" in panel and "sp-discard-all" in panel
    fx = tmp_path / "fixture.json"
    fx.write_text(json.dumps({"panel_html": panel}), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_r3_two_press_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
