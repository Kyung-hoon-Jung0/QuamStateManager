"""Drives the auto-by-default wiring allocation check
(tests/generate_autoalloc_selfcheck.cjs) under node + jsdom (docs/134).

Customer report: the wiring step (the /instrument "Modify wiring…" deep
link) showed only an Auto-allocate button + the line list — no diagram —
and the button looked dead. The selfcheck pins: step-5 entry auto-runs the
allocator; the cold chain (waiting placeholder → first USABLE env
auto-selected → allocation fires → diagram) needs zero clicks; a no-env
press answers AT the button; a failed auto attempt latches without killing
the manual button; hydrateFromSpec keeps a live env selection; regenerate
derives only the SOURCE chip's optional lines (the 9-of-20-flux chip that
ran the allocator out of DC channels); regen mode never leaks into a later
plain-Generate mount. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_autoalloc_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_autoalloc_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
