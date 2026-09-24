"""Drives tests/generate_regen_fsp_selfcheck.cjs under node + jsdom.

QA regenerate-r2-04 / r2-06: the Re-generate build asks (Live Edit's own FSP
popup) before a changed port FSP moves the port's calibrated pulses, re-POSTs
with the answer keyed by port + new FSP, never builds on cancel, and the
result panel names the amplitudes it rescaled. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_regen_fsp_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_regen_fsp_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
