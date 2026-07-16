"""Drives the hardened shared folder browser behavioral check
(tests/folder_browser_selfcheck.cjs) under node + jsdom.

Backs the customer feedback "folder picking sometimes hangs / loses the
path / resets" + Linux compatibility: fetch timeout → error row with a
working Retry; stale responses dropped; _currentPath only ever a
successfully-listed folder; POSIX breadcrumbs carry real slash paths
(the old builder joined crumbs with backslashes and dropped the leading
"/" — dead navigation on Linux); mkdir double-submit guard + failure
re-sync; per-input last-path restore. The server side is covered by
tests/test_browse_route.py. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "folder_browser_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_folder_browser_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
