"""w7 liveedit: a wide Live-Edit grid's per-interaction work, pinned by count.

Drives the real grid-virt.js + bulk-edit.js + pair-edit.js under jsdom via
``liveedit_big_grid_selfcheck.cjs``: the header-stats pass is one index (not
two whole-table scans per column), an empty search query builds no search
text, the cold right-hand tail of a grid over the cell gate is taken out of
layout by one rule per column and put back from its left end on demand, a far
JUMP reveals only the columns it lands on (a blank spacer column holds the
width of the run left of them, and a landing at the end stays at the end), and
the column-visibility pass walks only the columns that changed.

w8 gridscroll: the rules are written once and a column's on-class decides
(no stylesheet write while scrolling), a SLOW scroll keeps a bounded window on
both sides (columns left behind collapse behind a measured spacer and give
their cells back to fragments), nothing on screen moves, no cold cell shows,
and no step queries the whole table; with two grids in one scroller no frame
changes both and neither starves; the toolbars follow a scroll without walking
the grids.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "liveedit_big_grid_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_liveedit_big_grid_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=600,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    # the LAST line, not merely a zero exit: a pending await that never
    # settles ends node with exit 0 and half the checks unrun
    assert r.stdout.strip().splitlines()[-1] == "all checks passed (101 assertions)", r.stdout[-2000:]
