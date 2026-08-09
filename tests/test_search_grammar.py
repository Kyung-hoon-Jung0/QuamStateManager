"""Drives tests/search_grammar_selfcheck.cjs under node + jsdom.

The shared query grammar (space = AND, standalone ``|`` = OR, every other pipe
literal) pinned against the REAL shipped JS on all five client surfaces: the
SearchQuery module itself, the Json Tree View's DATA path and DOM path (pinned
equal to each other on one fixture — the first audit found the DOM path
unmeasured), the Live State Edit qubit grid, the pair grid's hidden-column
hint, and the Datasets table (free text, scopes, negation, tight binding).
Per-surface additivity: a no-pipe query answers exactly as before the grammar
landed. Skips without node + jsdom.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SELFCHECK = _ROOT / "tests" / "search_grammar_selfcheck.cjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_search_grammar_selfcheck_passes():
    r = subprocess.run(
        ["node", str(_SELFCHECK)],
        capture_output=True, text=True, cwd=str(_ROOT), timeout=240,
    )
    if r.returncode == 2:
        pytest.skip("jsdom not installed (run `npm install jsdom`)")
    assert r.returncode == 0, (r.stdout + r.stderr)
    assert "all checks passed" in r.stdout, (r.stdout + r.stderr)
