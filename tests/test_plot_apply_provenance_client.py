"""Run the shipped popup through its real Apply and retry handlers."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


def test_plot_popup_sends_displayed_origins():
    if shutil.which("node") is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", str(root / "tests/plot_apply_provenance_selfcheck.cjs")],
                            cwd=root, env=os.environ.copy(), capture_output=True,
                            text=True, encoding="utf-8", timeout=120)
    if result.returncode == 2 and "jsdom not installed" in result.stderr:
        pytest.skip("jsdom not installed")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "assertions passed" in result.stdout
