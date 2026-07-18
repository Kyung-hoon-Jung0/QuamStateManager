"""Drives the All-values v2 behavioral check
(tests/all_values_v2_selfcheck.cjs) under node + jsdom.

Pins, against the REAL all-values.js: resolvable-xref edit-through inputs
(resolved-value display, one /field/peek per path on focus, "writes to …
· shared by …" hint that never enters the table flow); dangling xrefs
read-only; list-element inputs; the array/empty ✎ JSON modal (peek-prefilled,
inline JSON + server errors, PARSED-value POST to /field/edit-batch, Esc
cancel); expected-type chips; structural 28px row parity (one td, 3 grid
children, no block elements); and the dirty-preserving applyPayload rebase
carrying row extras through a re-pull. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "all_values_v2_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_all_values_v2_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
