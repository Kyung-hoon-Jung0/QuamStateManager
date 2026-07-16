"""Drives the populate-step inline as-you-type validation behavioral check
(tests/generate_validation_selfcheck.cjs) under node + jsdom, and pins the
JS↔Python parity of the hardware constants it validates against.

The customer requirement under test: "if a user types 15.3 GHz, SM should
warn right away" — per-cell, unit-aware, on the keystroke (debounced), not
at blur or a later diagnostics step. The selfcheck also pins the layering
contract (inline = single-cell facts; conflict panel = cross-cell findings;
the sole overlap is the feedline Σ|amp| > 1 clip, which the customer wants
immediately).

The parity test keeps generate.js's VALIDATE_RANGES / BAND_RF_RANGES mirrors
in lock-step with the authoritative Python constants (core/diagnostics.py
MW_OUTPUT_FREQ_RANGE_HZ / MW_INPUT_FREQ_RANGE_HZ, core/spec_constraints.py
FULL_SCALE_POWER_DBM_RANGE / BAND_FREQ_RANGES) — same precedent as
test_run_build_delay.py's bandOf parity pin.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import diagnostics, spec_constraints

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_validation_selfcheck.cjs"
_GEN_JS = _ROOT / "quam_state_manager" / "web" / "static" / "generate.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_validation_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)


class TestJsPyConstantsParity:
    """generate.js hand-mirrors the Python hardware constants (the wizard is
    synchronous/offline — no constants endpoint); these pins fail the build
    the moment either side drifts."""

    def _js(self):
        return _GEN_JS.read_text(encoding="utf-8")

    def test_validate_ranges_match(self):
        m = re.search(
            r"var VALIDATE_RANGES = \{ drive: \[([^\]]+)\], "
            r"readout: \[([^\]]+)\], fsp: \[([^\]]+)\] \};",
            self._js(),
        )
        assert m, "VALIDATE_RANGES literal not found in generate.js"

        def nums(s):
            return tuple(float(x) for x in s.split(","))

        assert nums(m.group(1)) == tuple(
            float(v) for v in diagnostics.MW_OUTPUT_FREQ_RANGE_HZ
        ), "drive range drifted from diagnostics.MW_OUTPUT_FREQ_RANGE_HZ"
        assert nums(m.group(2)) == tuple(
            float(v) for v in diagnostics.MW_INPUT_FREQ_RANGE_HZ
        ), "readout range drifted from diagnostics.MW_INPUT_FREQ_RANGE_HZ"
        assert nums(m.group(3)) == tuple(
            float(v) for v in spec_constraints.FULL_SCALE_POWER_DBM_RANGE
        ), "fsp range drifted from spec_constraints.FULL_SCALE_POWER_DBM_RANGE"

    def test_band_rf_ranges_match(self):
        m = re.search(
            r"var BAND_RF_RANGES = \{ "
            r"1: \[([^\]]+)\], 2: \[([^\]]+)\], 3: \[([^\]]+)\] \};",
            self._js(),
        )
        assert m, "BAND_RF_RANGES literal not found in generate.js"
        for band in (1, 2, 3):
            js = tuple(float(x) for x in m.group(band).split(","))
            py = tuple(float(v) for v in spec_constraints.BAND_FREQ_RANGES[band])
            assert js == py, f"band {band} range drifted from spec_constraints"
