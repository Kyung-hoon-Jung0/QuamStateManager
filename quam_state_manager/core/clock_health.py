"""Read-only clock health: is this PC's clock synced, and which zone is it in? (docs/264)

Public interface. A UI is coded against these exact shapes; do not change them::

    status() -> {"ntp": {"synced": bool|None, "source": str|None, "last_sync_utc": str|None,
                         "offset_s": float|None, "detail": str},
                 "os_zone": {"iana": str|None, "utc_offset": "+09:00", "windows_name": str|None}}
    env_time_check(python_exe) -> {"python", "epoch_delta_s", "utc_offset", "matches_os", "detail"}

Only read-only queries ever run: ``w32tm /query /status /verbose`` and ``tzutil /g``
on Windows (one cmd.exe child per 60 s), ``timedatectl show`` elsewhere, and
``<python> -c`` for an environment check. Nothing here can change the clock, the
time zone or the NTP configuration. Stdlib only; the public functions never raise.
"""
from __future__ import annotations

import copy
import ctypes
import datetime as dt
import functools
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from zoneinfo import ZoneInfo

_CACHE_SECONDS = 60.0
_MAX_SYNC_AGE = 24 * 60 * 60
_ENV_MAX_DELTA_S = 2.0
# cmd.exe + w32tm + tzutil is three process spawns. On the loaded development PC
# one spawn measured 0.55-1.0 s and the chain 1.7-3.2 s, once past 5 s (docs/264),
# so 5 s would report "unknown" for a whole cache period on a merely busy PC.
_PROBE_TIMEOUT_S = 15
_LOCK = threading.Lock()
_ENV_LOCK = threading.Lock()
_STATUS_CACHE = None
_ENV_CACHE = {}
_UTC = dt.timezone.utc
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Windows zone key (``tzutil /g``) -> IANA name. Every pair is CLDR
# common/supplemental/windowsZones.xml's territory="001" mapZone, except India,
# where CLDR keeps the legacy ID "Asia/Calcutta" and this uses the current IANA
# name. tests/test_clock_health.py cross-checks every row's offsets against the
# OS's own registry zone data.
WINDOWS_TO_IANA = {
    "Korea Standard Time": "Asia/Seoul",
    "Tokyo Standard Time": "Asia/Tokyo",
    "China Standard Time": "Asia/Shanghai",
    "India Standard Time": "Asia/Kolkata",
    "UTC": "Etc/UTC",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Central Europe Standard Time": "Europe/Budapest",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Israel Standard Time": "Asia/Jerusalem",
}

# Source names w32time hard-codes in English (UTF-16 literals in w32time.dll and
# vmictimeprovider.dll). They are not in any .mui file, so they are never
# translated: a ko-KR w32tm prints a translated "Source" label, then
# "Local CMOS Clock".
_LOCAL_SOURCES = ("local cmos clock", "free-running system clock")
_HOST_SOURCES = ("vm ic time synchronization provider",)
_LOCL_REFID = "0x4c4f434c"  # ASCII "LOCL": the reference id of the local CMOS clock


def _unknown(detail):
    return {"synced": None, "source": None, "last_sync_utc": None,
            "offset_s": None, "detail": detail}


def _offset(seconds):
    minutes = round(seconds / 60)
    return f"{'+' if minutes >= 0 else '-'}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


class _SystemTime(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint16) for name in (
        "wYear", "wMonth", "wDayOfWeek", "wDay", "wHour", "wMinute", "wSecond", "wMilliseconds")]


class _TimeZoneInformation(ctypes.Structure):
    _fields_ = [("Bias", ctypes.c_long), ("StandardName", ctypes.c_wchar * 32),
                ("StandardDate", _SystemTime), ("StandardBias", ctypes.c_long),
                ("DaylightName", ctypes.c_wchar * 32), ("DaylightDate", _SystemTime),
                ("DaylightBias", ctypes.c_long)]


@functools.lru_cache(maxsize=1)
def _kernel32():
    # A private WinDLL: setting argtypes/restype here never leaks into
    # ctypes.windll.kernel32, which other code in the process shares.
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _bias_offset_seconds(state, bias, standard_bias, daylight_bias):
    """UTC offset (seconds east) from GetTimeZoneInformation's fields.

    [Win32 docs: TIME_ZONE_INFORMATION, member Bias] "UTC = local time + bias";
    StandardBias / DaylightBias: "This value is added to the value of the Bias
    member to form the bias used during standard time" / "... during daylight
    saving time". [GetTimeZoneInformation, Return value] 0 = TIME_ZONE_ID_UNKNOWN
    "Daylight saving time is not used in the current time zone", 1 = STANDARD,
    2 = DAYLIGHT. So offset = -(Bias + the bias of the state in force).
    Cross-checked against zoneinfo for every WINDOWS_TO_IANA row in the tests.
    """
    extra = {1: standard_bias, 2: daylight_bias}.get(state, 0)
    return -60 * (bias + extra)


def _os_offset():
    """The OS's current UTC offset, e.g. "+09:00".

    On Windows this asks the OS, not the C runtime: Python's local time honours a
    TZ environment variable (TZ=UTC0 turns KST into +00:00 on this machine), so
    ``datetime.now().astimezone()`` reports the *process's* zone, not the OS's.
    """
    if os.name == "nt":
        try:
            kernel32 = _kernel32()
            kernel32.GetTimeZoneInformation.restype = ctypes.c_uint32
            info = _TimeZoneInformation()
            state = kernel32.GetTimeZoneInformation(ctypes.byref(info))
            if state != 0xFFFFFFFF:  # TIME_ZONE_ID_INVALID
                return _offset(_bias_offset_seconds(state, info.Bias, info.StandardBias,
                                                    info.DaylightBias))
        except (OSError, AttributeError):
            pass
    return _offset(dt.datetime.now().astimezone().utcoffset().total_seconds())


def _decode(raw):
    if isinstance(raw, str):
        return raw
    # Redirected Windows console utilities use the OEM code page, independent
    # of PYTHONUTF8 and Python's locale encoding.
    encoding = f"cp{ctypes.windll.kernel32.GetOEMCP()}" if os.name == "nt" else "utf-8"
    return raw.decode(encoding, errors="replace")


def _run(args, timeout):
    """Run one read-only child; output goes to temporary files, never pipes.

    On Windows ``subprocess.run(..., timeout=)`` reads the pipes again after it
    kills a timed-out child, and a grandchild that still holds the write end
    (w32tm under cmd.exe) makes that read unbounded: a 1 s timeout took 8.4 s on
    this machine (docs/264). A file has no reader to block, so here the timeout is
    a real bound; a surviving grandchild only writes into a file nobody reads.
    """
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                 creationflags=_NO_WINDOW)
        try:
            code = child.wait(timeout)
        except subprocess.TimeoutExpired:
            child.kill()
            try:
                child.wait(1)
            except subprocess.TimeoutExpired:
                pass
            raise
        out.seek(0)
        err.seek(0)
        return subprocess.CompletedProcess(args, code, out.read(), err.read())


def _order_from_pattern(pattern):
    """0 = month before day, 1 = day before month, None = unknown, from a short-date pattern."""
    pattern = re.sub(r"'[^']*'", "", pattern or "")  # quoted literals are not fields
    day, month = pattern.find("d"), pattern.find("M")
    if day < 0 or month < 0:
        return None
    return 1 if day < month else 0


def _date_order():
    """Day/month order of the user's short-date pattern (LOCALE_SSHORTDATE).

    Only consulted for a year-LAST date, where "03/10/2026" is March or October
    depending on the locale. None when unknown: the caller then refuses the date
    instead of guessing.
    """
    if os.name != "nt":
        return None
    buf = ctypes.create_unicode_buffer(80)
    if not ctypes.windll.kernel32.GetLocaleInfoEx(None, 0x1F, buf, len(buf)):  # LOCALE_SSHORTDATE
        return None
    return _order_from_pattern(buf.value)


def _local_datetime_to_utc(value):
    """A naive OS-local wall time -> aware UTC.

    On Windows this is TzSpecificLocalTimeToSystemTime(NULL, ...): "If
    lpTimeZoneInformation is NULL, the function uses the currently active time
    zone" and it "takes into account whether daylight saving time (DST) is in
    effect for the local time to be converted" [Win32 docs]. Python's own
    conversion would follow a TZ environment variable instead of the OS.
    """
    if os.name == "nt":
        kernel32 = _kernel32()
        local = _SystemTime(value.year, value.month, 0, value.day,
                            value.hour, value.minute, value.second, 0)
        utc = _SystemTime()
        if not kernel32.TzSpecificLocalTimeToSystemTime(None, ctypes.byref(local), ctypes.byref(utc)):
            raise OSError(ctypes.get_last_error(), "TzSpecificLocalTimeToSystemTime failed")
        return dt.datetime(utc.wYear, utc.wMonth, utc.wDay, utc.wHour, utc.wMinute,
                           utc.wSecond, tzinfo=_UTC)
    return value.astimezone(_UTC)


# A short date (year first, or year last with a day/month order) then a time
# whose separator is ":" or "." (fi-FI prints "22.00.00"), with an optional
# English or ko-KR meridiem before or after it. The ko-KR designators are
# escapes because the shipped package is English-only (docs/264).
_MERIDIEM = {"AM": 0, "PM": 12, "\uc624\uc804": 0, "\uc624\ud6c4": 12}
_MERIDIEM_RE = "|".join(_MERIDIEM)
_SYNC_TIME = re.compile(
    r"(\d{1,4})[./-]\s*(\d{1,2})[./-]\s*(\d{1,4})\.?\s+"
    r"(?:(" + _MERIDIEM_RE + r")\s*)?(\d{1,2})[:.](\d{2})[:.](\d{2})(?:\s*(" + _MERIDIEM_RE + r"))?",
    re.IGNORECASE)


def _parse_local_sync(value):
    """w32tm's "Last Successful Sync Time" (OS-local wall time) -> aware UTC, or None."""
    value = value.replace("\u200e", "").replace("\u200f", "").strip()
    match = _SYNC_TIME.fullmatch(value)
    if not match:
        return None  # "unspecified", a translated equivalent, or a format not handled
    a, b, c, before, hour, minute, second, after = match.groups()
    a, b, c, hour = int(a), int(b), int(c), int(hour)
    if a >= 1000:
        year, month, day = a, b, c
    elif c >= 1000:
        order = _date_order()
        if order is None:
            return None
        day, month, year = (a, b, c) if order == 1 else (b, a, c)
    else:
        return None  # Two-digit years are ambiguous.
    meridiem = before or after
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + _MERIDIEM[meridiem.upper()]
    try:
        return _local_datetime_to_utc(dt.datetime(year, month, day, hour, int(minute), int(second)))
    except (ValueError, OverflowError, OSError):
        return None


def _parse_w32tm(output, now):
    """Parse ``w32tm /query /status [/verbose]`` by field POSITION, never by label.

    Labels are translated (the ko-KR table is in docs/264) but every line is
    "<label>: <value>" in one fixed order: leap indicator, stratum, precision,
    root delay, root dispersion, reference id, last successful sync time, source,
    poll interval, then (verbose) phase offset. Each positional value is checked
    against its own shape, so a foreign layout reads as unknown, never as synced.
    """
    try:
        fields = [line.split(":", 1)[1].strip() for line in output.splitlines() if ":" in line]
        if len(fields) < 9 or not re.match(r"^0x[0-9a-fA-F]{8}\b", fields[5]):
            return _unknown("Unrecognized Windows Time status output.")
        leap = re.match(r"^([0-3])\b", fields[0])
        stratum = re.match(r"^(\d+)\b", fields[1])
        if not (leap and stratum and re.match(r"^-?\d+\b", fields[2])
                and re.match(r"^-?\d+\b", fields[8])):
            return _unknown("Unrecognized Windows Time status fields.")
        leap, stratum = int(leap.group(1)), int(stratum.group(1))
        result = _unknown("No parseable successful synchronization time.")
        result["source"] = fields[7] or None
        if len(fields) > 9:
            phase = re.fullmatch(r"([+-]?\d+(?:[.,]\d+)?)\s*s", fields[9])
            if phase:
                result["offset_s"] = float(phase.group(1).replace(",", "."))
        last = _parse_local_sync(fields[6])
        if last is not None:
            result["last_sync_utc"] = last.isoformat().replace("+00:00", "Z")
        source = fields[7].casefold()
        reasons = []
        if any(name in source for name in _LOCAL_SOURCES):
            reasons.append(f"source is the PC's own clock ({fields[7]})")
        if fields[5].lower().startswith(_LOCL_REFID):
            reasons.append("reference id is LOCL (local CMOS clock)")
        if leap == 3:
            reasons.append("leap indicator 3 (not synchronized)")
        if stratum == 0:
            reasons.append("stratum 0 (unspecified)")
        if reasons:
            result.update(synced=False, detail="Not synchronized to a time server: "
                          + "; ".join(reasons) + ".")
        elif any(name in source for name in _HOST_SOURCES):
            result["detail"] = ("Time follows the virtual-machine host; whether the host "
                                "is synchronized is not visible from here.")
        elif last is not None and result["source"]:
            age = now - last.timestamp()
            result["synced"] = 0 <= age <= _MAX_SYNC_AGE
            result["detail"] = ("Successful external synchronization within 24 hours."
                                if result["synced"] else
                                "Successful synchronization is stale or in the future.")
        return result
    except Exception:
        return _unknown("Unrecognized Windows Time status output.")


# One child runs both read-only utilities. Exit markers keep a failed w32tm from
# being mistaken for success merely because tzutil succeeded.
_WINDOWS_COMMAND = ("w32tm /query /status /verbose & echo __SM_W32_EXIT__!errorlevel!"
                    " & tzutil /g & echo __SM_TZ_EXIT__!errorlevel!")


def _windows_probe():
    completed = _run(["cmd.exe", "/d", "/v:on", "/c", _WINDOWS_COMMAND], _PROBE_TIMEOUT_S)
    output = _decode(completed.stdout)
    match = re.fullmatch(r"(.*?)__SM_W32_EXIT__(-?\d+)\s*(.*?)__SM_TZ_EXIT__(-?\d+)\s*", output, re.S)
    if not match:
        return _unknown("Unrecognized Windows clock probe output."), None, None
    text, code, zone, zone_code = match.groups()
    if code == "0":
        ntp = _parse_w32tm(text, time.time())
    else:
        message = " ".join(part for part in (text.strip(), _decode(completed.stderr).strip()) if part)
        ntp = _unknown(f"Windows Time query failed ({code}): {message[:400]}")
    windows_name = zone.strip() if zone_code == "0" and zone.strip() else None
    return ntp, WINDOWS_TO_IANA.get(windows_name), windows_name


def _iana_zone():
    candidate = os.environ.get("TZ", "").lstrip(":")
    if not candidate:
        target = Path("/etc/localtime").resolve().as_posix()
        candidate = target.split("/zoneinfo/", 1)[1] if "/zoneinfo/" in target else ""
    if candidate:
        try:
            ZoneInfo(candidate)
            return candidate
        except (ValueError, KeyError):
            pass
    return None


def _posix_probe():
    iana = _iana_zone()
    ntp = _unknown("No supported time synchronization status provider.")
    if shutil.which("timedatectl"):
        completed = _run(["timedatectl", "show", "--property=NTPSynchronized", "--property=Timezone"], _PROBE_TIMEOUT_S)
        if completed.returncode:
            ntp = _unknown("timedatectl query failed: " + _decode(completed.stderr).strip()[:400])
        else:
            fields = dict(line.split("=", 1) for line in _decode(completed.stdout).splitlines() if "=" in line)
            iana = iana or fields.get("Timezone") or None
            ntp = _unknown("timedatectl reports synchronization=" + fields.get("NTPSynchronized", "unknown")
                           + "; last successful sync time is unavailable.")
            if fields.get("NTPSynchronized") == "no":
                ntp["synced"] = False
    return ntp, iana, None


def status() -> dict:
    """Return cached NTP evidence and the OS's current local UTC offset."""
    global _STATUS_CACHE
    result = {"ntp": _unknown("Clock probe unavailable."),
              "os_zone": {"iana": None, "utc_offset": "+00:00", "windows_name": None}}
    try:
        with _LOCK:
            now = time.monotonic()
            if _STATUS_CACHE is None or now - _STATUS_CACHE[0] >= _CACHE_SECONDS:
                try:
                    ntp, iana, windows_name = _windows_probe() if os.name == "nt" else _posix_probe()
                except Exception as exc:
                    ntp, iana, windows_name = _unknown(f"Clock probe failed: {type(exc).__name__}: {exc}"), None, None
                _STATUS_CACHE = (time.monotonic(), {"ntp": ntp, "os_zone": {
                    "iana": iana, "utc_offset": "+00:00", "windows_name": windows_name}})
            result = copy.deepcopy(_STATUS_CACHE[1])
        # A cached "synced" ages: 24 h is measured at every call, not at probe time.
        if result["ntp"]["synced"] is True:
            last = dt.datetime.fromisoformat(result["ntp"]["last_sync_utc"].replace("Z", "+00:00"))
            if not 0 <= time.time() - last.timestamp() <= _MAX_SYNC_AGE:
                result["ntp"].update(synced=False, detail="Successful synchronization is stale or in the future.")
        result["os_zone"]["utc_offset"] = _os_offset()
    except Exception as exc:
        result["ntp"] = _unknown(f"Clock status unavailable: {type(exc).__name__}: {exc}")
    return result


_ENV_SCRIPT = ("import datetime,json,time; "
               "d=datetime.datetime.now().astimezone(); "
               "print(json.dumps({'epoch':time.time(),'offset_s':d.utcoffset().total_seconds(),"
               "'tzname':d.tzname()}))")


def _matches(delta, utc_offset):
    return delta is not None and abs(delta) <= _ENV_MAX_DELTA_S and utc_offset == _os_offset()


def env_time_check(python_exe: str) -> dict:
    """Compare an interpreter's clock and zone with the OS; errors fail the check.

    epoch_delta_s is the child's time.time() minus SM's time.time() when the child
    has exited. Startup is excluded (the child samples just before printing);
    the child's exit latency (~25 ms measured) is included, biasing it negative.
    The child inherits SM's environment, so a TZ variable SM was started with
    shows up here as an offset that differs from the OS.
    """
    result = {"python": python_exe, "epoch_delta_s": None, "utc_offset": None,
              "matches_os": False, "detail": "Environment clock probe unavailable."}
    try:
        with _ENV_LOCK:
            cached = _ENV_CACHE.get(python_exe)
            if cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
                result = copy.deepcopy(cached[1])
                result["matches_os"] = _matches(result["epoch_delta_s"], result["utc_offset"])
                return result
            try:
                completed = _run([python_exe, "-c", _ENV_SCRIPT], 20)
                received = time.time()
                if completed.returncode:
                    raise ValueError("Interpreter exited with code " + str(completed.returncode))
                # The payload is the last line: a sitecustomize banner may precede it.
                lines = _decode(completed.stdout).strip().splitlines()
                payload = json.loads(lines[-1] if lines else "")
                epoch, offset = float(payload["epoch"]), float(payload["offset_s"])
                if not math.isfinite(epoch) or not math.isfinite(offset) or abs(offset) >= 86400:
                    raise ValueError("Invalid interpreter clock values")
                result["epoch_delta_s"] = epoch - received
                result["utc_offset"] = _offset(offset)
                result["matches_os"] = _matches(result["epoch_delta_s"], result["utc_offset"])
                result["detail"] = f"Interpreter tzname={payload['tzname']!r}; epoch sampled before receipt."
            except Exception as exc:
                result["detail"] = f"Environment clock probe failed: {type(exc).__name__}: {exc}"
            _ENV_CACHE[python_exe] = (time.monotonic(), copy.deepcopy(result))
    except Exception as exc:
        result["matches_os"] = False
        result["detail"] = f"Environment clock check unavailable: {type(exc).__name__}: {exc}"
    return result
