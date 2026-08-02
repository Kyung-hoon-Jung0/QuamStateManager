"""Drives the UndoNav behavioral check (tests/undo_nav_selfcheck.cjs) under
node + jsdom, against the REAL shipped app.js.

Pins docs/73 (r16 0-2): visible-target flash-in-place, hidden-column escape,
owner-surface mapping (qubit/pair inspector deep links, /bulk for
multi-entity, Explorer for ports/wiring), the typing stash + refill + one-shot
consume, the cellsReverted end-to-end wiring, and the tray tooltip naming the
next server-undo target. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "undo_nav_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_undo_nav_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
        timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
