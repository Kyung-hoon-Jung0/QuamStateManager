"""docs/256: one run instant, with evidence quality and independent witnesses."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from quam_state_manager.core import timefmt

UTC = timezone.utc
LOCAL = timezone(timedelta(hours=9))
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
FOLDER = Path("archive/2026-09-30/#7_measurement_195641")


def us(iso):
    delta = datetime.fromisoformat(iso).astimezone(UTC) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


@pytest.mark.parametrize("text, expected", [
    ("2026-09-30T19:56:41+09:00", "2026-09-30T10:56:41+00:00"),
    ("2026-09-30T19:56:41-04:00", "2026-09-30T23:56:41+00:00"),
    ("2026-09-30T19:56:41Z", "2026-09-30T19:56:41+00:00"),
    ("2026-09-30T19:56:41z", "2026-09-30T19:56:41+00:00"),
    ("2026-09-30T19:56:41.123456+09:00", "2026-09-30T10:56:41.123456+00:00"),
    ("2026-09-30T19:56:41.123-04:00", "2026-09-30T23:56:41.123000+00:00"),
    ("2026-09-30T19:56:41.000001Z", "2026-09-30T19:56:41.000001+00:00"),
    ("1969-12-31T23:59:59.999999Z", "1969-12-31T23:59:59.999999+00:00"),
    ("2026-09-30T19:56:41+05:30", "2026-09-30T14:26:41+00:00"),
])
def test_explicit_offset_defines_exact_instant(text, expected):
    assert timefmt.run_instant({"created_at": text}, FOLDER,
                               offset_hint="-08:00", local_tz=LOCAL) == (us(expected), "offset")


def test_created_at_wins_over_different_run_end():
    node = {"created_at": "2026-09-30T19:56:41.123456+09:00",
            "metadata": {"run_end": "2026-09-30T19:56:45.987654+09:00"}}
    assert timefmt.run_instant(node) == (us("2026-09-30T10:56:41.123456+00:00"), "offset")


@pytest.mark.parametrize("created", [None, "broken", "2026-09-30T19:56:41"])
def test_aware_run_end_precedes_naive_created_and_folder(created):
    node = {"created_at": created, "metadata": {"run_end": "2026-09-30T20:00:00.654321-04:00"}}
    assert timefmt.run_instant(node, FOLDER, offset_hint="+09:00", local_tz=LOCAL) == (
        us("2026-10-01T00:00:00.654321+00:00"), "offset")


@pytest.mark.parametrize("hint, expected, quality", [
    ("-04:00", "2026-09-30T23:56:41.123456+00:00", "archive_offset"),
    ("+05:30", "2026-09-30T14:26:41.123456+00:00", "archive_offset"),
    ("Z", "2026-09-30T19:56:41.123456+00:00", "archive_offset"),
    (None, "2026-09-30T10:56:41.123456+00:00", "assumed_local"),
    ("broken", "2026-09-30T10:56:41.123456+00:00", "assumed_local"),
    ("+24:00", "2026-09-30T10:56:41.123456+00:00", "assumed_local"),
    ("+04:60", "2026-09-30T10:56:41.123456+00:00", "assumed_local"),
])
def test_naive_created_uses_hint_else_injected_local(hint, expected, quality):
    node = {"created_at": "2026-09-30T19:56:41.123456",
            "metadata": {"run_end": "2026-09-30T20:00:00"}}
    assert timefmt.run_instant(node, FOLDER, offset_hint=hint, local_tz=LOCAL) == (us(expected), quality)


def test_naive_run_end_when_created_missing():
    assert timefmt.run_instant({"metadata": {"run_end": "2026-09-30T19:56:41.123456"}},
                               offset_hint="-04:00") == (
        us("2026-09-30T23:56:41.123456+00:00"), "archive_offset")


@pytest.mark.parametrize("hint, expected, quality", [
    ("-04:00", "2026-09-30T23:56:41+00:00", "archive_offset"),
    (None, "2026-09-30T10:56:41+00:00", "assumed_local"),
])
def test_folder_clock_uses_archive_offset_else_local(hint, expected, quality):
    assert timefmt.run_instant({}, FOLDER, offset_hint=hint, local_tz=LOCAL) == (us(expected), quality)


def test_default_local_uses_machine_zone():
    naive = datetime(2026, 9, 30, 19, 56, 41, 123456)
    expected = us(naive.astimezone(UTC).isoformat())
    assert timefmt.run_instant({"created_at": naive.isoformat()}) == (expected, "assumed_local")


@pytest.mark.parametrize("clock, expected", [
    ("2026-01-15T12:00:00", "2026-01-15T17:00:00+00:00"),
    ("2026-07-15T12:00:00", "2026-07-15T16:00:00+00:00"),
])
def test_injected_zone_uses_run_date_dst(clock, expected):
    assert timefmt.run_instant({"created_at": clock}, local_tz=ZoneInfo("America/New_York")) == (
        us(expected), "assumed_local")


@pytest.mark.parametrize("bad", [None, "", "broken", "2026-13-30T19:56:41+09:00",
                                "2026-09-30T99:56:41Z", "2026-09-30T19:56:41+99:00",
                                "2026-09-30", 123, True, {}, []])
def test_malformed_sources_fall_through(bad):
    node = {"created_at": bad, "metadata": {"run_end": bad}}
    assert timefmt.run_instant(node, FOLDER, offset_hint="-04:00") == (
        us("2026-09-30T23:56:41+00:00"), "archive_offset")
    assert timefmt.run_instant(node) == (None, "none")


@pytest.mark.parametrize("node", [None, [], 42, {"metadata": []}, {"metadata": None}])
def test_bad_node_or_metadata_is_missing_evidence(node):
    assert timefmt.run_instant(node) == (None, "none")


@pytest.mark.parametrize("folder", [None, "archive/2026-09-30/#7_measurement",
    "archive/2026-09-30/#7_measurement_195641_extra", "archive/2026-09-30/#7_measurement_999999",
    "archive/2026-02-30/#7_measurement_195641", "archive/not-a-date/#7_measurement_195641", 42])
def test_unusable_folder_never_invents_midnight(folder):
    assert timefmt.run_instant({}, folder, offset_hint="-04:00") == (None, "none")


@pytest.mark.parametrize("nodes, expected", [
    ([], None),
    ([{}, {"created_at": "broken"}, {"created_at": "2026-09-30T19:56:41"}], None),
    ([{"created_at": "2026-09-30T19:56:41-04:00"}] * 3 +
     [{"created_at": "2026-09-30T19:56:41+09:00"}], "-04:00"),
    ([{"created_at": "2026-09-30T19:56:41-04:00"},
      {"created_at": "2026-09-30T19:56:41+09:00"}], None),
    ([{"created_at": "2026-09-30T19:56:41Z"},
      {"created_at": "2026-09-30T19:56:41+00:00"},
      {"created_at": "2026-09-30T19:56:41+09:00"}], "+00:00"),
    ([{"metadata": {"run_end": "2026-09-30T19:56:41-04:00"}}], "-04:00"),
])
def test_archive_offset_votes(nodes, expected):
    assert timefmt.archive_offset_hint(iter(nodes)) == expected


def test_archive_counts_runs_once_and_prefers_created():
    node = {"created_at": "2026-09-30T19:56:41-04:00",
            "metadata": {"run_end": "2026-09-30T20:00:00+09:00"}}
    assert timefmt.archive_offset_hint([node]) == "-04:00"
    assert timefmt.archive_offset_hint([node, {"created_at": "2026-09-30T19:56:41+09:00"}]) is None


@pytest.mark.parametrize("node, mtime, first_seen, skew, classification", [
    ({}, None, None, None, "none"),
    ({"created_at": "1970-01-01T00:00:00Z"}, None, None, None, "none"),
    ({}, 0, None, None, "none"),
    ({}, None, 0, None, "none"),
    ({}, 0, 1_799_000_000, 1799, "small"),
    ({}, 0, 1_800_000_000, 1800, "ask"),
    ({"created_at": "1970-01-01T00:00:00Z"}, 1_799_000_000, None, 1799, "small"),
    ({"created_at": "1970-01-01T00:00:00Z"}, None, 1_800_000_000, 1800, "ask"),
    ({"created_at": "1970-01-01T00:00:00Z"}, 0, 0, 0, "small"),
    ({"created_at": "1970-01-01T00:00:00Z"}, -900_000_000, 900_000_000, 1800, "ask"),
    ({"created_at": "1970-01-01T00:00:00.000001Z"}, 0, 1_799_999_999, 1799.999999, "small"),
])
def test_witnesses_available_pairs_and_threshold(node, mtime, first_seen, skew, classification):
    result = timefmt.run_witnesses(node, mtime, first_seen)
    assert result == {"node_utc_us": timefmt.run_instant(node)[0],
                      "node_quality": timefmt.run_instant(node)[1],
                      "folder_mtime_utc_us": mtime, "first_seen_utc_us": first_seen,
                      "skew_s": skew, "skew_class": classification}
    assert timefmt.SKEW_ASK_S == 1800


def test_witness_node_uses_hint_and_injected_local():
    node = {"created_at": "2026-09-30T19:56:41.123456"}
    expected = us("2026-09-30T23:56:41.123456+00:00")
    result = timefmt.run_witnesses(node, expected, expected + 1_000_001,
                                  offset_hint="-04:00", local_tz=LOCAL)
    assert result["node_utc_us"] == expected
    assert result["node_quality"] == "archive_offset"
    assert result["skew_s"] == 1.000001
    assert result["skew_class"] == "small"
    assert timefmt.run_witnesses(node, local_tz=LOCAL)["node_utc_us"] == us(
        "2026-09-30T10:56:41.123456+00:00")
