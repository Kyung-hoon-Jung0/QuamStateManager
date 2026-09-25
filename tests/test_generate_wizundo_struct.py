"""Drives tests/generate_wizundo_struct_selfcheck.cjs under node + jsdom: the
wizard's Ctrl+Z undoes STRUCTURAL edits (QA regenerate-r2-30) -- a step-4 pair
Control/Target pick, + Add pair, the pair x, a step-5 wiring drag and a typed
step-5 Pin whose re-allocation re-rendered its box -- in
LIFO order with field edits, and never replays a snapshot that no longer
describes the wizard (a content swap, a re-allocation). Skips without node +
jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "generate_wizundo_struct_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_generate_wizundo_struct_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT),
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
