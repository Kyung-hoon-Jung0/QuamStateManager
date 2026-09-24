"""QA chipstatus-r2-02 / chipstatus-r2-03: Chip Status's live-change signals.

r2-02: the "changed vs live" outlines and tooltip lines stayed up after an
apply / Take live made live match (liveDiff re-read only at mount and in the
banner's show, and its tooltip line was append-only).

r2-03: after ✕ on "Live chip state changed on disk", no LATER external write
ever prompted again while the page stayed open (the dismiss was not tied to
the write it dismissed).

Both pins drive the real shipped JS under jsdom.
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


def test_the_changed_vs_live_marks_follow_live():
    """An apply / pull / restore re-reads /state/live-diff; 0 entries clears
    every outline and tooltip line; the count is current; a late earlier read
    never lands over a later one; a transient 503 keeps the marks and retries."""
    _run_selfcheck("chip_status_livediff_selfcheck.cjs")


def test_a_dismissed_live_banner_prompts_again_for_a_newer_write():
    """✕ silences the write that was seen, not every later one; a pending show
    never undoes a ✕; one banner (one live-content read) per write."""
    _run_selfcheck("chip_status_live_banner_selfcheck.cjs")
