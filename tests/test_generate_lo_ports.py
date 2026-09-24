"""Drives tests/generate_lo_ports_selfcheck.cjs under node + jsdom.

QA F16 / regenerate-r2-05 / regenerate-r2-07: the Generate / Re-generate
step-6 LO model is one LO per MW-FEM PORT (QM: "Each analog output port must
define either an `upconverter_frequency` ... or a `upconverters` field");
coupled ports share only a band ("Coupled ports must be in the same band, or
in bands `1` and `3`"). A forced "Re-solve LOs" never writes an infeasible
pick, an explicit band is checked against its row's LO/RF, and the LO map
shows the LO + band the build uses. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_lo_ports_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_lo_ports_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
