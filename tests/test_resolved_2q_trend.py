"""Chip Status > Trends: one resolved 2Q gate fidelity series per pair.

The raw per-key families (``InterleavedRB``, a lab alias ``IRB``) stay charted
as recorded; the resolved series is what the Overview tile and the topology
edge show (``query.pair_gate_fidelity`` per gate macro, the best physical
macro per pair), recomputed at every recorded change of its inputs.
"""
import re

import pytest

from quam_state_manager.web import routes
from tests.test_trends_all_entities import _hot_cold_chip, _charts, PAIRS

RES = routes._TREND_2Q_RESOLVED
IRB_FAM = "qubit_pairs.*.macros.*.fidelity.InterleavedRB"


def _get(c, q):
    return c.get("/topology/trends?" + q).get_data(as_text=True)


def _chart(body, metric):
    return next(c for c in _charts(body) if c["metric"] == metric)


def _edge_values(folder):
    """The number the Overview / 2Q Fid. tile reads per pair -- the topology
    edge's per-gate value (cz_fidelity) -- on the chip's newest saved state
    (the fixture archives the folder's state.json; the open working copy is
    the first one)."""
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.core.query import QueryEngine
    edges = QueryEngine(QuamStore(folder)).get_topology()["edges"]
    return {e["pair_id"]: e.get("cz_fidelity") for e in edges}


@pytest.fixture
def both_keys(tmp_path):
    # two per-gate keys that disagree, no recorded time or run id: the
    # resolver's key order picks InterleavedRB (never the larger IRB)
    return _hot_cold_chip(tmp_path, "both", {"macros.cz.fidelity.InterleavedRB": .90},
                          {"macros.cz.fidelity.IRB": .95}, steps=2)


@pytest.fixture
def one_key(tmp_path):
    return _hot_cold_chip(tmp_path, "one", {"macros.cz.fidelity.IRB": .93}, {}, steps=2)


class TestResolvedSeries:
    def test_default_shows_the_resolved_series_and_keeps_the_raw_badge(self, both_keys):
        body = _get(both_keys, "")
        assert 'data-trend-path="' + RES + '"' in body
        assert re.search('data-trend-path="' + re.escape(RES) + '"[^>]*aria-pressed="true"', body)
        assert 'data-trend-path="' + IRB_FAM + '"' in body
        ch = _chart(body, routes._TREND_2Q_RESOLVED_FAM)
        assert ch["label"] == "2Q gate fidelity (resolved)"
        assert {s["entity"] for s in ch["series"]} == set(PAIRS)

    def test_the_raw_family_is_still_charted_as_recorded(self, both_keys):
        body = _get(both_keys, "metrics=&paths=" + IRB_FAM)
        ch = _chart(body, "macros.*.fidelity.InterleavedRB")
        assert ch["series"]

    def test_each_point_names_its_source_key(self, both_keys):
        ch = _chart(_get(both_keys, "metrics=&paths=" + RES), routes._TREND_2Q_RESOLVED_FAM)
        for s in ch["series"]:
            for k, v in s["points"]:
                assert v is not None
                assert s["attr"][k]["sub"].startswith("source cz.InterleavedRB"), s["attr"][k]
            assert all(src.startswith("source cz.InterleavedRB") for src in s["sources"])

    def test_the_latest_point_equals_the_overview_value(self, both_keys, tmp_path):
        ch = _chart(_get(both_keys, "metrics=&paths=" + RES), routes._TREND_2Q_RESOLVED_FAM)
        edges = _edge_values(tmp_path / "both")
        for s in ch["series"]:
            assert s["points"][-1][1] == pytest.approx(edges[s["entity"]], abs=1e-12)
            # the picked key, not the larger alias
            assert s["points"][-1][1] < 0.93

    def test_a_chip_with_one_key_resolves_to_that_keys_series(self, one_key):
        res = _chart(_get(one_key, "metrics=&paths=" + RES), routes._TREND_2Q_RESOLVED_FAM)
        raw = _chart(_get(one_key, "metrics=&paths=qubit_pairs.*.macros.cz.fidelity.IRB"),
                     "macros.cz.fidelity.IRB")
        by_raw = {s["entity"].split(" \u00b7 ")[0]: s["points"] for s in raw["series"]}
        for s in res["series"]:
            assert [tuple(p) for p in s["points"]] == [tuple(p) for p in by_raw[s["entity"]]]


class TestResolverAtAPoint:
    def test_newest_recorded_time_decides(self):
        v = routes._resolved_2q_at({"cz": {
            "InterleavedRB": .90, "InterleavedRB_updated_at": "2026-01-01T00:00:00",
            "IRB": .95, "IRB_updated_at": "2026-02-01T00:00:00"}})
        assert v[:2] == (.95, "cz.IRB")

    def test_best_physical_macro_wins_across_macros(self):
        v = routes._resolved_2q_at({"a": {"InterleavedRB": 1.5}, "b": {"InterleavedRB": .97},
                                    "c": {"IRB": .96}})
        assert v[:2] == (.97, "b.InterleavedRB")

    def test_axis_keys_sort_by_event_number(self):
        keys = ["20260101T000000Z_e10", "20260101T000000Z_e9"]
        assert sorted(keys, key=routes._trend_axis_order)[0].endswith("_e9")
