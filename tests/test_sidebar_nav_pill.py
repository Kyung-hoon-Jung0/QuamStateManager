"""docs/136 r4 — the sidebar's submenu-row pill must be the plain pill's box.

Customer-reported from the real screen: the active highlight on rows that
carry trailing icons (Chip Status, Datasets, ...) painted a DIFFERENT box than
a plain link's — indented ~11px, 45px tall against 36, and a sub-item's pill
overlapped the parent pill by 2px.

jsdom does no layout, so the GEOMETRY was verified in a real Chrome (all seven
`nav-sub-row`s and the floatable li measured x2/w240/h36, identical to a plain
active link, sub-pill gap 3px). What CAN be pinned headlessly is the presence
of the load-bearing rules — each one below was the difference between a
matching and a mismatching box, so losing any of them regresses the screen.
Same precedent as `test_auto_sync`'s app.js grep.
"""

from __future__ import annotations

import re
from pathlib import Path

_CSS = (Path(__file__).resolve().parent.parent
        / "quam_state_manager" / "web" / "static" / "style.css"
        ).read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    """The first declaration block for *selector* (flattened)."""
    m = re.search(re.escape(selector) + r"\s*{([^}]*)}", _CSS)
    assert m, f"rule not found: {selector}"
    return " ".join(m.group(1).split())


class TestTheRowTakesTheAnchorsBleed:
    def test_the_sub_row_bleeds_like_an_anchor(self):
        """Pico bleeds every nav ANCHOR over the li padding with negative
        margins; the row div got none, so its pill sat indented and narrow.
        The bleed must use the same Pico vars the anchors use — the root font
        is 21px under UI scaling, so any px/rem literal would drift."""
        body = _rule(".sidebar-nav .nav-sub-row")
        assert "calc(var(--pico-nav-link-spacing-vertical) * -1)" in body
        assert "calc(var(--pico-nav-link-spacing-horizontal) * -1)" in body
        # top + sides ONLY: a bottom bleed pulls the subnav up under the pill
        assert re.search(r"margin:[^;]*\)\s+0\s*;?", body), body

    def test_the_inner_anchor_does_not_bleed_twice(self):
        assert "margin: 0" in _rule(".sidebar-nav .nav-sub-row > a")

    def test_the_floatable_li_is_flush(self):
        assert "padding: 0" in _rule(".sidebar-nav li.nav-floatable")
        assert "margin: 0" in _rule(".sidebar-nav li.nav-floatable > a")


class TestTheToggleFitsTheRow:
    def test_the_toggle_rule_is_scoped_under_sidebar_nav(self):
        """Bare `.nav-sub-toggle` lost to Pico's nav-button padding (computed
        7.875px against the declared 0), and 1.9em of the sidebar font is a
        ~30px glyph — together they made the row 45px tall."""
        body = _rule(".sidebar-nav .nav-sub-toggle")
        assert "font-size: 1.45em" in body
        assert "margin: 0" in body
        assert "padding: 0 .3rem" in body

    def test_no_unscoped_toggle_rule_remains(self):
        # Anchored at line start: a scoped compound selector legitimately ENDS
        # in `.nav-sub-toggle` (the active-row recolor does), and a lookbehind
        # on the dot alone matched those false positives.
        assert not re.search(r"(?m)^\.nav-sub-toggle\s*{", _CSS), \
            "a bare .nav-sub-toggle block would lose to Pico again"


class TestTheSubItemsStopOverlapping:
    def test_half_vertical_bleed_full_horizontal(self):
        """Full horizontal bleed keeps the sub-pill's right edge on the shared
        pill edge; HALF the vertical bleed is what removed the 2px overlap
        with the parent pill."""
        body = _rule(".sidebar-nav .nav-subitems li a")
        assert "calc(var(--pico-nav-link-spacing-vertical) * -0.5)" in body
        assert "calc(var(--pico-nav-link-spacing-horizontal) * -1)" in body
