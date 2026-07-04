"""Drives the Generate-Config absolute-power (dBm) input mode + hole-aware LO
solver behavioral check (tests/generate_power_selfcheck.cjs) under node + jsdom.

Pins the lab's confirmed power policy (strongest pulse picks an integer FSP,
preferred [0, 10] dBm, floor 0 / cap 18; amp band 0.01–0.5; readout banks
budget the worst-case coherent sum), the reference example (saturation
−20 dBm → FSP 0 / amp 0.1), the ±400 MHz + 5 MHz-demod-hole LO solver
(a lone resonator's LO must NOT sit on its RF — the legacy midpoint did),
and that manual mode is byte-for-byte unchanged. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_power_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_power_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
