"""v1.3.0 A4: the value-history panel opens beside the pressed clock's column,
so the inspector's other clocks stay reachable (and retarget the panel).

The client half lives in ``fh_place_selfcheck.cjs`` (jsdom over the shipped
FieldHistory module); this file runs it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_value_history_panel_leaves_the_clocks_reachable():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    r = subprocess.run(["node", "-e", "require('jsdom')"], cwd=ROOT, capture_output=True)
    if r.returncode:
        pytest.skip("jsdom not installed")
    r = subprocess.run(["node", "tests/fh_place_selfcheck.cjs"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r"ok \(\d+ assertions\)", r.stdout), r.stdout
