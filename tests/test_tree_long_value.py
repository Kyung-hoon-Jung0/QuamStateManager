"""QA JT-15: a long single-line tree value ends in an ellipsis.

A ~5.5k-character extras string pushed the row's copy/type/delete/? actions
~47,000 px to the right (flex + nowrap row, value with no min-width). jsdom
cascades the real style.css into getComputedStyle, so the rule is pinned on a
really rendered row, editor state included:
tests/tree_long_value_selfcheck.cjs.
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


def test_long_value_is_clipped_not_the_row():
    res = _run("tree_long_value_selfcheck.cjs")
    assert res.returncode == 0, res.stdout + "\n" + res.stderr
    assert "ALL OK" in res.stdout, res.stdout
    assert "the EDITING value is not clipped" in res.stdout
