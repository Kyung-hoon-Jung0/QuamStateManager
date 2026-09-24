"""QA r2-01 -- the inspector's up/down and [ ] walk the list the run was
opened from. A run opened from the Datasets TABLE steps through the table's
own filtered + sorted rows (not the raw run-id order of the folder); a run
opened from the sidebar tree keeps the docs/68 tree walk. Pinned against the
REAL dataset-virtual.js + app.js by ``tests/ds_table_nav_selfcheck.cjs``.

Review follow-up: in Pin & Browse a re-click of the PINNED row is a suppressed
swap (r2-03) that used to leave the table-nav marker on the pinned run, so the
current column fell back to the tree walk; the same harness now drives the
real pinned beforeSwap interceptor and pins the restored marker."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_table_opened_run_steps_through_the_filtered_rows():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "ds_table_nav_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 22, proc.stdout
