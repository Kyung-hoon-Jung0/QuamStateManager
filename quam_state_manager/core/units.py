"""Single source of truth for physical-unit display across the app.

QUAM stores quantities in **mixed** units by QM convention, and storage is
*never* re-based here — this module only converts for display/export:

  - coherence times (``T1``, ``T2ramsey``, ``T2echo``) in **seconds**
  - frequencies (``f_01``, ``anharmonicity``, ``chi``, ...) in **hertz**
  - pulse/gate/readout durations + delays (``*_length``, ``time_of_flight``,
    ``depletion_time``) in **nanoseconds** (stored as integers)
  - amplitudes are **dimensionless**; flux offsets / ``phi0_voltage`` in **volts**

Verified against real data in ``quam_states/{lab-A,deviceB,variantb}/state.json``.

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
import re
from functools import lru_cache
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
    # QA F-24: a qubit PAIR's `detuning` is a flux amplitude, not the Hz
    # `detuning` above (pulses, ZZ drive, XY-detuned channel). quam_builder
    # FluxTunableTransmonPair, verbatim: "detuning (Optional[float]): Flux
    # amplitude required to bring the qubits to the same energy in V". Its own
    # key, because the leaf name alone cannot tell the two apart.
    "pair_detuning": ("volt", "V"),
    # docs/136: the QDAC-II channel's idle DC bias. Its own key rather than
    # borrowing `z_joint_offset`'s — they are volts for the same reason but
    # not the same field, and a later change to one must not silently move
    # the other.
    "qdac_dc_offset": ("volt", "V"),
}

# SI base-unit label per dimension, for "you are editing raw X" hints.
_STORED_LABEL = {"freq": "Hz", "time": "s", "volt": "V", "duration_ns": "ns"}

# A key that NAMES its unit (``gaussian_filter_frequency_mhz``) is stored in
# that unit, not in the SI base the generic patterns assume: reading it as a
# Hz "frequency" printed 200 MHz as "0.0000 GHz" (UX round, P1-8). The suffix
# is anchored on its underscore, so ``_ghz`` never matches ``_hz`` and ``_ms``
# never matches ``_s``.
_NAME_UNIT_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("_ghz", "GHz"), ("_mhz", "MHz"), ("_khz", "kHz"), ("_hz", "Hz"),
    ("_ns", "ns"), ("_us", MICRO + "s"), ("_ms", "ms"), ("_s", "s"),
    ("_dbm", "dBm"), ("_db", "dB"), ("_mv", "mV"), ("_v", "V"),
)


def unit_compound(field: str) -> bool:
    """A ``*_per_<unit>`` key (``slope_control_hz_per_v``) names a compound
    unit (Hz/V): its last token is the denominator, never the unit it is
    stored in (R3B M3: 4.7e9 Hz/V read "4.68844e+09 V")."""
    f = str(field or "").lower()
    return "_per_" in f or f.startswith("per_")


def name_unit(field: str) -> str:
    """The unit a key's own name declares (``*_mhz`` -> ``"MHz"``), or ``""``
    (also for a ``*_per_<unit>`` compound, :func:`unit_compound`)."""
    f = str(field or "").lower()
    # R6e: "to_db" names a database action, not a decibel quantity.
    if unit_compound(f) or re.search(r"(?:^|[._])to_db$", f):
        return ""
    for suffix, label in _NAME_UNIT_SUFFIXES:
        if f.endswith(suffix) and len(f) > len(suffix):
            return label
    return ""


def _resolve_field(field: str) -> tuple[Optional[str], Optional[str]]:
    """Return ``(dimension, fixed_suffix)`` for *field*, or ``(None, None)``."""
    if field in _FIELD_FIXED:
        return _FIELD_FIXED[field]
    f = field.lower()
    if unit_compound(f):
        return (None, None)     # Hz/V is not a frequency, s^-1 not a time
    if f == "length" or f.endswith("_length"):
        return ("duration_ns", "ns")
    if f.endswith("_delay_ns") or f.endswith("_delay"):
        return ("duration_ns", "ns")
    if name_unit(f):
        # the name states its own unit; the patterns below would guess an SI
        # base it is not stored in. Display goes through name_unit instead.
        return (None, None)
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
    if not _is_number(value):
        return None
    if dimension is None:
        declared = name_unit(field)
        if not declared:
            return None
        # stored in the unit its name states: shown as stored, never rescaled
        return (str(value) if isinstance(value, int) else f"{value:.6g}", declared)
    if dimension == "duration_ns":
        return (_fmt_duration_ns(value), "ns")
    factor, decimals = _FIXED[fixed]
    return (f"{value * factor:.{decimals}f}", fixed)


def fixed_scale(field: str) -> Optional[tuple[float, str]]:
    """``(factor, suffix)`` the fixed-per-field display (``qty``) uses for
    *field* -- ``T1`` -> ``(1e6, "µs")`` -- or ``None`` for a field with no
    known unit. A chart axis that must agree with the tables reads this."""
    dimension, fixed = _resolve_field(field)
    if dimension is None:
        return None
    if dimension == "duration_ns":
        return (1.0, "ns")
    return (_FIXED[fixed][0], fixed)


def stored_unit_label(field: str) -> str:
    """Unit a *field* is stored in (for 'editing raw X' hints), or ''. The
    SI base of its dimension, or the unit its own name declares."""
    dimension, _ = _resolve_field(field)
    if dimension is None:
        return name_unit(field)
    return _STORED_LABEL.get(dimension, "")


def pair_field_key(field: str) -> str:
    """The units key of a field stored DIRECTLY on a qubit pair
    (``qubit_pairs.<pair>.<field>``).

    QA F-24: the pair's own ``detuning`` is a flux amplitude in volts
    (``pair_detuning`` above); by its leaf name alone it reads as the Hz
    ``detuning`` of pulses / the ZZ drive / the XY-detuned channel. Every
    pair-level surface that looks a unit up by key (the pair inspector,
    ``qsm show <pair>``) goes through here, so one value cannot get two units.
    """
    return "pair_detuning" if field == "detuning" else field


@lru_cache(maxsize=1)
def _curated_path_keys() -> tuple[tuple[re.Pattern, str, bool], ...]:
    """``(pattern, key, is_pair)`` for every curated property's dot-path
    template -- the inspector's own rows (``param_specs`` property maps)."""
    from quam_state_manager.core.param_specs import _PAIR_PROPERTY_MAP, _QUBIT_PROPERTY_MAP
    out = []
    for is_pair, rows in ((False, _QUBIT_PROPERTY_MAP), (True, _PAIR_PROPERTY_MAP)):
        for _section, key, tmpl in rows:
            if tmpl:
                rx = re.escape(tmpl).replace(re.escape("{name}"), r"[^.]+")
                out.append((re.compile("^" + rx + "$"), key, is_pair))
    return tuple(out)


def path_units_key(path: str) -> str:
    """The units key of a FULL stored dot path, chosen the way the inspector
    chooses it for the same leaf (docs/272).

    A curated property (``qubits.q1.z.joint_offset``) is looked up by its
    curated key (``z_joint_offset``), exactly as the qubit/pair inspector row
    for it is; any other leaf by its own name. A field held directly on a
    pair goes through :func:`pair_field_key`, as the pair inspector does, so
    ``qubit_pairs.<p>.detuning`` is volts and never the Hz ``detuning``.
    """
    p = str(path or "")
    parts = p.split(".")
    direct_pair = len(parts) == 3 and parts[0] == "qubit_pairs"
    for rx, key, is_pair in _curated_path_keys():
        if rx.match(p):
            return pair_field_key(key) if (is_pair and direct_pair and parts[2] == key) else key
    return pair_field_key(parts[-1]) if direct_pair else parts[-1]


def display_spec(path: str) -> Optional[dict]:
    """How a value stored at *path* is shown in its FIXED display unit -- the
    unit the inspector and the qubit/pair tables already show that field in
    (:func:`format_quantity`): ``{"unit", "scale", "dp", "stored"}`` with
    ``shown = stored_value * scale`` written with ``dp`` decimals, and
    ``stored`` the unit the raw value is kept in. ``None`` when the field has
    no known unit -- never a guessed one (docs/272, the Agent cards)."""
    dimension, fixed = _resolve_field(path_units_key(path))
    if dimension is None:
        return None
    stored = _STORED_LABEL.get(dimension, "")
    if dimension == "duration_ns":
        return {"unit": "ns", "scale": 1.0, "dp": 0, "stored": stored}
    factor, decimals = _FIXED[fixed]
    return {"unit": fixed, "scale": factor, "dp": decimals, "stored": stored}


def group_digits(value: Any) -> str:
    """Lossless full-digit display with thousands-comma grouping.

    Unlike :func:`format_quantity` (which *scales* to GHz/µs), this shows EVERY
    stored digit — so a frequency reads ``5,075,187,484.52453`` next to a ``(Hz)``
    column header, never a precision-losing ``5.075187e+09``. It is the editable
    representation in Bulk Edit and is **round-trip exact**: stripping the commas
    and re-parsing (``type_policy.parse_value``) yields the identical number/type.

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


# ---------------------------------------------------------------------------
# Units a run's own fit file declares (Datasets > Trends axes)
# ---------------------------------------------------------------------------
def _fit_key(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower()).strip("_")


#: R4 B2: words that name a pure number. A metric whose name holds one as a
#: whole word (``opt_amp_prefactor``, ``fidelity_opt``, ``snr_opt``,
#: ``success``) has no unit, whatever its fit file's attribute says: on the
#: real files those attributes were copied from elsewhere (``Hz`` on a pi-pulse
#: prefactor, ``V`` on a fidelity).
DIMENSIONLESS_WORDS = frozenset({"prefactor", "factor", "fidelity", "snr", "success"})
#: The units a pure number can honestly carry: a ratio (an SNR in dB, a
#: fidelity in %). Any other unit on a dimensionless name is not read.
RATIO_UNITS = frozenset({"%", "dB"})


def is_dimensionless_name(name: Any) -> bool:
    """Whether *name* holds a :data:`DIMENSIONLESS_WORDS` word as a whole
    word (split at anything that is not a letter or digit, ``_`` included)."""
    return any(w in DIMENSIONLESS_WORDS for w in re.split(r"[^a-z0-9]+", str(name or "").lower()))


def fit_metric_units(variables: Any, metrics: Any, axes: Any = (), booleans: Any = (),
                     *, fit_results: Any = None) -> dict[str, str]:
    """v1.5.1 (T1-F7): ``{metric: unit}`` for the fit-result *metrics* of a
    run, read from the ``units`` attribute its own ``ds_fit`` file gives a
    variable -- matched by the variable's name OR its ``long_name``
    (lower-cased, non-alphanumerics folded to ``_``): qualibration stores the
    fit result ``t1`` as the variable ``tau`` with ``long_name="T1"``,
    ``units="ns"``. A metric two variables claim with different units, and a
    metric no variable names, get no unit -- a unit is read, never guessed.
    ``variables`` is ``ndview.probe_file``'s ``vars`` list, ``axes`` its
    ``axes`` list.

    A unit attribute is only read where it is the variable's OWN. A lab's
    fit file copies attributes along (xarray keeps them through a reduction),
    so none is read from a variable whose ``(units, long_name)``:

    * is a coordinate's of the same file, the variable not being that
      coordinate (R3B M2: ``success`` with ``long_name="idle times"``,
      ``units="ns"`` -- the sweep axis's);
    * is that of a HIGHER-RANK variable it can be a reduction of (its dims
      are a subset of that variable's), the long_name non-empty (R4 B2:
      ``optimal_detuning`` with ``("V", "blob distance")`` -- the 2-D data
      variable ``distance``'s, an argmax of it over frequency).

    And no unit at all for a true/false metric (a ``bool`` variable -- on
    disk xarray writes it as int8 with the attribute ``dtype="bool"``, which
    ``probe_file`` reports as ``is_bool`` -- or a metric named in *booleans*,
    whose fit values are true/false), nor for a dimensionless name
    (:func:`is_dimensionless_name`) unless the unit is a ratio
    (:data:`RATIO_UNITS`)."""
    # With fit_results (Trends), every claimant must also prove its number
    # agrees with the plotted value for the same entity in the same run.
    def sig(v: dict) -> tuple:
        u, ln = v.get("units"), v.get("long_name")
        return (u.strip() if isinstance(u, str) else u, ln.strip() if isinstance(ln, str) else ln)

    rows = [v for v in variables or () if isinstance(v, dict)]
    dim_names = {str(d) for v in rows for d in (v.get("dims") or ())}
    coords = [v for v in rows if v.get("name") in dim_names] + [a for a in axes or () if isinstance(a, dict)]
    coord_names = {v.get("name") for v in coords}
    coord_sigs = {sig(v) for v in coords}
    # (units, long_name) -> the dim sets of the variables carrying it
    carried: dict[tuple, list] = {}
    for v in rows:
        u, ln = sig(v)
        if isinstance(u, str) and u and isinstance(ln, str) and ln:
            carried.setdefault((u, ln), []).append({str(d) for d in (v.get("dims") or ())})

    def copied_from_higher_rank(v: dict) -> bool:
        mine = {str(d) for d in (v.get("dims") or ())}
        return any(len(other) > len(mine) and mine <= other for other in carried.get(sig(v), ()))

    claims: dict[str, set] = {}
    evidence: dict[str, list] = {}
    for v in rows:
        unit = v.get("units")
        if not isinstance(unit, str) or not unit.strip():
            continue
        if v.get("is_bool") or str(v.get("dtype") or "") == "bool":
            continue
        if v.get("name") not in coord_names and sig(v) in coord_sigs:
            continue              # inherited from a coordinate, not declared
        if copied_from_higher_rank(v):
            continue              # inherited from the variable it reduces
        for k in {_fit_key(v.get("name")), _fit_key(v.get("long_name"))}:
            if k:
                claims.setdefault(k, set()).add(unit.strip())
                evidence.setdefault(k, []).append(v.get("fit_values") or {})
    skip = {str(b) for b in booleans or ()}
    out: dict[str, str] = {}
    for m in metrics or ():
        if str(m) in skip:
            continue
        got = claims.get(_fit_key(m))
        if got and len(got) == 1:
            unit = next(iter(got))
            if is_dimensionless_name(m) and unit not in RATIO_UNITS:
                continue          # a pure number: the attribute came from elsewhere
            if fit_results is not None:
                expected = {str(q): f[m] for q, f in fit_results.items()
                            if isinstance(f, dict) and m in f}
                def agrees(values):
                    if set(values) == {None} and len(expected) == 1:
                        values = {next(iter(expected)): values[None]}
                    return bool(expected) and all(
                        q in values and _is_number(value) and _is_number(values[q])
                        and math.isfinite(value) and math.isfinite(values[q])
                        and math.isclose(values[q], value, rel_tol=1e-7, abs_tol=0.0)
                        for q, value in expected.items())
                if not all(agrees(v) for v in evidence.get(_fit_key(m), [])):
                    continue
            out[str(m)] = unit
    return out
