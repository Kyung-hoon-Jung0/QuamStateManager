"""Intended against actual, per played pulse of a new gate (pure).

:func:`intent_rows` compares what the person asked for (the planned pulse:
its length and amplitude, with where each value came from) with what the
lab's config holds for the operation (the config length, the maximum and the
minimum of its samples, the play scale of the program). Each row::

    {"field", "operation", "channel",
     "intended": {"length": int | None, "length_source": str,
                  "amplitude": float | None, "amplitude_source": str},
     "actual": {"length_ns", "max_v", "min_v", "scale", "truncated", "total"} | None,
     "differs": {"length": bool, "amplitude": bool},
     "why": [str]}

``*_source`` is ``given`` / ``copied`` / ``default`` (the plan's field
provenance) or ``set by the class`` when the pulse has no such value of its
own (an inferred length, a class without ``amplitude``). ``actual`` is None
when the check did not place the operation. ``why`` says in plain words
where a difference comes from: the class computed the length, padding, the
play scale, a negative amplitude, the shape of the class, or that only part
of the samples was received.

Pure: no I/O, no QM-stack import.
"""
from __future__ import annotations

import math
from typing import Any

BY_CLASS = "set by the class"

#: relative slack of the amplitude comparison
AMP_TOL = 1e-6

WHY_CLASS_LENGTH = "The class computed the length from its other fields."
WHY_PADDING = "The class adds padding to the length."
WHY_SCALE = "The program plays the pulse with a scale of {k:g}, so the line reaches {v:+.4g} V."
WHY_NEGATIVE = "The amplitude is negative, so the extreme of the waveform is its minimum."
WHY_SHAPE = "The class shapes the waveform, so its extreme is not the amplitude."
WHY_PART = "Only the first {n} of {total} samples were received; the maximum and minimum cover those."
WHY_DELAY = "It starts at {t} ns: the port delay of {d} ns moves it."
WHY_NOT_PLAYED ="The check did not place this pulse, so there is nothing to compare."

_PADDING_FIELDS = ("post_zero_padding_length", "padding", "zero_padding_length")


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


def planned_pulse(plan: dict, field: str) -> dict | None:
    """The pulse dict the plan writes for *field* (the macro's own pulse, or
    the operation it names)."""
    creates = plan.get("creates") or []
    if not creates:
        return None
    macro = creates[0][1] if isinstance(creates[0][1], dict) else {}
    own = macro.get(field)
    if isinstance(own, dict):
        return own
    op = (plan.get("operations") or {}).get(field) or {}
    for path, value in creates[1:]:
        if path == op.get("path") and isinstance(value, dict):
            return value
    return None


def _intended(pulse: dict, prov: dict) -> dict:
    length, lsrc = None, BY_CLASS
    raw = pulse.get("length")
    if _num(raw) is not None:
        length, lsrc = int(raw), prov.get("length", "default")
    else:
        for k in ("pulse_length",):
            if _num(pulse.get(k)) is not None:
                length, lsrc = int(pulse[k]), prov.get(k, "default")
                break
    amp, asrc = None, BY_CLASS
    if _num(pulse.get("amplitude")) is not None:
        amp, asrc = float(pulse["amplitude"]), prov.get("amplitude", "default")
    return {"length": length, "length_source": lsrc, "amplitude": amp,
            "amplitude_source": asrc, "_inferred": not isinstance(raw, (int, float))}


def _actual(rows: list, key: str) -> dict | None:
    for r in rows:
        if r.get("operation") != key:
            continue
        vals = ([r["constant"]] if r.get("constant") is not None
                else list(r.get("samples") or []))
        return {"length_ns": r.get("length_ns"),
                "max_v": max(vals) if vals else None,
                "min_v": min(vals) if vals else None,
                "scale": _num(r.get("scale")) if _num(r.get("scale")) is not None else 1.0,
                "truncated": bool(r.get("truncated")),
                "total": r.get("total"), "received": len(vals),
                "start_ns": r.get("start_ns"), "delay_ns": r.get("delay_ns"),
                "limit_v": r.get("range_v"), "offset_v": r.get("offset_v")}
    return None


def _over_side(have: dict) -> tuple[bool, bool]:
    """(maximum above the upper limit, minimum below the lower limit), with
    the play scale and the channel offset applied."""
    lim, k = _num(have.get("limit_v")), have.get("scale") or 1.0
    off = _num(have.get("offset_v")) or 0.0
    if lim is None or have.get("max_v") is None:
        return False, False
    vals = [have["max_v"] * k + off, have["min_v"] * k + off]
    return max(vals) > lim + 1e-9, min(vals) < -lim - 1e-9


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= AMP_TOL * max(1.0, abs(a), abs(b))


def intent_rows(plan: dict, entry: dict | None) -> list[dict]:
    """One row per pulse the plan creates (see the module docstring)."""
    rows = (((entry or {}).get("timeline") or {}).get("rows")) or []
    out = []
    for field, op in (plan.get("operations") or {}).items():
        pulse = planned_pulse(plan, field)
        if not isinstance(pulse, dict):
            continue
        prov = (plan.get("fields") or {}).get(field) or {}
        want = _intended(pulse, prov)
        have = _actual(rows, op.get("key"))
        differs = {"length": False, "amplitude": False}
        why: list[str] = []
        if have is None:
            why.append(WHY_NOT_PLAYED)
        else:
            cfg_len = _num(have["length_ns"])
            if want["length"] is not None and cfg_len is not None and cfg_len != want["length"]:
                differs["length"] = True
                pads = [k for k in _PADDING_FIELDS if (_num(pulse.get(k)) or 0) > 0]
                why.append(WHY_PADDING if pads else WHY_CLASS_LENGTH)
            elif want["length"] is None and want["_inferred"]:
                why.append(WHY_CLASS_LENGTH)
            a = want["amplitude"]
            extreme = None
            if a is not None and have["max_v"] is not None:
                extreme = have["min_v"] if a < 0 else have["max_v"]
                if a < 0:
                    why.append(WHY_NEGATIVE)
                if not _close(extreme, a):
                    differs["amplitude"] = True
                    why.append(WHY_SHAPE)
            k = have["scale"]
            if k != 1.0 and extreme is not None:
                differs["amplitude"] = True
                why.append(WHY_SCALE.format(k=k, v=extreme * k))
            if have["truncated"]:
                why.append(WHY_PART.format(n=have["received"], total=have["total"]))
            if (have.get("delay_ns") or 0) > 0:
                why.append(WHY_DELAY.format(t=have["start_ns"], d=have["delay_ns"]))
            have = {k2: v for k2, v in have.items() if k2 != "received"}
        want.pop("_inferred", None)
        tones = {"length": "warn" if differs["length"] else None,
                 "amplitude": "warn" if differs["amplitude"] else None,
                 "max": None, "min": None}
        if have is not None:
            hi, lo = _over_side(have)
            tones["max"] = "danger" if hi else None      # only the cell that breaks the limit
            tones["min"] = "danger" if lo else None
        out.append({"field": field, "operation": op.get("key"), "channel": op.get("channel"),
                    "intended": want, "actual": have, "differs": differs, "tones": tones,
                    "why": why})
    return out
