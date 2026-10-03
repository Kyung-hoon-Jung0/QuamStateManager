"""Clock-health pins (docs/264): recorded English and Korean w32tm layouts, the real
probe output of the machine this was written on, and the read-only/never-raise contract.

Where the samples come from (nothing below is hand-typed from memory):
- REAL_PROBE_STDOUT is the exact stdout of the module's own cmd.exe probe on the
  development PC (Windows 11 26200, en-US UI, OEM code page 949, w32time stopped).
- The other w32tm layouts were rendered by a script from the OS's own resources on
  that PC: format strings 2501-2536/2555 and value strings 1300/1400/1401/3001-3120
  of C:/Windows/System32/{en-US,ko-KR}/w32tm.exe.mui, dates from
  GetDateFormatEx(DATE_SHORTDATE) + GetTimeFormatEx for en-US / ko-KR, and the
  0x80070426 text from FormatMessage in each language. The rendered English
  "stopped" line is byte-identical to what the real PC printed. Source names
  ("Local CMOS Clock", "Free-running System Clock") are hard-coded English inside
  w32time.dll, so they stay English in the Korean layouts. Not verified: the value
  spacing of the four verbose-tail lines in Korean (the parser does not read them).
"""
import ast
import datetime as dt
import json
import os
import shlex
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from quam_state_manager.core import clock_health as ch

NOW = dt.datetime(2026, 10, 3, 14, 0, tzinfo=dt.timezone.utc).timestamp()  # 23:00 KST

REAL_PROBE_STDOUT = (b"The following error occurred: The service has not been started. (0x80070426)\n"
                     b"__SM_W32_EXIT__-2147023834 \r\nKorea Standard Time__SM_TZ_EXIT__0\r\n")

ENGLISH_SYNCED = """\
Leap Indicator: 0(no warning)
Stratum: 3 (secondary reference - syncd by (S)NTP)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0312500s
Root Dispersion: 0.0700000s
ReferenceId: 0xCB007101 (source IP: 203.0.113.1)
Last Successful Sync Time: 10/3/2026 10:00:00 PM
Source: time.windows.com,0x9
Poll Interval: 10 (1024s)

Phase Offset: -0.0123456s
ClockRate: 0.0156250s
State Machine: 2 (Sync)
Time Source Flags: 0 (None)
Server Role: 0 (None)
Last Sync Error: 0 (The command completed successfully.)
Time since Last Good Sync Time: 3600.0000000s
"""
ENGLISH_STALE = """\
Leap Indicator: 0(no warning)
Stratum: 3 (secondary reference - syncd by (S)NTP)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0312500s
Root Dispersion: 0.0700000s
ReferenceId: 0xCB007101 (source IP: 203.0.113.1)
Last Successful Sync Time: 10/1/2026 10:00:00 PM
Source: time.windows.com,0x9
Poll Interval: 10 (1024s)

Phase Offset: -0.0123456s
ClockRate: 0.0156250s
State Machine: 2 (Sync)
Time Source Flags: 0 (None)
Server Role: 0 (None)
Last Sync Error: 0 (The command completed successfully.)
Time since Last Good Sync Time: 176400.0000000s
"""
ENGLISH_LOCAL_CMOS = """\
Leap Indicator: 0(no warning)
Stratum: 1 (primary reference - syncd by radio clock)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0000000s
Root Dispersion: 10.0000000s
ReferenceId: 0x4C4F434C (source name: "LOCL")
Last Successful Sync Time: 10/3/2026 10:00:00 PM
Source: Local CMOS Clock
Poll Interval: 6 (64s)

Phase Offset: 0.0000000s
ClockRate: 0.0156250s
State Machine: 2 (Sync)
Time Source Flags: 0 (None)
Server Role: 0 (None)
Last Sync Error: 0 (The command completed successfully.)
Time since Last Good Sync Time: 3600.0000000s
"""
ENGLISH_FREE_RUNNING = """\
Leap Indicator: 3(not synchronized)
Stratum: 0 (unspecified)
Precision: -23 (119.209ns per tick)
Root Delay: 0.0000000s
Root Dispersion: 0.0000000s
ReferenceId: 0x00000000 (unspecified)
Last Successful Sync Time: unspecified
Source: Free-running System Clock
Poll Interval: 10 (1024s)

Phase Offset: 0.0000000s
ClockRate: 0.0156250s
State Machine: 0 (Unset)
Time Source Flags: 0 (None)
Server Role: 0 (None)
Last Sync Error: 1 (The computer did not resync because no time data was available.)
Time since Last Good Sync Time: 4242.0000000s
"""
ENGLISH_STOPPED = "The following error occurred: The service has not been started. (0x80070426)\n"

KOREAN_SYNCED = """\
윤초 조정: 0(경고 없음)
계층: 3(보조 참조 - (S)NTP로 동기화됨)
정밀도: -23(틱당 119.209ns)
루트 지연: 0.0312500s
루트 분산: 0.0700000s
참조 ID: 0xCB007101(원본 IP: 203.0.113.1)
마지막으로 동기화한 시간: 2026-10-03 오후 10:00:00
원본: time.windows.com,0x9
폴링 간격: 10(1024s)

단계 오프셋: -0.0123456s
클록 속도: 0.0156250s
상태 시스템: 2 (동기화)
시간 원본 플래그: 0 (없음)
서버 역할: 0 (없음)
마지막 동기화 오류: 0 (명령이 성공적으로 완료되었습니다.)
마지막으로 동기화한 후 지난 시간: 3600.0000000s
"""
KOREAN_STALE = """\
윤초 조정: 0(경고 없음)
계층: 3(보조 참조 - (S)NTP로 동기화됨)
정밀도: -23(틱당 119.209ns)
루트 지연: 0.0312500s
루트 분산: 0.0700000s
참조 ID: 0xCB007101(원본 IP: 203.0.113.1)
마지막으로 동기화한 시간: 2026-10-01 오후 10:00:00
원본: time.windows.com,0x9
폴링 간격: 10(1024s)

단계 오프셋: -0.0123456s
클록 속도: 0.0156250s
상태 시스템: 2 (동기화)
시간 원본 플래그: 0 (없음)
서버 역할: 0 (없음)
마지막 동기화 오류: 0 (명령이 성공적으로 완료되었습니다.)
마지막으로 동기화한 후 지난 시간: 176400.0000000s
"""
KOREAN_LOCAL_CMOS = """\
윤초 조정: 0(경고 없음)
계층: 1(기본 참조 - 라디오 클록으로 동기화됨)
정밀도: -23(틱당 119.209ns)
루트 지연: 0.0000000s
루트 분산: 10.0000000s
참조 ID: 0x4C4F434C(원본 이름: "LOCL")
마지막으로 동기화한 시간: 2026-10-03 오후 10:00:00
원본: Local CMOS Clock
폴링 간격: 6(64s)

단계 오프셋: 0.0000000s
클록 속도: 0.0156250s
상태 시스템: 2 (동기화)
시간 원본 플래그: 0 (없음)
서버 역할: 0 (없음)
마지막 동기화 오류: 0 (명령이 성공적으로 완료되었습니다.)
마지막으로 동기화한 후 지난 시간: 3600.0000000s
"""
KOREAN_FREE_RUNNING = """\
윤초 조정: 3(동기화되지 않음)
계층: 0(지정되지 않음)
정밀도: -23(틱당 119.209ns)
루트 지연: 0.0000000s
루트 분산: 0.0000000s
참조 ID: 0x00000000(지정되지 않음)
마지막으로 동기화한 시간: 지정되지 않음
원본: Free-running System Clock
폴링 간격: 10(1024s)

단계 오프셋: 0.0000000s
클록 속도: 0.0156250s
상태 시스템: 0 (설정되지 않음)
시간 원본 플래그: 0 (없음)
서버 역할: 0 (없음)
마지막 동기화 오류: 1 (사용 가능한 시간 데이터가 없어 컴퓨터가 동기화하지 못했습니다.)
마지막으로 동기화한 후 지난 시간: 4242.0000000s
"""
KOREAN_STOPPED = "다음 오류가 발생했습니다. 서비스가 시작되지 않았습니다. (0x80070426)\n"

SYNCED = {"english": ENGLISH_SYNCED, "korean": KOREAN_SYNCED}
SERVER = "time.windows.com,0x9"
EXPECTED = {
    ("english", "synced"): (ENGLISH_SYNCED, True, SERVER, "2026-10-03T13:00:00Z", -0.0123456),
    ("korean", "synced"): (KOREAN_SYNCED, True, SERVER, "2026-10-03T13:00:00Z", -0.0123456),
    ("english", "stale"): (ENGLISH_STALE, False, SERVER, "2026-10-01T13:00:00Z", -0.0123456),
    ("korean", "stale"): (KOREAN_STALE, False, SERVER, "2026-10-01T13:00:00Z", -0.0123456),
    ("english", "local_cmos"): (ENGLISH_LOCAL_CMOS, False, "Local CMOS Clock", "2026-10-03T13:00:00Z", 0.0),
    ("korean", "local_cmos"): (KOREAN_LOCAL_CMOS, False, "Local CMOS Clock", "2026-10-03T13:00:00Z", 0.0),
    ("english", "free_running"): (ENGLISH_FREE_RUNNING, False, "Free-running System Clock", None, 0.0),
    ("korean", "free_running"): (KOREAN_FREE_RUNNING, False, "Free-running System Clock", None, 0.0),
}

# The exact command the probe may run; anything else (a /resync, /config, tzutil /s) is a defect.
READ_ONLY_COMMAND = ("w32tm /query /status /verbose & echo __SM_W32_EXIT__!errorlevel!"
                     " & tzutil /g & echo __SM_TZ_EXIT__!errorlevel!")


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    monkeypatch.setattr(ch, "_STATUS_CACHE", None)
    monkeypatch.setattr(ch, "_ENV_CACHE", {})


@pytest.fixture
def local_korea(monkeypatch):
    """Pin the OS zone the samples were rendered in (KST, no DST) and month-first dates."""
    monkeypatch.setattr(ch, "_local_datetime_to_utc", lambda value: value.replace(
        tzinfo=dt.timezone(dt.timedelta(hours=9))).astimezone(dt.timezone.utc))
    monkeypatch.setattr(ch, "_date_order", lambda: 0)


def completed(stdout=b"", code=0, stderr=b""):
    return SimpleNamespace(stdout=stdout, returncode=code, stderr=stderr)


def windows_output(sample, zone="Korea Standard Time", code=0, zone_code=0):
    return f"{sample}\n__SM_W32_EXIT__{code}\n{zone}__SM_TZ_EXIT__{zone_code}\n"


def with_field(sample, index, value):
    """Replace the value of the index-th "label: value" line, whatever the label's language."""
    lines, seen = sample.splitlines(keepends=True), -1
    for number, line in enumerate(lines):
        if ":" in line:
            seen += 1
            if seen == index:
                lines[number] = line.split(":", 1)[0] + ": " + value + "\n"
                edited = "".join(lines)
                assert edited != sample, "the edit must change the sample"
                return edited
    raise AssertionError(f"sample has no field {index}")


def other_tz_value():
    """A TZ string whose offset differs from this OS's current offset."""
    return "EST5" if ch._os_offset() == "+00:00" else "UTC0"


# ---------------------------------------------------------------- recorded layouts

@pytest.mark.parametrize("key", EXPECTED, ids=lambda key: "-".join(key))
def test_recorded_layouts(key, local_korea):
    sample, synced, source, last, offset = EXPECTED[key]
    result = ch._parse_w32tm(sample, NOW)
    assert (result["synced"], result["source"], result["last_sync_utc"], result["offset_s"]) == (
        synced, source, last, offset)
    assert result["detail"]


@pytest.mark.parametrize("language", SYNCED)
def test_recent_sync_exact(language, local_korea):
    assert ch._parse_w32tm(SYNCED[language], NOW) == {
        "synced": True, "source": SERVER, "last_sync_utc": "2026-10-03T13:00:00Z",
        "offset_s": -0.0123456, "detail": "Successful external synchronization within 24 hours."}


@pytest.mark.parametrize("sample,needle", [
    (ENGLISH_LOCAL_CMOS, "LOCL"), (KOREAN_LOCAL_CMOS, "Local CMOS Clock"),
    (ENGLISH_FREE_RUNNING, "leap indicator 3"), (KOREAN_FREE_RUNNING, "stratum 0")],
    ids=["english-local_cmos", "korean-local_cmos", "english-free_running", "korean-free_running"])
def test_local_layouts_say_why(sample, needle, local_korea):
    assert needle in ch._parse_w32tm(sample, NOW)["detail"]


@pytest.mark.parametrize("stdout,code", [
    (ENGLISH_STOPPED, -2147023834), (KOREAN_STOPPED, -2147023834), (KOREAN_STOPPED, 2147943462)])
def test_service_stopped(stdout, code, monkeypatch):
    monkeypatch.setattr(ch, "_run", lambda *args: completed(windows_output(stdout, code=code)))
    ntp, iana, name = ch._windows_probe()
    assert ntp == ch._unknown(f"Windows Time query failed ({code}): {stdout.strip()}")
    assert (iana, name) == ("Asia/Seoul", "Korea Standard Time")


def test_real_probe_capture(monkeypatch):
    """The bytes this machine's probe really printed -> the status() it really reported."""
    monkeypatch.setattr(ch, "_run", lambda *args: completed(REAL_PROBE_STDOUT))
    assert ch._windows_probe() == (ch._unknown(
        "Windows Time query failed (-2147023834): The following error occurred: "
        "The service has not been started. (0x80070426)"), "Asia/Seoul", "Korea Standard Time")


def test_failure_detail_keeps_stderr(monkeypatch):
    monkeypatch.setattr(ch, "_run", lambda *args: completed(
        windows_output("", code=5), stderr=b"Access is denied."))
    assert "Access is denied." in ch._windows_probe()[0]["detail"]


# ---------------------------------------------------------------- what makes a sync trustworthy

@pytest.mark.parametrize("language", SYNCED)
@pytest.mark.parametrize("age,expected", [(86400, True), (86400.01, False), (-1, False)])
def test_sync_age(language, age, expected, local_korea):
    result = ch._parse_w32tm(SYNCED[language], NOW - 3600 + age)
    assert result["synced"] is expected
    assert result["last_sync_utc"] == "2026-10-03T13:00:00Z"


@pytest.mark.parametrize("language", SYNCED)
@pytest.mark.parametrize("index,value", [
    (7, "Local CMOS Clock"), (7, "Free-running System Clock"), (7, "local cmos clock,0x9"),
    (5, "0x4C4F434C (source name: \"LOCL\")"), (5, "0x4c4f434c(원본 이름: \"LOCL\")"),
    (0, "3(not synchronized)"), (1, "0(지정되지 않음)")])
def test_each_local_signal_alone_refuses_sync(language, index, value, local_korea):
    """Each not-a-time-server signal flips an otherwise-trusted sample, by itself."""
    assert ch._parse_w32tm(SYNCED[language], NOW)["synced"] is True
    assert ch._parse_w32tm(with_field(SYNCED[language], index, value), NOW)["synced"] is False


@pytest.mark.parametrize("language", SYNCED)
def test_vm_host_source_is_unknown(language, local_korea):
    result = ch._parse_w32tm(with_field(SYNCED[language], 7, "VM IC Time Synchronization Provider"), NOW)
    assert result["synced"] is None
    assert result["source"] == "VM IC Time Synchronization Provider" and "host" in result["detail"]


@pytest.mark.parametrize("language,value", [("english", "unspecified"), ("korean", "지정되지 않음"),
                                            ("english", "(not a valid local time)"),
                                            ("korean", "10/03/26 10:00:00 PM")])
def test_missing_last_sync_is_unknown(language, value, local_korea):
    result = ch._parse_w32tm(with_field(SYNCED[language], 6, value), NOW)
    assert result["synced"] is None and result["last_sync_utc"] is None
    assert result["source"] == SERVER and result["detail"]


@pytest.mark.parametrize("language", SYNCED)
def test_empty_source_is_unknown(language, local_korea):
    """A recent date with no named source is not "a real time source"."""
    result = ch._parse_w32tm(with_field(SYNCED[language], 7, ""), NOW)
    assert (result["synced"], result["source"]) == (None, None)


def test_impossible_date_keeps_the_rest(local_korea):
    result = ch._parse_w32tm(with_field(ENGLISH_SYNCED, 6, "99/99/2026 10:00:00 PM"), NOW)
    assert (result["synced"], result["source"], result["offset_s"]) == (None, SERVER, -0.0123456)


@pytest.mark.parametrize("language", SYNCED)
def test_no_phase_offset(language, local_korea):
    sample = "\n".join(SYNCED[language].splitlines()[:9])
    result = ch._parse_w32tm(sample, NOW)
    assert result["synced"] is True
    assert result["offset_s"] is None


def test_decimal_comma(local_korea):
    assert ch._parse_w32tm(ENGLISH_SYNCED.replace("-0.0123456s", "+0,0123456s"), NOW)["offset_s"] == 0.0123456


def test_unknown_labels(local_korea):
    sample = "\n".join(f"field{index}:" + line.split(":", 1)[1]
                       for index, line in enumerate(KOREAN_SYNCED.splitlines()) if ":" in line)
    assert ch._parse_w32tm(sample, NOW)["synced"] is True


@pytest.mark.parametrize("sample", [
    "", "garbage", "oops: yes", ENGLISH_STOPPED, KOREAN_STOPPED,
    "".join(reversed(ENGLISH_SYNCED.splitlines(keepends=True))),
    ENGLISH_SYNCED.replace("0xCB007101", "bad"),
    ENGLISH_SYNCED.replace("Leap Indicator: 0", "Leap Indicator: bad"),
    ENGLISH_SYNCED.replace("Stratum: 3", "Stratum: bad"),
    ENGLISH_SYNCED.replace("Precision: -23", "Precision: bad"),
    ENGLISH_SYNCED.replace("Poll Interval: 10", "Poll Interval: bad")])
def test_foreign_layout_never_trusted(sample, local_korea):
    assert ch._parse_w32tm(sample, NOW)["synced"] is None


# ---------------------------------------------------------------- localized dates

@pytest.mark.parametrize("value,order,expected", [
    # Real renderings of 2026-10-03 22:00:00 (GetDateFormatEx + GetTimeFormatEx).
    ("2026-10-03 오후 10:00:00", 0, "2026-10-03T13:00:00+00:00"),        # ko-KR
    ("10/3/2026 10:00:00 PM", 0, "2026-10-03T13:00:00+00:00"),           # en-US
    ("03/10/2026 22:00:00", 1, "2026-10-03T13:00:00+00:00"),             # en-GB, fr-FR, he-IL
    ("03.10.2026 22:00:00", 1, "2026-10-03T13:00:00+00:00"),             # de-DE
    ("3.10.2026 22.00.00", 1, "2026-10-03T13:00:00+00:00"),              # fi-FI
    ("2026/10/03 22:00:00", None, "2026-10-03T13:00:00+00:00"),          # ja-JP
    ("2026/10/3 22:00:00", None, "2026-10-03T13:00:00+00:00"),           # zh-CN
    ("03-10-2026 22:00:00", 1, "2026-10-03T13:00:00+00:00"),             # hi-IN
    ("2026-10-03 오전 12:05:09", 0, "2026-10-02T15:05:09+00:00"),        # ko-KR, after midnight
    ("10/3/2026 12:05:09 AM", 0, "2026-10-02T15:05:09+00:00"),           # en-US, after midnight
    ("2026. 10. 03. 오후 12:00:00", None, "2026-10-03T03:00:00+00:00"),  # older ko-KR pattern
    ("\u200e10/3/2026 10:00:00 PM", 0, "2026-10-03T13:00:00+00:00"),     # bidi marks stripped
])
def test_local_date_formats(value, order, expected, local_korea, monkeypatch):
    monkeypatch.setattr(ch, "_date_order", lambda: order)
    assert ch._parse_local_sync(value).isoformat() == expected


@pytest.mark.parametrize("value,order", [
    ("03/10/2026 22:00:00", None),       # year last, order unknown: never guessed
    ("10/03/26 10:00:00 PM", 0),         # two-digit year
    ("10/3/2026 13:00:00 PM", 0),        # 13 PM
    ("10/3/2026 0:00:00 AM", 0),         # 0 AM
    ("unspecified", 0), ("", 0)])
def test_ambiguous_dates_are_refused(value, order, local_korea, monkeypatch):
    monkeypatch.setattr(ch, "_date_order", lambda: order)
    assert ch._parse_local_sync(value) is None


@pytest.mark.parametrize("pattern,order", [
    ("M/d/yyyy", 0), ("dd/MM/yyyy", 1), ("d.M.yyyy", 1), ("dd-MM-yyyy", 1), ("yyyy-MM-dd", 0),
    ("yyyy/M/d", 0), ("'d'M/d/yyyy", 0), ("yyyy", None), ("", None), (None, None)])
def test_order_from_pattern(pattern, order):
    assert ch._order_from_pattern(pattern) == order


# ---------------------------------------------------------------- zones and offsets

@pytest.mark.parametrize("name,iana", [*ch.WINDOWS_TO_IANA.items(), ("Unmapped Zone", None)])
def test_tzutil_mapping(name, iana, monkeypatch, local_korea):
    monkeypatch.setattr(ch, "_run", lambda *args: completed(windows_output(ENGLISH_SYNCED, name)))
    monkeypatch.setattr(ch.time, "time", lambda: NOW)
    ntp, actual, windows_name = ch._windows_probe()
    assert (actual, windows_name) == (iana, name)
    assert ntp["synced"] is True


def test_table_covers_the_spec():
    assert set(ch.WINDOWS_TO_IANA) == {
        "Korea Standard Time", "Tokyo Standard Time", "China Standard Time", "India Standard Time",
        "UTC", "GMT Standard Time", "W. Europe Standard Time", "Central Europe Standard Time",
        "Eastern Standard Time", "Central Standard Time", "Mountain Standard Time",
        "Pacific Standard Time", "Israel Standard Time"}


def test_tzutil_failure(monkeypatch, local_korea):
    monkeypatch.setattr(ch, "_run", lambda *args: completed(windows_output(ENGLISH_SYNCED, "error", zone_code=1)))
    assert ch._windows_probe()[1:] == (None, None)


@pytest.mark.parametrize("state,bias,standard,daylight,expected", [
    (0, -540, 0, 0, "+09:00"),      # Korea: no DST -> TIME_ZONE_ID_UNKNOWN
    (1, 480, 0, -60, "-08:00"),     # Pacific, winter
    (2, 480, 0, -60, "-07:00"),     # Pacific, summer
    (0, -330, 0, 0, "+05:30"),      # India
    (2, 210, 0, -60, "-02:30"),     # Newfoundland, summer
    (1, -60, 0, -60, "+01:00")])    # W. Europe, winter
def test_bias_offset_seconds(state, bias, standard, daylight, expected):
    assert ch._offset(ch._bias_offset_seconds(state, bias, standard, daylight)) == expected


@pytest.mark.skipif(os.name != "nt", reason="reads the Windows registry zone database")
@pytest.mark.parametrize("name,iana", ch.WINDOWS_TO_IANA.items())
def test_table_and_bias_agree_with_os_zone_data(name, iana):
    """Executable cross-check: the OS's own TZI for each Windows zone, through
    _bias_offset_seconds, gives the offsets zoneinfo gives for the mapped IANA zone."""
    import struct
    import winreg
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                         r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Time Zones" + "\\" + name)
    tzi = winreg.QueryValueEx(key, "TZI")[0]
    bias, standard, daylight = struct.unpack_from("<lll", tzi)
    has_dst = struct.unpack_from("<8H", tzi, 12)[1] != 0  # StandardDate.wMonth
    zone = ZoneInfo(iana)
    for month, state in ((1, 1), (7, 2)):  # every mapped zone is northern or DST-free
        expected = zone.utcoffset(dt.datetime(2026, month, 15, 12)).total_seconds()
        assert ch._bias_offset_seconds(state if has_dst else 0, bias, standard, daylight) == expected


@pytest.mark.parametrize("seconds,expected", [(32400, "+09:00"), (19800, "+05:30"),
                                             (-12600, "-03:30"), (0, "+00:00")])
def test_offset_format(seconds, expected):
    assert ch._offset(seconds) == expected


@pytest.mark.skipif(os.name != "nt", reason="on POSIX TZ is the process's documented zone")
def test_os_offset_ignores_tz_variable():
    """Python's local time follows TZ on Windows; the OS's zone does not."""
    code = ("import datetime as dt; from quam_state_manager.core import clock_health as ch; "
            "print(ch._os_offset(), dt.datetime.now().astimezone().utcoffset().total_seconds(), "
            "ch._local_datetime_to_utc(dt.datetime(2026, 1, 15, 12)).isoformat())")
    env = {**os.environ, "TZ": other_tz_value()}
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                         cwd=Path(__file__).resolve().parents[1], timeout=60).stdout.split()
    assert ch._offset(float(out[1])) != ch._os_offset(), "TZ must really move Python's offset"
    assert out[0] == ch._os_offset()
    assert out[2] == ch._local_datetime_to_utc(dt.datetime(2026, 1, 15, 12)).isoformat()


@pytest.mark.skipif(os.name != "nt", reason="Windows conversion path")
def test_local_to_utc_uses_the_os_zone():
    """OS-local "now" (UTC now + the OS's current offset) converts back to UTC now."""
    text = ch._os_offset()
    offset = dt.timedelta(hours=int(text[1:3]), minutes=int(text[4:])) * (1 if text[0] == "+" else -1)
    utc_now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    local = (utc_now + offset).replace(tzinfo=None)
    assert ch._local_datetime_to_utc(local) == utc_now


# ---------------------------------------------------------------- status(): cache, shape, never raises

def test_status_cache_and_shape(monkeypatch, local_korea):
    calls = []
    clock = [100.0]

    def run(args, timeout):
        calls.append((args, timeout))
        return completed(windows_output(KOREAN_SYNCED))

    monkeypatch.setattr(ch, "_run", run)
    monkeypatch.setattr(ch, "_posix_probe", ch._windows_probe)
    monkeypatch.setattr(ch.time, "time", lambda: NOW)
    monkeypatch.setattr(ch.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ch, "_os_offset", lambda: "+09:00")
    first = ch.status()
    assert first == {"ntp": ch._parse_w32tm(KOREAN_SYNCED, NOW), "os_zone": {
        "iana": "Asia/Seoul", "utc_offset": "+09:00", "windows_name": "Korea Standard Time"}}
    assert first["ntp"]["synced"] is True
    first["ntp"]["synced"] = False
    clock[0] += 59.99
    assert ch.status()["ntp"]["synced"] is True
    assert calls == [(["cmd.exe", "/d", "/v:on", "/c", READ_ONLY_COMMAND], 15)]
    clock[0] += .01
    ch.status()
    assert len(calls) == 2


def test_status_reads_offset_fresh(monkeypatch):
    monkeypatch.setattr(ch, "_windows_probe", lambda: (ch._unknown("x"), "Asia/Seoul", "Korea Standard Time"))
    monkeypatch.setattr(ch, "_posix_probe", ch._windows_probe)
    monkeypatch.setattr(ch, "_os_offset", lambda: "+09:00")
    assert ch.status()["os_zone"]["utc_offset"] == "+09:00"
    monkeypatch.setattr(ch, "_os_offset", lambda: "+10:00")  # a DST change inside the cache window
    assert ch.status()["os_zone"]["utc_offset"] == "+10:00"


def test_concurrent_cache(monkeypatch):
    calls = []

    def probe():
        calls.append(1)
        time.sleep(0.05)
        return ch._unknown("test"), "Etc/UTC", None
    monkeypatch.setattr(ch, "_windows_probe", probe)
    monkeypatch.setattr(ch, "_posix_probe", probe)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: ch.status(), range(16)))
    assert len(calls) == 1
    assert all(result == results[0] for result in results)


def test_cached_sync_ages_out(monkeypatch, local_korea):
    calls = []
    wall = [NOW - 3600 + 86400]

    def probe():
        calls.append(1)
        return ch._parse_w32tm(ENGLISH_SYNCED, wall[0]), "Asia/Seoul", "Korea Standard Time"
    monkeypatch.setattr(ch, "_windows_probe", probe)
    monkeypatch.setattr(ch, "_posix_probe", probe)
    monkeypatch.setattr(ch.time, "time", lambda: wall[0])
    assert ch.status()["ntp"]["synced"] is True
    wall[0] += 1
    assert ch.status()["ntp"]["synced"] is False
    assert len(calls) == 1


def test_decode_console_output(monkeypatch):
    if os.name == "nt":
        monkeypatch.setattr(ch.ctypes, "windll", SimpleNamespace(kernel32=SimpleNamespace(GetOEMCP=lambda: 949)))
        assert ch._decode(KOREAN_SYNCED.encode("cp949")) == KOREAN_SYNCED
    else:
        assert ch._decode(KOREAN_SYNCED.encode("utf-8")) == KOREAN_SYNCED
    assert ch._decode("text") == "text"


@pytest.mark.parametrize("fault", ["garbage", "timeout", "missing", "offset"])
def test_status_never_raises(monkeypatch, fault):
    calls = []

    def run(*args):
        calls.append(1)
        if fault == "timeout":
            raise subprocess.TimeoutExpired("probe", 5)
        if fault == "missing":
            raise FileNotFoundError("probe")
        return completed(b"garbage")
    monkeypatch.setattr(ch, "_run", run)
    monkeypatch.setattr(ch, "_posix_probe", ch._windows_probe)
    if fault == "offset":
        monkeypatch.setattr(ch, "_os_offset", lambda: 1 / 0)
    first, second = ch.status(), ch.status()
    assert first["ntp"]["synced"] is None and first["ntp"]["detail"]
    assert second["ntp"]["synced"] is None and len(calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="the real w32tm/tzutil probe")
def test_real_status_on_this_machine(monkeypatch):
    calls = []
    real_run = ch._run
    monkeypatch.setattr(ch, "_run", lambda *args: (calls.append(args), real_run(*args))[1])
    first = ch.status()
    assert set(first) == {"ntp", "os_zone"}
    assert set(first["ntp"]) == {"synced", "source", "last_sync_utc", "offset_s", "detail"}
    assert set(first["os_zone"]) == {"iana", "utc_offset", "windows_name"}
    assert first["ntp"]["synced"] in (True, False, None) and first["ntp"]["detail"]
    if first["ntp"]["synced"] is True:
        assert first["ntp"]["last_sync_utc"] and first["ntp"]["source"]
    assert first["os_zone"]["utc_offset"] == ch._os_offset()
    assert first["os_zone"]["windows_name"]  # tzutil /g always names the zone
    assert first["os_zone"]["iana"] == ch.WINDOWS_TO_IANA.get(first["os_zone"]["windows_name"])
    assert ch.status() == first and len(calls) == 1


# ---------------------------------------------------------------- _run: the timeout is a real bound

def test_run_returns_output():
    result = ch._run([sys.executable, "-c",
                      "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], 30)
    assert (result.stdout.strip(), result.stderr.strip(), result.returncode) == (b"out", b"err", 3)


def test_run_timeout_is_a_real_bound(monkeypatch):
    """A grandchild holding the output (w32tm under cmd.exe) must not stretch the
    timeout (subprocess.run took 8.4 s for 1 s here), and the child is killed."""
    children = []
    real_popen = ch.subprocess.Popen
    monkeypatch.setattr(ch.subprocess, "Popen", lambda *a, **k: children.append(real_popen(*a, **k)) or children[-1])
    args = (["cmd.exe", "/d", "/c", "ping -n 9 127.0.0.1 & echo done"] if os.name == "nt"
            else ["sh", "-c", "sleep 8; echo done"])
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        ch._run(args, 1)
    assert time.monotonic() - started < 4
    assert len(children) == 1 and children[0].poll() is not None, "the timed-out child must be killed"


# ---------------------------------------------------------------- env_time_check

def test_current_interpreter():
    result = ch.env_time_check(sys.executable)
    assert set(result) == {"python", "epoch_delta_s", "utc_offset", "matches_os", "detail"}
    assert result["python"] == sys.executable
    assert result["matches_os"] is True
    assert abs(result["epoch_delta_s"]) <= 2
    assert result["utc_offset"] == ch._os_offset()
    assert "tzname=" in result["detail"]


@pytest.mark.skipif(os.name != "nt", reason="on POSIX TZ moves the OS-offset reading too")
def test_inherited_tz_is_a_mismatch(monkeypatch):
    monkeypatch.setenv("TZ", other_tz_value())
    result = ch.env_time_check(sys.executable)
    assert result["matches_os"] is False
    assert result["utc_offset"] != ch._os_offset()
    assert abs(result["epoch_delta_s"]) <= 2


def test_fake_interpreter_wrong_offset(tmp_path):
    script = tmp_path / "wrong_clock.py"
    script.write_text("import datetime,json,time\n"
                      "offset=datetime.datetime.now().astimezone().utcoffset().total_seconds()+3600\n"
                      "print(json.dumps({'epoch':time.time(),'offset_s':offset,'tzname':'wrong'}))\n",
                      encoding="utf-8")
    shim = tmp_path / ("wrong.cmd" if os.name == "nt" else "wrong")
    if os.name == "nt":
        shim.write_text(f'@"{sys.executable}" "{script}"\n', encoding="utf-8")
    else:
        shim.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(script))}\n", encoding="utf-8")
        shim.chmod(0o700)
    result = ch.env_time_check(str(shim))
    assert result["matches_os"] is False
    assert result["utc_offset"] != ch._os_offset()
    assert result["epoch_delta_s"] is not None and abs(result["epoch_delta_s"]) < 2


@pytest.mark.parametrize("delta,offset,expected", [(2, 32400, True), (-2, 32400, True),
                                                 (2.01, 32400, False), (-2.01, 32400, False),
                                                 (0, 28800, False)])
def test_env_thresholds(delta, offset, expected, monkeypatch):
    monkeypatch.setattr(ch.time, "time", lambda: NOW)
    monkeypatch.setattr(ch, "_os_offset", lambda: "+09:00")
    monkeypatch.setattr(ch, "_run", lambda *args: completed(json.dumps({
        "epoch": NOW + delta, "offset_s": offset, "tzname": "test"})))
    result = ch.env_time_check("fake")
    assert result["matches_os"] is expected
    assert result["epoch_delta_s"] == pytest.approx(delta)
    assert result["utc_offset"] == ch._offset(offset)


def test_env_banner_before_payload(monkeypatch):
    monkeypatch.setattr(ch.time, "time", lambda: NOW)
    monkeypatch.setattr(ch, "_os_offset", lambda: "+09:00")
    monkeypatch.setattr(ch, "_run", lambda *args: completed(
        b"sitecustomize: hello\r\n" + json.dumps({"epoch": NOW, "offset_s": 32400, "tzname": "KST"}).encode() + b"\r\n"))
    result = ch.env_time_check("fake")
    assert result["matches_os"] is True and result["utc_offset"] == "+09:00"


def test_env_cache(monkeypatch):
    calls = []
    clock = [100.0]
    monkeypatch.setattr(ch.time, "time", lambda: NOW)
    monkeypatch.setattr(ch.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ch, "_os_offset", lambda: "+09:00")

    def run(args, timeout):
        calls.append((args, timeout))
        return completed(json.dumps({"epoch": NOW, "offset_s": 32400, "tzname": "KST"}))
    monkeypatch.setattr(ch, "_run", run)
    first = ch.env_time_check("fake")
    first["matches_os"] = False
    clock[0] += 59.99
    assert ch.env_time_check("fake")["matches_os"] is True and len(calls) == 1
    assert calls[0] == (["fake", "-c", ch._ENV_SCRIPT], 20)
    monkeypatch.setattr(ch, "_os_offset", lambda: "+08:00")
    assert ch.env_time_check("fake")["matches_os"] is False
    clock[0] += .01
    ch.env_time_check("fake")
    assert len(calls) == 2
    ch.env_time_check("another")
    assert len(calls) == 3


@pytest.mark.parametrize("fault", ["garbage", "empty", "missing", "timeout", "exit", "nan", "offset", "key", "exe"])
def test_env_never_raises(monkeypatch, fault):
    def run(*args):
        if fault == "missing":
            raise FileNotFoundError("fake")
        if fault == "timeout":
            raise subprocess.TimeoutExpired("fake", 20)
        if fault == "exit":
            return completed(json.dumps({"epoch": time.time(), "offset_s": 0, "tzname": "x"}), code=1)
        if fault == "nan":
            return completed('{"epoch":NaN,"offset_s":0,"tzname":"bad"}')
        if fault == "offset":
            return completed('{"epoch":0,"offset_s":86400,"tzname":"bad"}')
        if fault == "key":
            return completed('{}')
        if fault == "empty":
            return completed(b"")
        return completed(b"garbage")
    monkeypatch.setattr(ch, "_run", run)
    result = ch.env_time_check([] if fault == "exe" else "fake")
    assert result["matches_os"] is False
    assert "failed" in result["detail"] or "unavailable" in result["detail"]


# ---------------------------------------------------------------- POSIX

@pytest.mark.parametrize("synchronized,expected", [("yes", None), ("no", False), ("garbage", None)])
def test_timedatectl(synchronized, expected, monkeypatch):
    calls = []
    monkeypatch.setattr(ch, "_iana_zone", lambda: None)
    monkeypatch.setattr(ch.shutil, "which", lambda _: "/usr/bin/timedatectl")

    def run(args, timeout):
        calls.append((args, timeout))
        return completed(f"Timezone=Etc/UTC\nNTPSynchronized={synchronized}\n")
    monkeypatch.setattr(ch, "_run", run)
    ntp, iana, name = ch._posix_probe()
    assert ntp["synced"] is expected
    assert (iana, name) == ("Etc/UTC", None)
    assert "last successful sync time is unavailable" in ntp["detail"]
    assert calls == [(["timedatectl", "show", "--property=NTPSynchronized", "--property=Timezone"], 15)]


@pytest.mark.parametrize("available", [False, True])
def test_posix_unavailable(available, monkeypatch):
    monkeypatch.setattr(ch, "_iana_zone", lambda: "Etc/UTC")
    monkeypatch.setattr(ch.shutil, "which", lambda _: available)
    monkeypatch.setattr(ch, "_run", lambda *args: completed(code=1, stderr=b"no service"))
    ntp, iana, name = ch._posix_probe()
    assert ntp["synced"] is None and ntp["detail"]
    assert (iana, name) == ("Etc/UTC", None)


@pytest.mark.parametrize("zone,expected", [("Asia/Seoul", "Asia/Seoul"), (":Etc/UTC", "Etc/UTC"),
                                        ("Invalid/Zone", None), ("", "Europe/London")])
def test_posix_zone(zone, expected, monkeypatch):
    monkeypatch.setenv("TZ", zone)
    monkeypatch.setattr(ch.Path, "resolve", lambda self: ch.Path("/usr/share/zoneinfo/Europe/London"))
    assert ch._iana_zone() == expected


# ---------------------------------------------------------------- contract

def test_stdlib_only():
    tree = ast.parse(Path(ch.__file__).read_text(encoding="utf-8"))
    modules = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
               for alias in node.names}
    modules |= {node.module.split(".")[0] for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module and node.level == 0}
    assert modules and modules <= set(sys.stdlib_module_names) | {"__future__"}
