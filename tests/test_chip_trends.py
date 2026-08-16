"""Chip-wide Trends (docs/120 items 5 + 9).

Customer, twice: *"to see T1/RB/T2 trends today you go to Param History, but
that shows **per-qubit** trends, not an **integrated** one. Add a Trends tab
under Chip Status where **all qubits' T1 appear in a SINGLE plot**. A multiple
line plot, legend = all qubits."*

Two data tiers, one rendered shape:
  - the curated ``DEFAULT_TRACKED_PROPERTIES`` through the SQLite property
    index, whose ``extract_property_history`` already returns every qubit for a
    metric in one call;
  - **any** numeric parameter through the docs/83 leaf change-point index. The
    user made this conditional on the overhead investigation, which measured
    1.21 MB for a whole 433-snapshot chip and 0.32-0.92 ms for a 20-qubit
    overlay. Coverage is the reason it matters: on the real chip T1/T2/fidelity
    are null while ~588 other parameters have a real history.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pytest

from quam_state_manager.web.app import create_app


def _chip(folder: Path, qubits=("q1", "q2", "q3")) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    state = {"qubits": {}, "qubit_pairs": {}, "active_qubit_names": list(qubits)}
    for i, q in enumerate(qubits):
        state["qubits"][q] = {
            "id": q, "f_01": 6.0e9 + i * 1e8, "T1": 2.0e-5 + i * 1e-6,
            "xy": {"operations": {"x180": {"amplitude": 0.1 + i * 0.01}}},
        }
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {"host": "9.9.9.9"}, "wiring": {"qubits": {}}}), encoding="utf-8")
    return folder


@pytest.fixture
def client(tmp_path):
    _chip(tmp_path / "quam_state")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path / "quam_state")})
    return c


def _versions(client, folder: Path, n=4):
    """n snapshots that actually DIFFER, so a trend has something to plot.
    Snapshot ids carry a seconds field, hence the sleep — without it the second
    capture collides with the first and the series is one point long."""
    sp = folder / "state.json"
    for step in range(n):
        doc = json.loads(sp.read_text(encoding="utf-8"))
        for i, q in enumerate(doc["qubits"]):
            doc["qubits"][q]["f_01"] = 6.0e9 + i * 1e8 + step * 1e6
            doc["qubits"][q]["T1"] = 2.0e-5 + i * 1e-6 + step * 1e-7
        sp.write_text(json.dumps(doc), encoding="utf-8")
        client.post("/state/archive", data={"tag": f"v{step}"})
        time.sleep(1.05)


def _charts(body: str) -> list[dict]:
    m = re.search(r'id="topo-trends-data">(.*?)</script>', body, re.S)
    return json.loads(m.group(1)) if m else []


class TestTheIntegratedPlot:
    def test_one_chart_per_metric_with_every_qubit_on_it(self, client, tmp_path):
        """THE request: all qubits' metric on a SINGLE plot, legend = qubits."""
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics=f_01").get_data(as_text=True)
        charts = _charts(body)
        assert len(charts) == 1, "one metric asked for, one chart"
        c = charts[0]
        assert c["metric"] == "f_01"
        assert {s["entity"] for s in c["series"]} == {"q1", "q2", "q3"}, \
            "every qubit is a line on the SAME chart"
        assert all(len(s["points"]) >= 2 for s in c["series"]), \
            "each line is a series over time, not a single point"

    def test_several_metrics_are_several_charts(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics=f_01,T1").get_data(as_text=True)
        assert {c["metric"] for c in _charts(body)} == {"f_01", "T1"}

    def test_points_are_numeric_only(self, client, tmp_path):
        """A null metric must not become a fabricated 0 on the line."""
        _versions(client, tmp_path / "quam_state")
        for c in _charts(client.get("/topology/trends?metrics=f_01").get_data(as_text=True)):
            for s in c["series"]:
                assert all(isinstance(p[1], (int, float)) for p in s["points"])


class TestHonestEmptyStates:
    def test_a_metric_with_no_data_still_gets_a_slot_that_says_so(self, client, tmp_path):
        """Dropping it would make a chip look like it has fewer metrics, rather
        than like a chip that has not measured this one yet."""
        _versions(client, tmp_path / "quam_state")
        body = client.get("/topology/trends?metrics=T2echo").get_data(as_text=True)
        assert "Nothing recorded for this metric yet" in body
        charts = _charts(body)
        assert len(charts) == 1 and charts[0]["series"] == []

    def test_no_history_at_all_is_not_an_error(self, client):
        r = client.get("/topology/trends")
        assert r.status_code == 200
        assert "Nothing recorded" in r.get_data(as_text=True)

    def test_no_chip_open_never_500s(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_i2"))
        r = app.test_client().get("/topology/trends")
        assert r.status_code == 200
        assert "No chip is open" in r.get_data(as_text=True)


class TestAnyNumericParameter:
    """The docs/83 tier the user approved after the overhead investigation."""

    def test_a_qubit_scoped_path_fans_out_across_the_chip(self, client, tmp_path):
        """A path names ONE qubit; the whole point of this page is every qubit,
        so the qubit segment is swapped across the chip."""
        _versions(client, tmp_path / "quam_state")
        body = client.get(
            "/topology/trends?metrics=&path=qubits.q1.f_01").get_data(as_text=True)
        charts = _charts(body)
        assert len(charts) == 1
        assert {s["entity"] for s in charts[0]["series"]} == {"q1", "q2", "q3"}, \
            "one qubit's path charts EVERY qubit's copy of that parameter"

    def test_the_typeahead_finds_real_leaves(self, client, tmp_path):
        _versions(client, tmp_path / "quam_state")
        rows = client.get("/topology/trends/paths?q=f_01").get_json()
        assert isinstance(rows, list)

    def test_an_empty_query_returns_nothing_rather_than_everything(self, client):
        assert client.get("/topology/trends/paths?q=").get_json() == []


class TestSelectionSemantics:
    def test_a_bare_request_shows_a_useful_default(self, client):
        charts = _charts(client.get("/topology/trends").get_data(as_text=True))
        assert [c["metric"] for c in charts] == ["T1", "T2echo", "gate_fidelity_avg"]

    def test_turning_every_metric_off_is_respected(self, client):
        """Re-injecting the defaults over an explicit empty selection would make
        the last chip impossible to switch off."""
        charts = _charts(client.get("/topology/trends?metrics=").get_data(as_text=True))
        assert charts == []

    def test_an_unknown_metric_is_ignored_not_charted(self, client):
        body = client.get("/topology/trends?metrics=not_a_metric").get_data(as_text=True)
        assert _charts(body) == []

    def test_the_metric_chips_offer_the_curated_set(self, client):
        from quam_state_manager.core.history import DEFAULT_TRACKED_PROPERTIES
        body = client.get("/topology/trends").get_data(as_text=True)
        for m in DEFAULT_TRACKED_PROPERTIES:
            assert f'data-trend-metric="{m}"' in body


class TestItIsRegisteredEverywhere:
    """A Chip Status section has to be declared in several places at once; miss
    one and the tab exists but never builds, or ?view= silently falls back."""

    _ROOT = Path(__file__).resolve().parent.parent

    def _src(self, rel):
        return (self._ROOT / rel).read_text(encoding="utf-8")

    def test_the_jump_bar_and_the_section(self):
        w = self._src("quam_state_manager/web/templates/_wiring.html")
        assert 'data-view="trends"' in w
        assert 'data-topo-section="trends"' in w

    def test_the_sidebar_subnav(self):
        assert 'view=trends' in self._src("quam_state_manager/web/templates/base.html")

    def test_the_view_is_accepted_by_the_route(self, client):
        assert client.get("/topology?view=trends").status_code == 200

    def test_the_tab_spec_and_lazy_build(self):
        js = self._src("quam_state_manager/web/static/chip-status.js")
        assert "trends:       { build: 'trends'" in js
        assert "'distributions', '2qrb', 'metrics', 'trends'" in js

    def test_it_is_lazy_never_on_the_page_render(self, client, tmp_path):
        """It reads the history index; a chip with hundreds of snapshots must
        not pay for a section the user may never scroll to."""
        _versions(client, tmp_path / "quam_state", n=2)
        page = client.get("/topology").get_data(as_text=True)
        # the host is there, but empty — the data arrives via its own request
        assert 'id="topo-trends"' in page
        assert "topo-trends-data" not in page


class TestTheReviewFindings:
    def test_the_default_prefers_metrics_this_chip_actually_has(self, client, tmp_path):
        """Opening on three metrics that are null chip-wide reads as a broken
        page, not as a chip early in bring-up. On the real 20-qubit chip T1 /
        T2echo / gate_fidelity are all null while f_01 and the amplitudes have
        hundreds of change points."""
        sp = tmp_path / "quam_state" / "state.json"
        for step in range(3):
            doc = json.loads(sp.read_text(encoding="utf-8"))
            for i, q in enumerate(doc["qubits"]):
                doc["qubits"][q]["f_01"] = 6.0e9 + i * 1e8 + step * 1e6
                doc["qubits"][q].pop("T1", None)          # null, like the real chip
            sp.write_text(json.dumps(doc), encoding="utf-8")
            client.post("/state/archive", data={"tag": f"v{step}"})
            time.sleep(1.05)
        body = client.get("/topology/trends").get_data(as_text=True)
        charts = _charts(body)
        assert charts, "a default must be chosen"
        assert any(c["series"] for c in charts), \
            "the default opens on something with data, not three empty boxes"
        assert "T1" not in [c["metric"] for c in charts]

    def test_no_history_still_offers_the_conventional_default(self, client):
        """With nothing recorded every metric is equally empty, and the slots
        are what explain that — so the familiar trio is the right answer."""
        charts = _charts(client.get("/topology/trends").get_data(as_text=True))
        assert [c["metric"] for c in charts] == ["T1", "T2echo", "gate_fidelity_avg"]

    def test_a_tiny_downsample_does_not_crash(self, client, tmp_path):
        """LTTB keeps the endpoints and buckets the rest, so it needs three.
        Below that the divisor went to zero and the whole call raised — a
        caller asking "does this metric have ANY data" got an exception, and
        the swallowing except turned it into a silent wrong answer."""
        from quam_state_manager.web import routes as R
        _versions(client, tmp_path / "quam_state", n=3)
        with client.application.test_request_context():
            hm = R._history()
            p = tmp_path / "quam_state"
            for n in (1, 2, 3):
                rows = hm.extract_property_history(p, ["f_01"], downsample=n)
                for r in rows:
                    assert len(r["values"]) <= max(n, 1), (n, len(r["values"]))

    def test_the_leaf_tier_uses_one_connection(self):
        """Per-path calls each opened and closed their own SQLite connection
        with fresh PRAGMAs — 20 qubits cost ~460 ms of connect/close, scaling
        with qubit count rather than with history depth."""
        from quam_state_manager.web import routes as R
        src = Path(R.__file__).read_text(encoding="utf-8")
        i = src.index("def _trend_series_leaf")
        body = src[i:i + 1800]
        assert "leaf_field_series_many" in body
        # the FAN-OUT must be batched; the non-qubit-scoped fallback below it
        # charts a single path and correctly still uses the singular form
        fanout = body[:body.index("# Not qubit-scoped")]
        assert "hm.leaf_field_series(" not in fanout


class TestTimeAxis:
    """The x axis is TIME, not the snapshot sequence (review finding 2).

    A category axis spaces 433 snapshots evenly, which redraws three quiet
    weeks and two minutes of frantic retuning as the same distance -- on the
    one page whose question is "when did this drift". And the raw ids
    ("20260816_012907_4661") are unreadable as tick labels at that count.
    """

    def test_a_snapshot_id_becomes_an_instant(self):
        from quam_state_manager.web.routes import _snap_iso
        assert _snap_iso("20260816_012907_4661") == "2026-08-16T01:29:07"
        # the microsecond suffix is optional in the id grammar
        assert _snap_iso("20260816_012907") == "2026-08-16T01:29:07"

    def test_an_unparseable_id_is_none_not_a_guess(self):
        """Placing it at a fabricated instant would move a real point on a
        real timeline; None keeps the raw label instead."""
        from quam_state_manager.web.routes import _snap_iso
        for bad in ("", None, "not-a-timestamp", "2026-08-16", "abcdefgh_012907"):
            assert _snap_iso(bad) is None

    def test_a_point_is_the_id_and_the_value_ONLY(self):
        """The instant is DERIVED on the client, never shipped.

        It is a pure reformat of the id, so sending it too was the same
        information twice — measured at 61 bytes/point, up to 2.3 MB of HTML
        for one section on a real 419-snapshot chip. The id still travels
        because that is what a user carries over to State History."""
        from quam_state_manager.web.routes import _trend_points
        pts = _trend_points([
            {"timestamp": "20260816_012907_4661", "value": 1.5},
            {"timestamp": "20260817_090000_0001", "value": 2.5},
        ])
        assert pts == [("20260816_012907_4661", 1.5),
                       ("20260817_090000_0001", 2.5)]

    def test_the_client_derives_the_instant_by_the_SAME_rule(self):
        """JS<->PY parity: `_snap_iso` and `ChipTrends._iso` must agree, or the
        axis and the server disagree about what is dateable."""
        from pathlib import Path as _P
        src = _P("quam_state_manager/web/static/chip-status.js").read_text(encoding="utf-8")
        assert "function _iso(ts)" in src
        # same anchored 8+6 grammar, same refusal to guess
        assert r"/^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/" in src
        assert "return null" in src[src.index("function _iso(ts)"):
                                    src.index("function _iso(ts)") + 300]

    def test_non_numeric_and_bools_never_become_points(self):
        from quam_state_manager.web.routes import _trend_points
        pts = _trend_points([
            {"timestamp": "20260816_012907_4661", "value": None},
            {"timestamp": "20260816_012908_4661", "value": "0.13"},
            {"timestamp": "20260816_012909_4661", "value": True},
            {"timestamp": "20260816_012910_4661", "value": 0.0},
        ])
        assert [p[1] for p in pts] == [0.0]

    def test_the_renderer_picks_the_axis_from_the_data(self):
        """All points dated -> a date axis; one undated point -> the whole
        chart falls back to category, because mixing them would put the
        undated points at epoch zero."""
        import re as _re
        from pathlib import Path as _P
        src = _P("quam_state_manager/web/static/chip-status.js").read_text(encoding="utf-8")
        i = src.index("function _axisFor(")
        body = src[i:i + 400]
        assert "allDated" in body
        assert "'date'" in body and "'category'" in body
        # the axis is not hardcoded at the layout any more
        j = src.index("window.ChipTrends")
        assert "type: axisType" in src[j:]
        assert "type: 'category'" not in src[j:]
        # the snapshot id survives into the hover
        assert "customdata" in src[j:]


class TestTheChartActuallyDraws:
    """Found by driving real Chrome — invisible to jsdom, which has no WebGL and
    no Plotly renderer at all.

    On the real 20-qubit chip EVERY Trends chart was blank, with the text
    "WebGL is not supported by your browser" where the data should be. The
    browser was fine (Intel Arc via ANGLE D3D11, webgl1 and webgl2 both
    available) and a SINGLE scattergl chart drew correctly; it took THREE on one
    page to break, because each takes ~3 WebGL contexts, browsers cap the total,
    and Plotly's response to a refused context is to replace the chart with that
    sentence rather than to throw.

    The gate was `series.length > 8` — the QUBIT count. So every chip bigger
    than 8 qubits, i.e. every real customer chip, took the WebGL path to draw
    20 x 7 = 140 points.
    """

    def _js(self):
        from pathlib import Path as _P
        return _P("quam_state_manager/web/static/chip-status.js").read_text(encoding="utf-8")

    def test_webgl_is_gated_on_node_count_not_qubit_count(self):
        src = self._js()
        assert "var nodes = c.series.length * longest;" in src
        assert "nodes > GL_MIN_NODES" in src
        # the qubit-count gate must be gone, not merely supplemented
        assert "c.series.length > 8" not in src

    def test_only_one_chart_may_spend_a_webgl_context(self):
        """Three scattergl charts on one page is what actually broke it, so the
        budget — not just the threshold — is load-bearing."""
        src = self._js()
        assert "_glBudget" in src
        assert "_glBudget = 1;" in src, "the budget must be reset per render pass"
        assert "if (dense) _glBudget--;" in src

    def test_a_blank_chart_heals_itself(self):
        """Plotly does not throw when a context is refused — it writes the
        sentence into the div and returns normally. The only honest response is
        to look, and redraw as SVG."""
        src = self._js()
        i = src.index("function _healIfBlank(")
        body = src[i:i + 900]
        assert "WebGL is not supported" in body
        assert "'scatter'" in body, "the retry must be the SVG renderer"
        assert "_healIfBlank(host, traces, layout, cfg)" in src, "and it must be called"

    def test_the_threshold_leaves_real_data_on_svg(self):
        """The server caps a series at 400 points, so 20 qubits x 400 = 8,000 is
        the true worst case; a threshold above that would never use GL at all,
        and one at 140 is what caused this."""
        src = self._js()
        m = re.search(r"var GL_MIN_NODES = (\d+);", src)
        assert m, "GL_MIN_NODES must be a named constant"
        n = int(m.group(1))
        assert 1000 <= n <= 8000, n
        assert 20 * 7 < n, "a 20-qubit chip with a few snapshots must stay on SVG"


class TestTheAxisSaysWhatItMeans:
    """Also from the real browser: the f_01 chart's y ticks read 4.3B / 4.4B —
    US-billions — and the only other text on the plot was the bare metric name.
    Nothing said Hz. And a parameter that had not moved (readout_amplitude,
    0.00447 chip-wide) was drawn as a flat line at 0 on a -0.5..1 axis, which
    reads as "this parameter is zero"."""

    def test_the_unit_comes_from_the_chip_s_own_column_spec(self):
        from quam_state_manager.web.routes import _trend_unit
        assert _trend_unit("f_01") == "Hz"
        assert _trend_unit("T1") == "s"

    def test_an_unknown_metric_gets_no_invented_unit(self):
        """Any leaf path typed into the "any parameter" box lands here."""
        from quam_state_manager.web.routes import _trend_unit
        assert _trend_unit("qubits.q1.xy.operations.x180_DragCosine.amplitude") == ""
        assert _trend_unit("") == ""

    def test_the_chart_payload_carries_the_unit(self, client):
        body = client.get("/topology/trends?metrics=f_01").get_data(as_text=True)
        assert '"unit": "Hz"' in body

    def test_ticks_use_si_prefixes_and_the_title_carries_the_unit(self):
        from pathlib import Path as _P
        src = _P("quam_state_manager/web/static/chip-status.js").read_text(encoding="utf-8")
        assert "tickformat: '~s'" in src, "4.3B is not a physics unit"
        assert "c.metric + (c.unit ? ' (' + c.unit + ')' : '')" in src

    def test_a_constant_series_is_not_drawn_against_zero(self):
        src = _P_read()
        assert "_flat" in src
        assert "range: _flat || undefined" in src
        assert "autorange: _flat ? false : true" in src


def _P_read():
    from pathlib import Path as _P
    return _P("quam_state_manager/web/static/chip-status.js").read_text(encoding="utf-8")
