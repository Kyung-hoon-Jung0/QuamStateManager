"""Drives tests/generate_reset_step_selfcheck.cjs under node + jsdom.

docs/241: the Generate wizard's "Reset step" resets only the step on screen
and stays there (customer ask: Reset used to go back to the Environment page
and start the whole wizard over). On Re-generate a step resets to the source
chip's values. "Start over" keeps the old whole-wizard reset. Skips without
node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_reset_step_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_reset_step_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    out = r.stdout + r.stderr
    assert r.returncode == 0 and "FAIL" not in out, out[-3000:]
    assert "all passed" in r.stdout, out[-3000:]
