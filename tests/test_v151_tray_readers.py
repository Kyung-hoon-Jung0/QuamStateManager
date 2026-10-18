"""v1.5.1 (3b leftovers): the Json Tree pending tint and the tray undo
button's tooltip read the tray markup the server actually renders.

The rows moved to the sync panel; both readers kept looking for rows inside
``#pending-tray`` (``.tray-change-path`` / ``.tray-change-item``), so the
tint came only from commit sites and the tooltip never named the edit the
next press removes. Pinned on the REAL /state/tray fragment and the REAL
/explorer fragment (its inline script builds the tree) under jsdom.
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
HX = {"HX-Request": "true"}


def _state():
    qs = {f"q{i}": {"id": f"q{i}", "T1": 1.0e-5 * i, "T2": 2.0e-5 * i} for i in (1, 2, 3)}
    return {"qubits": qs, "qubit_pairs": {}, "active_qubit_names": sorted(qs)}


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "chip" / "quam_state"
    live.mkdir(parents=True)
    (live / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "c": c, "tmp": tmp_path}


def _run(env, fixture: dict) -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    fx = env["tmp"] / "fixture.json"
    fx.write_text(json.dumps(fixture), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "v151_tray_readers_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and "DONE" in r.stdout, r.stdout[-3000:] + r.stderr[-3000:]
    return r.stdout


def test_tint_and_undo_tooltip_read_the_real_tray(env):
    c = env["c"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "1.5e-05"}).get_json()["ok"]
    r = c.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.q3.T1", "value": 3.5e-05},
        {"dot_path": "qubits.q3.T2", "value": 6.5e-05}]})
    assert r.status_code == 200 and r.get_json()["ok"], r.data[:400]
    tray = c.get("/state/tray", headers=HX).get_data(as_text=True)
    assert 'id="tray-undo-btn"' in tray
    explorer = c.get("/explorer", headers=HX).get_data(as_text=True)
    assert 'id="explorer-tree-state"' in explorer
    out = _run(env, {"tray_html": tray, "explorer_html": explorer,
                     "pending": ["qubits.q1.T1", "qubits.q3.T1", "qubits.q3.T2"],
                     "clean": ["qubits.q2.T1", "qubits.q1.T2"],
                     "top_path": "qubits.q3.T2", "top_n": 2})
    assert "TIP" in out


def test_the_page_render_carries_the_undo_top_too(env):
    """The full-page tray gets its context from the context processor, not
    _render_tray -- both must stamp it (the docs/110 trap)."""
    c = env["c"]
    assert c.post("/field/edit", data={"dot_path": "qubits.q2.T1", "value": "2.5e-05"}).get_json()["ok"]
    page = c.get("/explorer").get_data(as_text=True)
    assert 'data-top-path="qubits.q2.T1"' in page and 'data-top-n="1"' in page
