"""Drives the docs/150 Overview per-panel customization behavioral check
(tests/overview_custom_selfcheck.cjs) under node + jsdom.

Display preferences ONLY — which tiles show and which aggregate each big
number states; every number is still the one computeAggregates output.
Pins: nothing stored at defaults; stat override switches + tags the big
number and persists; composite tiles remove-only; added tiles offer the
chip's REAL metric keys and render honestly with no values; reset clears;
preferences survive a re-mount. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "overview_custom_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_overview_custom_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
