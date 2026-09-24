"""QA F-21: the 2Q RB panels on Chip Status say which fidelity they show.

A Standard RB number is 1 - EPC per CLIFFORD, an interleaved one 1 - EPG per
GATE (docs/138). The section sits under "2Q Gate Fidelity" headings, so the
Standard RB panels read as gate fidelities until they said otherwise. The pin
drives the real shipped JS under jsdom (tests/chip_status_rb_panels_selfcheck.cjs).
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


def test_the_rb_panels_say_per_clifford_or_per_gate():
    """Headings, cell tooltips and bar-axis titles name the level; the
    section's own headings and the persisted density keys are unchanged."""
    _run_selfcheck("chip_status_rb_panels_selfcheck.cjs")
