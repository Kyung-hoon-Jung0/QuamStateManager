"""docs/301 F31: the step-4 QDAC-II band on a chip with no qubit flux line.

Drives tests/generate_qdac_fixedfreq_selfcheck.cjs under node + jsdom: on a
fixed-frequency chip the per-qubit source pickers read "None" (not "LF-FEM")
with the bias tee disabled, the band says the bias is optional, rendering it
writes nothing into the spec, and switching back to a flux-tunable chip
restores the labels with every previous choice. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_qdac_fixedfreq_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_qdac_band_on_a_chip_with_no_flux_line():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all 24 checks passed" in r.stdout, (r.stdout + r.stderr)
