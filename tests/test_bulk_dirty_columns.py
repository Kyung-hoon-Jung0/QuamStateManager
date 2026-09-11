"""docs/177 — an unapplied edit never vanishes, on the COLUMN axis too.

``BulkEdit.applyAll`` writes every dirty cell in the table. The ROW pickers have
refused to hide a row carrying an unapplied edit since docs/141 §4s, precisely
so that "Apply all" stays "apply what you see". The column picker and the column
search layer never learned it, so a dirty cell in a hidden column was written by
a press whose confirm named a count the presser could not account for — which is
docs/120's rule ("a press means what the presser could see"), on the write path.

The case that matters is not someone hiding a column they just typed in. It is a
MIRROR write — the coupled ``f_01`` ↔ ``xy.RF_frequency`` twin, an FSP+amplitudes
bundle (docs/160 §5e) — making an off-screen column dirty on its own, which no
picker click precedes.

Drives ``tests/bulk_dirtycol_selfcheck.cjs`` against the real shipped
``bulk-edit.js`` under node + jsdom. Skips without them.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "bulk_dirtycol_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_bulk_dirtycol_selfcheck_passes():
    """Pins, in the grid's own DOM:

    * the column picker still hides a column, and still persists the choice;
    * a column holding an unapplied edit is force-shown — by a value change
      alone, with no picker click, which is the mirror-write path;
    * the user's choice is OVERRIDDEN, not forgotten: the stored hidden set and
      the checkbox both still read hidden, and the column goes back to hidden
      the moment nothing is unapplied;
    * a search cannot filter away an unapplied edit either, while a column with
      nothing unapplied is filtered normally;
    * both layers at once, so neither one's stale verdict keeps it off screen;
    * and the invariant the round is about — no dirty cell sits in a column the
      presser cannot see.
    """
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "bulk_dirtycol_selfcheck ok" in r.stdout, (r.stdout + r.stderr)
