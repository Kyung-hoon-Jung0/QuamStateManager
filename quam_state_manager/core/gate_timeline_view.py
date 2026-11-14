"""The data of the gate timeline chart, from one gate's check result (pure).

:func:`timeline_data` turns a gate entry of :func:`core.gate_check.check_gates`
into the ``data`` that ``window.GateTimeline.render`` draws::

    {"lanes": [{"key", "group": "gate" | "spectator", "label", "port",
                "limit_v": [lo, hi] | None, "offset_v",
                "segments": [{"operation", "start_ns", "end_ns", "t_ns", "v",
                              "peak_v", "min_v", "length_ns", "truncated",
                              "flags"}],
                "sim": None}],
     "notes": [str], "cannot_draw": None | str}

Rules:

- lanes are ordered control z, target z, coupler, then every other element
  (the spectators) in the order they first play; the first three are the
  ``gate`` group;
- every sample is placed at ``start_ns + i * step`` where ``step`` is the
  port's sampling period (1 ns at 1 GS/s), times the play scale; a constant
  waveform is two points (start and end);
- ``limit_v`` is the port's output range with the channel offset taken off
  (``[-range - offset, range - offset]``), or None when the range is unknown;
- ``over_range``: a value plus the offset outside the port's range;
  ``bad_length``: the played length is not a whole number of ns, not a
  multiple of 4 ns or below 16 ns (the gate check's length rule);
- ``notes`` and ``cannot_draw`` of the check are passed through verbatim;
  when ``cannot_draw`` is set there are NO lanes.

Pure: no I/O, no QM-stack import.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from quam_state_manager.core.gate_check import LENGTH_MIN_NS, LENGTH_STEP_NS

#: the numeric slack of the range comparison (the gate check's own)
from quam_state_manager.core.diagnostics import _SAMPLE_EPS as RANGE_EPS

NOT_CHECKED = "cannot draw: the gate was not checked with the lab's code, so there is no config to draw"


def _port_text(port: Any) -> str | None:
    if port is None:
        return None
    if isinstance(port, (list, tuple)):
        return " / ".join(str(p) for p in port)
    return str(port)


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def sample_step_ns(rate: Any) -> float:
    """The sample period in ns of a port at *rate* samples/s (1 ns when the
    config does not state it)."""
    r = _num(rate)
    return 1e9 / r if r and r > 0 else 1.0


def length_flag(length: Any) -> bool:
    """True when *length* (ns) breaks the gate check's length rule."""
    f = _num(length)
    if f is None or f != int(f):
        return True
    n = int(f)
    return n % LENGTH_STEP_NS != 0 or n < LENGTH_MIN_NS


def _segment(row: dict) -> dict:
    scale = _num(row.get("scale"))
    scale = 1.0 if scale is None else scale
    start, end = row.get("start_ns"), row.get("end_ns")
    step = sample_step_ns(row.get("sampling_rate"))
    t: list[float] = []
    v: list[float] = []
    if row.get("constant") is not None:
        c = float(row["constant"]) * scale
        t, v = [float(start), float(end)], [c, c]
    elif isinstance(row.get("samples"), list):
        v = [float(x) * scale for x in row["samples"]]
        t = [float(start) + i * step for i in range(len(v))]
    peak = max(v) if v else None
    low = min(v) if v else None
    played = (end - start) if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None
    flags = []
    rng, off = _num(row.get("range_v")), _num(row.get("offset_v"))
    if rng is not None and v:
        o = off or 0.0
        if peak + o > rng + RANGE_EPS or low + o < -rng - RANGE_EPS:
            flags.append("over_range")
    if played is None or length_flag(played):
        flags.append("bad_length")
    return {"operation": row.get("operation"), "start_ns": start, "end_ns": end,
            "t_ns": t, "v": v, "peak_v": peak, "min_v": low, "length_ns": played,
            "truncated": bool(row.get("truncated")), "flags": flags}


def _limit(row: dict) -> list[float] | None:
    rng, off = _num(row.get("range_v")), _num(row.get("offset_v"))
    if rng is None or off is None:
        return None
    return [-rng - off, rng - off]


def timeline_data(entry: dict | None, *, control: str | None, target: str | None,
                  coupler_elements: Iterable[str] = ()) -> dict:
    """The chart data of one gate entry (see the module docstring).

    *coupler_elements*: the element names that are the pair's coupler (the
    elements the gate's coupler pulses play on)."""
    tl = (entry or {}).get("timeline")
    if not isinstance(tl, dict):
        return {"lanes": [], "notes": [], "cannot_draw": NOT_CHECKED}
    notes = list(tl.get("notes") or [])
    if tl.get("cannot_draw"):
        return {"lanes": [], "notes": notes, "cannot_draw": tl["cannot_draw"]}
    gate_order = [e for e in (f"{control}.z" if control else None,
                              f"{target}.z" if target else None) if e]
    couplers = [e for e in coupler_elements if e and e not in gate_order]
    lanes: dict[str, dict] = {}
    seen: list[str] = []
    for row in tl.get("rows") or []:
        el = row.get("element")
        if el not in lanes:
            seen.append(el)
            lanes[el] = {"key": el, "group": "gate" if (el in gate_order or el in couplers)
                         else "spectator", "label": el, "port": _port_text(row.get("port")),
                         "limit_v": _limit(row), "offset_v": _num(row.get("offset_v")) or 0.0,
                         "segments": [], "sim": None}
        lane = lanes[el]
        if lane["limit_v"] is None:
            lane["limit_v"] = _limit(row)
        lane["segments"].append(_segment(row))
    order = ([e for e in gate_order if e in lanes] + [e for e in couplers if e in lanes]
             + [e for e in seen if e not in gate_order and e not in couplers])
    return {"lanes": [lanes[e] for e in order], "notes": notes, "cannot_draw": None}
