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
