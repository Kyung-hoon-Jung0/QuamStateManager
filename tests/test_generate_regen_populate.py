"""Drives the r16 regen populate-protect + scripts-path wizard behavioral
check (tests/generate_regen_populate_selfcheck.cjs) under node + jsdom.

Pins docs/72's wizard side: the hydration-time populate baseline is a deep
copy, autoApplyStandardDefaults no-ops in regenerate mode, applyLoAssignments
is fill-only-empty there (force re-solve marks cells touched; data-dirty
cells never clobbered), scripts export defaults ON with the path following
the output folder until touched, and the populate band column stores INT
1..3. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_regen_populate_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_regen_populate_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
