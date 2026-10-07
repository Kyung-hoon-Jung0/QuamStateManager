"""docs/301 F35: the Populate step's live waveform preview never covers the row
being edited or the table header.

Drives tests/generate_preview_dock_selfcheck.cjs under node + jsdom: the panel
sits after every populate table and docks at the bottom of the pane (sticky
bottom, never sticky top), a focused cell the docked panel would cover is
scrolled up above it, and a cell already clear of it is left where it is.
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_preview_dock_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_preview_docks_below_the_row_being_edited():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all 9 checks passed" in r.stdout, (r.stdout + r.stderr)
