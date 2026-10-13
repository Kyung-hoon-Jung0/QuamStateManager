"""Datasets search survives a reload within the same tab."""
import subprocess
from pathlib import Path


def test_dataset_filter_reload_selfcheck():
    result = subprocess.run(["node", str(Path(__file__).with_name("dataset_filter_reload_selfcheck.cjs"))],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 checks passed" in result.stdout
