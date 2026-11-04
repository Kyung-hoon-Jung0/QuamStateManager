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
    # 2026-11-04 polish: a default at the bias limit stays at or below it, no first-click warning -> 50.
    assert "50 assertions passed" in result.stdout


def test_map_tools_selects_show_their_whole_label():
    """A select is not width-capped and keeps room for its arrow (the fit-model
    select read "Smoothing and contour" under the arrow at a 10rem cap)."""
    css = (Path(__file__).resolve().parents[1] / "quam_state_manager/web/static/map-tools.css").read_text(encoding="utf-8")
    rule = next(line for line in css.splitlines() if line.startswith(".map-tools-controls select {"))
    assert "max-width: none" in rule and "padding: .2rem 1.9rem .2rem .4rem" in rule
    assert not any(line.startswith(".map-tools-controls select,") for line in css.splitlines())
