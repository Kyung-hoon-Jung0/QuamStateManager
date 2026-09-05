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
    def test_the_box_is_a_square_custom_control_sized_to_the_text(self):
        """docs/165: multiple users, independently, said the rows were too
        small to read. The row font went 1.06 -> 1.32em, so the box goes with
        it -- a control that stays 18px beside 1.32em text reads as an
        afterthought. Square is the part that must never drift: a non-square
        box reads as a text field."""
        r = _rule(".tree-entry-label input[type=checkbox]")
        assert "appearance: none" in r
        assert "width: 22px" in r and "height: 22px" in r        # square — never a text-field shape
        assert "border-radius: 5px" in r
        assert "var(--pico-primary)" in r                          # the theme's own blue, no literal

    def test_the_tick_has_three_states_and_is_visible_before_it_is_ticked(self):
        """docs/165 (customer): an empty box did not say it was tickable. The
        tick mark now EXISTS in every state and only its loudness changes --
        a faint grey ghost when empty, brighter under the pointer, white on
        the filled blue box when ticked. The geometry lives on the base rule
        so the three states cannot drift into three different shapes."""
        base = _rule(".tree-entry-label input[type=checkbox]::after")
        assert "rotate(45deg)" in base                 # the tick's shape, defined once
        assert "var(--pico-muted-color)" in base       # grey while empty, no literal
        ghost = float(re.search(r"opacity:\s*([0-9.]+)", base).group(1))
        assert 0 < ghost < 0.5, f"empty must be a HINT, not a tick: {ghost}"

        css = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
        m = re.search(r"\.tree-entry-label:hover input\[type=checkbox\]::after[^{]*\{([^}]*)\}", css)
        assert m, "hovering the row must bring the tick up"
        hover = float(re.search(r"opacity:\s*([0-9.]+)", m.group(1)).group(1))
        assert hover > ghost, f"hover must be louder than empty ({hover} vs {ghost})"

        on = _rule(".tree-entry-label input[type=checkbox]:checked")
        assert "background: var(--pico-primary)" in on          # the box fills
        mark = _rule(".tree-entry-label input[type=checkbox]:checked::after")
        assert "border-color: #fff" in mark                     # the tick turns white
        assert float(re.search(r"opacity:\s*([0-9.]+)", mark).group(1)) == 1.0

    def test_row_hover_brightens_the_box(self):
        css = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
        assert re.search(r"\.tree-entry-label:hover input\[type=checkbox\][^{]*\{[^}]*border-color: var\(--pico-primary\)", css)

    def test_the_box_says_what_it_is_for(self):
        m = re.search(r'<input type="checkbox" name="paths"[^>]*>', _MACROS)
        assert m and 'title="Tick to select this run' in m.group(0)
        assert "Compare Selected" in m.group(0) and "Trend Tracker" in m.group(0)


class TestRowSize:
    def test_the_rows_are_the_size_the_customers_asked_for(self):
        """docs/165 took 1.06 -> 1.32em, the 1.25x multiple users agreed on.
        2026-09-05 the same customers asked for "아주 조금만 더 작게" and it
        stepped once to 1.26em -- still far above the 1.06 they called too
        small, which is what the RANGE is for. The badge and the name are each
        1em OF THE ROW, so they follow from this one number; the date header is
        a sibling and must match explicitly, or it ends up smaller than the
        rows beneath it. The exact literal is pinned in
        test_sidebar_run_rows.py; this range is the band it must stay in."""
        css = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)
        def var(name):
            return float(re.search(name + r":\s*([0-9.]+)em", css).group(1))
        row = var("--tree-entry-label-font")
        assert 1.20 <= row <= 1.36, f"rows should be ~1.26em, got {row}"
        assert var("--tree-run-id-font") == 1.0, "the run badge follows the row"
        assert var("--tree-entry-name-font") == 1.0, "the name follows the row"
        assert var("--tree-date-label-font") >= row,             "a date header must never be smaller than the rows under it"


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

    def test_the_compare_buttons_wrap_so_the_hint_drops_below(self):
        # F-LAYOUT-HINT: .compare-hint is `flex: 1 1 100%`, which only reads as
        # "a line UNDER the buttons" inside a WRAPPING flex container. Without
        # flex-wrap it stays on the buttons' line and, with basis 100% vs the
        # buttons' basis 0, collapses both buttons over two lines on every load.
        r = _rule(".compare-buttons")
        assert "display: flex" in r and "flex-wrap: wrap" in r
        assert "flex: 1 1 100%" in _rule(".compare-hint")   # the hint takes the wrapped line


class TestSettingsPopoverScrolls:
    def test_the_settings_dropdown_caps_and_scrolls(self):
        # F-SETTINGS-TALL: the popover is anchored at the viewport top and can't
        # be dragged higher, so once its content is taller than the viewport the
        # bottom group is unreachable -- there was no scrollbar (overflow was the
        # default `visible`). Cap it to the viewport and let it scroll.
        r = _rule(".settings-dropdown")
        assert "max-height:" in r and "100vh" in r
        assert "overflow-y: auto" in r


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
