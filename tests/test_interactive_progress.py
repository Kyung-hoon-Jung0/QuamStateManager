"""D-1/D-2 (docs/305): the Interactive progress strip, run under jsdom against the
REAL app.js (tests/interactive_progress_selfcheck.cjs)."""
import shutil
import subprocess
from pathlib import Path

import pytest

_SELFCHECK = Path(__file__).resolve().parent / "interactive_progress_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_interactive_progress_strip():
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert res.stdout.count("  ok  ") >= 18, res.stdout
