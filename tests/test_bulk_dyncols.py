"""Drives the r6 item-4 Table-View-full-coverage behavioral check
(tests/bulk_dyncols_selfcheck.cjs) under node + jsdom.

Pins the REAL bulk-edit.js: the openJsonCell whole-value JSON modal (peek
prefill, bad-JSON inline error, server-400 inline error, Ctrl+Enter posting the
PARSED value with expect_chip, preview + committed-marker update, tray/diag
side-effects, Esc cancel); the "N hidden columns match — Show" search hint over
the not-enabled dynamic model (Show persists to quam_bulk_dyncols + reloads the
pane); the collapsible dynamic groups in the Properties menu; and the
document-level configRequest listener that attaches dyncols= to /bulk GETs only
(never /bulk/all-values, replace-not-duplicate). Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "bulk_dyncols_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_bulk_dyncols_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
