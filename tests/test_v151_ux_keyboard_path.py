"""P1-7 (U19): keyboard-only editing is practical.

A keyboard edit took ~270 keys: "/" focused the grid search but Enter and Down
did not enter the grid (70 Tabs through ~30 filter chips with no focus ring),
Ctrl+Enter ("Apply all (when armed)") did nothing once the row had committed,
and there was no skip link past ~80 sidebar stops. Now: Enter / Down from the
search land on the first matching cell, the chip strip is one tab stop with
arrow keys and a focus ring, Ctrl+Enter is Apply (the grid's, else the top
bar's, as a click), and "Skip to content" leads the page. Behaviour pinned in
``tests/kb_path_selfcheck.cjs`` against the REAL app.js + bulk-edit.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_WEB = _ROOT / "quam_state_manager" / "web"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_the_keyboard_path_in_the_real_scripts():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "kb_path_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"


def test_the_skip_link_is_the_first_stop(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    html = app.test_client().get("/").get_data(as_text=True)
    body = html[html.index("<body"):]
    first_a = body.index("<a ")
    assert body[first_a:].startswith('<a href="#table-pane" class="skip-link"'), body[first_a:first_a + 80]
    css = (_WEB / "static" / "style.css").read_text(encoding="utf-8")
    assert ".skip-link:focus" in css and ".bulk-chip:focus-visible" in css


def test_the_chip_strip_renders_one_tab_stop():
    tpl = (_WEB / "templates" / "_bulkedit.html").read_text(encoding="utf-8")
    assert 'tabindex="{{ 0 if loop.first else -1 }}"' in tpl


def test_the_documented_shortcuts_match_what_works():
    js = (_WEB / "static" / "app.js").read_text(encoding="utf-8")
    assert "'Apply all (when armed)'" not in js
    assert "['Ctrl+Enter', 'Apply: the grid\\'s un-applied cells, else the top-bar Apply']" in js
    help_ = (_WEB / "templates" / "_help.html").read_text(encoding="utf-8")
    assert "<div>Apply all</div>" not in help_
    assert "else the top-bar <b>Apply</b> (same confirm)" in help_
    assert "first matching cell" in help_
