"""docs/112 (#12) — datasets daily-flow driver.

Entirely client-side (j/k/Enter keyboard nav, the "↻ Newest" sort-reset
chip, digest-follows-filter): server routes and /datasets HTML are
untouched; the behavior is pinned by ``tests/ds_flow_selfcheck.cjs``
against the REAL dataset-virtual.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "ds_flow_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_ds_flow_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=120)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
