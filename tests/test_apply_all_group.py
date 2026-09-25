"""QA diagnostics-r2-15: one Apply all (e.g. after a fill-down) is ONE server
change group -- one Review bundle, one Ctrl+Z (docs/20) -- instead of one per
row. The client half runs the real bulk-edit.js + pair-edit.js under jsdom
(`apply_all_group_selfcheck.cjs`); the server's join rule is pinned in
`test_web.py::TestBatchUndoAtomic`.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "apply_all_group_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_apply_all_group_selfcheck_passes():
    r = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True,
                       encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
