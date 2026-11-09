"""Sidebar compare actions, persistent disclosure, and automatic Trend chart.

D-1 through D-6 are exercised by the jsdom harness.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_compare_float_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "compare_float_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - listed = selection model" in res.stdout
