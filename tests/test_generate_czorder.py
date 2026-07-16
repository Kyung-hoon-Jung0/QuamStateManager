"""Drives the CZ automatic control/target orientation behavioral check
(tests/generate_czorder_selfcheck.cjs) under node + jsdom.

Customer requirement: for CZ gates the pair roles are frequency-derived —
higher RF_freq = control, lower = target, automatically. The selfcheck pins
the flip on RF_freq entry (populate bucket + moving_qubit role + pinned
wiring lines + allocation keys all follow the pair id), the no-flip cases
(equal / missing frequency / cz_order=manual / CR chip), flip-back on
re-edit, the step-4 dropdown marking a hand-picked order manual, the
draft-restore correction, and the review-step orientation summary.
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_czorder_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_czorder_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
