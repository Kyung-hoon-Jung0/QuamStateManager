"""Pure in-process waveform synthesis for the Pulses page live preview.

Re-implements ``waveform_function()`` for every class in
:mod:`quam_state_manager.core.pulse_catalog` using numpy + scipy only — the
app process never imports the QM stack (CLAUDE.md invariant). The formulas
are transcribed from the authoritative quam 0.5.0a3 + qualang_tools sources
in the user's ``LabC`` env and pinned bit-for-bit by the golden tests
(``tests/test_waveform_golden.py``); scipy's ``gaussian``/``blackman``
windows and ``gaussian_filter1d`` are the *same functions* quam calls, so
those paths are exact by construction.

Two layers:

- ``synthesize_raw(key, params)`` mirrors quam exactly, including raising
  ``ValueError`` on the same invalid inputs — used by the golden tests.
- ``synthesize(...)`` / ``synth_for_operation(...)`` never raise: they
  return a payload dict with ``ok``/``error``/``param_errors`` for the web
  layer, expanding constant waveforms to flat lines for plotting.

The preview is labeled "synthesized" in the UI; ``machine.generate_config()``
(subprocess) remains the ground truth surface.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Callable

import numpy as np
from scipy.ndimage import gaussian_filter1d
from scipy.signal.windows import blackman as _blackman_window
from scipy.signal.windows import gaussian as _gaussian_window

from quam_state_manager.core.pulse_catalog import (
    PULSE_CATALOG,
    PulseSpec,
    infer_spec_ex,
    inferred_length,
    resolve_qclass,
    unmodeled_fields,
)

__all__ = ["synthesize", "synthesize_raw", "synth_for_operation",
           "decimate_minmax", "sparkline_svg"]

# Hard cap so a typo'd length can't allocate gigabytes in the Flask process.
MAX_SAMPLES = 200_000

_erf_vec = np.vectorize(math.erf, otypes=[float])


# ---------------------------------------------------------------------------
# qualang_tools.config.waveform_tools transcriptions (verbatim math)
# ---------------------------------------------------------------------------

def _drag_gaussian_pulse_waveforms(amplitude, length, sigma, alpha, anharmonicity,
                                   detuning=0.0, subtracted=True):
    if alpha != 0 and anharmonicity == 0:
        raise ValueError("Cannot create a DRAG pulse with `anharmonicity=0`")
    t = np.arange(length, step=1.0)
    center = (length - 1) / 2
    gauss_wave = amplitude * np.exp(-((t - center) ** 2) / (2 * sigma**2))
    gauss_der_wave = (
        amplitude * (-2 * 1e9 * (t - center) / (2 * sigma**2))
        * np.exp(-((t - center) ** 2) / (2 * sigma**2))
    )
    if subtracted:
        gauss_wave = gauss_wave - gauss_wave[-1]
    z = gauss_wave + 1j * 0
    if anharmonicity != detuning:
        z += 1j * gauss_der_wave * (alpha / (2 * np.pi * anharmonicity - 2 * np.pi * detuning))
    elif alpha != 0:
        raise ValueError(
            "The complex envelop for the DRAG waveform cannot be created if"
            " anharmonicity = detuning and alpha != 0."
        )
    z *= np.exp(1j * 2 * np.pi * detuning * t * 1e-9)
    return z.real, z.imag


def _drag_cosine_pulse_waveforms(amplitude, length, alpha, anharmonicity, detuning=0.0):
    if alpha != 0 and anharmonicity == 0:
        raise ValueError("Cannot create a DRAG pulse with `anharmonicity=0`")
    if length < 2:
        # quam divides by (length - 1) here and yields nan/inf; reject instead.
        raise ValueError("DragCosinePulse requires length >= 2")
    t = np.arange(length, step=1.0)
    end_point = length - 1
    cos_wave = 0.5 * amplitude * (1 - np.cos(t * 2 * np.pi / end_point))
    sin_wave = 0.5 * amplitude * (2 * np.pi / end_point * 1e9) * np.sin(t * 2 * np.pi / end_point)
    z = cos_wave + 1j * 0
    if anharmonicity != detuning:
        z += 1j * sin_wave * (alpha / (2 * np.pi * anharmonicity - 2 * np.pi * detuning))
    elif alpha != 0:
        raise ValueError(
            "The complex envelop for the DRAG waveform cannot be created if"
            " anharmonicity = detuning and alpha != 0."
        )
    z *= np.exp(1j * 2 * np.pi * detuning * t * 1e-9)
    return z.real, z.imag


def _flattop_rise(kind: str, amplitude: float, rise_fall_length: int,
                  sigma: float | None = None) -> list[float]:
    """Rise part of the four flat-top families (fall = reversed rise)."""
    n = int(rise_fall_length)
    if kind == "gaussian":
        std = sigma if sigma is not None else rise_fall_length / 5
        wave = amplitude * _gaussian_window(int(np.round(2 * n)), std)
        return list(wave[:n])
    if kind == "cosine":
        return list(amplitude * 0.5 * (1 - np.cos(np.linspace(0, np.pi, n))))
    if kind == "tanh":
        return list(amplitude * 0.5 * (1 + np.tanh(np.linspace(-4, 4, n))))
    if kind == "blackman":
        wave = amplitude * _blackman_window(2 * n)
        return list(wave[:n])
    raise ValueError(f"unknown flat-top window {kind!r}")


def _flattop_waveform(kind: str, amplitude: float, flat_length: int,
                      rise_fall_length: int) -> np.ndarray:
    rise = _flattop_rise(kind, amplitude, rise_fall_length)
    return np.array(rise + [amplitude] * int(flat_length) + rise[::-1])


def _blackman_integral_waveform(pulse_length, v_start, v_end):
    if pulse_length < 2:
        # quam divides by (pulse_length - 1) and emits a nan sample WITHOUT raising
        # (verified against the LabC env: generate_config() does NOT crash here),
        # unlike DragCosine which genuinely raises. Mirror quam so the diagnostics
        # layer never over-reports an invalid-waveform error for a len<2 Blackman.
        return np.full(max(int(pulse_length), 0), float("nan"))
    time = np.linspace(0, pulse_length - 1, int(pulse_length))
    return v_start + (
        time / (pulse_length - 1)
        - (25 / (42 * np.pi)) * np.sin(2 * np.pi * time / (pulse_length - 1))
        + (1 / (21 * np.pi)) * np.sin(4 * np.pi * time / (pulse_length - 1))
    ) * (v_end - v_start)


# ---------------------------------------------------------------------------
# quam pulse-class transcriptions
# ---------------------------------------------------------------------------
# Each function takes the resolved param dict and returns exactly what the
# quam class's waveform_function() returns (scalar / array / list, real or
# complex). ``length`` for inferred classes must already be resolved by the
# caller (synthesize handles that via pulse_catalog.inferred_length).

def _axis_rotate_envelope(env, axis_angle):
    if axis_angle is not None:
        return env * np.exp(1j * float(axis_angle))
    return env


def _square(p):
    waveform = float(p["amplitude"])
    if p.get("axis_angle") is not None:
        waveform = waveform * np.exp(1j * float(p["axis_angle"]))
    return waveform


def _gaussian(p):
    length = int(p["length"])
    if length < 1:
        raise ValueError("GaussianPulse requires length >= 1")
    sigma = float(p["sigma"])
    if sigma == 0:
        raise ValueError("GaussianPulse requires sigma != 0")
    t = np.arange(length, dtype=int)
    center = (length - 1) / 2
    waveform = float(p["amplitude"]) * np.exp(-((t - center) ** 2) / (2 * sigma**2))
    if p.get("subtracted", True):
        waveform = waveform - waveform[-1]
    return _axis_rotate_envelope(waveform, p.get("axis_angle"))


def _drag_gaussian(p):
    i_wf, q_wf = _drag_gaussian_pulse_waveforms(
        amplitude=float(p["amplitude"]), length=int(p["length"]),
        sigma=float(p["sigma"]), alpha=float(p["alpha"]),
        anharmonicity=float(p["anharmonicity"]),
        detuning=float(p.get("detuning", 0.0) or 0.0),
        subtracted=p.get("subtracted", True),
    )
    i_arr, q_arr = np.array(i_wf), np.array(q_wf)
    angle = float(p["axis_angle"])
    i_rot = i_arr * np.cos(angle) - q_arr * np.sin(angle)
    q_rot = i_arr * np.sin(angle) + q_arr * np.cos(angle)
    return i_rot + 1.0j * q_rot


def _drag_cosine(p):
    i_wf, q_wf = _drag_cosine_pulse_waveforms(
        amplitude=float(p["amplitude"]), length=int(p["length"]),
        alpha=float(p["alpha"]), anharmonicity=float(p["anharmonicity"]),
        detuning=float(p.get("detuning", 0.0) or 0.0),
    )
    i_arr, q_arr = np.array(i_wf), np.array(q_wf)
    angle = float(p["axis_angle"])
    i_rot = i_arr * np.cos(angle) - q_arr * np.sin(angle)
    q_rot = i_arr * np.sin(angle) + q_arr * np.cos(angle)
    return i_rot + 1.0j * q_rot


def _flattop_factory(kind: str, class_name: str):
    def fn(p):
        length = int(p["length"])
        flat_length = int(p.get("flat_length", 0) or 0)
        rise_fall_length = (length - flat_length) // 2
        if flat_length + 2 * rise_fall_length != length:
            raise ValueError(
                f"{class_name} requires (length - flat_length) to be even"
                f" (length={length}, flat_length={flat_length})"
            )
        waveform = _flattop_waveform(kind, float(p["amplitude"]), flat_length,
                                     rise_fall_length)
        return _axis_rotate_envelope(waveform, p.get("axis_angle"))
    return fn


def _blackman_integral(p):
    waveform = np.array(_blackman_integral_waveform(
        pulse_length=int(p["length"]),
        v_start=float(p["v_start"]), v_end=float(p["v_end"]),
    ))
    return _axis_rotate_envelope(waveform, p.get("axis_angle"))


def _ceiling_with_epsilon(value: float) -> float:
    eps = float(np.finfo(float).eps)
    truncated = value - (value * 10.0 * eps)
    return float(np.ceil(truncated))


def _erf_square(p):
    risetime_samples = int(p["risetime_samples"])
    flat_length = int(p["flat_length"])
    if risetime_samples <= 0:
        raise ValueError("ErfSquarePulse.risetime_samples must be positive")
    if flat_length < 0:
        raise ValueError("ErfSquarePulse.flat_length must be non-negative")
    sample_rate = float(p.get("sample_rate", 1e9) or 1e9)
    length = int(p["length"])

    duration_s = (flat_length + risetime_samples) / sample_rate
    risetime_s = risetime_samples / sample_rate

    n_samples = int(_ceiling_with_epsilon(duration_s * sample_rate))
    t = np.arange(n_samples, dtype=np.float64) / sample_rate

    fwhm = 0.5 * risetime_s
    t1 = fwhm
    t2 = duration_s - fwhm
    sigma = 0.5 * fwhm / (2.0 * math.log(2.0)) ** 0.5

    env = 0.5 * (_erf_vec((t - t1) / sigma) - _erf_vec((t - t2) / sigma))
    if not p.get("positive_polarity", True):
        env = -env
    env = float(p["amplitude"]) * env

    zero_pad_len = length - len(env)
    left_pad = zero_pad_len // 2
    right_pad = zero_pad_len - left_pad
    env = np.concatenate((np.zeros(left_pad), env, np.zeros(right_pad)))

    phase = float(p.get("phase", 0.0) or 0.0)
    detuning = float(p.get("detuning", 0.0) or 0.0)
    if phase == 0.0 and detuning == 0.0:
        return env
    n = np.arange(len(env), dtype=np.float64)
    rot = np.exp(2j * np.pi * (detuning * n / sample_rate + phase))
    return env.astype(np.float64, copy=False) * rot


def _snz(p):
    flat_length = int(p["flat_length"])
    t_phi_eff = float(p.get("t_phi_eff", 0.0) or 0.0)
    padding = int(p.get("padding", 0) or 0)
    if t_phi_eff < 0:
        raise ValueError("SNZPulse.t_phi_eff must be non-negative")
    if flat_length <= 0:
        raise ValueError("SNZPulse.flat_length must be positive")
    if flat_length % 2 != 0:
        raise ValueError(
            f"SNZPulse.flat_length={flat_length} must be even to "
            "split equally into positive and negative halves."
        )
    if padding < 0:
        raise ValueError("SNZPulse.padding must be non-negative")

    t_phi = int(math.floor(t_phi_eff / 2.0)) * 2
    b_over_a = 1.0 - (t_phi_eff - t_phi) / 2.0
    length = int(p["length"])

    amplitude = float(p["amplitude"])
    half = flat_length // 2
    b_sample = amplitude * b_over_a

    core = np.concatenate([
        amplitude * np.ones(half), [b_sample], np.zeros(t_phi),
        [-b_sample], -amplitude * np.ones(half),
    ])

    total_pad = length - len(core)
    left_pad = total_pad // 2
    right_pad = total_pad - left_pad
    waveform = np.concatenate([np.zeros(left_pad), core, np.zeros(right_pad)])
    waveform = _axis_rotate_envelope(waveform, p.get("axis_angle"))
    return waveform.tolist()


def _gaussian_filtered_factory(bipolar: bool):
    cls = ("GaussianFilteredSymmetricBipolarPulse" if bipolar
           else "GaussianFilteredSquarePulse")

    def fn(p):
        pulse_length = int(p["pulse_length"])
        padding = int(p.get("post_zero_padding_length", 0) or 0)
        freq_mhz = float(p["gaussian_filter_frequency_mhz"])
        sample_rate = float(p.get("sample_rate", 1e9) or 1e9)
        amplitude = float(p["amplitude"])
        length = int(p["length"])

        if pulse_length <= 0:
            raise ValueError(f"{cls}.pulse_length must be positive")
        if bipolar and pulse_length % 2 != 0:
            raise ValueError(f"{cls}.pulse_length must be even")
        if padding < 0:
            raise ValueError(f"{cls}.post_zero_padding_length must be non-negative")
        if freq_mhz <= 0:
            raise ValueError(f"{cls}.gaussian_filter_frequency_mhz must be positive (MHz)")
        if sample_rate <= 0:
            raise ValueError(f"{cls}.sample_rate must be positive (Hz)")

        if amplitude == 0:
            return np.zeros(length, dtype=np.float64)

        zero_pad_len = length - pulse_length
        left_pad = zero_pad_len // 2
        right_pad = zero_pad_len - left_pad
        if bipolar:
            half_len = pulse_length // 2
            core = np.concatenate((
                amplitude * np.ones(half_len, dtype=np.float64),
                -amplitude * np.ones(half_len, dtype=np.float64),
            ))
        else:
            core = amplitude * np.ones(pulse_length, dtype=np.float64)
        env = np.concatenate((
            np.zeros(left_pad, dtype=np.float64), core,
            np.zeros(right_pad, dtype=np.float64),
        ))

        f_hz = freq_mhz * 1e6
        sigma = sample_rate / (2.0 * np.pi * f_hz)
        env = gaussian_filter1d(env, sigma=sigma)
        peak = float(np.max(np.abs(env)))
        if peak > 0:
            scale = (amplitude / peak) if bipolar else (abs(amplitude) / peak)
            env = env * scale
        else:
            env = np.zeros(length, dtype=np.float64)

        return _axis_rotate_envelope(env, p.get("axis_angle"))
    return fn


def _waveform_passthrough(p):
    waveform_i = p.get("waveform_I")
    if not isinstance(waveform_i, (list, tuple)):
        raise ValueError("WaveformPulse.waveform_I must be a list of floats")
    waveform_q = p.get("waveform_Q")
    if waveform_q is None:
        return np.array(waveform_i, dtype=float)
    return np.array(waveform_i, dtype=float) + 1.0j * np.array(waveform_q, dtype=float)


def _flattop_gaussian_deprecated(p):
    # NB: quam passes self.sigma to flattop_gaussian_waveform only when the
    # installed qualang_tools accepts a sigma kwarg (inspect.signature check).
    # The LabC qualang_tools does NOT, so sigma is ignored and the window std
    # is rise_fall_length / 5 — pinned by the ftgauss_dep_pad golden case.
    smoothing_length = int(p.get("smoothing_length", 0) or 0)
    rise_fall_length = smoothing_length // 2
    if smoothing_length % 2 != 0:
        raise ValueError("FlatTopGaussianPulse rise_fall_length must be a multiple of 2")
    rise = _flattop_rise("gaussian", float(p["amplitude"]), rise_fall_length)
    waveform = np.array(
        rise + [float(p["amplitude"])] * int(p["flat_length"]) + rise[::-1])
    zero_pad_len = int(p["length"]) - len(waveform)
    left_pad = zero_pad_len // 2
    right_pad = zero_pad_len - left_pad
    waveform = np.concatenate((np.zeros(left_pad), waveform, np.zeros(right_pad)))
    return _axis_rotate_envelope(waveform, p.get("axis_angle"))


def _cosine_bipolar_deprecated(p):
    def halfcos(n: int):
        if n <= 0:
            return np.array([])
        t = np.arange(n) / n
        return 0.5 * (1 - np.cos(np.pi * t))

    def cos_switch(n: int):
        if n <= 0:
            return np.array([])
        k = np.arange(n, dtype=float)
        theta = (k + 0.5) * np.pi / n
        return np.cos(theta)

    length = int(p["length"])
    flat = int(p["flat_length"])
    smoothing = int(p.get("smoothing_length", 0) or 0)

    if flat > length:
        raise ValueError(
            f"CosineBipolarPulse.flat_length={flat} cannot exceed total length={length}."
        )
    if flat % 2 != 0:
        raise ValueError(
            f"CosineBipolarPulse.flat_length={flat} must be an even number to split "
            "equally into + and - halves."
        )
    if length - (smoothing + flat) < 0:
        raise ValueError(
            f"CosineBipolarPulse.smoothing_time + flat_length ="
            f" {smoothing + flat} exceeds total length={length}."
        )

    if smoothing == 0:
        rise_len = switch_len = fall_len = 0
    else:
        base = smoothing // 4
        extra = smoothing % 4
        rise_len = base + (1 if extra in (2, 3) else 0)
        switch_len = 2 * base + (1 if extra in (1, 3) else 0)
        fall_len = base + (1 if extra in (2, 3) else 0)

    amplitude = float(p["amplitude"])
    seg_rise = amplitude * halfcos(rise_len)
    seg_flat_pos = amplitude * np.ones(flat // 2)
    seg_switch = amplitude * cos_switch(switch_len)
    seg_flat_neg = -amplitude * np.ones(flat // 2)
    seg_fall = -amplitude * halfcos(fall_len)[::-1]

    zero_pad_len = length - (smoothing + flat)
    left_pad = zero_pad_len // 2
    right_pad = zero_pad_len - left_pad

    waveform = np.concatenate([
        np.zeros(left_pad), seg_rise, seg_flat_pos, seg_switch,
        seg_flat_neg, seg_fall, np.zeros(right_pad),
    ])
    waveform = _axis_rotate_envelope(waveform, p.get("axis_angle"))
    return waveform.tolist()


_RAW_FUNCS: dict[str, Callable[[dict], Any]] = {
    "SquarePulse": _square,
    "SquareReadoutPulse": _square,  # inherits SquarePulse.waveform_function
    "GaussianPulse": _gaussian,
    "DragGaussianPulse": _drag_gaussian,
    "DragCosinePulse": _drag_cosine,
    "FlatTopGaussianPulse": _flattop_factory("gaussian", "FlatTopGaussianPulse"),
    "FlatTopCosinePulse": _flattop_factory("cosine", "FlatTopCosinePulse"),
    "FlatTopTanhPulse": _flattop_factory("tanh", "FlatTopTanhPulse"),
    "FlatTopBlackmanPulse": _flattop_factory("blackman", "FlatTopBlackmanPulse"),
    "BlackmanIntegralPulse": _blackman_integral,
    "ErfSquarePulse": _erf_square,
    "SNZPulse": _snz,
    "GaussianFilteredSquarePulse": _gaussian_filtered_factory(bipolar=False),
    "GaussianFilteredSymmetricBipolarPulse": _gaussian_filtered_factory(bipolar=True),
    "WaveformPulse": _waveform_passthrough,
    "_FlatTopGaussianPulse": _flattop_gaussian_deprecated,
    "_CosineBipolarPulse": _cosine_bipolar_deprecated,
}


def synthesize_raw(key: str, params: dict[str, Any]) -> Any:
    """Exact mirror of ``quam.<class>.waveform_function()`` for golden tests.

    *params* must contain plain literals (pointers already resolved) and,
    for inferred-length classes, a concrete integer ``length``. Raises
    ``ValueError``/``KeyError`` on the same inputs quam rejects.
    """
    fn = _RAW_FUNCS.get(key)
    if fn is None:
        raise KeyError(f"no synthesizer for pulse class {key!r}")
    return fn(params)


# ---------------------------------------------------------------------------
# Graceful payload layer (web-facing)
# ---------------------------------------------------------------------------

def _coerce_param(spec_param, value):
    """Best-effort coercion of a form/JSON value to the declared kind."""
    if value is None:
        return None
    kind = spec_param.kind if spec_param is not None else None
    if kind == "int":
        return int(float(value))
    if kind == "float":
        return float(value)
    if kind == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if kind == "list_float":
        if isinstance(value, (list, tuple)):
            return [float(v) for v in value]
        return value
    return value


def _payload_error(error: str, *, spec_key: str | None = None,
                   param_errors: dict | None = None,
                   class_match: str | None = None,
                   unmodeled: list | None = None,
                   warnings: list | None = None) -> dict:
    # class_match / unmodeled_fields / warnings must survive error payloads —
    # the likeliest churn case is a leaf match that ALSO misses a required
    # param, and diagnostics gates its skip on class_match.
    return {
        "ok": False, "kind": None, "iq": False,
        "x_ns": [], "i": [], "q": None,
        "length": None, "constant_value": None,
        "spec_key": spec_key, "error": error,
        "warnings": list(warnings) if warnings else [],
        "param_errors": param_errors or {},
        "class_match": class_match,
        "unmodeled_fields": list(unmodeled) if unmodeled else [],
    }


def synthesize(qclass_or_key: str, params: dict[str, Any], *,
               max_samples: int = MAX_SAMPLES,
               class_match: str | None = None) -> dict:
    """Synthesize a waveform payload for the live preview. Never raises.

    Pointer-valued params must be resolved by the caller (see
    :func:`synth_for_operation`); a leftover ``#``-string lands in
    ``param_errors``. Constant waveforms (Square family) are expanded to a
    flat line of ``length`` samples for plotting, with the scalar kept in
    ``constant_value``.

    *class_match* lets a store-aware caller pass the real match provenance
    (``infer_spec_ex``'s ``how``) — a bare ``spec.key`` resolves as "exact"
    here, which would misreport leaf/implicit matches. ``"implicit"``
    suppresses the unmodeled-fields warning (the spec is a structural guess,
    not a class claim).
    """
    spec, how = resolve_qclass(qclass_or_key)
    if class_match is not None:
        how = class_match
    if spec is None:
        return _payload_error(
            f"no synthesizer for pulse class {qclass_or_key!r}")

    warnings: list[str] = []
    param_errors: dict[str, str] = {}
    resolved: dict[str, Any] = {}

    # Fields the spec does not model are DROPPED by the whitelist loop below
    # — say so, or a remapped class with a new shape field renders a
    # confidently wrong preview. Never flips ``ok``.
    unmodeled = [] if how == "implicit" else unmodeled_fields(spec, params)
    if unmodeled:
        warnings.append(
            "field(s) the catalog spec does not model (ignored by the "
            "preview): " + ", ".join(unmodeled))

    for p in spec.params:
        if p.name in params:
            value = params[p.name]
        else:
            # renamed-field aliases (e.g. quam_builder 0.4.0 stores the
            # GaussianFiltered* padding as padding_length) — normalized
            # onto the canonical name so the raw synth funcs see one name
            for alias in p.aliases:
                if alias in params:
                    value = params[alias]
                    break
            else:
                value = p.default if not p.required else None
        if isinstance(value, str) and value.startswith(("#/", "#./", "#../")):
            if p.synth:
                param_errors[p.name] = f"unresolved pointer {value!r}"
            continue
        if value is None:
            if p.required and p.synth:
                param_errors[p.name] = "missing required parameter"
            resolved[p.name] = None
            continue
        try:
            resolved[p.name] = _coerce_param(p, value)
        except (TypeError, ValueError):
            param_errors[p.name] = f"cannot parse {value!r} as {p.kind}"

    # Length resolution (explicit / inferred / derived).
    length = None
    if spec.length_mode == "derived":
        wf = resolved.get("waveform_I")
        length = len(wf) if isinstance(wf, (list, tuple)) else None
        if length is None:
            param_errors.setdefault("waveform_I", "missing waveform samples")
    elif spec.length_mode == "inferred":
        # A stored numeric length overrides the inferred formula — quam's
        # length is a plain field whose POINTER default invokes the runtime
        # property; a literal in the state file wins there too (keeps the
        # preview consistent with resolve_length and the ground truth).
        raw_len = params.get("length")
        if isinstance(raw_len, (int, float)) and not isinstance(raw_len, bool):
            length = int(raw_len)
        else:
            length = inferred_length(spec.key, resolved)
        if length is None and "length" not in param_errors:
            param_errors["length"] = "cannot infer length from parameters"
        resolved["length"] = length
    else:
        raw_len = resolved.get("length")
        if raw_len is None:
            param_errors.setdefault("length", "missing length")
        else:
            length = int(raw_len)
            resolved["length"] = length

    if param_errors:
        first = next(iter(param_errors.items()))
        return _payload_error(f"{first[0]}: {first[1]}", spec_key=spec.key,
                              param_errors=param_errors, class_match=how,
                              unmodeled=unmodeled, warnings=warnings)

    if length is not None and length > max_samples:
        return _payload_error(
            f"length {length} exceeds the preview cap of {max_samples} samples",
            spec_key=spec.key, param_errors={"length": "too long for preview"},
            class_match=how, unmodeled=unmodeled, warnings=warnings)
    if length is not None and length < 0:
        return _payload_error("length must be non-negative", spec_key=spec.key,
                              param_errors={"length": "negative"},
                              class_match=how, unmodeled=unmodeled,
                              warnings=warnings)

    try:
        raw = synthesize_raw(spec.key, resolved)
    except (ValueError, KeyError, ZeroDivisionError, IndexError) as exc:
        return _payload_error(str(exc), spec_key=spec.key, class_match=how,
                              unmodeled=unmodeled, warnings=warnings)

    payload = _shape_payload(spec, raw, length, warnings)
    payload["class_match"] = how
    payload["unmodeled_fields"] = unmodeled
    return payload


def _shape_payload(spec: PulseSpec, raw: Any, length: int | None,
                   warnings: list[str]) -> dict:
    """Mirror ``Pulse.calculate_waveform`` post-processing and build the payload."""
    # quam: a 2-tuple of (I, Q) becomes complex — none of our raw funcs
    # return tuples today, but keep parity with calculate_waveform.
    if isinstance(raw, tuple) and len(raw) == 2:
        if isinstance(raw[0], (list, np.ndarray)):
            raw = np.array(raw[0]) + 1.0j * np.array(raw[1])
        else:
            raw = raw[0] + 1.0j * raw[1]

    constant_value = None
    if isinstance(raw, (int, float, complex)) and not isinstance(raw, bool):
        # Constant waveform — expand for plotting.
        constant_value = raw
        n = int(length) if length else 16
        if length is None:
            warnings.append("constant pulse without length; plotted over 16 ns")
        arr = np.full(n, raw)
    else:
        arr = np.asarray(raw)

    if np.iscomplexobj(arr):
        i = arr.real.astype(float)
        q = arr.imag.astype(float)
        is_iq = True
    else:
        i = arr.astype(float)
        q = None
        is_iq = False

    n = len(i)
    payload = {
        "ok": True,
        "kind": "constant" if constant_value is not None else "arbitrary",
        "iq": is_iq,
        "x_ns": list(range(n)),
        "i": i.tolist(),
        "q": q.tolist() if q is not None else None,
        "length": int(length) if length is not None else n,
        "constant_value": (
            {"real": constant_value.real, "imag": constant_value.imag}
            if isinstance(constant_value, complex)
            else constant_value
        ),
        "spec_key": spec.key,
        "error": None,
        "warnings": warnings,
        "param_errors": {},
    }
    return payload


# ---------------------------------------------------------------------------
# Store-aware synthesis (resolves pointers, follows aliases)
# ---------------------------------------------------------------------------

def synth_for_operation(store, op_path: str, *,
                        overrides: dict[str, Any] | None = None,
                        max_samples: int = MAX_SAMPLES) -> dict:
    """Synthesize the waveform of the operation at *op_path* in *store*.

    Two-phase (the ``field_peek`` pattern): snapshot + pointer-resolve under
    ``store._lock``, then run the numpy work outside the lock. Alias
    operations (a bare ``"#./other_op"`` string) are followed to their
    target; the payload notes ``alias_of``. *overrides* lets the live
    preview substitute uncommitted form values for individual params.
    """
    from quam_state_manager.core.pointer_path import resolve_field_target

    context_slot = op_path.rsplit(".", 1)[-1]

    with store._lock:
        try:
            raw_op = store.get_value(op_path)
        except (KeyError, TypeError, ValueError, IndexError):
            return _payload_error(f"operation not found: {op_path}")

        alias_of = None
        actual_path = op_path
        if isinstance(raw_op, str) and raw_op.startswith(("#/", "#./", "#../")):
            # NB: resolve_field_target nulls container values in
            # resolved_value (_scalar), so re-fetch the dict by path.
            target = resolve_field_target(store.merged, op_path)
            resolved_dict = None
            if target.get("resolvable"):
                try:
                    resolved_dict = store.get_value(target["resolved_path"])
                except (KeyError, TypeError, ValueError, IndexError):
                    resolved_dict = None
            if isinstance(resolved_dict, dict):
                alias_of = target["resolved_path"]
                actual_path = target["resolved_path"]
                raw_op = resolved_dict
            else:
                payload = _payload_error(
                    f"alias points at unresolvable target: {raw_op!r}")
                payload["alias_of"] = None
                return payload

        if not isinstance(raw_op, dict):
            return _payload_error(f"not a pulse dict: {op_path}")

        snapshot = copy.deepcopy(raw_op)
        # Resolve pointer-valued params relative to the real operation path.
        resolved_params: dict[str, Any] = {}
        pointer_fields: dict[str, dict] = {}
        for fname, fval in snapshot.items():
            if fname == "__class__":
                continue
            if isinstance(fval, str) and fval.startswith(("#/", "#./", "#../")):
                target = resolve_field_target(store.merged, f"{actual_path}.{fname}")
                info = {
                    "pointer": fval,
                    "resolved": bool(target.get("resolvable")),
                    "target_path": target.get("resolved_path"),
                }
                pointer_fields[fname] = info
                if target.get("resolvable"):
                    rv = target.get("resolved_value")
                    if rv is None:
                        # containers come back nulled (_scalar) — re-fetch
                        try:
                            rv = store.get_value(target["resolved_path"])
                        except (KeyError, TypeError, ValueError, IndexError):
                            rv = None
                    resolved_params[fname] = rv
                else:
                    # leave the raw pointer in place; synthesize() will
                    # classify it (error only when shape-relevant)
                    resolved_params[fname] = fval
            else:
                resolved_params[fname] = fval

    # ---- outside the lock ----
    if overrides:
        for key, value in overrides.items():
            resolved_params[key] = value

    qclass = snapshot.get("__class__")
    spec, how = infer_spec_ex(snapshot, context_slot=context_slot)
    if spec is None:
        payload = _payload_error(
            f"unrecognized pulse class {qclass!r}" if qclass
            else "pulse has no __class__ and no recognizable context")
        payload.update({"path": op_path, "alias_of": alias_of,
                        "pointer_fields": pointer_fields,
                        "resolved_params": resolved_params,
                        "qclass": qclass})
        return payload

    # resolved_params here is the full body minus __class__, BEFORE the
    # container filter below — so the unmodeled-fields check inside
    # synthesize() sees list/dict-valued unknowns too.
    payload = synthesize(spec.key, resolved_params, max_samples=max_samples,
                         class_match=how)
    payload.update({
        "path": op_path,
        "alias_of": alias_of,
        "pointer_fields": pointer_fields,
        "resolved_params": {k: v for k, v in resolved_params.items()
                            if not isinstance(v, (list, dict))},
        "qclass": qclass or spec.qclass,
    })
    return payload


# ---------------------------------------------------------------------------
# Display decimation
# ---------------------------------------------------------------------------

def decimate_minmax(values: list[float], max_points: int) -> tuple[list[int], list[float], bool]:
    """Min/max-pair bucket decimation preserving envelope spikes.

    Returns ``(x_indices, values, was_decimated)``. Output is at most
    ``max_points`` (+1 for a final odd sample) values; pairs per bucket keep
    both extremes so narrow features (SNZ B-samples) survive.
    """
    n = len(values)
    if n <= max_points:
        return list(range(n)), list(values), False
    arr = np.asarray(values, dtype=float)
    n_buckets = max(1, max_points // 2)
    bounds = np.linspace(0, n, n_buckets + 1, dtype=int)
    xs: list[int] = []
    ys: list[float] = []
    for b in range(n_buckets):
        lo, hi = int(bounds[b]), int(bounds[b + 1])  # plain ints — JSON-safe
        if hi <= lo:
            continue
        chunk = arr[lo:hi]
        i_min = int(np.argmin(chunk)) + lo
        i_max = int(np.argmax(chunk)) + lo
        for idx in sorted({i_min, i_max}):
            xs.append(idx)
            ys.append(float(arr[idx]))
    return xs, ys, True


def sparkline_svg(payload: dict, *, width: int = 90, height: int = 24,
                  max_points: int = 120) -> str | None:
    """Tiny inline-SVG waveform thumbnail for the library table.

    Strokes use ``currentColor`` so the sparkline obeys light/dark themes
    with zero new color tokens (CSS sets the color + per-trace opacity).
    Returns None for failed payloads.
    """
    if not payload.get("ok") or not payload.get("i"):
        return None

    traces = [("spark-i", payload["i"])]
    if payload.get("q") is not None and any(payload["q"]):
        traces.append(("spark-q", payload["q"]))

    # shared y-scale across I and Q so relative magnitude reads correctly
    all_vals = [v for _, vals in traces for v in vals]
    y_min, y_max = min(all_vals), max(all_vals)
    y_min, y_max = min(y_min, 0.0), max(y_max, 0.0)  # keep the 0 baseline
    span = (y_max - y_min) or 1.0
    pad = 1.5

    polylines = []
    for css_class, vals in traces:
        xs, ys, _ = decimate_minmax(vals, max_points)
        n = len(payload["i"])
        denominator = max(n - 1, 1)
        points = " ".join(
            f"{pad + (width - 2 * pad) * x / denominator:.1f},"
            f"{pad + (height - 2 * pad) * (1 - (y - y_min) / span):.1f}"
            for x, y in zip(xs, ys)
        )
        polylines.append(
            f'<polyline class="{css_class}" points="{points}" fill="none"'
            f' stroke="currentColor" stroke-width="1.2"'
            f' vector-effect="non-scaling-stroke"/>'
        )

    return (
        f'<svg class="pulse-spark" viewBox="0 0 {width} {height}"'
        f' width="{width}" height="{height}" aria-hidden="true"'
        f' preserveAspectRatio="none">{"".join(polylines)}</svg>'
    )
