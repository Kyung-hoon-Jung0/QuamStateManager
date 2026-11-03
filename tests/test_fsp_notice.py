"""Drives tests/fsp_notice_selfcheck.cjs (D-43/D-44 compensation notice)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_fsp_notice_selfcheck():
    if not shutil.which("node"):
        pytest.skip("node not installed")
    r = subprocess.run(["node", "tests/fsp_notice_selfcheck.cjs"], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if "Cannot find module 'jsdom'" in r.stderr:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
