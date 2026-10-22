"""Exercise shipped sidebar reveal logic under jsdom."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
@pytest.mark.parametrize("case", ["decide", "load", "sync", "collapsed", "details", "noscroll"])
def test_sidebar_reveal(case):
    result = subprocess.run(
        ["node", "tests/sidebar_reveal_selfcheck.cjs", case], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_reveal_selector_names_the_shipped_sidebar():
    js = (ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    m = re.search(r"window\.SidebarReveal = \{\s*SELECTOR: '([^']+)'", js)
    assert m, "SidebarReveal.SELECTOR not found"
    sel = m.group(1)
    assert re.fullmatch(r"#[\w-]+", sel), "pin only understands an id selector: " + sel
    html = (ROOT / "quam_state_manager/web/templates/base.html").read_text(encoding="utf-8")
    assert re.search(r'<aside\b[^>]*\bid="%s"' % re.escape(sel[1:]), html), sel + " not in base.html"
    assert "querySelector(R.SELECTOR)" in js
