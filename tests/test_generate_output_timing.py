"""Drives tests/generate_output_timing_selfcheck.cjs under node + jsdom: the
Review row "Output timing" (the rule) and the build result timing table.
Skips without node + jsdom.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_output_timing_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_output_timing_selfcheck_passes():
    r = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "OK (" in r.stdout, (r.stdout + r.stderr)
