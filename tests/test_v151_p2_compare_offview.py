"""v1.5.1 U P2-7: the sidebar's compare row and filter box keep their shape.

* "Compare Selected (2 — 2 not in view)" broke over five lines in its third of
  the sidebar; the label is the count only, the off-view runs get their own
  line under the buttons (REAL page + REAL filtered tree, in
  tests/v151_compare_offview_selfcheck.cjs).
* The filter box wrapped a run name on under its "?" once its own scrollbar
  appeared; its right padding now holds both (shipped stylesheet over the
  REAL page, tests/v151_css_selfcheck.cjs).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app
from tests.css_pin import run_css_cases

ROOT = Path(__file__).resolve().parent.parent


def _run(root: Path, rid: int, name: str) -> None:
    run = root / "2026-10-16" / f"#{rid}_{name}_10150{rid}"
    (run / "quam_state").mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({"id": rid, "metadata": {"name": name, "status": "finished"},
                                               "data": {"parameters": {"model": {"qubits": ["q1"]}}}}),
                                   encoding="utf-8")
    (run / "quam_state" / "state.json").write_text('{"qubits": {"q1": {"id": "q1"}}}', encoding="utf-8")
    (run / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")


@pytest.fixture
def c(tmp_path):
    for rid, name in ((1, "ramsey"), (2, "ramsey"), (3, "spectroscopy")):
        _run(tmp_path / "data", rid, name)
    client = create_app(testing=True, instance_path=str(tmp_path / "_i")).test_client()
    assert client.post("/workspace/add", data={"folder": str(tmp_path / "data")}).status_code == 200
    return client


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_label_stays_short_and_off_view_runs_get_a_line(c, tmp_path):
    page = c.get("/datasets").get_data(as_text=True)
    filtered = c.get("/workspace/tree?name=spectroscopy").get_data(as_text=True)
    assert 'id="compare-offview"' in page and "#2_ramsey" not in filtered
    fx = tmp_path / "fx.json"
    tree = c.get("/workspace/tree").get_data(as_text=True)
    fx.write_text(json.dumps({"page": page, "tree": tree, "filtered_tree": filtered, "n_runs": 3,
                              "hidden_by_filter": ["#1_ramsey", "#2_ramsey"]}), encoding="utf-8")
    p = subprocess.run(["node", str(ROOT / "tests" / "v151_compare_offview_selfcheck.cjs"), str(fx)],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT), timeout=120)
    if p.returncode == 2 and "jsdom not installed" in (p.stderr or ""):
        pytest.skip("jsdom not installed")
    assert p.returncode == 0, f"stdout:\n{p.stdout}\nstderr:\n{p.stderr}"


def test_the_filter_box_leaves_room_for_its_help_button(c, tmp_path):
    page = c.get("/datasets").get_data(as_text=True)
    run_css_cases(tmp_path, [{"name": "sidebar filter", "html": page, "checks": [
        {"sel": "#sidebar-filter-input", "prop": "paddingRight", "eq": "2.7rem",
         "why": "the ? (1.5rem) and the box's scrollbar both fit right of the text"}]}])
