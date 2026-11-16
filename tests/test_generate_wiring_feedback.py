"""Drives tests/generate_wiring_feedback_selfcheck.cjs under node + jsdom: step-5
pin forms, feedline summary freshness, the stranded "Checking wiring" status and
the per-pair CZ shape default. Skips without node + jsdom.
"""

# Selfcheck examples use generic device names and lab keys.
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_wiring_feedback_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_wiring_feedback_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
     timeout=300)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
