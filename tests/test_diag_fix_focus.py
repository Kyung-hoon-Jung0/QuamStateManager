"""QA F-S + diagnostics-r2-20 -- what a one-click Diagnostics fix leaves behind.

F-S: ``applyDiagFix`` re-rendered the whole page (GET /diagnostics into
``#table-pane``), so every domain came back with the server's default open
state -- a Config domain the user had folded sprang open. The fix now goes
through the one announcer (``_diagChanged``) and the ``#diag-findings``
self-refresh, which already carries the folds (docs/141 4l-review).

diagnostics-r2-20: that refresh replaces the fixed row and the focused button
with it, so focus fell to ``<body>`` and a keyboard user restarted 40+ Tab stops
from the top. Focus now lands on the finding that took the fixed one's place --
only when the swap dropped it (docs/75), never after a mouse press.

Behaviour, not markup: ``diag_fix_focus_selfcheck.cjs`` runs the shipped app.js
under jsdom with an htmx stub that performs the swaps the real one would.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_diag_fix_focus_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "diag_fix_focus_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2 and "jsdom not installed" in (r.stdout + r.stderr):
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "diag_fix_focus_selfcheck ok" in r.stdout, (r.stdout + r.stderr)


def test_the_fix_never_re_renders_the_whole_pane():
    """The static half of S1, so a jsdom-less env still catches the regression."""
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    i = js.index("window.applyDiagFix = function")
    seg = js[i:js.index("window.togglePreviewIssues", i)]
    assert '"#table-pane"' not in seg and "'#table-pane'" not in seg
    assert "_diagChanged(" in seg
