"""Exercise the real floating panel, theme rendering and Escape ladder."""
import os
from pathlib import Path
import subprocess


def test_float_wiring_selfcheck():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", "tests/float_wiring_selfcheck.cjs"], cwd=root,
                            env=os.environ.copy(), capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 assertions passed" in result.stdout
