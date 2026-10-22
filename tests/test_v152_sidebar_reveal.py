"""Exercise shipped sidebar reveal logic under jsdom."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("case", ["decide", "load", "sync"])
def test_sidebar_reveal(case):
    result = subprocess.run(
        ["node", "tests/sidebar_reveal_selfcheck.cjs", case], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
