"""Execute the shipped cz-plan.js under jsdom (card, notes, picker, Last set column, late answers)."""
import os
import subprocess
from pathlib import Path

import pytest


def test_cz_plan_client():
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(["node", "-e", "require('jsdom')"], cwd=root, env=os.environ.copy(),
                           capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("jsdom not installed")
    result = subprocess.run(["node", str(root / "tests/cz_plan_selfcheck.cjs")], cwd=root, env=os.environ.copy(),
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    # v1.4.1: 31 -> 35, include the production render queue and panel invalidation pins.
    assert "35 assertions passed" in result.stdout


def test_cz_chip_map_client():
    """The chip frequency map: columns, segment styles, refusals, picker, order, click."""
    root = Path(__file__).resolve().parents[1]
    probe = subprocess.run(["node", "-e", "require('jsdom')"], cwd=root, env=os.environ.copy(),
                           capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("jsdom not installed")
    result = subprocess.run(["node", str(root / "tests/cz_map_selfcheck.cjs")], cwd=root, env=os.environ.copy(),
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    # v1.4.1: 17 -> 34, include numeric widths, production hover/purge and render queue pins.
    assert "34 assertions passed" in result.stdout
