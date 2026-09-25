"""QA JT-20: the Json tree works from the keyboard.

Executed against the real shipped app.js + manual.js under jsdom
(tests/tree_keyboard_selfcheck.cjs): one Tab stop per tree, arrows walk the
visible rows (lazy nodes built, search-hidden rows skipped), Right/Left open
and close, Enter/F2 do the value's own click (edit, or copy on a read-only
tree), the editor hands focus back to its row, F1 on a focused row opens the
manual, handled keys never also reach a page-level handler, and Shift+Tab out
of the tree is not a trap.
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


def test_tree_keyboard_selfcheck():
    res = _run("tree_keyboard_selfcheck.cjs")
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ALL OK" in res.stdout, res.stdout
    for line in ("ArrowRight on a closed node opens it",
                 "and hands focus back to its ROW",
                 "F1 on a focused row opens the manual on that row",
                 "ArrowDown never lands on a row the search hid",
                 "a read-only copy tree is not a Tab stop",
                 "the JSON pencils are not Tab stops inside a keyboard tree",
                 "Shift+Tab goes past the tree"):
        assert line in res.stdout, line
