"""docs/141 §4q — ONE vertical scroller on Live State Edit and Chip Status.

Users had two bars: the grid (and the topology map) scrolled inside itself
while #table-pane scrolled the page, so they scrolled the grid to its end
only to scroll the page again. Now #table-pane is the only vertical
scroller: the bulk wrap is a max-content frame (no overflow, no height cap),
the topology hero grows to its map, the sticky header / row head / apply
column anchor to the pane (its padding subtracted), the top scrollbar proxy
is gone, and cold-column hydration listens to the pane's scroll.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / "quam_state_manager/web/static/style.css").read_text(encoding="utf-8")
JS = (ROOT / "quam_state_manager/web/static/bulk-edit.js").read_text(encoding="utf-8")
TPL = (ROOT / "quam_state_manager/web/templates/_bulkedit.html").read_text(encoding="utf-8")


def _rule(selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, selector
    return m.group(1)


class TestOneScroller:
    def test_the_bulk_wrap_is_a_frame_not_a_scroller(self):
        r = _rule(".bulk-table-wrap")
        assert "overflow: visible" in r and "max-height: none" in r and "width: max-content" in r
        assert "calc(100vh" not in r

    def test_the_topology_hero_has_no_inner_vertical_scroll(self):
        r = _rule(".topo-hero-scroll")
        assert "overflow-y: visible" in r and "max-height: none" in r and "overflow-x: auto" in r
        assert "82vh" not in r

    def test_the_sticky_parts_anchor_to_the_pane(self):
        assert "top: calc(-1 * var(--table-pane-pad-v, 0px))" in _rule(".bulk-table thead th")
        assert "calc(var(--bulk-grouphead-h, 1.7rem) - var(--table-pane-pad-v, 0px))" in _rule(".bulk-table .bulk-head-row th")
        assert "left: calc(-1 * var(--table-pane-pad-h, 0px))" in _rule(".bulk-corner, .bulk-rowhead")
        assert "right: calc(-1 * var(--table-pane-pad-h, 0px))" in _rule(".bulk-apply-col")
        assert "--table-pane-pad-v:" in CSS and "--table-pane-pad-h:" in CSS

    def test_the_rows_between_the_grids_stay_put_sideways(self):
        """position:sticky cannot hold a row that is exactly as wide as its
        containing block (real Chrome: the toolbar left at -2475 px), so the
        grid moves those rows by the pane's scrollLeft itself.

        docs/141 4ac: this test indexed a selector list that §4s (0c72989)
        deleted, so it raised ValueError before reaching a single assertion
        and had been RED for the last eight commits of the range — while its
        second line asserted the very ``will-change: transform`` §4s removed
        *because it was the bug* (it made each bar a stacking context and the
        chip bar painted over the Properties / Qubits popovers). Repairing
        only the string would have re-asserted the defect. It now pins what
        §4s actually shipped, and the mutation check below is the reason the
        rewrite exists: with the old form, deleting the mount-time
        ``_pinBarsToScroll()`` changed nothing in this file's output.
        """
        toolbar = _rule(".bulk-panel .bulk-toolbar")
        assert "z-index: 8" in toolbar and "position: relative" in toolbar
        assert "position: sticky" not in toolbar
        for sel in (".bulk-panel .bulk-chipbar, .bulk-panel .bulk-dyn-truncated, .bulk-panel .bulk-virt-note",
                    ".bulk-panel .bulk-pair-divider"):
            r = _rule(sel)
            assert "z-index: 6" in r and "position: sticky" not in r
        # 4s: the toolbar owns the popovers, so it must outrank the bars below it
        assert CSS.index(".bulk-panel .bulk-toolbar {") >= 0
        i = CSS.index(".bulk-panel .bulk-toolbar {")
        assert "will-change" not in CSS[i:i + 400], \
            "4s removed will-change from these rows -- it made each one a stacking context"
        assert "function _pinBarsToScroll() {" in JS and "'translateX(' + x + 'px)'" in JS
        k = JS.index("_virtInit();            // docs/105 #1")
        # the live call at mount, not a commented-out one (mutation-checked)
        assert "\n            _pinBarsToScroll();" in JS[k:k + 1800]

    def test_a_restored_pane_re_derives_the_bars(self):
        """docs/141 4ac (CRITICAL, R7-1). The bars' inline transform is a
        function of ``#table-pane.scrollLeft``; docs/110's PaneState parks the
        bars (they are the pane's children) but not the pane's own scrollLeft,
        and the docs/139 skip-restore path deliberately does not re-run the
        mount. So returning to Live State Edit re-attached a toolbar still
        translated by 3,000 px over a pane at 0 — the search box, the
        Properties / Qubits / Pairs pickers, Apply all and the chip bar all
        painted outside the pane, with no console error and no clue, until the
        user happened to scroll sideways.
        """
        app = (ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
        assert "scrollX: p.scrollLeft" in app, "the parked pane remembers its sideways position"
        assert "p.scrollLeft = e.scrollX || 0;" in app, "and gets it back before paneRestored fires"
        i = app.index("p.scrollLeft = e.scrollX || 0;")
        j = app.index("paneRestored")
        assert i < j, "scrollLeft must be restored BEFORE the event the grid listens to"
        assert "document.addEventListener('paneRestored'" in JS, \
            "bulk-edit.js re-derives the bars when its pane comes back"
        k = JS.index("document.addEventListener('paneRestored'")
        assert "_pinBarsToScroll()" in JS[k:k + 500]

    def test_the_top_scrollbar_proxy_is_gone(self):
        assert 'id="bulk-scroll-top"' not in TPL
        assert ".bulk-scroll-top { display: none; }" in CSS

    def test_hydration_listens_to_the_pane(self):
        assert "function _scrollerOf(t) {" in JS
        assert "return document.getElementById('table-pane') || t.closest('.bulk-table-wrap') || t.parentElement;" in JS
        # docs/141 4ad: the hydration core moved to grid-virt.js; the qubit
        # grid hands it the scroller, which is what this pin is about.
        i = JS.index("window.GridVirt.create({")
        assert "scroller: _scrollerOf," in JS[i:i + 1400]
        core = (ROOT / "quam_state_manager/web/static/grid-virt.js").read_text(encoding="utf-8")
        j = core.index("function init() {")
        assert "var wrap = scrollerOf(t);" in core[j:j + 3000]
        # the visual frame is still what the note is inserted before (the note
        # moved into the shared core with the rest of the mechanism, 4ad)
        j = core.index("function note(msg) {")
        assert "t.closest('.bulk-table-wrap')" in core[j:j + 400]


class TestRendered:
    def test_the_page_ships_no_proxy_and_the_frame(self, tmp_path):
        q = {"id": "qA1", "f_01": 5e9, "xy": {"operations": {"x180": {"amplitude": 0.1}}},
             "resonator": {"operations": {"readout": {"amplitude": 0.04}}}}
        (tmp_path / "state.json").write_text(json.dumps({"qubits": {"qA1": q}, "qubit_pairs": {}, "active_qubit_names": ["qA1"]}), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {"host": "1.1.1.1"}}), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
        html = c.get("/bulk", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "bulk-scroll-top" not in html
        assert '<div class="bulk-table-wrap">' in html
