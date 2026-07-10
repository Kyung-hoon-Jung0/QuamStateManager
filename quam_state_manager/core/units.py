"""Single source of truth for physical-unit display across the app.

QUAM stores quantities in **mixed** units by QM convention, and storage is
*never* re-based here — this module only converts for display/export:

  - coherence times (``T1``, ``T2ramsey``, ``T2echo``) in **seconds**
  - frequencies (``f_01``, ``anharmonicity``, ``chi``, ...) in **hertz**
  - pulse/gate/readout durations + delays (``*_length``, ``time_of_flight``,
    ``depletion_time``) in **nanoseconds** (stored as integers)
  - amplitudes are **dimensionless**; flux offsets / ``phi0_voltage`` in **volts**

Verified against real data in ``quam_states/{LabA,deviceB,variantb}/state.json``.

Two display strategies live here:

  * **fixed-per-field** (``format_quantity`` / the ``qty`` Jinja filter) — used by
    the qubit/pair tables, the inspector, and exports, so a column stays in one
    unit (T1 always µs, f_01 always GHz). Predictable + sortable.
  * **auto-scale-by-magnitude** (``format_metric`` / ``humanize`` /
    ``pick_axis_scale``) — used by dataset metric chips and Plotly axes.

Both share one canonical ladder so they can never drift apart again.

CRITICAL typography note: the micro sign below is U+00B5 (``µ``). It must never
reach a CSS ``text-transform: uppercase`` context — Unicode uppercases it to
Greek capital Mu ``Μ`` (U+039C), which is visually identical to Latin ``M`` and
reads as *milli*. Templates wrap unit text in ``<span class="unit">`` (which is
styled ``text-transform: none``); export headers use the ASCII tokens below.
"""

from __future__ import annotations

import math
from typing import Any, Optional

# U+00B5 MICRO SIGN (NOT U+03BC Greek mu — they render identically but only this
# is the canonical SI micro sign callers/tests compare against). A unit test
# asserts ord(MICRO) == 0xB5 to catch an accidental editor substitution.
MICRO = "µ"

# ---------------------------------------------------------------------------
# Canonical ladders: ordered (min_abs_threshold, factor, suffix, decimals).
# The first tier whose threshold <= |value| wins. The final tier has threshold
# 0.0 so it always matches (including value 0). These reproduce, exactly, the
# legacy ``dataset._format_metric`` formatting.
# ---------------------------------------------------------------------------
_LADDERS: dict[str, list[tuple[float, float, str, int]]] = {
    "freq": [
        (1e9, 1e-9, "GHz", 4),
        (1e6, 1e-6, "MHz", 2),
        (1e3, 1e-3, "kHz", 1),
        (0.0, 1.0, "Hz", 1),
    ],
    "time": [
        (1.0, 1.0, "s", 2),
        (1e-3, 1e3, "ms", 2),
        (1e-6, 1e6, MICRO + "s", 2),
        (0.0, 1e9, "ns", 1),
    ],
    "volt": [
        (1.0, 1.0, "V", 4),
        (1e-3, 1e3, "mV", 2),
        (0.0, 1e6, MICRO + "V", 1),
    ],
}

# suffix -> (factor, decimals) for *fixed* (non-laddered) display.
_FIXED: dict[str, tuple[float, int]] = {
    "GHz": (1e-9, 4), "MHz": (1e-6, 2), "kHz": (1e-3, 1), "Hz": (1.0, 1),
    "s": (1.0, 2), "ms": (1e3, 2), MICRO + "s": (1e6, 2), "ns": (1e9, 1),
    "V": (1.0, 4), "mV": (1e3, 2), MICRO + "V": (1e6, 1),
}

# ASCII tokens for machine-readable export headers (micro -> 'u', so a CSV
# column reads ``T1_us`` not ``T1_µs``).
_ASCII_UNIT: dict[str, str] = {
    "GHz": "GHz", "MHz": "MHz", "kHz": "kHz", "Hz": "Hz",
    "s": "s", "ms": "ms", MICRO + "s": "us", "ns": "ns",
    "V": "V", "mV": "mV", MICRO + "V": "uV",
}

# ---------------------------------------------------------------------------
# Field -> (dimension, fixed_suffix). Exact names first, then a few safe
# QM-convention patterns. Anything unrecognised resolves to (None, None) and is
# rendered raw — we never *guess* a unit, since a wrong label is itself the bug
# this module exists to prevent.
# ---------------------------------------------------------------------------
_FIELD_FIXED: dict[str, tuple[str, str]] = {
    # coherence times (seconds) -> µs
    "T1": ("time", MICRO + "s"),
    "T2ramsey": ("time", MICRO + "s"),
    "T2echo": ("time", MICRO + "s"),
    # frequencies (Hz) -> GHz
    "f_01": ("freq", "GHz"),
    "f_12": ("freq", "GHz"),
    "readout_frequency": ("freq", "GHz"),
    "readout_RF_frequency": ("freq", "GHz"),
    "xy_RF_frequency": ("freq", "GHz"),
    "RF_frequency": ("freq", "GHz"),
    "LO_frequency": ("freq", "GHz"),
    "frequency_bare": ("freq", "GHz"),
    # frequencies (Hz) -> MHz (small spans / shifts)
    "anharmonicity": ("freq", "MHz"),
    "chi": ("freq", "MHz"),
    "xy_intermediate_frequency": ("freq", "MHz"),
    "intermediate_frequency": ("freq", "MHz"),
    "detuning": ("freq", "MHz"),
    # durations (nanoseconds, integer) -> ns
    "x180_length": ("duration_ns", "ns"),
    "x90_length": ("duration_ns", "ns"),
    "readout_length": ("duration_ns", "ns"),
    "time_of_flight": ("duration_ns", "ns"),
    "depletion_time": ("duration_ns", "ns"),
    "smearing": ("duration_ns", "ns"),
    # flux / offsets (volts) -> V (fixed)
    "phi0_voltage": ("volt", "V"),
    "z_joint_offset": ("volt", "V"),
    "z_independent_offset": ("volt", "V"),
    "coupler_decouple_offset": ("volt", "V"),
    "coupler_interaction_offset": ("volt", "V"),
    "mutual_flux_bias": ("volt", "V"),
}

# SI base-unit label per dimension, for "you are editing raw X" hints.
_STORED_LABEL = {"freq": "Hz", "time": "s", "volt": "V", "duration_ns": "ns"}


def _resolve_field(field: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(dimension, fixed_suffix)`` for *field*, or ``(None, None)``."""
    if field in _FIELD_FIXED:
        return _FIELD_FIXED[field]
    f = field.lower()
    if f == "length" or f.endswith("_length"):
        return ("duration_ns", "ns")
    if f.endswith("_delay_ns") or f.endswith("_delay"):
        return ("duration_ns", "ns")
    if "frequency" in f:
        if any(tok in f for tok in ("intermediate", "anharm", "detun", "chi")):
            return ("freq", "MHz")
        return ("freq", "GHz")
    # amplitudes / everything else: dimensionless or unknown -> no conversion.
    return (None, None)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt_duration_ns(value: float) -> str:
    """ns durations are stored as integers; render `40` not `40.0`/`4.0e+01`."""
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def _pick_tier(dimension: str, magnitude: float) -> tuple[float, str, int]:
    """Return ``(factor, suffix, decimals)`` for the tier matching *magnitude*."""
    for threshold, factor, suffix, decimals in _LADDERS[dimension]:
        if magnitude >= threshold:
            return factor, suffix, decimals
    last = _LADDERS[dimension][-1]
    return last[1], last[2], last[3]


# ---------------------------------------------------------------------------
# Fixed-per-field display
# ---------------------------------------------------------------------------
def format_quantity(value: Any, field: str) -> Optional[tuple[str, str]]:
    """Return ``(number_string, unit_label)`` for *field*'s *value*.

    Returns ``None`` when the field has no known unit, or the value is not a
    plain number (``None``, a JSON-pointer string, a list/dict). The unit label
    uses the SI micro sign U+00B5 where applicable.
    """
    dimension, fixed = _resolve_field(field)
    if dimension is None or not _is_number(value):
        return None
    if dimension == "duration_ns":
        return (_fmt_duration_ns(value), "ns")
    factor, decimals = _FIXED[fixed]
    return (f"{value * factor:.{decimals}f}", fixed)


def stored_unit_label(field: str) -> str:
    """SI base unit a *field* is stored in (for 'editing raw X' hints), or ''."""
    dimension, _ = _resolve_field(field)
    return _STORED_LABEL.get(dimension or "", "")


def group_digits(value: Any) -> str:
    """Lossless full-digit display with thousands-comma grouping.

    Unlike :func:`format_quantity` (which *scales* to GHz/µs), this shows EVERY
    stored digit — so a frequency reads ``5,075,187,484.52453`` next to a ``(Hz)``
    column header, never a precision-losing ``5.075187e+09``. It is the editable
    representation in Bulk Edit and is **round-trip exact**: stripping the commas
    and re-parsing (``cli._parse_value``) yields the identical number/type.

    - ``int``           -> ``"5,050,000,000"`` (group every 3 digits)
    - ``float``         -> ``repr(v)`` (shortest round-tripping form) with the
                           integer part comma-grouped, fraction kept verbatim:
                           ``5075187484.52453`` -> ``"5,075,187,484.52453"``,
                           ``0.215`` -> ``"0.215"``. Exponential reprs (tiny/huge
                           magnitudes) are returned as-is (grouping is meaningless).
    - ``bool`` / ``str`` / ``None`` / non-finite -> ``str(value)`` unchanged.
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if not math.isfinite(value):
            return repr(value)
        r = repr(value)
        if "e" in r or "E" in r:
            return r
        if "." in r:
            int_part, frac = r.split(".", 1)
            neg = int_part.startswith("-")
            digits = int_part[1:] if neg else int_part
            grouped = f"{int(digits):,}" if digits else "0"
            return ("-" if neg else "") + grouped + "." + frac
        return f"{int(r):,}"
    return str(value)


def _plain(value: Any) -> str:
    """Fallback rendering for a numeric value with no known unit."""
    if isinstance(value, float):
        if abs(value) >= 1e6 or (0 < abs(value) < 1e-3):
            return f"{value:.4e}"
        return f"{value:.4f}"
    return str(value)


def qty_filter(value: Any, field: str, mode: str = "num") -> str:
    """Jinja filter. ``mode``:

    - ``"num"``  -> scaled number only (the column header carries the unit)
    - ``"full"`` -> ``"<num> <unit>"`` for known units, else ``""`` (so a
      template can gate a humanized preview badge on a non-empty result)
    - ``"unit"`` -> the unit label only, else ``""``
    """
    fq = format_quantity(value, field)
    if mode == "unit":
        return fq[1] if fq else ""
    if value is None:
        return "" if mode == "full" else "-"
    if fq is None:
        return "" if mode == "full" else _plain(value)
    num, label = fq
    return f"{num} {label}" if mode == "full" else num


# ---------------------------------------------------------------------------
# Auto-scale-by-magnitude display (dataset metrics + Plotly axes)
# ---------------------------------------------------------------------------
def humanize(value: float, dimension: str) -> tuple[float, str]:
    """Auto-scale a single value by magnitude -> ``(scaled_value, suffix)``."""
    factor, suffix, _ = _pick_tier(dimension, abs(value) if value else 0.0)
    return value * factor, suffix


def pick_axis_scale(dimension: str, max_abs: float) -> tuple[float, str]:
    """Axis scaling: pick one ``(factor, suffix)`` for a whole array by its max."""
    factor, suffix, _ = _pick_tier(dimension, max_abs)
    return factor, suffix


def format_metric(val: float, unit: str) -> str:
    """Auto-scaled ``"<number> <unit>"`` string.

    *unit* is one of ``"Hz"``, ``"s"``, ``"V"``, ``"%"`` or ``""`` (generic).
    Reproduces the legacy ``dataset._format_metric`` output exactly (e.g.
    ``format_metric(8e-6, "s") == "8.00 µs"``).
    """
    abs_val = abs(val) if val != 0 else 0
    dimension = {"Hz": "freq", "s": "time", "V": "volt"}.get(unit)
    if dimension is not None:
        factor, suffix, decimals = _pick_tier(dimension, abs_val)
        return f"{val * factor:.{decimals}f} {suffix}"
    if unit == "%":
        return f"{val * 100:.1f}%" if abs_val <= 1 else f"{val:.1f}%"
    if abs_val >= 1e6:
        return f"{val:.4e}"
    if 0 < abs_val < 1e-3:
        return f"{val:.4e}"
    return f"{val:.4f}"


# ---------------------------------------------------------------------------
# Export labeling (CSV / Markdown)
# ---------------------------------------------------------------------------
def export_header(field: str) -> str:
    """Column header for *field*: unit-suffixed (``f_01_GHz``, ``T1_us``) when a
    unit is known, else the bare field name. Uses ASCII unit tokens."""
    dimension, fixed = _resolve_field(field)
    if dimension is None:
        return field
    return f"{field}_{_ASCII_UNIT.get(fixed, fixed)}"


def export_value(field: str, value: Any) -> Any:
    """Converted export value for *field* (raw SI -> display unit). ns durations
    and non-numeric / unknown-unit values pass through unchanged."""
    dimension, fixed = _resolve_field(field)
    if dimension is None or dimension == "duration_ns" or not _is_number(value):
        return value
    return value * _FIXED[fixed][0]
