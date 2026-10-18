"""P1-3 (U05): the plot-apply popup says "staged" for a value that was only
staged, and "applied" only when an armed Auto-Sync session writes the chip.

Drives ``tests/plot_apply_staged_selfcheck.cjs`` against the REAL app.js.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_popup_says_staged_unless_the_chip_was_written():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "plot_apply_staged_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
