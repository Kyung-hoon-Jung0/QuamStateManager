"""Drives tests/generate_csv_import_selfcheck.cjs under node + jsdom.

The wizard's port-label CSV import (docs/54): a CSV that fails to parse is
refused without first asking to replace the chip (QA F20), and the import is
an undo barrier -- Ctrl+Z after it says it can't undo the import instead of
reverting an older, unrelated field, re-applying the pre-import architecture,
or restoring a pre-import board-deleted qubit (QA generate-r2-30). Skips
without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_csv_import_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_csv_import_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "checks passed" in r.stdout, (r.stdout + r.stderr)
