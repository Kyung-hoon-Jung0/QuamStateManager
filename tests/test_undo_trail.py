"""Undo trail + tree search list + class-based column hiding (night session
2026-08-28): drivers for the jsdom harnesses and the greppable pins."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"


def _run(name: str, must: str):
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / name)], capture_output=True, text=True,
                         encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert must in res.stdout


def test_undo_trail_selfcheck():
    _run("undo_trail_selfcheck.cjs", "ok - go to field flashes + scrolls a VISIBLE field")


def test_tree_search_list_selfcheck():
    _run("tree_search_list_selfcheck.cjs", "ok - a broad query renders the result list instead of expanding")


def test_the_trail_ships_and_the_grid_hides_columns_by_class():
    base = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "undo-trail.js" in base
    bulk = (_ROOT / "quam_state_manager" / "web" / "templates" / "_bulkedit.html").read_text(encoding="utf-8")
    assert bulk.count("ck-{{ loop.index0 }}") == 4, "th + td of both grids carry the column index class"
    js = (_STATIC / "bulk-edit.js").read_text(encoding="utf-8")
    assert "'#bulk-table td.ck-' + _idxOf[k]" in js, "search hides columns by class selector"
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "uncovered += (res.uncovered || []).length;" in app, \
        "only a dishonest repaint (a cell found but not repaintable) triggers the grid re-GET"
