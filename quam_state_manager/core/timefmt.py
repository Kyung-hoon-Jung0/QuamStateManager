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
from datetime import datetime, timezone
from typing import Any

_STAMP = re.compile(r"^(\d{4})(\d{2})(\d{2})[_\- ]?(\d{2})(\d{2})(\d{2})")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


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


def local_text(d: datetime | None = None) -> str:
    """``YYYY-MM-DD HH:MM:SS (UTC+9)`` in THIS machine's zone -- for pages no
    script will localize (the printable report). The same form the browser
    shows (SnapTime.display), so a printout and the screen agree."""
    d = (d or datetime.now(timezone.utc)).astimezone()
    return d.strftime("%Y-%m-%d %H:%M:%S") + f" ({offset_label(d)})"
