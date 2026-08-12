"""The auto-apply flusher's client half, executed under jsdom against the REAL
`web/static/auto-apply.js` (tests/auto_apply_selfcheck.cjs).

The server half is tests/test_auto_apply.py. What can only be checked here is
the timing rule the user chose — flush immediately, and coalesce everything
that arrives while a write is in flight into exactly one follow-up — plus the
fact that the trigger survives an outerHTML tray replacement, which is how two
of the three swap channels in this app actually land.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "auto_apply_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_auto_apply_client_timing():
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([_node(), str(_SELFCHECK)], capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert res.returncode == 0, f"auto-apply selfcheck failed:\n{res.stdout}\n{res.stderr}"
    assert res.stdout.count("  ok  ") >= 13, res.stdout
