"""docs/111 (#11) — grid editing toolkit driver.

The feature is entirely client-side (fill-down, paste-a-column,
multi-select, pinning, dyn-reload edit carry): /bulk HTML is byte-identical
and every server pin holds — the client behavior is pinned by
``tests/grid_editing_selfcheck.cjs`` against the REAL bulk-edit.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "grid_editing_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_grid_editing_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=120)
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
