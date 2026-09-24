"""QA liveedit-r2-21: focus comes back after the JSON cell editor closes and
after Apply all / Apply to live, instead of dropping to <body>.

The behaviour lives in `bulk-edit.js` / `pair-edit.js` and is driven under
jsdom by `focus_return_selfcheck.cjs` (the real openJsonCell, the real
applyAll of both grids against a mocked /field/edit-batch, with Chrome's
focus fixup of a disabled button reproduced by hand). The Flat View's JSON
modal is pinned in `all_values_v2_selfcheck.cjs` (V5).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "focus_return_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_focus_return_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
