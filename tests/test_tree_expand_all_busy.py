"""QA JT-16: Depth "All" says so before the page pauses.

On the real 5-qubit chip (31,227 nodes) the press froze the page ~5 s with no
word. The Explorer's All now goes through jsonTreeExpandAllUi, which above a
row budget paints a note + disables the button + marks the tree busy BEFORE
the expand and clears them after; below it the press is the old synchronous
one, and window.jsonTreeExpandAll stays synchronous for its other callers.
Executed under jsdom: tests/tree_expand_all_busy_selfcheck.cjs.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def _run(name: str) -> subprocess.CompletedProcess:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    res = subprocess.run([node, str(_ROOT / "tests" / name)], capture_output=True,
                         text=True, encoding="utf-8", errors="replace",
                         cwd=str(_ROOT), timeout=300)
    if res.returncode == 2 and "jsdom not installed" in (res.stderr or ""):
        pytest.skip("jsdom not installed")
    return res


def test_expand_all_announces_itself():
    res = _run("tree_expand_all_busy_selfcheck.cjs")
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ALL OK" in res.stdout, res.stdout
    assert "and NOTHING is expanded yet" in res.stdout
