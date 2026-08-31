"""docs/141 §4o — the Chip Status page, laid out the way the user asked.

1) Overview first; 2) Health right below it, WITHOUT the row-level "Tiles"
size control — that control now sits right of each panel's own title;
3) Topology next; 4) then Trends; 5) then Fidelity, which absorbed the old
"Gate (2Q)" tab: 2Q gate (RB) first, then 1Q gate, then readout; 5-1) the
IQ-blob metric is "Readout Fidelity (GE)" / "(GEF)" everywhere in SM, badge
form "Read. Fid. (GE)" — never "IQ Blob", never "Assign".

Pinned here: the template's section order and sub-nav; the sidebar
sub-links; the route accepting ?view=health and still ?view=gate; the JS
tables (TAB_SPEC, PANEL_DEFS) and the per-panel control; the glossary,
thresholds and Trends labels; the GEF metric derived by QueryEngine and by
the history index (extraction + the v3→v4 content upgrade). The jsdom
harness (chip_density_selfcheck.cjs) pins the per-panel size behaviour.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import chip_health
from quam_state_manager.core.query import _assignment_fidelity, _assignment_fidelity_n
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
TPL = ROOT / "quam_state_manager" / "web" / "templates"
JS = (ROOT / "quam_state_manager" / "web" / "static" / "chip-status.js").read_text(encoding="utf-8")
WIRING = (TPL / "_wiring.html").read_text(encoding="utf-8")
BASE = (TPL / "base.html").read_text(encoding="utf-8")


def _state() -> dict:
    def _q(i, gef):
        q = {"id": f"q{i}", "f_01": 5e9 + i * 1e6, "anharmonicity": -220e6, "T1": 2.4e-5, "T2ramsey": 2e-5, "T2echo": 3e-5,
             "gate_fidelity": {"averaged": 0.995, "x180": 0.996, "x90": 0.997},
             "xy": {"RF_frequency": 5e9, "operations": {"x180_DragCosine": {"amplitude": 0.1, "length": 40}}},
             "resonator": {"RF_frequency": 7e9,
                           "confusion_matrix": [[0.97, 0.03], [0.05, 0.95]],
                           "operations": {"readout": {"amplitude": 0.04, "length": 1000}}},
             "z": {"joint_offset": 0.05}, "grid_location": f"{i},0"}
        if gef:
            q["resonator"]["gef_confusion_matrix"] = [[0.90, 0.06, 0.04], [0.08, 0.86, 0.06], [0.05, 0.10, 0.85]]
        return q
    return {"qubits": {"q0": _q(0, True), "q1": _q(1, True), "q2": _q(2, False)},
            "qubit_pairs": {}, "active_qubit_names": ["q0", "q1", "q2"]}


@pytest.fixture
def client(tmp_path: Path):
    (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {"qubits": {}}, "network": {"host": "10.1.1.1"}}),
                                          encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
    return c


class TestSectionOrder:
    def test_the_page_order_is_overview_health_topology_trends_fidelity(self):
        marks = [m.group(1) or m.group(2) for m in re.finditer(
            r'<div class="topo-section[^"]*" (?:data-topo-section="(\w+)"|id="(sec-\w+)")', WIRING)]
        # docs/148: fidelity split into 2Q (keeps the historical "fidelity"
        # anchor) then 1Q then readout, each its own section
        assert marks[:7] == ["overview", "health", "sec-topology", "trends",
                             "fidelity", "fid1q", "fidro"], marks
        fid = WIRING[WIRING.index('data-topo-section="fidelity"'):]
        fid = fid[:fid.index("<!--")]
        assert 'id="topo-2q-rb-panels"' in fid
        assert '<h3 class="topo-section-title">2Q Gate Fidelity</h3>' in fid
        # 1Q then readout hosts, then the remaining metric groups
        assert (WIRING.index('id="topo-fidelity-1q-panels"')
                < WIRING.index('id="topo-fidelity-ro-panels"')
                < WIRING.index('id="topo-metric-panels"'))

    def test_the_browser_sees_the_sections_in_order(self, client):
        """Parsed the way a browser parses it — the raw-text pin above cannot
        tell a section from a section swallowed by an unclosed banner comment
        (exactly what the first cut of this layout did: the Fidelity wrapper
        was in the bytes and absent from the DOM, real Chrome, 2026-08-29)."""
        from html.parser import HTMLParser

        class P(HTMLParser):
            def __init__(self):
                super().__init__()
                self.seen = []
                self.comments = 0

            def handle_starttag(self, tag, attrs):
                a = dict(attrs)
                if a.get("data-topo-section"):
                    self.seen.append(a["data-topo-section"])
                elif a.get("id") in ("sec-topology", "topo-fidelity-1q-panels",
                                     "topo-fidelity-ro-panels", "topo-metric-panels"):
                    self.seen.append(a["id"])

            def handle_comment(self, data):
                self.comments += 1
                assert "<div" not in data, f"markup inside a comment: {data[:120]!r}"

        body = client.get("/topology", headers={"HX-Request": "true"}).get_data(as_text=True)
        p = P()
        p.feed(body)
        assert p.seen == ["overview", "health", "sec-topology", "trends", "fidelity", "2qrb",
                          "fid1q", "topo-fidelity-1q-panels",
                          "fidro", "topo-fidelity-ro-panels", "metrics"], p.seen
        assert p.comments >= 8

    def test_the_subnav_follows_the_page_and_has_no_gate_tab(self):
        views = re.findall(r'class="topo-subnav-btn" role="tab" data-view="(\w+)"', WIRING)
        assert views == ["overview", "health", "topology", "trends", "fidelity2q", "fidelity1q",
                         "readout", "coherence", "frequencies", "calibration"]
        assert "Gate (2Q)" not in WIRING

    def test_the_sidebar_sublinks_follow_the_same_order(self):
        ul = BASE[BASE.index('id="chip-status-subnav"'):]
        ul = ul[:ul.index("</ul>")]
        assert re.findall(r'data-view="(\w+)"', ul) == ["overview", "health", "topology", "trends",
                                                         "fidelity2q", "fidelity1q", "readout",
                                                         "coherence", "frequencies", "calibration"]
        assert ">Health<" in ul and "Gate (2Q)" not in ul

    def test_the_health_row_lost_the_tiles_control(self):
        health = WIRING[WIRING.index('data-topo-section="health"'):WIRING.index("{# Phase C")]
        assert "topo-density" not in health and "Tiles" not in health and "density-preset" not in health

    def test_the_route_accepts_health_and_still_gate(self, client):
        for v in ("health", "gate", "fidelity", "overview"):
            r = client.get(f"/topology?view={v}", headers={"HX-Request": "true"})
            assert r.status_code == 200
            assert f"chipView: {json.dumps(v)}" in r.get_data(as_text=True), v


class TestClientTables:
    def test_tab_spec(self):
        spec = JS[JS.index("var TAB_SPEC = {"):JS.index("var _chipSectionBuilt")]
        keys = re.findall(r"^\s+(\w+):\s+\{ build:", spec, re.M)
        assert keys == ["overview", "health", "topology", "fidelity2q", "fidelity1q", "readout",
                        "coherence", "frequencies", "calibration", "trends"]
        assert "gate:" not in spec
        assert "fidelity2q:   { build: '2qrb',          sel: '[data-topo-section=\"fidelity\"]' }" in spec
        assert "if (view === 'gate' || view === 'fidelity') view = 'fidelity2q';" in JS

    def test_panel_defs_order_and_names(self):
        defs = JS[JS.index("var PANEL_DEFS = ["):JS.index("function findProp")]
        keys = re.findall(r"\{key:'(\w+)',", defs)
        fid = [k for k in keys if "fidelity" in k]
        # docs/148b (customer): the readout block reads GE (g, e) then GEF (g, e, f)
        assert fid == ["gate_fidelity_avg", "gate_fidelity_x180", "gate_fidelity_x90",
                       "assignment_fidelity", "ro_fidelity_g", "ro_fidelity_e",
                       "assignment_fidelity_gef", "ro_fidelity_gef_g",
                       "ro_fidelity_gef_e", "ro_fidelity_gef_f"]
        assert "title:'Readout Fidelity (GE) (%)'" in defs and "title:'Readout Fidelity (GEF) (%)'" in defs
        # every readout panel names the leaf its honest-empty line fills from
        assert defs.count("source:'confusion_matrix'") == 3
        assert defs.count("source:'gef_confusion_matrix'") == 4
        assert "IQ Blob" not in JS and "Assign" not in JS

    def test_every_panel_title_carries_its_own_size_control(self):
        # the metric panels and the 2Q-gate panels both put the control right of the title
        assert JS.count("window.ChipStatus.density.controlHtml(") == 2
        # S · M · L AND the fine slider (the user asked the slider back), both
        # bound to the panel key, smaller than the title
        ctl = JS[JS.index("function controlHtml(key) {"):JS.index("function init() {")]
        assert 'class="topo-density-pslider" data-density-panel="' in ctl and 'min="\' + MIN + \'" max="\' + MAX' in ctl
        assert "d.addEventListener('input', function (e) {" in JS
        css = (ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
        assert ".topo-density-ctl-panel { font-size: 0.58em;" in css
        assert ".topo-density-ctl-panel button.density-preset { font-size: 0.68rem; height: 1.25rem;" in css
        assert ".topo-density-ctl-panel input.topo-density-pslider { width: 4rem;" in css
        # the floors the user asked for: panel size down to 0.35, the hero map down to 0.25x
        assert "MIN = 0.35, MAX = 1.15" in JS
        assert 'class="topo-hero-zslider" min="0.25" max="4"' in JS
        assert "zoom = Math.min(4, Math.max(0.25, z));" in JS and "if (zRaw >= 0.25 && zRaw <= 4) zoom = zRaw;" in JS
        assert 'data-density-panel="\' + def.key + \'"' in JS
        assert "data-density-panel=\"' + _esc(dKey) + '\"" in JS
        # the 2Q RB block is a sub-heading of Fidelity, not its own h3 section
        assert "2Q Gate Fidelity \\u2014 RB</h4>" in JS
        assert "Gate Fidelity \\u2014 2Q RB</h3>" not in JS

    def test_a_jump_below_trends_is_re_anchored_when_trends_lands(self):
        """Trends is above Fidelity now and arrives late: the jump guard is a
        top-level core, noted on every scrolled jump, and re-run once the
        Trends swap lands (the harness pins its behaviour)."""
        assert "window.ChipStatus.jumpGuard = (function () {" in JS
        assert "if (scroll !== false) _jump.note(view);" in JS
        after = JS[JS.index("var p = htmx.ajax('GET', '/topology/trends'"):]
        assert "requestAnimationFrame(function () { _jump.reanchor(); });" in after[:1500]
        assert ".topo-dashboard .topo-section { scroll-margin-top:" in (ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")

    def test_the_overview_names_readout_fidelity_ge_and_gef(self):
        assert "metricTile('Readout Fidelity (GE)'" in JS
        assert "metricTile('Readout Fidelity (GEF)'" in JS
        assert "metricTile('Readout Fidelity'," not in JS


class TestNaming:
    def test_the_glossary(self):
        ge = chip_health.metric_meta("assignment_fidelity")
        gef = chip_health.metric_meta("assignment_fidelity_gef")
        assert ge["label"] == "Readout fidelity (GE)" and ge["abbr"] == "Read. Fid. (GE)"
        assert gef["label"] == "Readout fidelity (GEF)" and gef["abbr"] == "Read. Fid. (GEF)"
        assert gef["direction"] == "higher"
        for m in chip_health.METRIC_META.values():
            assert "IQ Blob" not in m["label"] and "Assign" not in m["label"] and "Assign" not in m["abbr"]
        assert chip_health.DEFAULT_THRESHOLDS["assignment_fidelity_gef"]["warn"] == 0.90
        assert chip_health.verdict(0.85, chip_health.DEFAULT_THRESHOLDS["assignment_fidelity_gef"]) == "warn"
        assert chip_health.physicality("assignment_fidelity_gef", 1.2) is False

    def test_trends_labels(self):
        from quam_state_manager.web import routes as R
        assert R._TREND_LABEL_OVERRIDES == {"assignment_fidelity": "Readout Fidelity (GE)",
                                            "assignment_fidelity_gef": "Readout Fidelity (GEF)"}


class TestGefMetric:
    def test_the_formula_reads_all_three_states(self):
        cm = [[0.90, 0.06, 0.04], [0.08, 0.86, 0.06], [0.05, 0.10, 0.85]]
        assert _assignment_fidelity_n(cm) == pytest.approx((0.90 + 0.86 + 0.85) / 3)
        # the 2x2 helper is unchanged for GE
        assert _assignment_fidelity([[0.97, 0.03], [0.05, 0.95]]) == pytest.approx(0.96)
        # a matrix that is not row-stochastic reads missing, never a number
        assert _assignment_fidelity_n([[900, 60, 40], [80, 860, 60], [50, 100, 850]]) is None
        assert _assignment_fidelity_n(None) is None and _assignment_fidelity_n([[1.0]]) is None

    def test_query_engine_derives_it_per_qubit(self, client):
        from quam_state_manager.web import routes as R
        with client.application.app_context():
            with client.application.test_request_context("/"):
                nodes = {n["id"]: n for n in R._engine().get_topology()["nodes"]}
        q0, q2 = nodes["q0"], nodes["q2"]
        assert q0["assignment_fidelity_gef"] == pytest.approx((0.90 + 0.86 + 0.85) / 3)
        assert q0["metrics"]["assignment_fidelity_gef"]["value"] == pytest.approx((0.90 + 0.86 + 0.85) / 3)
        assert q0["assignment_fidelity"] == pytest.approx(0.96)
        assert q2["assignment_fidelity_gef"] is None and q2["assignment_fidelity"] == pytest.approx(0.96)
        # docs/148b: per-state diagonals of the same two matrices
        assert q0["ro_fidelity_g"] == pytest.approx(0.97)
        assert q0["ro_fidelity_gef_g"] == pytest.approx(0.90)
        assert q0["ro_fidelity_gef_e"] == pytest.approx(0.86)
        assert q0["ro_fidelity_gef_f"] == pytest.approx(0.85)
        assert q0["metrics"]["ro_fidelity_gef_f"]["value"] == pytest.approx(0.85)
        assert (q2["ro_fidelity_gef_g"] is None and q2["ro_fidelity_gef_e"] is None
                and q2["ro_fidelity_gef_f"] is None)

    def test_the_chip_status_page_ships_it(self, client):
        body = client.get("/topology", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert '"assignment_fidelity_gef"' in body
        assert "Readout fidelity (GEF)" in body          # metric_meta reaches the page

    def test_history_index_extracts_and_upgrades_it(self, tmp_path):
        from quam_state_manager.core.history import (
            HistoryManager, _INDEX_SCHEMA_VERSION, DEFAULT_TRACKED_PROPERTIES)
        assert "assignment_fidelity_gef" in DEFAULT_TRACKED_PROPERTIES and _INDEX_SCHEMA_VERSION >= 4
        chip = tmp_path / "chip" / "quam_state"
        chip.mkdir(parents=True)
        (chip / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        (chip / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {"host": "1.1.1.1", "cluster_name": "C"}}),
                                          encoding="utf-8")
        hm = HistoryManager(tmp_path / "instance")
        hm.check_and_snapshot(chip, "manual", force=True)
        idx = hm._index_path(chip)
        gef = (0.90 + 0.86 + 0.85) / 3
        conn = sqlite3.connect(str(idx), isolation_level=None)
        try:
            # a freshly built index carries the GEF rows for the qubits that have the matrix
            got = dict(conn.execute("SELECT qubit, value FROM param_history WHERE property='assignment_fidelity_gef'").fetchall())
            assert got["q0"] == pytest.approx(gef) and got["q1"] == pytest.approx(gef)
            assert got.get("q2") is None
            # simulate a v3 index: no GEF rows yet
            conn.execute("DELETE FROM param_history WHERE property='assignment_fidelity_gef'")
            conn.execute("PRAGMA user_version=3")
        finally:
            conn.close()
        hm._schema_verified.discard(str(hm._history_dir(chip)))
        hm._ensure_index_fresh(chip)
        conn = sqlite3.connect(str(idx), isolation_level=None)
        try:
            got = dict(conn.execute("SELECT qubit, value FROM param_history WHERE property='assignment_fidelity_gef'").fetchall())
            ver = conn.execute("PRAGMA user_version").fetchone()[0]
        finally:
            conn.close()
        assert got.get("q0") == pytest.approx(gef), got
        assert ver == _INDEX_SCHEMA_VERSION


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_chip_density_selfcheck():
    """Per-panel tile size against the REAL chip-status.js under jsdom."""
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(ROOT / "tests" / "chip_density_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", timeout=180, cwd=str(ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 10, r.stdout


class Test4acGefHonesty:
    """docs/141 4ac -- what the GEF metric refuses to claim (R2-9, R7-6/R2-11)."""

    def test_a_nan_never_passes_as_a_probability_distribution(self):
        """`nan < -1e-9` is False and `abs(nan - 1.0) > 0.02` is False, so every
        numeric gate the validator had let a NaN through: the row "summed to 1"
        and a matrix that is not a distribution produced a confident, GREEN
        fidelity. The validator is shared, so this hardens the GE metric and
        the per-state diagonals too."""
        from quam_state_manager.core import query

        nan = float("nan")
        assert query._valid_confusion_matrix([[nan, 0.0], [0.0, 1.0]]) is False
        assert query._valid_confusion_matrix([[1.0, nan], [0.0, 1.0]]) is False
        assert query._valid_confusion_matrix([[float("inf"), 0.0], [0.0, 1.0]]) is False
        assert query._assignment_fidelity([[nan, 0.0], [0.0, 1.0]]) is None
        assert query._assignment_fidelity_n(
            [[nan, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]) is None
        assert query._cm_diag([[nan, 0.0], [0.0, 1.0]], 1) is None
        # and a real matrix is untouched
        assert query._valid_confusion_matrix([[0.98, 0.02], [0.03, 0.97]]) is True
        assert query._assignment_fidelity([[0.98, 0.02], [0.03, 0.97]]) == pytest.approx(0.975)

    def test_a_two_state_matrix_is_not_a_gef_number(self):
        """A 2x2 stored under `gef_confusion_matrix` averaged to a correct GE
        value wearing the GEF label -- and scored against the GEF thresholds,
        which are deliberately LOWER because three-state discrimination runs
        worse. A blank is what SM already shows for a chip with no matrix."""
        from quam_state_manager.core import query

        two = [[0.98, 0.02], [0.03, 0.97]]
        three = [[0.95, 0.03, 0.02], [0.04, 0.93, 0.03], [0.05, 0.05, 0.90]]
        assert query._assignment_fidelity_n(two) is None
        assert query._assignment_fidelity_n(three) == pytest.approx((0.95 + 0.93 + 0.90) / 3)
        # the GE metric still reads a 2x2 -- it is the one that means two states
        assert query._assignment_fidelity(two) == pytest.approx(0.975)
        # the floor is a named parameter, so a caller that really wants n>=2
        # has to say so
        assert query._assignment_fidelity_n(two, n_min=2) == pytest.approx(0.975)
        assert query._GEF_MIN_STATES == 3
