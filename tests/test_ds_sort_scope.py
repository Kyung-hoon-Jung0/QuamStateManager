"""QA datasets-r2-23 -- the Sort banner's fit-metric sort, scoped to the rows
in view. Pinned by ``tests/ds_sort_scope_selfcheck.cjs`` against the REAL
dataset-virtual.js under jsdom: sorting by a key none of the filtered runs
carry says "no values in these runs" (the old check was judged over the whole
workspace and could never fire) and keeps the newest-first order (the
both-missing tie-break flipped it to id-ascending)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "ds_sort_scope_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_ds_sort_scope_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
