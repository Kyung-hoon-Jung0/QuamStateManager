"""v1.5.1 (perf) -- the ledger's day zone is read from the OS, not from the clock probe.

A lab with no project time zone binds its ledger days to this PC's zone
(``project_time.pc_zone``). That zone came from the clock status, whose probe
is cmd.exe + w32tm + tzutil (0.3-3 s, once a minute): measured on a copy of a
customer chip as a quarter of the Calibration log's first request, and again
on the first request of every minute after. ``clock_health.os_zone`` reads the
same zone key tzutil prints, straight from the OS; the clock status answers
only when the OS cannot be asked that way.
"""
from __future__ import annotations

import ctypes
import os

import pytest

from quam_state_manager.core import clock_health, project_time

ZONES = [
    ({"iana": "Asia/Seoul", "utc_offset": "+09:00", "windows_name": "Korea Standard Time"}, "Asia/Seoul"),
    ({"iana": None, "utc_offset": "+09:00", "windows_name": "Korea Standard Time_dstoff"}, "Etc/GMT-9"),
    ({"iana": None, "utc_offset": "-04:00", "windows_name": None}, "Etc/GMT+4"),
    ({"iana": None, "utc_offset": "+00:00", "windows_name": None}, "UTC"),
    ({"iana": None, "utc_offset": "+05:30", "windows_name": None}, None),
]


@pytest.mark.parametrize("os_zone, expected", ZONES)
def test_the_zone_the_os_reports_never_asks_the_clock_status(monkeypatch, os_zone, expected):
    def probe(force=False):
        raise AssertionError("the NTP probe ran for a zone the OS reported")
    monkeypatch.setattr(clock_health, "os_zone", lambda: dict(os_zone))
    monkeypatch.setattr(project_time, "clock_status", probe)
    assert project_time.pc_zone() == expected


def test_a_real_lookup_runs_no_probe(monkeypatch):
    if clock_health.os_zone() is None:
        pytest.skip("this OS gives no zone record without the probe")
    ran = []
    monkeypatch.setattr(clock_health, "_windows_probe", lambda: ran.append("w") or (None, None, None))
    monkeypatch.setattr(clock_health, "_posix_probe", lambda: ran.append("p") or (None, None, None))
    monkeypatch.setattr(clock_health, "_STATUS_CACHE", None)
    project_time._CLOCK_MEMO.clear()
    for _ in range(3):
        project_time.pc_zone()
    assert ran == [], "the clock probe ran to find the zone"


@pytest.mark.skipif(os.name != "nt", reason="the Windows zone key")
def test_the_os_record_names_what_tzutil_names():
    """The fast record and the probe's (tzutil /g) agree on this PC."""
    fast = clock_health.os_zone()
    clock_health._STATUS_CACHE = None
    probed = clock_health.status()["os_zone"]
    assert fast is not None
    if probed["windows_name"] is None:
        pytest.skip("the probe could not read the zone on this PC")
    assert fast == probed


class _FakeKernel:
    def __init__(self, name, dst_off):
        self.name, self.dst_off = name, dst_off

        class Fn:
            restype = None

            def __call__(fn, ref):          # noqa: N805 -- a bound stand-in
                info = ctypes.cast(ref, ctypes.POINTER(clock_health._DynamicTimeZoneInformation)).contents
                info.TimeZoneKeyName = self.name
                info.DynamicDaylightTimeDisabled = self.dst_off
                return 0
        self.GetDynamicTimeZoneInformation = Fn()


@pytest.mark.skipif(os.name != "nt", reason="the Windows zone key")
@pytest.mark.parametrize("name, off, windows_name, iana", [
    ("Korea Standard Time", 0, "Korea Standard Time", "Asia/Seoul"),
    ("Korea Standard Time", 1, "Korea Standard Time_dstoff", None),     # tzutil's spelling: no table row
    ("No Such Zone", 0, "No Such Zone", None),
])
def test_the_key_maps_through_the_probes_table(monkeypatch, name, off, windows_name, iana):
    monkeypatch.setattr(clock_health, "_kernel32", lambda: _FakeKernel(name, off))
    monkeypatch.setattr(clock_health, "_os_offset", lambda: "+09:00")
    assert clock_health.os_zone() == {"iana": iana, "utc_offset": "+09:00", "windows_name": windows_name}
