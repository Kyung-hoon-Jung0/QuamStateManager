"""Drives tests/flat_to_table_selfcheck.cjs under node + jsdom (QA liveedit-r2-02).

Pins, against the REAL all-values.js + bulk-edit.js: an edit committed in the
Live State Edit Flat View lands in the Table View in place -- the committed
value as the cell's clean baseline, the modified marker + before->after
baseline, the recomputed header min/max and extreme colouring, an alias cell
matched by data-resolved -- with no full /bulk re-GET; and a change the patch
cannot repaint honestly resyncs the Table (once) when it is shown again.
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "flat_to_table_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_flat_edit_reaches_the_table_view():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_back_from_flat_view_returns_to_table_view():
    """QA liveedit F17: tests/bulk_pane_history_selfcheck.cjs."""
    r = subprocess.run(
        ["node", str(_ROOT / "tests" / "bulk_pane_history_selfcheck.cjs")],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
