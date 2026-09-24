"""QA F9 + F10: what Escape, an undo repaint and Reset put back, the column
header (min/max + extreme colouring) and the row error message follow.

The behaviour lives in `bulk-edit.js` / `pair-edit.js` and is driven under
jsdom by `grid_stats_selfcheck.cjs` (the real applyRow against a mocked
/field/edit-batch refusal, the real document-level Escape handler, the real
revertPaths / resetDirty of both grids).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "grid_stats_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_grid_stats_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
