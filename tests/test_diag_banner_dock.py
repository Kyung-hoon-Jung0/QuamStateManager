"""The late crash banner never covers the controls a user works with (docs/251, C-13).

It floated at the bottom-left -- over the Agent composer's select, the plan-mode
selects and Agent Setup's Test buttons -- until a main-pane swap, a pointer over
the head block or a hidden tab, none of which a user working inside one page
produces. Now it floats over exactly the box it will take in the flow and joins
the flow at the first moment MEASURED to move nothing under the pointer (the
pointer's scroller absorbs the shift when it can); it never appears under a
resting pointer.

Runs the shipped app.js + style.css under jsdom with a modelled layout:
``tests/diag_banner_dock_selfcheck.cjs``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "diag_banner_dock_selfcheck.cjs"


def test_diag_banner_dock_selfcheck():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    if not (_ROOT / "node_modules" / "jsdom").exists():
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True,
                       encoding="utf-8", timeout=120, cwd=str(_ROOT))
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "all ok" in r.stdout
    assert "FAIL" not in out
    for line in (
        "ok - the floating banner sits on the box it takes in the flow",
        "ok - pointer on a bottom-anchored control: the banner joins the flow at once",
        "ok - the scroller scrolled by exactly the banner height",
        "ok - pointer on content docking would move: the banner keeps floating",
        "ok - and it does not appear under the resting pointer (held)",
        "ok - pointer on the floating banner itself: it joins the flow",
        "ok - the pointer left the window: it joins the flow",
        "ok - and never scrolls a pane whose content was just replaced",
    ):
        assert line in r.stdout, line
