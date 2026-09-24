"""QA diagnostics-r2-03: a failed "Validate deeply (Quam.load)" shows its reason.

/config/regenerate answers a failure with 502 (400 with no env) and an
explanatory body naming the failing path. htmx 2.x drops error bodies unless
app.js's beforeSwap allowance opts the target in, which it does for
`.config-status-host` targets only -- the Diagnostics slot was not one, so the
user saw a generic "please try again" toast. Pinned under jsdom against the
shipped template markup + the real app.js handler.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_diag_deep_validate_selfcheck():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    r = subprocess.run(["node", str(_ROOT / "tests" / "diag_deep_validate_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8",
                       cwd=str(_ROOT), timeout=120)
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ALL OK" in r.stdout, r.stdout + r.stderr
