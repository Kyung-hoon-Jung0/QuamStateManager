"""QA round (agents menu): after a browser Back the Agent home, the float and
Agent setup must be LIVE, not a picture restored from htmx's body snapshot.
The pins live in tests/agent_history_restore_selfcheck.cjs (real agent.js +
agent-setup.js under jsdom); measured in real Chrome with
tests/browser/journeys/agent_back.cjs and agent_setup_back.cjs."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "agent_history_restore_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_agent_history_restore_selfcheck():
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True, encoding="utf-8",
                       timeout=180, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    assert " 0 failed" in r.stdout
