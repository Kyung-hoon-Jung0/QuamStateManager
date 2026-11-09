"""Sidebar compare actions, persistent disclosure, and automatic Trend chart.

D-1 through D-6 are exercised by the jsdom harness.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_compare_float_selfcheck():
    node = shutil.which("node")
    if node is None or subprocess.run([node, "-e", "require('jsdom')"], capture_output=True, cwd=str(_ROOT)).returncode != 0:
        pytest.skip("jsdom not installed for node")
    res = subprocess.run([node, str(_ROOT / "tests" / "compare_float_selfcheck.cjs")],
                         capture_output=True, text=True, encoding="utf-8", cwd=str(_ROOT))
    assert res.returncode == 0, res.stdout + res.stderr
    assert "ok - listed = selection model" in res.stdout
    assert "ok - D-7 removing the middle focuses the next row" in res.stdout
    assert "ok - D-8 height after rows rebuild is published synchronously" in res.stdout


def test_d4_anchor_and_tree_share_one_parent_in_base_html():
    """The sticky bar only pins while its parent spans the run tree: in the
    real template the anchor is followed directly by #sidebar-tree."""
    import re
    html = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf8")
    i = html.index('<div class="compare-float-anchor">')
    tail = re.sub(r"\{#.*?#\}", "", html[i:], flags=re.S)
    j = tail.index("</div></div>") + len("</div></div>")
    assert tail[j:].lstrip().startswith('<div id="sidebar-tree"')
