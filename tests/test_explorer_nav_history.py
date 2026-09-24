"""QA F16 -- a jump into the Explorer is a navigation.

A run figure's per-qubit "Edit qN" button (and every other
``_navigateToExplorerPath`` caller) loaded /explorer into #table-pane with no
history entry -- the URL and the sidebar still said /datasets and Back left the
app -- and the q button did not collapse the run below first, so the Explorer
arrived as a sliver. The client behaviour is pinned by
``tests/explorer_nav_history_selfcheck.cjs`` (REAL htmx + REAL app.js, a fake
XMLHttpRequest as the server); the q button's markup is pinned here on what
the REAL template renders.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent


def test_figure_qubit_button_is_the_collapsing_navigation(tmp_path):
    f = tmp_path / "data"
    d = f / "2026-09-15" / "#7_03_resonator_spectroscopy_single_070000"
    d.mkdir(parents=True)
    (d / "node.json").write_text(json.dumps({
        "metadata": {"name": "03_resonator_spectroscopy_single", "status": "successful",
                     "run_start": "2026-09-15T01:00:00", "run_end": "2026-09-15T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q3"]}}, "outcomes": {}},
        "id": 7, "parents": [], "created_at": "2026-09-15T01:00:00",
    }), encoding="utf-8")
    (d / "data.json").write_text(json.dumps({"figures": {"amplitude": "./figures.amplitude.png"}}),
                                 encoding="utf-8")
    (d / "figures.amplitude.png").write_bytes(b"\x89PNG")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/workspace/add", data={"folder": str(f)})
    with app.app_context():
        key = routes._folder_key(f)
    html = c.get(f"/dataset/{key}:7", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'title="Edit q3"' in html
    btn = html.split('title="Edit q3"', 1)[0].rsplit("<button", 1)[1]
    assert "goToQubitState(" in btn and "_navigateToExplorerPath(" not in btn, btn


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_explorer_jump_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "explorer_nav_history_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 12, proc.stdout
