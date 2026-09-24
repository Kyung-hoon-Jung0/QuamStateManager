"""QA F-19b: a lone value on a Chip Status panel gets ONE colour.

A 2Q RB panel with one measured pair painted its tile mid-scale and its only
bar with the low end of the bar palette, and the bar then jumped to mid-scale
on the first bar-palette switch. The 1Q metric panels' bars had the same
arithmetic. The pin drives the real shipped JS under jsdom
(tests/chip_status_lone_value_selfcheck.cjs).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _run_selfcheck(name: str) -> None:
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / name)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout + r.stderr)


def test_a_lone_value_has_one_colour_on_tile_and_bar():
    """Tile and bar share the lone value's mid-scale position, a palette
    switch does not move it, and multi-value panels are unchanged."""
    _run_selfcheck("chip_status_lone_value_selfcheck.cjs")
