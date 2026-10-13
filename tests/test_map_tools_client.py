"""Execute native controls and authoritative Apply with the shipped browser code."""
import os
from pathlib import Path
import subprocess


def test_map_tools_native_controls_preview_replay_and_lifecycle():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", str(root / "tests/map_tools_selfcheck.cjs")],
        cwd=root, env=os.environ.copy(), capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    # v1.4.1: 23 -> 24 assertions, including revocation after a slider change.
    assert "24 assertions passed" in result.stdout
