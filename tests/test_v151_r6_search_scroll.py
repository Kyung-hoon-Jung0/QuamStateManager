"""v1.5.1 (r6 P2-9): header Search results open at the top of the pane.

The results swap into the inspector pane, the same scroller the previous
inspector used, and kept its offset: they opened 661 px down with the first
results and the "Pages and actions" line out of view. Pinned with a REAL
qubit inspector and REAL search results and the SHIPPED app.js
(tests/v151_r6_search_scroll_selfcheck.cjs).
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
HX = {"HX-Request": "true"}
_WIRING = {"network": {"host": "127.0.0.1", "cluster_name": "C1"}}


@pytest.mark.skipif(shutil.which("node") is None, reason="node unavailable")
def test_search_results_open_at_the_top(tmp_path):
    live = tmp_path / "chip"
    live.mkdir()
    (live / "state.json").write_text(json.dumps({
        "qubits": {q: {"id": q, "T1": 1e-5, "T2ramsey": 2e-5} for q in ("q1", "q2", "q3")},
        "qubit_pairs": {}, "active_qubit_names": ["q1", "q2", "q3"]}), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(_WIRING), encoding="utf-8")
    routes._quam_cache.clear()
    c = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    qubit = c.get("/qubit/q1", headers=HX).get_data(as_text=True)
    search = c.get("/search?q=T1", headers=HX).get_data(as_text=True)
    search_qubit = c.get("/search?q=T1&category=qubit", headers=HX).get_data(as_text=True)
    assert "inspector-badge-qubit" in qubit and "search-panel" in search and "search-panel" in search_qubit
    fx = tmp_path / "fx.json"
    fx.write_text(json.dumps({"qubit": qubit, "search": search, "search_qubit": search_qubit}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_r6_search_scroll_selfcheck.cjs"), str(fx)],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if p.returncode == 2 and "jsdom not installed" in p.stderr:
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, p.stdout + p.stderr
