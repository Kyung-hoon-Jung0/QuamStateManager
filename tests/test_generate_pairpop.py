"""Drives the Generate-Config per-pair 2Q-gate populate-column behavioral check
(tests/generate_pairpop_selfcheck.cjs) under node + jsdom.

Asserts the populate step renders the right columns for the chip's gate (CR's
drive/cancel/correction phases vs CZ's variant + flux params), that CR and
CZ-fixed pairs (which have no coupler wiring line) still get a populate table,
and that values flow into spec.populate.pairs. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_pairpop_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_pairpop_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
