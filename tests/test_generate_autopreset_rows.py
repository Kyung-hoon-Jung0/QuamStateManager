"""Drives tests/generate_autopreset_rows_selfcheck.cjs under node + jsdom.

QA generate-r2-05: the Populate step's standard-defaults prefill ran ONCE per
draft, so qubits/pairs added after the first visit (5 -> 3 -> 5, 20 -> 200)
stayed blank and built with the builder's own, different defaults. The
selfcheck pins the per-row record: new rows prefill, cleared cells stay
cleared, renames are not new rows, old drafts migrate, Start over resets, a
board delete forgets the row. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_autopreset_rows_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_autopreset_rows_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
