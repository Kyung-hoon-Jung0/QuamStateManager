"""Drives tests/generate_crwiring_selfcheck.cjs under node + jsdom: the step-5
pin <-> channel mapping (CR/ZZ are MW drive tones, docs/54; a resonator pin
names the OUTPUT and its input follows the LO partner, QA F6), ALLOC_KEY,
deriveLines' CR/ZZ lines and applyPortCsv. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_crwiring_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_crwiring_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
