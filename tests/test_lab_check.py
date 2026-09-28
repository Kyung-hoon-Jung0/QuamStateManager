"""The "checking with your class's own code..." seam (web/static/lab-check.js).

Drives tests/lab_check_selfcheck.cjs under jsdom, and pins that the seam is
loaded on every page (base.html) so every editing surface gets it."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_lab_check_selfcheck():
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True,
                       capture_output=True, timeout=30, cwd=str(ROOT))
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "lab_check_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=120, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 40, r.stdout
    # w9/labwarm: the "Preparing your lab code..." pins ran (10, 11)
    assert "says Preparing" in r.stdout and "delete step says Preparing" in r.stdout


def test_every_page_loads_the_seam():
    base = (ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(
        encoding="utf-8")
    assert "asset_url('lab-check.js')" in base
    css = (ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(
        encoding="utf-8")
    assert ".lab-check-badge" in css and ".lab-check-refused" in css
