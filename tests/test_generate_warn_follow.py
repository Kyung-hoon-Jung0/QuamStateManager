"""Drives tests/generate_warn_follow_selfcheck.cjs under node + jsdom.

docs/239: a Generate-Config populate warning (a port's LO / IF window, span,
band, the feedline sum|amp| clip, an amp cell judged against a typed FSP)
is re-derived on the debounced keystroke of ANY value it depends on and on
commit, so it disappears the moment the condition holds -- including when the
user fixes it through a coupled field (another tone's RF, the LO instead of
the band, a sibling readout amplitude). Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_warn_follow_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_warn_follow_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT), timeout=120,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
