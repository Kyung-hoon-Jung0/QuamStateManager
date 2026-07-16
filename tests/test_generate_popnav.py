"""Drives the Populate-step arrow-key grid-navigation behavioral check
(tests/generate_popnav_selfcheck.cjs) under node + jsdom.

Customer feedback: value boxes must be walkable with the arrows — → leaves
the box only from the caret END, ← only from the START (mid-text arrows
keep native caret movement), ↑/↓ always move within the column including
the "Set all" row. Selects are never hijacked; disabled cells (FSP in
absolute power mode) are skipped; the keystroke live-write keeps values
committed across focus moves. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_popnav_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_popnav_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
