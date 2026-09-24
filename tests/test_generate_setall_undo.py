"""Drives tests/generate_setall_undo_selfcheck.cjs under node + jsdom.

QA regenerate-r2-23: Ctrl+Z after a Populate "Set all" restores each row's own
previous value (and the populate-touched marks, and in absolute power mode the
re-allocated amps + FSPs) instead of replaying the Set-all box's "" as an empty
commit that blanked the whole column. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_setall_undo_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_setall_undo_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
