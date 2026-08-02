"""Drives the r16 6 apply-UX behavioral check (tests/apply_ux_selfcheck.cjs)
under node + jsdom against the REAL app.js.

Pins the docs/65 amendment: a declined needs_confirm shows an explicit
Cancelled toast; applyEditsToLive re-checks the SERVER (GET /state/tray)
before declaring "nothing to apply" on stale tray attributes, applies when
the fresh tray shows pending work, and no-op-toasts honestly (one recheck,
no loop) when genuinely empty. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "apply_ux_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_apply_ux_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
        timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
