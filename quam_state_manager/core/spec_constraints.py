"""Hardware constraint catalogue + value checkers for the QUAM state.

The diagnostics linter (:mod:`core.diagnostics`) flags *structural* breakage.
This module adds **hardware value-spec** checks: a field whose value a real
OPX1000 / QOP would reject — e.g. ``time_of_flight`` must be an integer multiple
of 4; ``full_scale_power_dbm`` must be an integer in −11..18 dBm (1 dB step on
QOP 360+); MW ``band`` ∈ {1,2,3}.

Deliberately scoped to **genuine hardware constraints only**. Physics parameters
(T1, T2, f_01, anharmonicity, amplitudes, coupler amp, …) legitimately take any
value (QM project philosophy: *trust researcher input*), so they are simply
absent from the catalogue → zero false positives. All findings are warnings (one
error: an out-of-set ``band``); nothing here blocks an edit — the linter only
surfaces, it never validates on write.

Pure data + logic: no Flask, no QM stack. ``spec_findings(root)`` returns plain
dicts; :func:`core.diagnostics._spec_findings` wraps them into ``Finding``\\ s.
"""

from __future__ import annotations

import re
from typing import Any

from quam_state_manager.core.loader import _walk

# --- Named constants (tweak here; some are setup/QOP-version dependent) ------
TIME_OF_FLIGHT_MIN_NS = 24          # conservative; exact min is setup/QOP dependent
PULSE_LENGTH_MIN_NS = 16
PULSE_LENGTH_MAX_NS = 2 ** 26       # 2^24 clock cycles; catches a fat-fingered extra digit
FULL_SCALE_POWER_DBM_RANGE = (-11, 18)   # inclusive, 1 dB step (QOP 360+; old 3 dB grid obsolete)
VALID_BANDS = (1, 2, 3)
# MW-FEM measurable IF ceiling = ADC anti-alias / Nyquist at 1 GSa/s. Raised from
# the old 400/440 MHz: real production resonators legitimately reach |IF| ≈ 485 MHz
# (so 400/440 would false-positive on a literal IF — see value-spec research). The
# field is normally the #./inferred_intermediate_frequency pointer, so this only
# bites a hand-entered literal; 500 MHz is the true hardware bound.
IF_LIMIT_XY_HZ = 500e6
IF_LIMIT_RESONATOR_HZ = 500e6
IF_FLOOR_MW_HZ = 5e6                # MW-FEM can't demodulate |IF| ≤ 5 MHz (readout)
GAIN_DB_MW_INPUT = (0, 32)
GAIN_DB_LF_INPUT = (-3, 29)
GAIN_DB_OPXPLUS_INPUT = (-12, 20)   # OPX+/OPX1 analog input — dispatched on port __class__
SAMPLING_RATE_VALID = (1e9, 2e9)    # LF-FEM toggle (MW-FEM is fixed 1 GSa/s; not checked)
OUTPUT_MODE_VALID = ("direct", "amplified")        # LF-FEM analog output
UPSAMPLING_MODE_VALID = ("mw", "pulse")            # LF-FEM analog output
LO_MODE_VALID = ("auto", "always_on")              # MW-FEM input, QOP 3.7+
OCTAVE_LO_RANGE_HZ = (2e9, 18e9)    # Octave up/down-converter LO
OCTAVE_GAIN_RANGE_DB = (-20, 20)    # Octave RF_outputs gain (0.5 dB step NOT enforced — FP-prone)
# Octave enum fields (octave guide). All absent on the MW-FEM fleet, so the str-enum
# checker skips None/pointer → these are pure future-proofing with zero FP risk.
OCTAVE_RF_OUTPUT_MODE_VALID = ("always_on", "always_off", "triggered", "triggered_reversed")
OCTAVE_LO_SOURCE_VALID = ("internal", "external")
OCTAVE_INPUT_ATTENUATORS_VALID = ("ON", "OFF")
OCTAVE_IF_MODE_VALID = ("direct", "envelope", "mixer", "off")
BAND_FREQ_RANGES = {1: (50e6, 5.5e9), 2: (4.5e9, 7.5e9), 3: (6.5e9, 10.5e9)}  # Hz

# Coupled MW-FEM port pairs (per FEM) that must share a compatible band.
# Each entry is (port_a, side_a, port_b, side_b) where side is "out"|"in".
COUPLED_PORT_PAIRS = [
    ("1", "out", "1", "in"),
    ("2", "out", "3", "out"),
    ("4", "out", "5", "out"),
    ("6", "out", "7", "out"),
    ("8", "out", "2", "in"),
]
# Band compatibility for coupled ports: 1&3 ok; 1&2 and 2&3 NOT.
BAND_COMPAT = {(1, 1), (2, 2), (3, 3), (1, 3), (3, 1)}

_OPS_LEN_RE = re.compile(r"(^|\.)operations\.[^.]+\.length$")


# --- Low-level numeric helpers ----------------------------------------------

def _num(v: Any) -> float | None:
    """Return *v* as a float if it's a real (non-bool) number to check, else None.

    Strings (JSON pointers like ``#./…``/``#../…`` or any text), ``None`` and
    ``bool`` all return ``None`` → the field is skipped (no false positives).
    """
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _is_integral(x: float) -> bool:
    return float(x).is_integer()


def as_int(v: Any) -> int | None:
    """Coerce *v* to int only if it's an integral non-bool number, else None."""
    n = _num(v)
    if n is None or not _is_integral(n):
        return None
    return int(n)


# --- Checkers (return None when OK, else a short reason string) --------------

def _check_multiple_min(v: Any, *, mult: int, minimum: int,
                        maximum: int | None = None) -> str | None:
    n = _num(v)
    if n is None:
        return None
    if not _is_integral(n):
        return f"must be an integer multiple of {mult}"
    iv = int(n)
    if iv % mult != 0:
        return f"must be a multiple of {mult} (got {iv})"
    if iv < minimum:
        return f"must be ≥ {minimum} ns (got {iv})"
    if maximum is not None and iv > maximum:
        return f"must be ≤ {maximum} ns (got {iv})"
    return None


def _check_range_float(v: Any, lo: float, hi: float) -> str | None:
    """Inclusive numeric range allowing non-integer values (octave LO/gain)."""
    n = _num(v)
    if n is None:
        return None
    if not (lo <= n <= hi):
        return f"must be in [{lo:g}, {hi:g}] (got {n:g})"
    return None


def _check_in_set(v: Any, valid: tuple) -> str | None:
    """Numeric set-membership (e.g. sampling_rate ∈ {1e9, 2e9}); skips pointer/None/bool."""
    n = _num(v)
    if n is None:
        return None
    if float(n) not in {float(x) for x in valid}:
        allowed = ", ".join(f"{x:g}" for x in valid)
        return f"must be one of {{{allowed}}} (got {n:g})"
    return None


def _check_str_enum(v: Any, valid: tuple) -> str | None:
    """String-enum membership; skips None/absent and JSON-pointer strings so a
    missing field is NEVER flagged (e.g. lo_mode is absent pre-QOP-3.7)."""
    if not isinstance(v, str) or v.startswith(("#/", "#./", "#../")):
        return None
    if v not in valid:
        allowed = ", ".join(valid)
        return f"must be one of {{{allowed}}} (got {v!r})"
    return None


def _port_class_suffix(cfg: Any) -> str:
    """Trailing class name of a port leaf, e.g. ``LFFEMAnalogOutputPort`` — the
    robust hardware discriminator (the section NAME is unreliable: OPX+ stores
    analog ports under a section literally named ``analog_inputs`` with a
    DIFFERENT gain range than an OPX1000 LF-FEM)."""
    cls = cfg.get("__class__") if isinstance(cfg, dict) else None
    return cls.rsplit(".", 1)[-1] if isinstance(cls, str) else ""


def _is_lffem_port(cfg: Any) -> bool:
    """LF-FEM port (or a class-less synthetic fixture, treated as LF-FEM in an
    ``analog_*`` section). False for an explicit non-LF-FEM class."""
    suf = _port_class_suffix(cfg)
    return suf == "" or suf.startswith("LFFEM")


def _input_gain_range(cfg: Any, section: str) -> tuple | None:
    """gain_db range for an input port, dispatched on its ``__class__`` (the
    hardware), falling back to the section name for class-less fixtures."""
    suf = _port_class_suffix(cfg)
    if suf == "MWFEMAnalogInputPort":
        return GAIN_DB_MW_INPUT
    if suf == "LFFEMAnalogInputPort":
        return GAIN_DB_LF_INPUT
    if suf == "OPXPlusAnalogInputPort":
        return GAIN_DB_OPXPLUS_INPUT
    if section == "mw_inputs":
        return GAIN_DB_MW_INPUT
    if section == "analog_inputs":
        return GAIN_DB_LF_INPUT
    return None


def _check_int_range(v: Any, lo: int, hi: int) -> str | None:
    n = _num(v)
    if n is None:
        return None
    if not _is_integral(n):
        return f"must be an integer in [{lo}, {hi}]"
    iv = int(n)
    if not (lo <= iv <= hi):
        return f"must be in [{lo}, {hi}] (got {iv})"
    return None


def _check_abs_max(v: Any, limit: float) -> str | None:
    n = _num(v)
    if n is None:
        return None
    if abs(n) > limit:
        return f"|value| must be ≤ {limit:.0f} Hz (got {n:.0f})"
    return None


def _check_band(v: Any) -> str | None:
    n = _num(v)
    if n is None:
        return None
    if not _is_integral(n) or int(n) not in VALID_BANDS:
        return "must be 1, 2, or 3"
    return None


def _check_smearing(v: Any, tof: Any) -> str | None:
    n = _num(v)
    if n is None:
        return None
    if not _is_integral(n):
        return "must be an integer (ns)"
    iv = int(n)
    if iv < 0:
        return f"must be ≥ 0 (got {iv})"
    t = as_int(tof)
    if t is not None and iv > t - 8:
        return f"must be ≤ time_of_flight − 8 = {t - 8} (got {iv})"
    return None


def _mk(severity: str, category: str, dot_path: str, message: str, value: Any) -> dict:
    return {
        "severity": severity,
        "category": category,
        "location": dot_path,
        "message": message,
        "detail": repr(value),
        "jump_path": dot_path,
    }


# --- Per-component visitors (explicit context: xy vs resonator, siblings) ----

def _operations_length_findings(base: str, comp: dict) -> list[dict]:
    out: list[dict] = []
    for rel, value, _ in _walk(comp):
        if not _OPS_LEN_RE.search(rel):
            continue
        reason = _check_multiple_min(value, mult=4, minimum=PULSE_LENGTH_MIN_NS,
                                     maximum=PULSE_LENGTH_MAX_NS)
        if reason:
            out.append(_mk("warning", "value_spec_length", f"{base}.{rel}",
                           f"pulse length {reason}", value))
    return out


def _visit_qubit(qname: str, q: dict) -> list[dict]:
    out: list[dict] = []
    base = f"qubits.{qname}"

    res = q.get("resonator")
    if isinstance(res, dict):
        tof = res.get("time_of_flight")
        reason = _check_multiple_min(tof, mult=4, minimum=TIME_OF_FLIGHT_MIN_NS)
        if reason:
            out.append(_mk("warning", "value_spec_tof",
                           f"{base}.resonator.time_of_flight",
                           f"time_of_flight {reason}", tof))
        reason = _check_smearing(res.get("smearing"), tof)
        if reason:
            out.append(_mk("warning", "value_spec_smearing",
                           f"{base}.resonator.smearing", f"smearing {reason}",
                           res.get("smearing")))
        reason = _check_abs_max(res.get("intermediate_frequency"), IF_LIMIT_RESONATOR_HZ)
        if reason:
            out.append(_mk("warning", "value_spec_if",
                           f"{base}.resonator.intermediate_frequency",
                           f"resonator intermediate_frequency {reason}",
                           res.get("intermediate_frequency")))

    xy = q.get("xy")
    if isinstance(xy, dict):
        reason = _check_abs_max(xy.get("intermediate_frequency"), IF_LIMIT_XY_HZ)
        if reason:
            out.append(_mk("warning", "value_spec_if",
                           f"{base}.xy.intermediate_frequency",
                           f"xy intermediate_frequency {reason}",
                           xy.get("intermediate_frequency")))

    out.extend(_operations_length_findings(base, q))
    return out


def _iter_ports(ports: dict, section: str):
    node = ports.get(section)
    if not isinstance(node, dict):
        return
    for ctrl, fems in node.items():
        if not isinstance(fems, dict):
            continue
        for fem, portmap in fems.items():
            if not isinstance(portmap, dict):
                continue
            for port, cfg in portmap.items():
                if isinstance(cfg, dict):
                    yield ctrl, fem, port, cfg


def _gain_finding(out: list[dict], base: str, cfg: dict, section: str) -> None:
    rng = _input_gain_range(cfg, section)
    if rng is None:
        return
    reason = _check_int_range(cfg.get("gain_db"), *rng)
    if reason:
        out.append(_mk("warning", "value_spec_gain", f"{base}.gain_db",
                       f"gain_db {reason}", cfg.get("gain_db")))


def _visit_ports(ports: dict) -> list[dict]:
    out: list[dict] = []
    for ctrl, fem, port, cfg in _iter_ports(ports, "mw_outputs"):
        base = f"ports.mw_outputs.{ctrl}.{fem}.{port}"
        reason = _check_int_range(cfg.get("full_scale_power_dbm"), *FULL_SCALE_POWER_DBM_RANGE)
        if reason:
            out.append(_mk("warning", "value_spec_power", f"{base}.full_scale_power_dbm",
                           f"full_scale_power_dbm {reason}", cfg.get("full_scale_power_dbm")))
        reason = _check_band(cfg.get("band"))
        if reason:
            out.append(_mk("error", "value_spec_band", f"{base}.band",
                           f"band {reason}", cfg.get("band")))
    for ctrl, fem, port, cfg in _iter_ports(ports, "mw_inputs"):
        base = f"ports.mw_inputs.{ctrl}.{fem}.{port}"
        reason = _check_band(cfg.get("band"))
        if reason:
            out.append(_mk("error", "value_spec_band", f"{base}.band",
                           f"band {reason}", cfg.get("band")))
        _gain_finding(out, base, cfg, "mw_inputs")
        # lo_mode (QOP 3.7+) — absent on older firmware, so _check_str_enum skips None.
        reason = _check_str_enum(cfg.get("lo_mode"), LO_MODE_VALID)
        if reason:
            out.append(_mk("warning", "value_spec_lo_mode", f"{base}.lo_mode",
                           f"lo_mode {reason}", cfg.get("lo_mode")))
    for ctrl, fem, port, cfg in _iter_ports(ports, "analog_inputs"):
        base = f"ports.analog_inputs.{ctrl}.{fem}.{port}"
        _gain_finding(out, base, cfg, "analog_inputs")
        if _is_lffem_port(cfg):
            reason = _check_in_set(cfg.get("sampling_rate"), SAMPLING_RATE_VALID)
            if reason:
                out.append(_mk("warning", "value_spec_sampling_rate", f"{base}.sampling_rate",
                               f"sampling_rate {reason}", cfg.get("sampling_rate")))
    for ctrl, fem, port, cfg in _iter_ports(ports, "analog_outputs"):
        if not _is_lffem_port(cfg):
            continue  # LF-FEM-only fields; an explicit non-LF-FEM class is skipped
        base = f"ports.analog_outputs.{ctrl}.{fem}.{port}"
        reason = _check_str_enum(cfg.get("output_mode"), OUTPUT_MODE_VALID)
        if reason:
            out.append(_mk("warning", "value_spec_output_mode", f"{base}.output_mode",
                           f"output_mode {reason}", cfg.get("output_mode")))
        reason = _check_str_enum(cfg.get("upsampling_mode"), UPSAMPLING_MODE_VALID)
        if reason:
            out.append(_mk("warning", "value_spec_upsampling_mode", f"{base}.upsampling_mode",
                           f"upsampling_mode {reason}", cfg.get("upsampling_mode")))
        reason = _check_in_set(cfg.get("sampling_rate"), SAMPLING_RATE_VALID)
        if reason:
            out.append(_mk("warning", "value_spec_sampling_rate", f"{base}.sampling_rate",
                           f"sampling_rate {reason}", cfg.get("sampling_rate")))
    return out


def _visit_octaves(octaves: dict) -> list[dict]:
    """Octave up/down-converter LO range + RF_outputs gain range. All literal-only
    (LO_frequency is normally a pointer → skipped); future-proofing for an
    Octave/OPX+ chip (the MW-FEM fleet has no octaves)."""
    out: list[dict] = []
    for oname, oc in octaves.items():
        if not isinstance(oc, dict):
            continue
        for section in ("RF_outputs", "RF_inputs"):
            chans = oc.get(section)
            if not isinstance(chans, dict):
                continue
            for ch, cfg in chans.items():
                if not isinstance(cfg, dict):
                    continue
                base = f"octaves.{oname}.{section}.{ch}"
                reason = _check_range_float(cfg.get("LO_frequency"), *OCTAVE_LO_RANGE_HZ)
                if reason:
                    out.append(_mk("warning", "value_spec_octave_lo", f"{base}.LO_frequency",
                                   f"octave LO_frequency {reason} Hz", cfg.get("LO_frequency")))
                # LO_source enum (up- and down-converters both carry it).
                reason = _check_str_enum(cfg.get("LO_source"), OCTAVE_LO_SOURCE_VALID)
                if reason:
                    out.append(_mk("warning", "value_spec_octave_lo_source", f"{base}.LO_source",
                                   f"octave LO_source {reason}", cfg.get("LO_source")))
                if section == "RF_outputs":   # gain + output trigger mode + attenuators
                    reason = _check_range_float(cfg.get("gain"), *OCTAVE_GAIN_RANGE_DB)
                    if reason:
                        out.append(_mk("warning", "value_spec_octave_gain", f"{base}.gain",
                                       f"octave gain {reason} dB", cfg.get("gain")))
                    reason = _check_str_enum(cfg.get("output_mode"), OCTAVE_RF_OUTPUT_MODE_VALID)
                    if reason:
                        out.append(_mk("warning", "value_spec_octave_output_mode",
                                       f"{base}.output_mode", f"octave output_mode {reason}",
                                       cfg.get("output_mode")))
                    reason = _check_str_enum(cfg.get("input_attenuators"), OCTAVE_INPUT_ATTENUATORS_VALID)
                    if reason:
                        out.append(_mk("warning", "value_spec_octave_input_attenuators",
                                       f"{base}.input_attenuators",
                                       f"octave input_attenuators {reason}",
                                       cfg.get("input_attenuators")))
                else:   # RF_inputs — per-quadrature IF demod mode
                    for q in ("IF_mode_I", "IF_mode_Q"):
                        reason = _check_str_enum(cfg.get(q), OCTAVE_IF_MODE_VALID)
                        if reason:
                            out.append(_mk("warning", "value_spec_octave_if_mode",
                                           f"{base}.{q}", f"octave {q} {reason}", cfg.get(q)))
    return out


# --- Public entry ------------------------------------------------------------

def spec_findings(root: dict) -> list[dict]:
    """Validate field values against the hardware catalogue.

    Returns plain dicts ``{severity, category, location, message, detail,
    jump_path}``. Only catalogued fields are considered; pointers/None/bool are
    skipped. Connectivity/coupling checks live in :mod:`core.diagnostics`.
    """
    out: list[dict] = []
    if not isinstance(root, dict):
        return out

    qubits = root.get("qubits")
    if isinstance(qubits, dict):
        for qname, q in qubits.items():
            if isinstance(q, dict):
                out.extend(_visit_qubit(qname, q))

    # qubit_pairs carry cz pulse operations too; check their pulse lengths
    # (most are pointer strings → skipped; only literal bad lengths flag).
    pairs = root.get("qubit_pairs")
    if isinstance(pairs, dict):
        for pname, p in pairs.items():
            if isinstance(p, dict):
                out.extend(_operations_length_findings(f"qubit_pairs.{pname}", p))

    ports = root.get("ports")
    if isinstance(ports, dict):
        out.extend(_visit_ports(ports))

    octaves = root.get("octaves")
    if isinstance(octaves, dict):
        out.extend(_visit_octaves(octaves))

    return out
