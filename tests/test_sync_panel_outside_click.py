"""v1.5.3: a click outside the sync panel closes it and still reaches its target
(one press on Reload with the panel open both closes the panel and runs Reload).
Driven through the real app.js under jsdom."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_outside_click_closes_the_sync_panel_and_reaches_the_target():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "sync_panel_outside_click_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout or "")[-3000:] + (r.stderr or "")[-2000:]
