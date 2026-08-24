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


# ── docs/136 WS5b/WS6/WS7 — the flux-source selector, populate and cabling ────

_FLUXSRC = _ROOT / "tests" / "generate_fluxsource_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_fluxsource_selfcheck_passes():
    """Drives the wizard's QDAC-as-a-component surfaces under node + jsdom.

    Pins the chip-level Flux source being DERIVED from the per-qubit shapes
    (so "Per qubit…" is a report, never a command); a bias tee KEEPING its OPX
    flux line; the band's three-way source picker; prunePopulate reaching
    spec.qdac.qubits; the trigger-cabling table (round-robin, one cable per
    ext input, and a re-generate-carried pin never withdrawn by the wizard's
    own grouping); and the Populate QDAC cells writing onto spec.qdac.qubits
    while refusing to CREATE an entry. Mutation-checked 10/10 when written.
    """
    r = subprocess.run(
        ["node", str(_FLUXSRC)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=180,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
