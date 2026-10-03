"""One reading of every timestamp the app shows (docs/244).

A timestamp reaches a page in four spellings:

* SM's own snapshot stamp ``YYYYMMDD_HHMMSS[_ffffff]`` -- written in UTC
  (``history._now_ts``);
* an ISO-8601 string WITH an offset -- QUAlibrate's ``node.json``
  (``metadata.run_start = "2026-09-30T19:55:46.424+09:00"``) or a ``Z``;
* an ISO-8601 string WITHOUT one -- SM's own UTC records;
* epoch seconds.

The display filters used to read only the first 19 characters of an ISO
string and append ``Z``, so ``19:55:46+09:00`` became ``19:55:46 UTC`` and every
viewer's clock showed the run nine hours late. :func:`to_utc` keeps the
offset: it returns the INSTANT, in UTC, or ``None`` when the text is not a
time (the caller then shows it as written -- never a guessed time).
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any

_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})[_\- ]?(\d{2})(\d{2})(\d{2})")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")

# Product decision: ask once per project at or above thirty minutes (docs/256).
SKEW_ASK_S = 30 * 60
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _run_dates(node_json: Any) -> tuple[datetime | None, datetime | None]:
    node = node_json if isinstance(node_json, Mapping) else {}
    metadata = node.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    return _run_iso(node.get("created_at")), _run_iso(metadata.get("run_end"))


def _run_iso(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not _ISO.match(text):
        return None
    try:
        return datetime.fromisoformat(
            text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text)
    except ValueError:
        return None


def _folder_clock(folder: Any) -> datetime | None:
    if folder is None:
        return None
    try:
        path = Path(folder)
        match = re.search(r"_([0-9]{6})$", path.name)
        if match is None or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", path.parent.name):
            return None
        return datetime.strptime(path.parent.name + match[1], "%Y-%m-%d%H%M%S")
    except (TypeError, ValueError):
        return None


def _hint_zone(offset_hint: str | None) -> tzinfo | None:
    if offset_hint in ("Z", "z"):
        return timezone.utc
    if not isinstance(offset_hint, str):
        return None
    match = re.fullmatch(r"([+-])([0-9]{2}):([0-9]{2})(?::([0-9]{2})(\.[0-9]{1,6})?)?", offset_hint)
    if match is None:
        return None
    sign, hours, minutes, seconds, fraction = match.groups()
    if int(hours) >= 24 or int(minutes) >= 60 or int(seconds or 0) >= 60:
        return None
    delta = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds or 0),
                      microseconds=int((fraction or ".")[1:].ljust(6, "0")))
    return timezone(delta if sign == "+" else -delta)


def _utc_us(date: datetime) -> int | None:
    try:
        delta = date.astimezone(timezone.utc) - _EPOCH
    except (ValueError, OverflowError, OSError):
        return None
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def run_instant(node_json: Any, folder: Any = None, *,
                offset_hint: str | None = None, local_tz: tzinfo | None = None) -> tuple[int | None, str]:
    """Return a run's UTC epoch microseconds and evidence quality (docs/256).

    Product decision: prefer aware created_at, then aware metadata.run_end;
    otherwise prefer naive created_at, naive run_end, then the folder clock.
    Product decision: interpret naive evidence in the archive offset hint,
    else local_tz (the machine's zone by default), and label that assumption.
    [derived] An explicit ISO offset defines an instant independently of the
    server's zone; integer epoch arithmetic preserves every microsecond.
    [derived] Folder digits provide only a wall clock, not a timezone.
    Malformed sources fall through; no usable source returns (None, "none").
    This pure function performs no filesystem reads.
    """
    dates = _run_dates(node_json)
    for date in dates:
        if date is not None and date.utcoffset() is not None:
            instant = _utc_us(date)
            if instant is not None:
                return instant, "offset"
    zone = _hint_zone(offset_hint)
    quality = "archive_offset" if zone is not None else "assumed_local"
    for date in (*dates, _folder_clock(folder)):
        if date is None or date.tzinfo is not None:
            continue
        instant = _utc_us(date.replace(tzinfo=zone if zone is not None else local_tz))
        if instant is not None:
            return instant, quality
    return None, "none"


def archive_offset_hint(node_jsons: Iterable[Any]) -> str | None:
    """Return the most frequent run offset, or None for a tie/empty evidence.

    Product decision: each run gets one vote, preferring aware created_at
    over aware metadata.run_end, and the unique largest count supplies the
    archive hint. A tie supplies no hint, even if some offsets are present.
    [derived] Z and +00:00 denote the same offset and must share a vote;
    numeric offsets are normalized without consulting the server's zone.
    """
    offsets: Counter[str] = Counter()
    for node in node_jsons:
        for date in _run_dates(node):
            if date is not None and date.utcoffset() is not None:
                label = date.strftime("%z")
                label = label[:3] + ":" + label[3:5] + (
                    ":" + label[5:] if len(label) > 5 else "")
                offsets[label] += 1
                break
    ranked = offsets.most_common(2)
    if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def run_witnesses(node_json: Any, folder_mtime_utc_us: int | None = None,
                  first_seen_utc_us: int | None = None, *,
                  offset_hint: str | None = None, local_tz: tzinfo | None = None) -> dict:
    """Return node_utc_us, node_quality, folder mtime, first seen, and skew.

    The two supplied witnesses are already UTC epoch microseconds; the
    node's own witness comes from run_instant without a folder fallback.
    [derived] The largest absolute pairwise skew is (max - min) / 1e6;
    fewer than two available witnesses cannot establish skew.
    Product decision: skew below SKEW_ASK_S is "small"; at or above it is
    "ask"; absent skew is "none". SKEW_ASK_S = 30 * 60 means SM never asks
    below thirty minutes and asks once per project at/above that boundary.
    This function only classifies; S1 must implement the persisted ask policy.
    """
    instant, quality = run_instant(node_json, offset_hint=offset_hint, local_tz=local_tz)
    available = [value for value in (instant, folder_mtime_utc_us, first_seen_utc_us)
                 if value is not None]
    skew_s = (max(available) - min(available)) / 1_000_000 if len(available) >= 2 else None
    skew_class = "none" if skew_s is None else "ask" if skew_s >= SKEW_ASK_S else "small"
    return {"node_utc_us": instant, "node_quality": quality,
            "folder_mtime_utc_us": folder_mtime_utc_us, "first_seen_utc_us": first_seen_utc_us,
            "skew_s": skew_s, "skew_class": skew_class}


def to_utc(ts: Any) -> datetime | None:
    """The instant *ts* names, as an aware UTC datetime, or ``None``."""
    if ts is None or isinstance(ts, bool):
        return None
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, datetime):
        return (ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    s = str(ts).strip()
    if _ISO.match(s):
        txt = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
        try:
            d = datetime.fromisoformat(txt.replace(" ", "T", 1))
        except ValueError:
            d = None
        if d is not None:
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)      # SM's own naive ISO is UTC
            return d.astimezone(timezone.utc)
    m = _STAMP.match(s)
    if m:
        y, mo, d_, h, mi, se = (int(g) for g in m.groups())
        try:
            return datetime(y, mo, d_, h, mi, se, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def iso_z(d: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` -- what a ``data-utc`` attribute carries."""
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_text(d: datetime, short: bool = False) -> str:
    """The plain-text UTC form (attribute sites and the no-JS fallback)."""
    d = d.astimezone(timezone.utc)
    return d.strftime("%m-%d %H:%M") if short else d.strftime("%Y-%m-%d %H:%M:%S UTC")


def offset_label(d: datetime) -> str:
    """``UTC``, ``UTC+9``, ``UTC-4``, ``UTC+5:30`` for an aware datetime."""
    off = d.utcoffset()
    mins = int(off.total_seconds() // 60) if off is not None else 0
    if not mins:
        return "UTC"
    sign = "-" if mins < 0 else "+"
    h, m = divmod(abs(mins), 60)
    return f"UTC{sign}{h}" + (f":{m:02d}" if m else "")


def local_text(d: datetime | None = None, zone: str | None = None) -> str:
    """``YYYY-MM-DD HH:MM:SS (UTC+9)`` in the project's display zone (docs/263;
    an IANA name), else THIS machine's zone -- for pages no script will
    localize (the printable report). The same form the browser shows
    (SnapTime.display), so a printout and the screen agree."""
    tz = None
    if zone:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(zone)
        except Exception:  # noqa: BLE001 -- no tzdata / not a zone: the machine's
            tz = None
    d = (d or datetime.now(timezone.utc)).astimezone(tz)
    return d.strftime("%Y-%m-%d %H:%M:%S") + f" ({offset_label(d)})"
