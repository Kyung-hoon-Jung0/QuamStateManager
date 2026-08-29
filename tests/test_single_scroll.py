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
        grid moves those rows by the pane's scrollLeft itself."""
        i = CSS.index(".bulk-panel .bulk-toolbar, .bulk-panel .bulk-chipbar, .bulk-panel .bulk-pair-divider,")
        assert "will-change: transform" in CSS[i:i + 300] and "position: sticky" not in CSS[i:i + 300]
        assert "function _pinBarsToScroll() {" in JS and "'translateX(' + x + 'px)'" in JS
        k = JS.index("_virtInit();            // docs/105 #1")
        # the live call at mount, not a commented-out one (mutation-checked)
        assert "\n            _pinBarsToScroll();" in JS[k:k + 900]

    def test_the_top_scrollbar_proxy_is_gone(self):
        assert 'id="bulk-scroll-top"' not in TPL
        assert ".bulk-scroll-top { display: none; }" in CSS

    def test_hydration_listens_to_the_pane(self):
        assert "function _scrollerOf(t) {" in JS
        assert "return document.getElementById('table-pane') || t.closest('.bulk-table-wrap') || t.parentElement;" in JS
        i = JS.index("function _virtInit() {")
        assert "var wrap = _scrollerOf(t);" in JS[i:i + 3000]
        # the visual frame is still what the note is inserted before
        j = JS.index("function _virtNote(msg) {")
        assert "t.closest('.bulk-table-wrap')" in JS[j:j + 400]


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
