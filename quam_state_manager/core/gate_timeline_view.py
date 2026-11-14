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
NOT_RUN = "cannot draw: the gate does not run with the lab's code, so there is no config to draw"


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
        checks = (entry or {}).get("checks") or {}
        ran_but_failed = any((checks.get(c) or {}).get("status") == "fail" for c in ("load_config", "ops"))
        return {"lanes": [], "notes": [], "cannot_draw": NOT_RUN if ran_but_failed else NOT_CHECKED}
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
    out = [lanes[e] for e in order]
    for i, ln in enumerate(out):
        ln["color_index"] = i          # the lane's identity colour (block, lane, table dot)
        if ln["group"] == "gate":
            ln["color"] = i            # the timeline component draws this index
    return {"lanes": out, "notes": notes, "cannot_draw": None}


def fmt_num(v: float) -> str:
    """The shortest exact form a person reads: 12 significant digits, no trailing
    zeros (0.30000000000000004 -> 0.3; 0.1001 stays 0.1001)."""
    return repr(float(f"{float(v):.12g}")).removesuffix(".0")


def _fmt_v(v: float) -> str:
    return f"{fmt_num(v)} V"


def tiles(data: dict) -> list[dict]:
    """The key numbers of a checked gate, at most four, from :func:`timeline_data`
    (pure; the dialog never computes or splits them). Each tile is ``{"key",
    "label", "value", "unit", "note", "tone"}``: a number, its unit and one
    short quiet line; tone ``neutral`` | ``note`` | ``warn`` | ``danger``:

    - ``lines``: the number of gate lines that play (note: their names);
    - ``length``: latest end minus earliest start over the gate's lines, ns;
    - ``peak``: the highest absolute level to 4 significant digits, V (note:
      the line that reaches it), ``danger`` when a segment is over its limit;
    - ``timing`` (two or more lines only): the smallest gap in ns, ``warn``,
      "gap, no overlap" when no two lines overlap; else the start difference
      in ns, ``note``, "start difference, the lines overlap", or 0 ns,
      ``neutral``, "same start".

    Nothing is invented: with no drawn gate line the list is empty."""
    lanes = [ln for ln in (data or {}).get("lanes") or []
             if ln.get("group") == "gate" and ln.get("segments")]
    if not lanes:
        return []
    spans = [(ln["key"], min(s["start_ns"] for s in ln["segments"]),
              max(s["end_ns"] for s in ln["segments"])) for ln in lanes]
    out = [{"key": "lines", "label": "Lines", "value": str(len(lanes)), "unit": "",
            "note": ", ".join(k for k, _a, _b in spans), "tone": "neutral"}]
    t0, t1 = min(a for _k, a, _b in spans), max(b for _k, _a, b in spans)
    out.append({"key": "length", "label": "Gate length", "value": f"{t1 - t0:g}", "unit": "ns",
                "note": f"{t0:g} to {t1:g} ns", "tone": "neutral"})
    best, where, over, over_off = None, None, False, 0.0
    for ln in lanes:
        for s in ln["segments"]:
            for v in (s.get("peak_v"), s.get("min_v")):
                if v is not None and (best is None or abs(v) > abs(best)):
                    best, where = v, ln["key"]
            if "over_range" in (s.get("flags") or []):
                over, over_off = True, float(ln.get("offset_v") or 0.0)
    if best is not None:
        out.append({"key": "peak", "label": "Highest level", "value": f"{best:.4g}", "unit": "V",
                    "note": f"on {where}" + ((", over the port's range" + (f" (with the {fmt_num(over_off)} V offset)" if over_off else "")) if over else ""),
                    "tone": "danger" if over else "neutral"})
    if len(spans) >= 2:
        ordered = sorted(spans, key=lambda x: x[1])
        overlap = any(a[2] > b[1] for i, a in enumerate(ordered) for b in ordered[i + 1:])
        starts = [a for _k, a, _b in spans]
        if not overlap:
            gap = min(b[1] - a[2] for a, b in zip(ordered, ordered[1:]))
            out.append({"key": "timing", "label": "Timing", "value": f"{gap:g}", "unit": "ns",
                        "note": "gap, no overlap", "tone": "warn"})
        elif max(starts) != min(starts):
            out.append({"key": "timing", "label": "Timing", "value": f"{max(starts) - min(starts):g}",
                        "unit": "ns", "note": "start difference, the lines overlap", "tone": "note"})
        else:
            out.append({"key": "timing", "label": "Timing", "value": "0", "unit": "ns",
                        "note": "same start", "tone": "neutral"})
    return out
