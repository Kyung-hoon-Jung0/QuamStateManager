"""The shareable chip report: its sections, and the pieces of it that are not
Flask (docs/277).

``SECTIONS`` is the one registry the panel, the section route, the download
whitelist and the server's finalize step all read. A section that is not
``available`` (the calibration log, until the hub ledger lands -- docs/277
section 7) is listed, disabled, and refused everywhere else.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from quam_state_manager.core.report_redact import Redactor


@dataclass(frozen=True)
class Section:
    key: str
    label: str
    desc: str
    default: bool = True
    available: bool = True
    note: str = ""


SECTIONS: tuple[Section, ...] = (
    Section("overview", "Overview",
            "Chip name, SM version, when the file was made (project time zone), "
            "source folder and entity counts."),
    Section("chip_status", "Chip Status",
            "The component map and every component table: qubits, readout, flux, "
            "QDAC, ports, pairs, RB, 2Q gates, couplers."),
    Section("trends", "Trends",
            "Chip Status trends: one chart per curated metric with history, one "
            "line per qubit, over the window below."),
    Section("pulses", "Pulses",
            "The Pulses table: every pulse with its waveform thumbnail, length, "
            "amplitude and how many operations use it."),
    Section("zline", "Z-line distortion",
            "Every flux line's output filters, and the step response through "
            "exponential + FIR together."),
    Section("wiring", "Instrument wiring",
            "The rack diagram and a port table: which element is cabled to which "
            "port, with band, LO and power."),
    Section("diagnostics", "Diagnostics",
            "Every Diagnostics finding: severity, domain, location and message."),
    Section("calibration_log", "Calibration log",
            "Run-by-run record of what each calibration wrote.",
            default=False, available=True),
    Section("raw", "Raw state tree",
            "state.json and wiring.json as a collapsible tree, pointer strings as "
            "stored. The whole chip; off by default. "
            "Contains the chip name even when Overview is unchecked.",
            default=False),
)

SECTION_BY_KEY = {s.key: s for s in SECTIONS}
AVAILABLE_KEYS = tuple(s.key for s in SECTIONS if s.available)
DEFAULT_KEYS = tuple(s.key for s in SECTIONS if s.available and s.default)

#: The trends window choices (value, label). Cropping the series the screen
#: draws, exactly like zooming its chart -- the points are not recomputed.
WINDOWS: tuple[tuple[str, str], ...] = (
    ("all", "All history"), ("365", "Last 365 days"), ("90", "Last 90 days"),
    ("30", "Last 30 days"), ("7", "Last 7 days"))
DEFAULT_WINDOW = "all"

#: Raw tree: plain JSON up to this many bytes (searchable in the file), gzip +
#: base64 above it (a 30-qubit chip's state is ~9 MB minified, ~1.5 MB packed).
RAW_PLAIN_MAX = 1_500_000


def parse_sections(arg: str | None) -> list[str]:
    """``?sections=`` -> the available keys it names, in registry order.
    Absent -> the defaults; present but empty -> nothing."""
    if arg is None:
        return list(DEFAULT_KEYS)
    want = {s.strip() for s in str(arg).split(",") if s.strip()}
    return [k for k in AVAILABLE_KEYS if k in want]


def parse_window(arg: str | None) -> str:
    v = str(arg or "").strip()
    return v if v in dict(WINDOWS) else DEFAULT_WINDOW


def window_start(window: str, now: datetime | None = None) -> float | None:
    """Epoch seconds of the window's start, ``None`` for all history."""
    if window == "all" or not str(window).isdigit():
        return None
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=int(window))).timestamp()


def window_label(window: str) -> str:
    return dict(WINDOWS).get(window, "All history")


def parse_redact(arg: str | None) -> bool:
    """The switch defaults ON: only an explicit ``0`` turns it off."""
    return str(arg if arg is not None else "1").strip() != "0"


# --------------------------------------------------------------------- raw tree
_SCRIPT_ESC = {ord("<"): "\\u003c", ord(">"): "\\u003e", ord("&"): "\\u0026",
               0x2028: "\\u2028", 0x2029: "\\u2029"}


def _script_safe(text: str) -> str:
    """JSON inside ``<script type="application/json">``: ``<``, ``>`` and ``&``
    become six-character ``u003c``-style JSON escapes (``JSON.parse`` returns
    the original characters; the HTML tokeniser sees none of them -- what the
    app's ``script_json`` filter does), and U+2028/2029 are escaped too."""
    return text.translate(_SCRIPT_ESC)


def raw_payload(state: Any, wiring: Any, redactor: Redactor | None = None,
                *, plain_max: int = RAW_PLAIN_MAX) -> dict:
    """The raw section's data: ``{"enc", "text", "json_bytes"}``.

    The documents are taken as stored (pointers unresolved). With a
    *redactor* they are redacted STRUCTURALLY first (docs/277 section 3), so
    nothing in the packed form needs a text pass later."""
    doc = {"state.json": state, "wiring.json": wiring}
    if redactor is not None:
        doc = redactor.redact_tree(doc)
    text = "".join(json.JSONEncoder(separators=(",", ":"), ensure_ascii=False).iterencode(doc))
    raw = text.encode("utf-8")
    if len(raw) <= plain_max:
        return {"enc": "json", "text": _script_safe(text), "json_bytes": len(raw)}
    packed = base64.b64encode(gzip.compress(raw, compresslevel=6, mtime=0)).decode("ascii")
    return {"enc": "gzip-base64", "text": packed, "json_bytes": len(raw)}


def decode_raw_payload(enc: str, text: str) -> Any:
    """The inverse of :func:`raw_payload` (tests and fault injection)."""
    if enc == "gzip-base64":
        return json.loads(gzip.decompress(base64.b64decode(text)).decode("utf-8"))
    return json.loads(text)


# ------------------------------------------------------------------ sparklines
_SPARK_RE = re.compile(r'^<svg class="pulse-spark" viewBox="([^"]+)"([^>]*)>(.*)</svg>$', re.S)


def dedupe_sparks(svgs: Iterable[str | None], prefix: str = "rsp") -> tuple[str, list]:
    """Store each distinct thumbnail ONCE: returns ``(defs_html, refs)`` where
    ``refs[i]`` replaces ``svgs[i]`` with a ``<use>`` of its ``<symbol>``.

    A big library repeats the same waveform hundreds of times (every qubit's
    copy of one calibrated pulse); the picture is identical, so the file holds
    it once. Anything not in the sparkline's own shape is passed through."""
    ids: dict[str, str] = {}
    symbols: list[str] = []
    refs: list = []
    for svg in svgs:
        m = _SPARK_RE.match(svg) if isinstance(svg, str) else None
        if not m:
            refs.append(svg)
            continue
        view, attrs, inner = m.groups()
        key = view + "\0" + inner
        sid = ids.get(key)
        if sid is None:
            sid = f"{prefix}{len(ids)}"
            ids[key] = sid
            symbols.append(f'<symbol id="{sid}" viewBox="{view}" preserveAspectRatio="none">'
                           f'{inner}</symbol>')
        refs.append(f'<svg class="pulse-spark" viewBox="{view}"{attrs}>'
                    f'<use href="#{sid}"/></svg>')
    defs = ""
    if symbols:
        defs = ('<svg class="pulse-spark rep-spark-defs" width="0" height="0" aria-hidden="true"'
                ' style="position:absolute;width:0;height:0;overflow:hidden">'
                + "".join(symbols) + "</svg>")
    return defs, refs


def failure_html(exc: BaseException | str, *, redact: bool = False) -> str:
    """The one honest line a section shows when its source raised."""
    import html as _html
    if redact:
        kind = type(exc).__name__ if isinstance(exc, BaseException) else "Error"
        why = f"{kind}: the section source failed."
    elif isinstance(exc, OSError) and exc.strerror:
        why = f"{type(exc).__name__}: {exc.strerror}"
        if exc.filename:
            why += f": {exc.filename}"
        if exc.filename2:
            why += f": {exc.filename2}"
    else:
        why = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return (f'<p class="rep-fail" role="alert">Could not be built: '
            f'{_html.escape(str(why))}</p>')
