"""QA r2-02 + r2-03 -- Pin & Browse. The "Loading #id..." chip is cleared by
the swap the pinned-compare interceptor performs itself (htmx fires no
afterSwap for a cancelled swap), and the pinned run is identified by
(folder, run id) so the same run NUMBER from another folder opens beside it.
Pinned against the REAL app.js by ``tests/pin_compare_selfcheck.cjs``."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_pin_compare_client_selfcheck():
    proc = subprocess.run(
        ["node", str(_ROOT / "tests" / "pin_compare_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert proc.stdout.count("ok - ") >= 12, proc.stdout
