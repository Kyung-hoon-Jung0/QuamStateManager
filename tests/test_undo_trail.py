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
    # docs/141 4d: the class rules are static (one per column index, written
    # once) and a keystroke toggles only `sh-N` on the table for the delta
    assert "'#bulk-table.sh-' + n + ' td.ck-' + n + ' { display: none !important; }'" in js, \
        "search hides columns by a STATIC class-selector sheet"
    assert "_want['sh-' + _idxOf[k]] = 1;" in js, "and toggles per-column sh- classes on the table"
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "uncovered += (res.uncovered || []).length;" in app, \
        "only a dishonest repaint (a cell found but not repaintable) triggers the grid re-GET"


def test_undo_repaint_selfcheck_carries_the_uncovered_contract():
    _run("undo_repaint_selfcheck.cjs", "ok - readOnly: a found-but-unwritable cell IS uncovered")


def test_undo_pages_selfcheck():
    _run("undo_pages_selfcheck.cjs", "ok - a click-away after the undo does NOT re-commit")


def test_undo_never_navigates_by_itself():
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "if (window.UndoNav) window.UndoNav.flashVisible(d.entries || []);" in app, \
        "cellsReverted flashes what is visible; only the trail's button navigates"
    assert "if (window.UndoNav) window.UndoNav.handle(d.entries || []);" not in app
    assert "window._treeModelSet = _treeModelSet;" in app, "the tree model follows edit / undo / redo"
    assert 'if (input.hasAttribute("data-committed")) input.setAttribute("data-committed", oldValueStr);' in app


def test_review_fixes_of_4ffee11():
    # found-but-unwritable stays uncovered (docs/124 M-10): the grids never
    # gate the push on `wrote` again
    for name in ("bulk-edit.js", "pair-edit.js"):
        js = (_STATIC / name).read_text(encoding="utf-8")
        assert "else if (wrote) uncovered.push" not in js, name
        assert "c.readOnly || c.tagName !== 'INPUT'" in js, name
        assert ".bulk-col-hist, .key-help-btn')) return;" in js, name + ": the header sort ignores the ? button itself"
    bulk = (_STATIC / "bulk-edit.js").read_text(encoding="utf-8")
    assert ".bulk-cell-list[data-path=" in bulk, "the listedit preview span counts as FOUND"
    app = (_STATIC / "app.js").read_text(encoding="utf-8")
    assert "container.insertBefore(el, container.firstChild);" in app, "the tree result list lives INSIDE its tree"
    assert "if (_tree) _keyHelpOn = !!_tree._keyHelp;" in app, "_rebuildNode derives the ? flag from its own tree"
    manual = (_STATIC / "manual.js").read_text(encoding="utf-8")
    assert "e.preventDefault(); e.stopPropagation();" not in manual, "the ? handler must not silence click-away listeners"
    css = (_STATIC / "style.css").read_text(encoding="utf-8")
    assert "position: fixed; right: 0.8rem; bottom: 5rem; z-index: 130;" in css, "the trail sits above the toast sink"
