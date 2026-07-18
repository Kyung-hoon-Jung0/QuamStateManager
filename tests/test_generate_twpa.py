"""Drives the wizard TWPA-plumbing behavioral check
(tests/generate_twpa_selfcheck.cjs) under node + jsdom.

Pins the review-r6 TWPA-loss fixes: hydrateFromSpec normalizes bare-string
twpa ids (old sidecars) to the wizard's {id, qubits} objects; deriveLines
preserves twpa_pump/twpa_isolation lines + pinned channels across re-derives
(it used to wipe them, so a re-generated chip built without its TWPAs); a
newly added TWPA gets an unpinned pump line. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_twpa_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_twpa_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
