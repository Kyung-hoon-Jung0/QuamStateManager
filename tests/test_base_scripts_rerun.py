"""Body script lifecycle regression, exercised in jsdom."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_base_scripts_rerun_selfcheck():
    root = Path(__file__).resolve().parents[1]
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed")
    result = subprocess.run(
        [node, str(root / "tests" / "base_scripts_rerun_selfcheck.cjs")],
        cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all ok" in result.stdout
