"""Drives the instrument-rack sizing check (tests/instrument_fit_selfcheck.cjs)
under node + jsdom (docs/135).

Customer report: the Instrument Wiring page showed "3 MW + 4 LF" FEMs and the
"Modify wiring…" wizard showed "3 MW + 2 LF, cut off" — for one chip whose
real inventory is 3 MW + 5 LF. Cause: the rack <svg> carried a natural width
plus an inline `max-width:100%` and NO viewBox, so the element's box shrank
while the drawing kept its coordinates — everything past the host width was
painted outside the visible box, and the host's `overflow-x:auto` never saw
an overflow to scroll. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "instrument_fit_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_instrument_fit_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
