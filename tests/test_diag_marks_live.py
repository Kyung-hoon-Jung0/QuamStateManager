"""Drives tests/diag_marks_live_selfcheck.cjs under node + jsdom (jsontree-r2-19).

The Json Tree View's ⚠ row marks and the sidebar diagnostics dots follow an
edit ('diagnostics-changed') and an acknowledgement followed by the PaneState
keep-alive restore ('paneRestored') without a reload; the re-mark never reopens
a folded subtree, and a late findings.json reply cannot repaint a stale verdict.
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "diag_marks_live_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_diag_marks_live_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=240,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
