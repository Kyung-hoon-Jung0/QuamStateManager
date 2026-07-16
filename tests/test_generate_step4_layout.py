"""Drives the Step-4 "Qubits" page readability-redesign behavioral check
(tests/generate_step4_layout_selfcheck.cjs) under node + jsdom.

Customer feedback: the page was hard to read and hid its two most important
tools (chip board, qubit naming) behind collapsed <details>. The selfcheck
pins the flow-band layout contract: zero collapsibles on step 4 (board +
naming are plain always-visible divs; the grid renders on the count change
alone), the count-0 board empty state, the partial-placement warning tint,
Grid-input↔zone sync, gate-aware pair headers (CZ neutral "Qubit ↔ Qubit"
because roles are frequency-assigned at Populate; CR keeps the directional
"Control → Target"), the manual-orientation chip, the explicitly-labeled
read-only control-line confirmation block with a live pair-count echo, the
feedline-grouping summary, and the step-6 reference mirrors defaulting OPEN
with a remembered per-user collapse. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_step4_layout_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_step4_layout_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
