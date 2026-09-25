"""QA r2-06 -- the full-page run view.

On ``/dataset/<uid>`` (and after the inspector's full-page button) the run
lives in ``#table-pane``; its controls assumed the inspector: the close button
emptied an empty pane, the down arrow and the parent link stacked the next run
underneath, and "Go to state" replaced the run with the Explorer. The client
behaviour is pinned by ``tests/ds_fullpage_fragcheck.cjs`` (REAL app.js + REAL
htmx) on the markup the REAL template renders here.
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


def _seed(root: Path, run_id: int, parent: int | None) -> None:
    d = root / "2026-09-15" / f"#{run_id}_03_resonator_spectroscopy_single_0{run_id % 10}0000"
    d.mkdir(parents=True)
    (d / "node.json").write_text(json.dumps({
        "metadata": {"name": "03_resonator_spectroscopy_single", "status": "successful",
                     "run_start": "2026-09-15T01:00:00", "run_end": "2026-09-15T01:00:01"},
        "data": {"parameters": {"model": {"qubits": ["q3"]}}, "outcomes": {}},
        "id": run_id, "parents": [parent] if parent else [],
        "created_at": "2026-09-15T01:00:00",
    }), encoding="utf-8")
    (d / "data.json").write_text(json.dumps({"fit_results": {"q3": {"f": 1.0}}}),
                                 encoding="utf-8")


@pytest.fixture
def rendered(tmp_path):
    f = tmp_path / "data"
    _seed(f, 4103, None)
    _seed(f, 4113, 4103)
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/workspace/add", data={"folder": str(f)})
    with app.app_context():
        key = routes._folder_key(f)
    html = c.get(f"/dataset/{key}:4113",
                 headers={"HX-Request": "true"}).get_data(as_text=True)
    return key, html


def test_run_controls_act_on_the_pane_that_holds_the_run(rendered):
    _key, html = rendered
    # the markup the selfcheck drives
    assert 'onclick="dsCloseRun(this)"' in html
    assert 'onclick="dsNavRun(1, this)"' in html
    assert 'hx-target="closest #table-pane, #inspector-pane"' in html


def test_full_page_hides_the_inspector_only_buttons():
    # on the full page the expand button is where you already are and pin
    # compares inside the inspector only -- both were dead buttons there
    css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
    rule = css.split("#table-pane .inspector-header-dataset .inspector-expand,", 1)
    assert len(rule) == 2, "full-page hide rule missing"
    head = rule[1].split("}", 1)[0]
    assert ".inspector-pin" in head and "display: none" in head, head


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_full_page_client_selfcheck(rendered, tmp_path):
    _key, html = rendered
    page = tmp_path / "detail.html"
    page.write_text(html, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "ds_fullpage_fragcheck.cjs"), str(page)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 15, proc.stdout
