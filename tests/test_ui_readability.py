"""Drives the r5 readability-batch behavioral check
(tests/ui_readability_selfcheck.cjs) under node + jsdom.

Pins: rollingStats / trendStatTraces (the moving-average + ±σ statistics
layer on history/trend charts — skips short series, band derives from the
line color, silent in legend/hover); setUiScale (global CSS zoom 80–150%
in 10% steps, persisted in quam_ui_scale, label sync, cleared at 100%);
the sidebar compare multi-select (shift-click range, live count on the
Compare/Trend buttons, Clear chip); and the folder browser's dataset-mode
highlighting (is-dataset rows only with kind=dataset; quam_state
highlighting preserved in state mode). Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "ui_readability_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_ui_readability_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
