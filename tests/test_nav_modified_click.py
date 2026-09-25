"""QA generate-r2-22: Ctrl/Cmd/Shift+click on an ``<a href hx-get>`` nav link
opens a tab and leaves the current page alone.

The bundled htmx 2.0.4 swaps the pane on a modified click too (it exempts only
hx-boost anchors), so a Ctrl+click on the sidebar replaced the Generate wizard
mid-step. ``window.NavModifiedClick`` in app.js stops a modified primary click
in the capture phase, before htmx's listener on the anchor, without preventing
the default. The behaviour is driven for real (real htmx + the shipped block)
by ``tests/nav_modified_click_selfcheck.cjs``.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_APP = _ROOT / "quam_state_manager" / "web" / "static" / "app.js"


def test_the_guard_never_prevents_the_default():
    """Preventing the default would cancel the new tab too -- the whole point
    is that only htmx is kept out of the event."""
    src = _APP.read_text(encoding="utf-8")
    start = src.index("window.NavModifiedClick = (function () {")
    block = src[start:src.index("})();", start)]
    assert "stopPropagation()" in block
    assert "preventDefault" not in block
    assert re.search(r'addEventListener\("click", onClick, true\)', block), "must be a CAPTURE listener"


def test_modified_click_under_real_htmx():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    if subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "nav_modified_click_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace",
                         cwd=str(_ROOT), timeout=300)
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "all checks passed" in res.stdout
