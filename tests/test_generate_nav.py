"""Drives the Generate-Config wizard's step-navigation / draft-persistence
behavioral check (tests/generate_nav_selfcheck.cjs) under node + jsdom.

The selfcheck mounts the real _generate.html + generate.js in a jsdom DOM and
asserts that free Back/Forward navigation and a leave-and-return page swap never
lose entered data — including the webview "blur race" where a value is typed but
its commit event never fires. Skips when node or jsdom is unavailable (the
selfcheck exits 2 for a missing jsdom), so CI without a JS toolchain still runs
the rest of the suite. Install once with ``npm install jsdom``.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_nav_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_nav_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
