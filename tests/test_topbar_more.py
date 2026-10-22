"""v1.5.3 (design A): the topbar keeps the chip name, the Sync status control and
Review; every other control is the same node moved into one keyboard-accessible
overflow menu. Structure is pinned on the rendered page; behaviour through the
real app.js under jsdom (tests/topbar_more_selfcheck.cjs)."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
MOVED = ('id="topbar-reload"', 'id="global-search"', 'id="state-version-slot"',
         'id="diag-tray-slot"', 'id="agent-pill"', 'class="wb-link"')


@pytest.fixture
def page(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    return app.test_client().get("/").get_data(as_text=True)


def _header(html: str) -> str:
    i = html.index('<header class="topbar">')
    return html[i:html.index("</header>", i) + len("</header>")]


def test_the_bar_keeps_only_sync_and_review_and_one_menu(page):
    head = _header(page)
    first = head[head.index("<ul>"):head.index('<ul class="topbar-right">')]
    assert 'id="topbar-tray-slot"' in first
    menu = head[head.index('id="topbar-more-menu"'):]
    for item in MOVED:
        assert item not in first, item
        assert item in menu, item
        assert page.count(item) == 1, item           # moved, not duplicated
    btn = re.search(r'<button[^>]*id="topbar-more-btn"[^>]*>', head).group(0)
    assert 'aria-expanded="false"' in btn and 'aria-controls="topbar-more-menu"' in btn


def test_the_menu_works_from_the_keyboard(page, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    f = tmp_path / "header.html"
    f.write_text(_header(page), encoding="utf-8")
    r = subprocess.run([node, str(ROOT / "tests" / "topbar_more_selfcheck.cjs"), str(f)],
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    if r.returncode == 2 and "jsdom not installed" in (r.stderr or ""):
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, (r.stdout or "")[-3000:] + (r.stderr or "")[-2000:]
