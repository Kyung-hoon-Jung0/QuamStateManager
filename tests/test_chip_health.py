"""Tests for core/chip_health — calibration-recency parsing, spec verdicts, and
chip-wide aggregates that feed the Chip Status health dashboard."""

from __future__ import annotations

from datetime import datetime, timezone

from quam_state_manager.core import chip_health as ch


# --- timestamp parsing ------------------------------------------------------


def test_parse_gmt_plus_offset():
    dt = ch.parse_quam_timestamp("2026-05-16 03:02:28 GMT+2")
    # 03:02:28 at GMT+2 == 01:02:28 UTC
    assert dt == datetime(2026, 5, 16, 1, 2, 28, tzinfo=timezone.utc)


def test_parse_gmt_minus_offset():
    dt = ch.parse_quam_timestamp("2026-02-19 11:41:03 GMT-5")
    # 11:41:03 at GMT-5 == 16:41:03 UTC
    assert dt == datetime(2026, 2, 19, 16, 41, 3, tzinfo=timezone.utc)


def test_parse_offset_with_minutes():
    dt = ch.parse_quam_timestamp("2026-01-01 00:00:00 GMT+5:30")
    assert dt == datetime(2025, 12, 31, 18, 30, 0, tzinfo=timezone.utc)


def test_parse_no_offset_is_utc():
    dt = ch.parse_quam_timestamp("2026-05-16 03:02:28")
    assert dt == datetime(2026, 5, 16, 3, 2, 28, tzinfo=timezone.utc)


def test_parse_iso_fallback():
    dt = ch.parse_quam_timestamp("2026-05-16T03:02:28+02:00")
    assert dt == datetime(2026, 5, 16, 1, 2, 28, tzinfo=timezone.utc)


def test_parse_garbage_is_none():
    for bad in [None, "", "not a date", 12345, "2026-13-99 99:99:99"]:
        assert ch.parse_quam_timestamp(bad) is None


def test_epoch_ms_roundtrip():
    e = ch.epoch_ms("2026-05-16 03:02:28 GMT+2")
    assert e == int(datetime(2026, 5, 16, 1, 2, 28, tzinfo=timezone.utc).timestamp() * 1000)
    assert ch.epoch_ms("garbage") is None


def test_newest_epoch_ms_walks_subtree():
    node = {
        "gate_fidelity": {"averaged": 0.99, "averaged_updated_at": "2026-05-16 03:00:00 GMT+0"},
        "resonator": {"operations": {"readout": {"updated_at": "2026-05-13 14:00:00 GMT+0"}}},
        "T1": 2.4e-5,  # no timestamp
    }
    newest = ch.newest_epoch_ms(node)
    assert newest == ch.epoch_ms("2026-05-16 03:00:00 GMT+0")  # the fresher of the two


def test_newest_epoch_ms_none_when_no_timestamps():
    assert ch.newest_epoch_ms({"T1": 1.0, "nested": {"x": [1, 2, 3]}}) is None


# --- verdict ----------------------------------------------------------------


def test_verdict_higher_is_better():
    th = {"warn": 0.99, "fail": 0.95, "direction": "higher"}
    assert ch.verdict(0.999, th) == "pass"
    assert ch.verdict(0.99, th) == "pass"     # boundary: >= warn
    assert ch.verdict(0.97, th) == "warn"
    assert ch.verdict(0.95, th) == "warn"     # boundary: >= fail
    assert ch.verdict(0.90, th) == "fail"


def test_verdict_lower_is_better():
    th = {"warn": 1.0, "fail": 2.0, "direction": "lower"}
    assert ch.verdict(0.5, th) == "pass"
    assert ch.verdict(1.5, th) == "warn"
    assert ch.verdict(3.0, th) == "fail"


def test_verdict_handles_none_and_nonnumeric():
    th = {"warn": 1, "fail": 0}
    assert ch.verdict(None, th) is None
    assert ch.verdict("0.5", th) is None
    assert ch.verdict(True, th) is None        # bool is not a metric
    assert ch.verdict(0.5, None) is None


def test_default_thresholds_cover_headline_metrics():
    for k in ("T1", "T2ramsey", "T2echo", "assignment_fidelity",
              "gate_fidelity_avg", "cz_fidelity"):
        assert k in ch.DEFAULT_THRESHOLDS
        assert ch.DEFAULT_THRESHOLDS[k]["warn"] > ch.DEFAULT_THRESHOLDS[k]["fail"]


# --- metric glossary (METRIC_META — the single label/direction source) ------


def test_metric_meta_covers_every_topology_key():
    # METRIC_META must cover the full key universe the client renders, so no
    # tooltip/arrow render site ever falls back for a real metric.
    from quam_state_manager.core import query
    for k in list(query._NODE_METRIC_KEYS) + list(query._EDGE_METRIC_KEYS):
        m = ch.metric_meta(k)
        assert m["label"] and m["abbr"]
        assert m["direction"] in ("higher", "lower", "neutral")
        assert "blurb" in m


def test_metric_meta_fallback_is_neutral_for_unknown_key():
    m = ch.metric_meta("totally_made_up")
    assert m == {"label": "totally_made_up", "abbr": "totally_made_up",
                 "direction": "neutral", "blurb": ""}


def test_default_thresholds_label_and_direction_come_from_meta():
    # The arrow (META.direction) and the verdict colour (threshold.direction) must
    # share ONE definition — assert no drift between the two.
    for k, th in ch.DEFAULT_THRESHOLDS.items():
        assert th["direction"] == ch.metric_meta(k)["direction"]
        assert th["label"] == ch.metric_meta(k)["label"]


def test_threshold_metrics_are_higher_is_better():
    for k in ch.DEFAULT_THRESHOLDS:
        assert ch.metric_meta(k)["direction"] == "higher"


# --- aggregate --------------------------------------------------------------


def test_aggregate_basic_stats():
    rows = [{"T1": 10.0}, {"T1": 20.0}, {"T1": 30.0}]
    agg = ch.aggregate(rows)
    assert agg["T1"] == {"min": 10.0, "max": 30.0, "avg": 20.0, "median": 20.0, "count": 3}


def test_aggregate_even_count_median():
    rows = [{"x": 1.0}, {"x": 2.0}, {"x": 3.0}, {"x": 4.0}]
    assert ch.aggregate(rows)["x"]["median"] == 2.5


def test_aggregate_skips_bools_strings_none_and_skip_keys():
    rows = [
        {"id": "qA1", "f": 0.9, "flag": True, "miss": None},
        {"id": "qA2", "f": 0.8, "flag": False, "miss": 5.0},
    ]
    agg = ch.aggregate(rows, skip={"id"})
    assert "id" not in agg and "flag" not in agg
    assert agg["f"]["count"] == 2
    # 'miss' only had one numeric value (None skipped)
    assert agg["miss"]["count"] == 1


# --- physicality (trust floor) ---------------------------------------------


def test_physicality_fidelity_bounds():
    assert ch.physicality("gate_fidelity_avg", 0.999) is True
    assert ch.physicality("gate_fidelity_avg", 1.0) is True          # perfect = boundary OK
    assert ch.physicality("cz_fidelity", 1.5345) is False            # the observed 153.45%
    assert ch.physicality("assignment_fidelity", 0.0) is False       # 0 fidelity = degenerate
    assert ch.physicality("ro_fidelity_g", -0.1) is False


def test_physicality_coherence_must_be_positive_finite():
    assert ch.physicality("T1", 2.4e-5) is True
    assert ch.physicality("T2echo", -4.7e-4) is False                # the observed -473µs
    assert ch.physicality("T1", float("nan")) is False
    assert ch.physicality("T2ramsey", float("inf")) is False


def test_physicality_unlisted_metric_and_none():
    # anharmonicity is legitimately negative → not constrained
    assert ch.physicality("anharmonicity", -2.1e8) is True
    assert ch.physicality("f_01", 5.07e9) is True
    assert ch.physicality("f_01", float("nan")) is False             # non-finite always fails
    assert ch.physicality("T1", None) is True                        # missing ≠ unphysical


# --- make_record ------------------------------------------------------------


def test_make_record_physical_value():
    r = ch.make_record("gate_fidelity_avg", 0.999, updated_at=123)
    assert r["value"] == 0.999 and r["raw"] == 0.999
    assert r["physical"] is True and r["unresolved"] is False
    assert r["verdict"] == "pass" and r["updated_at"] == 123
    # placeholder fields present from day one
    assert r["n"] is None and r["sigma"] is None and r["provenance"] is None


def test_make_record_quarantines_unphysical():
    r = ch.make_record("cz_fidelity", 1.5345)
    assert r["value"] is None          # excluded from aggregates/colour
    assert r["raw"] == 1.5345          # kept for the "likely failed fit" tooltip
    assert r["physical"] is False
    assert r["verdict"] is None        # a failed fit can NEVER read pass-green


def test_make_record_unresolved_pointer():
    r = ch.make_record("f_01", None, unresolved=True)
    assert r["value"] is None and r["raw"] is None
    assert r["unresolved"] is True and r["verdict"] is None


def test_make_record_respects_custom_thresholds():
    th = {"gate_fidelity_avg": {"warn": 0.999, "fail": 0.99, "direction": "higher"}}
    assert ch.make_record("gate_fidelity_avg", 0.995, thresholds=th)["verdict"] == "warn"


# --- aggregate_records (physical-gated, honest counts) ----------------------


def _node(metrics):
    return {"metrics": {k: ch.make_record(k, v) for k, v in metrics.items()}}


def test_aggregate_records_excludes_unphysical_from_stats():
    rows = [
        _node({"T1": 2.0e-5}),
        _node({"T1": 3.0e-5}),
        _node({"T1": -4.7e-4}),          # unphysical → quarantined
        {"metrics": {}},                  # T1 missing entirely
    ]
    agg = ch.aggregate_records(rows, ["T1"])["T1"]
    assert agg["measured"] == 2 and agg["bad"] == 1 and agg["missing"] == 1
    assert agg["total"] == 4 and agg["count"] == 2
    assert agg["min"] == 2.0e-5 and agg["max"] == 3.0e-5   # the -473µs does NOT stretch the domain
    assert agg["avg"] == 2.5e-5


def test_aggregate_records_counts_unresolved_as_missing():
    rows = [
        _node({"f_01": 5.0e9}),
        {"metrics": {"f_01": ch.make_record("f_01", None, unresolved=True)}},
    ]
    agg = ch.aggregate_records(rows, ["f_01"])["f_01"]
    assert agg["measured"] == 1 and agg["unresolved"] == 1 and agg["missing"] == 1
    assert agg["bad"] == 0
