"""Execute native controls and authoritative Apply with the shipped browser code."""
import os
from pathlib import Path
import subprocess


def test_map_tools_native_controls_preview_replay_and_lifecycle():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(["node", str(root / "tests/map_tools_selfcheck.cjs")],
        cwd=root, env=os.environ.copy(), capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    # v1.4.1: 30 -> 32 assertions, including reason-only unsupported maps and a separate fit verdict.
    # bug report 2026-10-15: placement, the cap as a bias limit, every panel in one request -> 41;
    # its review: no stale Apply across a Readout switch, at most 16 panels per request -> 44;
    # per-panel answers, Apply on Map 2, mixed panel types, a restored side view -> 48.
    assert "48 assertions passed" in result.stdout
