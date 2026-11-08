"""Drives tests/map_add_buttons_selfcheck.cjs: the component map's add buttons."""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_selfcheck():
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    r = subprocess.run([node, str(_ROOT / "tests" / "map_add_buttons_selfcheck.cjs")], cwd=_ROOT,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
