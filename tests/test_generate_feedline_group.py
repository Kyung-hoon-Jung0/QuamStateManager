"""Drives the step-5 feedline-name check (tests/generate_feedline_group_selfcheck.cjs)
under node + jsdom.

QA F25: any step-5 drag (a drive drag included) renamed every feedline group
"fl_<con>_<slot>_<port>". A drag now keeps each feedline's name while still
re-deriving membership from the dragged layout (same rr output port <=> same
group). Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_feedline_group_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_feedline_group_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
