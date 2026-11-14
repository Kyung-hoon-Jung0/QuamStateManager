"""Chip Status > Trends: the Overview's validity rule on every point.

* F1/F2: a value outside its physical range (``chip_health.physicality``, a
  pair fidelity family by the fidelity bound) leaves the line and the axis
  and is listed in ``excluded``; a chart with all-valid points is unchanged.
* F1: a run tagged ``invalid`` (the Datasets tag store) leaves its values out.
* F3: a qubit chip pressed past the cap is named, never dropped silently.
"""
import re

from quam_state_manager.core.dataset import INVALID_TAG
from quam_state_manager.web import routes
from tests.test_trends_all_entities import _hot_cold_chip, _charts

IRB = "qubit_pairs.*.macros.cz.fidelity.IRB"


def _chart(body, metric):
    return next(c for c in _charts(body) if c["metric"] == metric)


class TestPhysicalRule:
    def test_the_rule_is_the_overviews(self):
        assert not routes._trend_value_physical("T2echo", "qubit", -8.1e-2)
        assert not routes._trend_value_physical("gate_fidelity_avg", "qubit", 0.0)
        assert routes._trend_value_physical("T2echo", "qubit", 3e-5)
        assert not routes._trend_value_physical("macros.*.fidelity.InterleavedRB", "pair", 2.167)
        assert routes._trend_value_physical("macros.*.fidelity.InterleavedRB", "pair", 0.95)
        assert not routes._trend_value_physical(routes._TREND_2Q_RESOLVED_FAM, "pair", 1.05)
        # not a fidelity: any finite number is physical
        assert routes._trend_value_physical("macros.cz.phase_shift_control", "pair", 3.2)
        assert routes._trend_value_physical("f_01", "qubit", -1.0)

    def test_the_edges(self):
        # exactly 100 % is a valid fidelity, exactly 0 s is not a valid T2
        assert routes._trend_value_physical("gate_fidelity_avg", "qubit", 1.0)
        assert not routes._trend_value_physical("gate_fidelity_avg", "qubit", 1.0 + 1e-5)
        assert routes._trend_value_physical("macros.*.fidelity.InterleavedRB", "pair", 1.0)
        assert not routes._trend_value_physical("T2echo", "qubit", 0.0)
        assert not routes._trend_value_physical("T2echo", "qubit", float("nan"))

    def test_trends_and_overview_agree_value_by_value(self):
        from quam_state_manager.core import chip_health
        keys = sorted(set(chip_health._FIDELITY_KEYS) | set(chip_health._POSITIVE_KEYS))
        vals = [-1.0, 0.0, 1e-9, 0.5, 1.0, 1.0000001, 2.33, float("inf")]
        for k in keys:
            for v in vals:
                assert routes._trend_value_physical(k, "qubit", v) == chip_health.physicality(k, v), (k, v)

    def test_an_excluded_point_leaves_a_gap_and_is_listed(self):
        ser = {"points": [("a", 0.95), ("b", 2.1), ("c", 0.96), ("d", 0.96)],
               "held": {"d": "c"}}
        n = routes._trend_exclude(ser, "macros.cz.fidelity.IRB", "pair", frozenset())
        assert n == 1
        assert ser["points"] == [("a", 0.95), ("b", None), ("c", 0.96), ("d", 0.96)]
        assert ser["excluded"] == [["b", 2.1, "unphysical"]]

    def test_a_held_endpoint_of_an_excluded_value_goes_with_it(self):
        ser = {"points": [("a", 0.95), ("b", 1.3), ("c", 1.3)], "held": {"c": "b"}}
        routes._trend_exclude(ser, "macros.cz.fidelity.IRB", "pair", frozenset())
        assert ser["points"][-1] == ("c", None)
        assert len(ser["excluded"]) == 1

    def test_an_all_valid_series_is_untouched(self):
        ser = {"points": [("a", 0.95), ("b", 0.97)]}
        before = dict(ser)
        assert routes._trend_exclude(ser, "macros.cz.fidelity.IRB", "pair", frozenset()) == 0
        assert ser == before and "excluded" not in ser

    def test_an_invalid_runs_point_is_excluded(self):
        ser = {"points": [("a", 0.95), ("b", 0.97)],
               "attr": {"b": {"saved_uid": "k:7"}}}
        routes._trend_exclude(ser, "macros.cz.fidelity.IRB", "pair", frozenset({"k:7"}))
        assert ser["excluded"] == [["b", 0.97, "invalid"]]
        assert ser["points"][1] == ("b", None)

    def test_a_left_out_run_value_outside_the_range_is_excluded(self):
        ser = {"points": [("a", 0.95)], "left_out": [["x", 2.0, 5, "not_kept", "w", .95, 0, None, None]]}
        routes._trend_exclude(ser, "macros.cz.fidelity.IRB", "pair", frozenset())
        assert "left_out" not in ser
        assert ser["excluded"] == [["x", 2.0, "unphysical"]]


class TestRoute:
    def test_an_unphysical_fidelity_is_left_out_of_the_chart(self, tmp_path):
        c = _hot_cold_chip(tmp_path, "bad", {"macros.cz.fidelity.IRB": 1.5}, {}, steps=2)
        body = c.get("/topology/trends?metrics=&paths=" + IRB).get_data(as_text=True)
        ch = _chart(body, "macros.cz.fidelity.IRB")
        assert ch["n_excluded"] > 0
        for s in ch["series"]:
            assert all(v is None for _, v in s["points"]), s["points"]
        assert "outside the physical range" in ch["excluded_note"]
        assert 'id="topo-trends-show-excluded"' in body

    def test_an_all_valid_chart_has_no_toggle_and_no_note(self, tmp_path):
        c = _hot_cold_chip(tmp_path, "good", {"macros.cz.fidelity.IRB": .93}, {}, steps=2)
        body = c.get("/topology/trends?metrics=&paths=" + IRB).get_data(as_text=True)
        ch = _chart(body, "macros.cz.fidelity.IRB")
        assert ch["n_excluded"] == 0 and ch["excluded_note"] == ""
        assert "topo-trends-show-excluded" not in body
        assert all("excluded" not in s for s in ch["series"])

    def test_a_run_tagged_invalid_is_left_out(self, tmp_path, monkeypatch):
        """The tag set is part of the fragment's cache key: tagging a run
        re-renders, and that run's values leave the line."""
        c = _hot_cold_chip(tmp_path, "inv", {"macros.cz.fidelity.IRB": .93}, {}, steps=2)
        url = "/topology/trends?metrics=&paths=" + IRB
        ch = _chart(c.get(url).get_data(as_text=True), "macros.cz.fidelity.IRB")
        assert ch["n_excluded"] == 0
        # the invalid-run rule reads the measured run (never first_uid)
        monkeypatch.setattr(routes, "_trend_point_measured_uid", lambda info: "k:1")
        monkeypatch.setattr(routes, "_invalid_run_uids", lambda: frozenset({"k:1"}))
        ch2 = _chart(c.get(url).get_data(as_text=True), "macros.cz.fidelity.IRB")
        assert ch2["n_excluded"] > 0
        assert all(e[2] == "invalid" for s in ch2["series"] for e in s.get("excluded") or [])
        assert "from a run marked invalid" in ch2["excluded_note"]


class TestMetricCap:
    def test_a_ninth_qubit_metric_is_named(self, tmp_path):
        c = _hot_cold_chip(tmp_path, "cap", {"macros.cz.fidelity.IRB": .93}, {}, steps=1)
        metrics = list(routes.DEFAULT_TRACKED_PROPERTIES)[:9]
        body = c.get("/topology/trends?metrics=" + ",".join(metrics)).get_data(as_text=True)
        assert "data-trends-metric-cap" in body
        ninth = routes._trend_metric_label(metrics[8])
        m = re.search(r'data-trends-metric-cap><p class="ui-callout-text">([^<]*)<', body)
        assert m and ninth in m.group(1).replace("&#39;", "'"), m and m.group(1)
        assert "Deselect one" in m.group(1)

    def test_eight_metrics_say_nothing(self, tmp_path):
        c = _hot_cold_chip(tmp_path, "cap8", {"macros.cz.fidelity.IRB": .93}, {}, steps=1)
        metrics = list(routes.DEFAULT_TRACKED_PROPERTIES)[:8]
        body = c.get("/topology/trends?metrics=" + ",".join(metrics)).get_data(as_text=True)
        assert "data-trends-metric-cap" not in body


class TestInvalidTagStore:
    def test_runs_with_tag_reads_the_store(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        ds = DatasetStore.__new__(DatasetStore)
        import threading
        ds._tags_lock = threading.Lock()
        ds._tags_data = {"tags": {"3": [INVALID_TAG], "4": ["x"], "9": ["x", INVALID_TAG]}}
        assert ds.runs_with_tag(INVALID_TAG) == [3, 9]


class TestInvalidElsewhere:
    """N4: a tag matches a point by dataset uid (folder key + run id). Tags in
    a folder no drawn point comes from are named, never matched by number."""

    def _charts(self, uid):
        return [{"series": [{"attr": {"k1": {"saved_uid": uid}}}]}]

    def test_tags_in_another_folder_are_named(self):
        note = routes._trend_invalid_elsewhere_note(self._charts("orig:7"),
                                                    frozenset({"copy:7", "copy:9"}))
        assert note.startswith("2 runs tagged")
        assert "do not come from" in note

    def test_tags_in_the_charts_folder_say_nothing(self):
        assert routes._trend_invalid_elsewhere_note(self._charts("orig:7"),
                                                    frozenset({"orig:7", "orig:3"})) == ""

    def test_never_excluded_by_run_number_alone(self):
        ser = {"points": [("a", 0.95)], "attr": {"a": {"saved_uid": "orig:7"}}}
        routes._trend_exclude(ser, "macros.cz.fidelity.IRB", "pair", frozenset({"copy:7"}))
        assert "excluded" not in ser and ser["points"] == [("a", 0.95)]

    def test_the_route_shows_the_note(self, tmp_path, monkeypatch):
        c = _hot_cold_chip(tmp_path, "else", {"macros.cz.fidelity.IRB": .93}, {}, steps=2)
        monkeypatch.setattr(routes, "_trend_point_run_uid", lambda info: "orig:1")
        monkeypatch.setattr(routes, "_invalid_run_uids", lambda: frozenset({"copy:1"}))
        body = c.get("/topology/trends?metrics=&paths=" + IRB).get_data(as_text=True)
        assert "data-trends-invalid-elsewhere" in body
        assert _chart(body, "macros.cz.fidelity.IRB")["n_excluded"] == 0


class TestFirstSeenIsNotMeasured:
    """A value first seen in the run the ledger starts with was not measured
    by that run: tagging that run invalid leaves the start value in."""

    def test_a_first_seen_value_is_not_excluded_by_its_runs_tag(self):
        ser = {"points": [("a", 0.95), ("b", 0.97)],
               "attr": {"a": {"first_uid": "k:1"}, "b": {"uid": "k:2"}}}
        routes._trend_exclude(ser, "macros.cz.fidelity.IRB", "pair", frozenset({"k:1"}))
        assert ser["points"] == [("a", 0.95), ("b", 0.97)] and "excluded" not in ser

    def test_its_writer_or_saver_still_is(self):
        ser = {"points": [("a", 0.95), ("b", 0.97)],
               "attr": {"a": {"first_uid": "k:1", "saved_uid": "k:1"}, "b": {"uid": "k:2"}}}
        routes._trend_exclude(ser, "macros.cz.fidelity.IRB", "pair", frozenset({"k:1", "k:2"}))
        assert ser["excluded"] == [["a", 0.95, "invalid"], ["b", 0.97, "invalid"]]

    def test_the_link_still_names_the_first_run(self):
        assert routes._trend_point_run_uid({"first_uid": "k:1"}) == "k:1"


class _Tbl:
    """A ledger table stand-in: the leaf rows the resolved 2Q line reads."""

    def __init__(self, rows):
        self.rows, self.paths, self.attrs = rows, list(rows), {}

    def leaf_matching_paths(self, pat):
        import fnmatch
        return [p for p in self.rows if fnmatch.fnmatchcase(p, pat)
                and p.count(".") == pat.count(".")]

    def leaf_series_many(self, paths, hold_to_newest=True):
        return {p: self.rows[p] for p in paths if p in self.rows}


class TestResolvedAllUnphysicalIsCounted:
    def test_a_point_where_every_macro_is_unphysical_is_counted(self):
        dp = "qubit_pairs.qA-qB.macros.cz.fidelity.InterleavedRB"
        tbl = _Tbl({dp: [("k1", 0.95), ("k2", 1.4), ("k3", 0.96)]})
        out = routes._trend_series_resolved_2q(None, None, ["qA-qB"], tbl=tbl)
        assert len(out) == 1
        ser = out[0]
        assert [v for _, v in ser["points"]] == [0.95, None, 0.96]
        n = routes._trend_exclude(ser, ser["metric"], "pair", frozenset())
        assert n == 1 and ser["excluded"] == [["k2", 1.4, "unphysical"]]
        assert routes._trend_excluded_note([e[2] for e in ser["excluded"]]).startswith("1 value left out")
