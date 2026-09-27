"""QA agents round (3): no ancestor-position :has() on <body>/<html>.

`body:has(#table-pane > .agent-home) #status-bar` (lifting the toast sink above
the agent composer) made every DOM mutation anywhere re-match the whole page:
on /bulk big30x one insert+layout went from 0.1 ms to 118-166 ms, Live-Edit
per-key typing 150 -> 278 ms. agent.js now keeps `body.ag-composer-on` instead.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_SELFCHECK = _ROOT / "tests" / "agent_composer_class_selfcheck.cjs"


def _strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


@pytest.mark.parametrize("css_file", sorted(p.name for p in _STATIC.glob("*.css") if ".min." not in p.name))
def test_no_body_or_html_level_has(css_file):
    css = _strip_comments((_STATIC / css_file).read_text(encoding="utf-8"))
    hits = re.findall(r"(?:^|[\s,{}>+~])(?:body|html|:root)\s*:has\(", css, flags=re.M)
    assert not hits, f"{css_file}: ancestor-level :has() on body/html re-matches the page on every mutation: {hits}"


def test_status_bar_lift_rides_the_body_class():
    css = _strip_comments((_STATIC / "style.css").read_text(encoding="utf-8"))
    assert re.search(r"body\.ag-composer-on\s+#status-bar\s*\{\s*bottom:\s*6\.5rem", css)
    js = (_STATIC / "agent.js").read_text(encoding="utf-8")
    assert '"ag-composer-on"' in js and "function syncComposerClass()" in js


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_agent_composer_class_selfcheck():
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_SELFCHECK)], capture_output=True, text=True, encoding="utf-8",
                       timeout=120, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 10, r.stdout
