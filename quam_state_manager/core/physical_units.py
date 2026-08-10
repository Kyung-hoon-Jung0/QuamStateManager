"""True physical output for pulse amplitudes (docs/109).

A stored ``amplitude`` is a bare scale factor — it says nothing about what
actually leaves the instrument. The physics is channel-kind dependent and
already pinned elsewhere in SM:

- **MW channel** (drive / readout — the port carries ``full_scale_power_dbm``):
  ``P_dBm = FSP + 20*log10(|amp|)`` — the exact identity the FSP-compensation
  feature and ``autofit/power_rows`` enforce bit-exactly.
- **LF channel** (flux / analog_outputs port): the waveform amplitude IS the
  output voltage — the annotation is unit NAMING (mV/V), not a conversion.

Honesty rules: annotate ONLY when the chain fully resolves to a numeric FSP
(MW) or an analog_outputs port (LF); anything else — dangling pointer, text
value, missing port — returns ``None`` and the surface stays blank. Never
invent. Lengths need no help here: they are stored in ns and the inspector's
``qty`` filter + the grid's ``(ns)`` headers already say so.

Pure module: dict-walking only, no store, no I/O.
"""

from __future__ import annotations

import math
from typing import Any

from .pointer_path import resolve_field_target, _walk

#: How many ancestors above the amplitude leaf may be searched for the channel
#: (the dict carrying ``opx_output``). Real shapes are 2 (``<ch>.operations.
#: <op>.amplitude`` -> op -> operations -> channel) plus one for safety.
_MAX_UP = 4


def channel_of(merged: dict, amp_path: str) -> str | None:
    """The nearest ancestor of *amp_path* whose dict carries ``opx_output``.

    Works on the alias or the resolved path alike: non-dict ancestors (a
    pointer STRING at ``operations.x180``) are skipped, and the channel dict
    (``qubits.<q>.xy`` / ``.resonator`` / ``.z``) is always a real dict.
    """
    segs = amp_path.split(".")
    for up in range(1, _MAX_UP + 1):
        if len(segs) - up < 1:
            break
        anc = segs[: len(segs) - up]
        found, node = _walk(merged, anc)
        if found and isinstance(node, dict) and "opx_output" in node:
            return ".".join(anc)
    return None


def amp_annotation(merged: dict, amp_path: str, amp_value: Any) -> dict | None:
    """Physical annotation for one amplitude leaf, or ``None`` (stay blank).

    Returns ``{"kind": "mw", "fsp": float, "dbm": float, "text": str}`` or
    ``{"kind": "lf", "volts": float, "text": str}``.
    """
    if isinstance(amp_value, bool) or not isinstance(amp_value, (int, float)):
        return None
    if not amp_path.endswith(".amplitude"):
        return None
    ch = channel_of(merged, amp_path)
    if ch is None:
        return None
    try:
        ft = resolve_field_target(merged, ch + ".opx_output.full_scale_power_dbm")
    except Exception:
        ft = {}
    fsp = ft.get("resolved_value") if ft.get("resolvable") else None
    if isinstance(fsp, (int, float)) and not isinstance(fsp, bool):
        if amp_value == 0:
            return None            # no output — a fabricated "-inf dBm" helps no one
        dbm = float(fsp) + 20.0 * math.log10(abs(float(amp_value)))
        return {"kind": "mw", "fsp": float(fsp), "dbm": dbm,
                "text": f"{dbm:.1f} dBm"}
    # Not an MW port — LF (flux) if the channel's port resolves under
    # ports.analog_outputs; the amplitude is then literally volts.
    try:
        pft = resolve_field_target(merged, ch + ".opx_output")
    except Exception:
        pft = {}
    if not pft.get("resolvable"):
        return None
    rp = pft.get("resolved_path") or ""
    if ".analog_outputs." not in f".{rp}.":
        return None
    v = float(amp_value)
    return {"kind": "lf", "volts": v, "text": format_volts(v)}


def format_volts(v: float) -> str:
    """3-sig-fig engineering volts: 0.012 -> ``12 mV``, 0.5 -> ``500 mV``,
    1.2 -> ``1.2 V``. Amplitude 0 still formats (``0 V``) — an LF zero is a
    real, meaningful level, unlike an MW log of zero."""
    if abs(v) >= 1.0 or v == 0:
        return f"{v:.3g} V"
    return f"{v * 1e3:.3g} mV"
