"""Drives the wizard QDAC-II bias plumbing behavioral check
(tests/generate_qdac_selfcheck.cjs) under node + jsdom.

Pins: spec.qdac normalization on hydrate; toggling a qubit's QDAC bias adds/
removes its spec.qdac.qubits[qid] entry; deriveLines omits the flux line for
a QDAC-biased qubit while keeping it for other qubits on the same chip (the
mixed-architecture case); applyQubitIdMap re-keys spec.qdac.qubits on rename.
Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_qdac_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_qdac_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
