"""Drives the step-4 qubit naming scheme behavioral check
(tests/generate_naming_selfcheck.cjs) under node + jsdom.

Customer requirement: user-settable qubit notation — scheme presets
(q1…, q0…, board-derived grid letters qA1/qB2, custom prefix + start)
plus per-qubit inline rename. The selfcheck pins the one-pass identity
remap (populate buckets, pair entries + keys, TWPA lists, allocation
drop), the name rule (leading lowercase 'q', no '-', no whitespace,
unique — with input restore on an invalid rename), the scheme-aware
renumber gate (fires on default-scheme holes, silent after a hand
rename), draft round-trip, count-change append/truncate semantics,
and regenerate mode hiding the controls. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_naming_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_naming_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
