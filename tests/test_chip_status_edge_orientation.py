"""Drives the Chip Status topology edge-orientation behavioral check
(tests/chip_status_edge_orientation_selfcheck.cjs) under node + jsdom.

Pins the REAL chip-status.js round-2 design (customer feedback: "the diagram
doesn't say which qubit is control vs target"; round-1's small in-line SVG
arrowhead read as a decoration, not a direction): NO <polygon> arrowhead
exists anywhere; instead EVERY rendered pair with a resolved control/target
(CR, CZ, and uncalibrated couplers alike) gets a "source→target" label whose
target-facing edge is reshaped into a point via a .topo-edge-label-arrow-*
clip-path class, the pointer direction snapped to the dominant axis of the
pair. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "chip_status_edge_orientation_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_chip_status_edge_orientation_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
