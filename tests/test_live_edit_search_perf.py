"""Drives the Live State Edit search-performance behavioral check
(tests/bulk_search_selfcheck.cjs) under node + jsdom.

Audit finding (docs/62): typing in the Live Edit search box ran TWO
un-debounced full-table scans per keystroke (~150 cols × ~30 rows ≈ 2000
cells rescanned + class-toggled by bulk-edit.js AND pair-edit.js) — the
reported "typing keywords in Live Edit is slow". Pins the debounce, the
haystack cache's correctness, its invalidation on cell edits, and
sort-stability. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "bulk_search_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_bulk_search_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


def test_pair_grid_search_is_debounced_too():
    """Source pin: the pair grid's second listener on the shared #bulk-search
    box must stay debounced — restoring a bare applySearch reference would
    silently re-introduce half the per-keystroke cost."""
    js = (_ROOT / "quam_state_manager" / "web" / "static" / "pair-edit.js").read_text(
        encoding="utf-8")
    assert "_pairSearchTimer = setTimeout(applySearch" in js
    assert "addEventListener('input', applySearch)" not in js
