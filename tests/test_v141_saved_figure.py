"""Saved PNGs fit the visible pane and retain full-size zoom."""
import subprocess
from pathlib import Path


def test_saved_figure_fit_selfcheck():
    result = subprocess.run(["node", str(Path(__file__).with_name("saved_figure_fit_selfcheck.cjs"))],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "6 checks passed" in result.stdout
