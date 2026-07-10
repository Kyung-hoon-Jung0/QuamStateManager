"""Drives the Generate-Config chip-topology board behavioral check
(tests/generate_topoboard_selfcheck.cjs) under node + jsdom: place qubits
(grid_location), draw/remove arbitrary edges (qubit_pairs), drag to move, resize
the zone, dropdown sync, and draft persistence. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_topoboard_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_topoboard_selfcheck_passes():
    r = subprocess.run(["node", str(_SELFCHECK)], capture_output=True, text=True, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
