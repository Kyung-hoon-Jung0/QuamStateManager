"""QA datasets-r2-21 -- the Alt+click compare basket (app.js). Pinned by
``tests/ds_basket_selfcheck.cjs`` against the REAL app.js under jsdom: a 9th
pick is refused with a toast (it was dropped silently), and Compare takes the
fixed bar off screen (it covered the compare view's figures and outlived the
view's x) while keeping the picks."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "ds_basket_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_ds_basket_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_SELFCHECK)], capture_output=True, text=True,
        cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
