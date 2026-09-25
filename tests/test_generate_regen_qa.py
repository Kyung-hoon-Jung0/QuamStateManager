"""Drives tests/generate_regen_qa_selfcheck.cjs under node + jsdom: the
Re-generate wizard's QA fixes (regenerate-r2-09 pair reversal, r2-10 TWPA
rename, and the F1 / r2-09 / r2-10 build-panel lines). Skips without jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_regen_qa_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_regen_qa_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all" in r.stdout and "checks passed" in r.stdout, (r.stdout + r.stderr)
