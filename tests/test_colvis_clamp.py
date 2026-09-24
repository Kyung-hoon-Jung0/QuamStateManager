"""QA liveedit-r2-30: an opened `.bulk-colvis` picker (Qubits / Pairs /
Properties / Datasets column pickers) is nudged back inside the pane that
clips it, instead of running past the window's right edge at 800-911 px.

The behaviour lives in `app.js` (`_clampColvisMenu` + a document-level
`toggle` listener) and is driven under jsdom by `colvis_clamp_selfcheck.cjs`
with the rects measured in real Chrome.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "colvis_clamp_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_colvis_clamp_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
