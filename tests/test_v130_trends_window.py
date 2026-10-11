"""v1.3.0 A3: every chart of one Trends view opens on ONE time window.

The client half lives in ``trends_window_selfcheck.cjs`` (jsdom over the
shipped chip-status.js); this file runs it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _node_with_jsdom():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    r = subprocess.run(["node", "-e", "require('jsdom')"], cwd=ROOT, capture_output=True)
    if r.returncode:
        pytest.skip("jsdom not installed")


def test_trends_charts_share_one_window():
    _node_with_jsdom()
    r = subprocess.run(["node", "tests/trends_window_selfcheck.cjs"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"ok \(\d+ assertions\)", r.stdout), r.stdout
