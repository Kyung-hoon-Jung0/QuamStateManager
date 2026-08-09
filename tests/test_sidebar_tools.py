"""Settings + Calculator as sidebar tools (docs/89).

The report: both lived in the far top-RIGHT, the corner of a wide window
furthest from where the eye lives, and the calculator's glyph was ∵ (U+2235,
the "because" sign), so nobody recognised it. They are now the first thing in
the sidebar, with icons AND labels.

Two structural traps this had to avoid, both pinned below:
  - the sidebar collapses to width 0 (not an icon rail), so the tools would
    become unreachable — a topbar pair appears only while it is collapsed
  - the sidebar is overflow-y:auto, which CLIPS an absolutely-positioned
    child, so the popovers live at body level and are anchored position:fixed

The behavioural proof runs the real shipped JS under jsdom
(tests/sidebar_tools_selfcheck.cjs).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "quam_state_manager" / "web" / "static"
_TPL = _ROOT / "quam_state_manager" / "web" / "templates"
_SELFCHECK = _ROOT / "tests" / "sidebar_tools_selfcheck.cjs"


def _node() -> str | None:
    return shutil.which("node")


@pytest.fixture
def page(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    return app.test_client().get("/").get_data(as_text=True)


class TestSidebarRow:
    def test_tools_are_first_in_the_sidebar_with_labels(self, page):
        assert 'class="sidebar-tools"' in page
        assert "Settings</span>" in page and "Calculator</span>" in page
        # Settings first (the user's call), and both above Projects
        assert page.index("Settings</span>") < page.index("Calculator</span>")
        assert page.index('class="sidebar-tools"') < page.index(">Projects<")

    def test_the_old_topbar_wrappers_are_gone(self, page):
        """Moved, not copied — a second always-visible control would be worse
        than the original problem."""
        assert 'class="calc-wrap"' not in page
        assert 'class="settings-wrap"' not in page

    def test_icons_are_svg_that_inherit_the_theme(self, page):
        assert "ic-gear" in page and "ic-calc" in page
        assert 'stroke="currentColor"' in page
        # the unrecognisable "because" sign is gone
        assert "&#8757;" not in page


class TestReachableWhenCollapsed:
    def test_topbar_fallback_exists(self, page):
        assert "topbar-tools-fallback" in page
        fb = page[page.index("topbar-tools-fallback"):][:1200]
        assert "settings-btn" in fb and "calc-btn" in fb

    def test_it_is_hidden_until_the_sidebar_collapses(self):
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        assert ".topbar-tools-fallback { display: none; }" in css
        assert ".sidebar-collapsed .topbar-tools-fallback { display: flex" in css

    def test_the_sidebar_really_does_collapse_to_nothing(self):
        """The premise of the fallback. If this ever became an icon rail the
        fallback could go — but it must not be removed while this holds."""
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        block = css[css.index(".sidebar-collapsed #sidebar {"):][:220]
        assert "width: 0" in block and "pointer-events: none" in block


class TestPopoversEscapeTheSidebar:
    def test_popovers_render_outside_the_aside(self, page):
        assert page.index('id="calc-popover"') > page.index("</aside>")
        assert page.index('id="settings-dropdown"') > page.index("</aside>")

    def test_anchored_rule_overrides_the_absolute_base(self):
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        assert ".pop-anchored { position: fixed !important;" in css
        # the base rules still carry right:0/top:100% from the topbar era, so
        # the reset has to be explicit or the panel lands in the wrong corner
        assert "right: auto !important" in css

    def test_the_sidebar_still_scrolls(self):
        """Why the popovers had to move at all."""
        css = (_STATIC / "style.css").read_text(encoding="utf-8")
        block = css[css.index("#sidebar {"):][:400]
        assert "overflow-y: auto" in block


class TestShortcut:
    def test_alt_c_is_wired_and_guarded(self):
        src = (_STATIC / "calc.js").read_text(encoding="utf-8")
        assert "altKey" in src and "toggleCalc()" in src
        # never steals the keystroke while the user is typing
        assert "TEXTAREA" in src and "isContentEditable" in src

    def test_it_is_advertised(self, page):
        assert "Alt+C" in page


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_sidebar_tools_selfcheck():
    """Anchoring, the collapsed-sidebar trigger, the singleton, Alt+C and the
    dragged-popover carve-out, against the REAL app.js + calc.js."""
    try:
        subprocess.run([_node(), "-e", "require('jsdom')"],
                       check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([_node(), str(_SELFCHECK)], capture_output=True,
                       text=True, encoding="utf-8", timeout=180, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 20, r.stdout
