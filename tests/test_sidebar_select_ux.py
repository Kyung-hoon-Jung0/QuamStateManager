"""docs/161 — the sidebar's tick box is a real control, and Compare makes room.

Customer: the tick box was too small and most users did not know what it was
for; and after Compare Selected the result landed under the expanded run
detail, so "did the compare open at all?".

Pinned: the 18 px custom box (square, rounded, SM-blue fill + white check),
its tooltip, the hint line under the compare buttons (shown only while
nothing is ticked — the jsdom selfcheck drives the toggle), and the
beforeRequest hook that collapses the inspector when the compare form fires.
"""

from __future__ import annotations

import re
from pathlib import Path

from quam_state_manager.web.app import create_app

_ROOT = Path(__file__).resolve().parent.parent
_CSS = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
_BASE = (_ROOT / "quam_state_manager" / "web" / "templates" / "base.html").read_text(encoding="utf-8")
_MACROS = (_ROOT / "quam_state_manager" / "web" / "templates" / "_sidebar_tree_macros.html").read_text(encoding="utf-8")
_APP_JS = (_ROOT / "quam_state_manager" / "web" / "static" / "app.js").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    css = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert m, selector
    return m.group(1)


class TestTickBox:
    def test_the_box_is_a_square_18px_custom_control(self):
        r = _rule(".tree-entry-label input[type=checkbox]")
        assert "appearance: none" in r
        assert "width: 18px" in r and "height: 18px" in r        # square — never a text-field shape
        assert "border-radius: 5px" in r
        assert "var(--pico-primary)" in r                          # the theme's own blue, no literal

    def test_ticked_is_a_solid_fill_with_a_white_check(self):
        on = _rule(".tree-entry-label input[type=checkbox]:checked")
        assert "background: var(--pico-primary)" in on
        mark = _rule(".tree-entry-label input[type=checkbox]:checked::after")
        assert "rotate(45deg)" in mark and "border: solid #fff" in mark

    def test_row_hover_brightens_the_box(self):
        css = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
        assert re.search(r"\.tree-entry-label:hover input\[type=checkbox\][^{]*\{[^}]*border-color: var\(--pico-primary\)", css)

    def test_the_box_says_what_it_is_for(self):
        m = re.search(r'<input type="checkbox" name="paths"[^>]*>', _MACROS)
        assert m and 'title="Tick to select this run' in m.group(0)
        assert "Compare Selected" in m.group(0) and "Trend Tracker" in m.group(0)


class TestHint:
    def test_the_hint_renders_inside_the_compare_form(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        html = app.test_client().get("/").get_data(as_text=True)
        form = re.search(r'<form id="compare-form".*?</form>', html, re.S).group(0)
        assert 'id="compare-hint"' in form and "Tick runs in the list" in form
        # the JS toggles it: shown at 0 ticks, hidden otherwise
        i = _APP_JS.index("function syncCompareCount()")
        assert "hint.hidden = n > 0" in _APP_JS[i:i + 2500]

    def test_hint_style_is_quiet(self):
        r = _rule(".compare-hint")
        assert "var(--pico-muted-color)" in r
        assert "display: none" in _rule(".compare-hint[hidden]")


class TestCompareMakesRoom:
    def test_the_compare_form_collapses_an_expanded_inspector(self):
        i = _BASE.index('document.addEventListener("htmx:beforeRequest", function(e) {')
        seg = _BASE[i:i + 700]
        assert 'closest("#compare-form")' in seg
        assert "inspectorHasContent()" in seg and "inspectorExpanded" in seg
        assert 'window._applySplitPreset("collapsed")' in seg
        # the r13 ⑦ menu-navigation collapse keeps its narrower gate (dataset detail only)
        j = _BASE.index('closest(".sidebar-nav a[href]")')
        assert 'querySelector("#ds-detail-root")' in _BASE[j:j + 600]
