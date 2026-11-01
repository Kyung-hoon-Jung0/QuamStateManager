"""User-view sweep, Chip Status section: pins for the data and behaviour fixes.

The report's flux table, the map key under the report's map, and the client
rules (bar colour = map colour, bar axis from near the data, bar labels with
significant figures, the scroll-spy at the top, the empty-tile reasons, the
"N of M have no value" line, the Expand pill off the edge axes, and hiding an
edge label a hover badge would half-cover) are executed in jsdom-free form by
running the shipped functions where they are standalone, else pinned at source.
"""
import re
import subprocess
from pathlib import Path

import pytest

from tests.test_chip_report import client  # noqa: F401  (fixture)

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "quam_state_manager" / "web" / "static"
CS = (STATIC / "chip-status.js").read_text(encoding="utf-8")
TG = (STATIC / "topo-graph.js").read_text(encoding="utf-8")


class TestReport:
    def test_phi0_current_claims_no_unit(self, client):  # noqa: F811
        """The class declares amperes, real chips store values of 45-73 under
        it: the column must not print a unit it cannot prove."""
        b = client.get("/chip-status/report").get_data(as_text=True)
        assert "current (A)" not in b
        assert "phi0_current (as stored)" in b

    def test_the_map_has_a_key(self, client):  # noqa: F811
        b = client.get("/chip-status/report").get_data(as_text=True)
        i = b.index('id="component-map"')
        key = b.index("rep-map-legend", i)
        assert key - i < 800
        assert "each circle is a qubit" in b


def _fn(src, name):
    i = src.index("function " + name)
    depth, j = 0, src.index("{", i)
    for k in range(j, len(src)):
        depth += {"{": 1, "}": -1}.get(src[k], 0)
        if depth == 0:
            return src[i:k + 1]
    raise AssertionError(name)


def _node(js):
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


class TestBars:
    def test_bar_label_keeps_significant_figures(self):
        out = _node(_fn(CS, "fmtBarLabel") + ";console.log([fmtBarLabel(0.03929,'x'),"
                    "fmtBarLabel(123.456,'x'),fmtBarLabel(5.7056,'f_01')].join('|'))")
        assert out == "0.03929|123.5|5.7056"

    def test_bar_colour_is_the_map_colour(self):
        body = _fn(CS, "_barFill")
        assert "_tmapPaint(interpolateColor(t, dCfg.colorScale)" in body
        assert "return _barFill(t, _tmap);" in CS
        # a palette switch repaints the bars beside the map too
        sp = CS[CS.index("window.switchPalette"):CS.index("window.switchBarPalette")]
        assert "recolorBarCharts()" in sp

    def test_axis_starts_near_the_data_and_names_the_unit(self):
        assert "var xTitle = _tmapTitle(def.title, def.key);" in CS
        assert re.search(r"if \(!/fidelity/\.test\(def\.key\) && displayVals\.length\)", CS)
        assert "xAxis.range = [Math.max(0, dLo - dPad), dHi + dPad];" in CS
        assert "xaxis: xAxis," in CS


class TestPanels:
    def test_ghosted_nodes_are_counted_and_named(self):
        assert "qubits have no '" in CS
        assert "_mv(n, def.key) == null && !_badFit(n, def.key)" in CS

    def test_empty_converted_tiles_say_why(self):
        assert "srbGateTile.sub = srbAgg.count" in CS
        assert "irbCliffTile.sub = irbAgg.count" in CS
        assert "run 2Q Standard RB" in CS and "run 2Q Interleaved RB" in CS

    def test_scrollspy_top_selects_the_first_section(self):
        spy = _fn(CS, "_setupScrollSpy")
        assert "pane.scrollTop <= 2" in spy
        assert spy.index("pane.scrollTop <= 2") > spy.index("if (jumped) best = jumped;")


class TestMapHover:
    def test_expand_pill_prefers_the_stone_corners(self):
        body = _fn(TG, "positionDetail")
        assert "var cands = pair ?" in body
        assert "[a.right - b.width * 0.25, a.top - b.height * 0.75]" in body
        assert "if (hit < bestHit)" in body

    def test_a_hover_badge_hides_the_label_it_covers(self):
        body = _fn(TG, "bindLinked")
        assert "function unclutter()" in body
        assert "unclutter();" in body
        assert "t.style.visibility = 'hidden'" in body
        assert "hidden.forEach(function (el) { el.style.visibility = ''; });" in body
