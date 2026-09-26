"""Z-line distortion: what an LF-FEM port's output filters do to a flux pulse.

A flux (z) line's analog output port carries two digital filters in the QUAM
state -- ``exponential_filter`` (the IIR stage, a list of ``(A, tau)``) and
``feedforward_filter`` (the FIR stage, a list of taps) -- and the OPX applies
BOTH to every sample it plays on that port. This module answers "what leaves
the DAC when this pulse is played on this line?" for the ideal step and for the
qubit's own flux pulses. Pure: numpy + scipy only, no store, no I/O, no quam.

Provenance of every rule (house rule: a derived formula is not a citation):

* ``[paper: QM docs, Guides/output_filter.md]`` -- the official QM
  documentation, read at ``D:\\work\\documentation-website`` (read-only). The
  quoted sentences below are pinned verbatim by
  ``tests/test_zline_filters.py::TestQuotesVerbatim``.
* ``[paper: QM docs, Guides/opx1000_fems.md]`` -- upsampling + output range.
* ``[paper: qualang_tools digital_filters]`` -- QM's own filter-design library
  (the code QM's docs point users to), used as the INDEPENDENT implementation
  the tests compare against.
* ``[derived]`` -- worked out here, checked numerically in the tests (named
  per function).

What the documentation says, verbatim
-------------------------------------

Sampling and size (OPX1000 LF-FEM, QOP >= 3.3):

    "The OPX1000 LF-FEM output filter consists of one FIR filter with 48 taps,
    and 6 IIR filters, each with a single feedback tap."

    "The IIR filters can cover exponential decays and high-pass compensation
    filters with time constants ranging from 1 ns to 1 second."

    "The filters always operate at 2 GSa/s, and each FIR tap is 0.5 ns."

    "Note that the filters are applied after the [Crosstalk Correction
    Matrix](../Guides/features.md/#crosstalk-correction-matrix) and before the
    DC Offset."

The exponential stage describes the DISTORTION it compensates:

    "The exponential compensation filter is used to compensate for an
    exponential decaying over/undershoot of the signal."

    "Multiple exponential and a high-pass response can be modeled by the
    following step response:"

    "$$s(t) = \\left(A_{dc} + \\sum_{n=1}^N A_n e^{-\\frac{t}{\\tau_n}}\\right)\\cdot u(t)$$"

    "The default value is $A_{dc}=1$."

    "The `high_pass` field can also be used, but only when the
    `exponential_dc_gain` is not set."

    "Using it is equivalent to setting `exponential_dc_gain` to $0$ and adding
    to the exponential list the value ($1$, $\\tau_{hp}$)."

and qualang_tools, whose fit output users paste into that field:

    "The tuple list of $(A, \\tau)$ can now be copied and pasted directly in
    the configuration file in the ```filter``` -> ```exponential``` section."

    "each tuple represents an exponential decay of the shape
    `1 + A * exp(-t/tau)`. `tau` is in ns."

The FIR stage and the limits:

    "Feedforward taps are limited to the range (-2,2)."

    "To disable a filter, we simply omit it from the configuration or set it
    to an empty list/None in the following way:"

    "If the output is outside the allowed range, it is clipped to the nearest
    allowed value according to its sign."

QOP-version difference (QOP 3.4 and earlier):

    "The filters are cascaded sequentially, such that the output of one filter
    is the input of the next one."

    "The cascaded implementation of the exponential filters means that the
    actual coefficients of the filters is convolved."

What is NOT in the documentation, and what this module does about it
----------------------------------------------------------------------

1. How the firmware discretizes the correction (fixed-point, bilinear vs
   pole-matching) is not published. This module uses the bilinear (Tustin)
   transform -- the one qualang_tools' ``single_exponential_correction`` uses
   -- and the page says the curve is a model, never a bit-exact replay.
2. Which QOP version the lab runs is not in the state. The default model is
   the QOP >= 3.5 sum form above; ``model="cascade"`` gives the QOP <= 3.4
   cascade. They coincide for a single exponential and differ for several.
3. ``upsampling_mode="mw"`` upsamples through a 14-tap filter whose taps are
   not published; a 1 GSa/s pulse on such a port is shown sample-doubled and
   the result carries a note saying so.
4. ``feedback_filter`` (the pre-QOP-3.3 / OPX+ raw feedback taps) is not
   modeled: a port that sets it gets a note and NO filtered curve -- a curve
   that silently left out a stage would be the wrong curve.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from scipy import signal

__all__ = [
    "TS_NS", "FS_HZ", "MAX_FIR_TAPS", "MAX_IIR", "FF_TAP_LIMIT",
    "PortFilter", "parse_port_filter", "correction_zpk", "correction_sos",
    "apply_filters", "step_response", "model_dc_gain", "model_notes", "upsample_to_2gs", "pulse_response",
    "MAX_PULSE_SAMPLES",
    "resolve_zline", "zline_entities", "zline_row", "row_from_resolved",
]

#: [paper: output_filter.md "The filters always operate at 2 GSa/s, and each
#: FIR tap is 0.5 ns."]
TS_NS = 0.5
FS_HZ = 2e9
#: [paper: output_filter.md "one FIR filter with 48 taps, and 6 IIR filters"]
MAX_FIR_TAPS = 48
MAX_IIR = 6
#: [paper: output_filter.md "Feedforward taps are limited to the range (-2,2)."]
FF_TAP_LIMIT = 2.0
#: [paper: output_filter.md "time constants ranging from 1 ns to 1 second"]
DOC_TAU_MIN_NS = 1.0
DOC_TAU_MAX_NS = 1e9
#: [derived] below one 2 GSa/s sample a decay is not representable by a
#: sampled filter at all; 1e-3 ns and less is a value written in SECONDS.
HARD_TAU_MIN_NS = TS_NS
SECONDS_LIKE_NS = 1e-3
HARD_TAU_MAX_NS = 1e11
#: [paper: opx1000_fems.md "`direct` - The output range is between -0.5 V and
#: 0.5 V." / "`amplified` - The output range is between -2.5 V and 2.5 V."]
OUTPUT_RANGE_V = {"direct": 0.5, "amplified": 2.5}
#: step-response horizon: long enough for the slowest decay, capped so one
#: request stays well under a second at 2 GSa/s. [derived]
STEP_MAX_NS = 1_000_000.0
STEP_MIN_NS = 400.0
STEP_TAU_MULT = 8.0
#: points shipped to the browser per trace [derived: log-spaced decimation]
STEP_DENSE_NS = 100.0
STEP_LOG_POINTS = 700
#: longest pulse drawn (1 GSa/s samples) -- 20 us [derived: page budget]
MAX_PULSE_SAMPLES = 20_000

#: The decay QOP <= 3.4 adds to the high-pass, in ns [paper: output_filter.md
#: "Note that a decay of 0.5 seconds is automatically added to the high-pass
#: filter."].
HP_AUTO_DECAY_NS = 5e8


def _note(level: str, code: str, text: str) -> dict:
    """One honest line for the page. level: block (no filtered curve) |
    warn (curve drawn, read with care) | info."""
    return {"level": level, "code": code, "text": text}


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) else None


@dataclass(frozen=True)
class PortFilter:
    """A port's filter settings, validated. ``exponential`` is the EFFECTIVE
    list (a ``high_pass`` folded in as ``(1, tau_hp)``) and ``dc_gain`` the
    effective ``A_dc`` (0 with a high-pass, else the stored value, else 1)."""
    exponential: tuple[tuple[float, float], ...] = ()
    dc_gain: float = 1.0
    feedforward: tuple[float, ...] = ()
    sampling_rate: float = 1e9
    upsampling_mode: str = "mw"
    output_mode: str = "direct"
    high_pass: float | None = None

    @property
    def has_iir(self) -> bool:
        return bool(self.exponential) or self.dc_gain != 1.0

    @property
    def has_fir(self) -> bool:
        return bool(self.feedforward)

    def key(self) -> tuple:
        """A content key (hashable) -- what a memo of a response keys on."""
        return (self.exponential, self.dc_gain, self.feedforward,
                self.sampling_rate, self.upsampling_mode, self.output_mode)


def parse_port_filter(port: Any) -> tuple[PortFilter | None, list[dict]]:
    """Read + validate the filter fields of one analog-output port dict.

    Returns ``(PortFilter, notes)``; ``PortFilter`` is ``None`` whenever a
    ``block`` note was raised -- the caller then draws no filtered curve.
    Never raises: an unexpected failure inside the model is itself a
    ``block`` note, so one bad port can never take a whole page down.
    """
    try:
        return _parse_port_filter(port)
    except Exception as exc:  # noqa: BLE001 -- the documented contract
        return None, [_note("block", "model_error",
                            f"SM could not model this port's filters ({type(exc).__name__}: {exc}). "
                            "No curve rather than a guessed one.")]


def _parse_port_filter(port: Any) -> tuple[PortFilter | None, list[dict]]:
    notes: list[dict] = []
    if not isinstance(port, dict):
        return None, [_note("block", "no_port", "The z line's output port is not a port entry in this state.")]

    block = False

    # ---- feedback_filter: not modeled (module docstring, item 4) ----
    fb = port.get("feedback_filter")
    if fb not in (None, []):
        notes.append(_note("block", "feedback_unmodeled",
                           "This port sets feedback_filter (raw IIR taps, pre-QOP-3.3 / OPX+). "
                           "SM does not model it, so no filtered curve is drawn."))
        block = True

    # ---- exponential ----
    raw_exp = port.get("exponential_filter")
    exps: list[tuple[float, float]] = []
    if raw_exp is None:
        pass
    elif not isinstance(raw_exp, (list, tuple)):
        notes.append(_note("block", "exp_not_list",
                           f"exponential_filter is {type(raw_exp).__name__}, not a list of (A, tau) pairs."))
        block = True
    else:
        for i, pair in enumerate(raw_exp):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                notes.append(_note("block", "exp_bad_pair",
                                   f"exponential_filter[{i}] is not an (A, tau) pair: {pair!r}."))
                block = True
                continue
            a, tau = _num(pair[0]), _num(pair[1])
            if a is None or tau is None:
                notes.append(_note("block", "exp_not_number",
                                   f"exponential_filter[{i}] = {list(pair)!r} is not two finite numbers."))
                block = True
                continue
            if tau <= 0:
                notes.append(_note("block", "exp_tau_nonpositive",
                                   f"exponential_filter[{i}] has tau = {tau:g} ns. A decay needs tau > 0; "
                                   "a non-positive tau is a growing exponent, not a filter."))
                block = True
                continue
            if tau <= SECONDS_LIKE_NS:
                notes.append(_note("block", "exp_tau_units",
                                   f"exponential_filter[{i}] has tau = {tau:g}. Taus are in ns; this "
                                   f"looks like a value in seconds ({tau * 1e9:g} ns?)."))
                block = True
                continue
            if tau < HARD_TAU_MIN_NS or tau > HARD_TAU_MAX_NS:
                notes.append(_note("block", "exp_tau_unrepresentable",
                                   f"exponential_filter[{i}] has tau = {tau:g} ns, outside anything a "
                                   "2 GSa/s filter can represent."))
                block = True
                continue
            if tau < DOC_TAU_MIN_NS or tau > DOC_TAU_MAX_NS:
                notes.append(_note("warn", "exp_tau_outside_doc",
                                   f"exponential_filter[{i}] tau = {tau:g} ns is outside the documented "
                                   "1 ns - 1 s range; the hardware may not reproduce this curve."))
            if a == 0.0:
                notes.append(_note("info", "exp_zero_amp",
                                   f"exponential_filter[{i}] has A = 0 (tau = {tau:g} ns): it does nothing."))
                continue
            exps.append((a, tau))

    # ---- dc gain / high pass ----
    dc_raw = port.get("exponential_dc_gain")
    hp_raw = port.get("high_pass_filter")
    dc_gain = 1.0
    high_pass = None
    if dc_raw is not None:
        d = _num(dc_raw)
        if d is None:
            notes.append(_note("block", "dc_not_number", f"exponential_dc_gain = {dc_raw!r} is not a number."))
            block = True
        else:
            dc_gain = d
            if d != 1.0 and exps:
                notes.append(_note("info", "dc_gain_convention",
                                   f"exponential_dc_gain = {d:g}. Drawn with the QM docs' form "
                                   "s(t) = A_dc + sum A_n exp(-t/tau_n); qualang_tools' README example "
                                   "divides each A by A_dc before writing it, which is a different "
                                   "curve if these A were fitted that way."))
    if hp_raw is not None:
        h = _num(hp_raw)
        if dc_raw is not None:
            notes.append(_note("block", "hp_with_dc",
                               "Both high_pass_filter and exponential_dc_gain are set; QM accepts "
                               "high_pass only when exponential_dc_gain is not set."))
            block = True
        elif h is None or h <= 0:
            notes.append(_note("block", "hp_bad", f"high_pass_filter = {hp_raw!r} is not a positive time in ns."))
            block = True
        else:
            high_pass = h
            dc_gain = 0.0
            exps.append((1.0, h))
            notes.append(_note("warn", "hp_ideal",
                               f"high_pass_filter = {h:g} ns: on QOP >= 3.5 the ideal high-pass "
                               "compensation integrates (A_dc = 0) -- a step output keeps rising and a "
                               "pulse that is not net-zero leaves a residual. (QOP <= 3.4 adds a 0.5 s "
                               "decay; the cascade model draws that.)"))

    if len(exps) > MAX_IIR:
        notes.append(_note("block", "too_many_iir",
                           f"{len(exps)} exponentials; the LF-FEM has {MAX_IIR} IIR filters."))
        block = True

    # ---- feedforward ----
    raw_ff = port.get("feedforward_filter")
    taps: list[float] = []
    if raw_ff is None:
        pass
    elif not isinstance(raw_ff, (list, tuple)):
        notes.append(_note("block", "ff_not_list",
                           f"feedforward_filter is {type(raw_ff).__name__}, not a list of taps."))
        block = True
    elif len(raw_ff) == 0:
        notes.append(_note("info", "ff_empty",
                           "feedforward_filter is an empty list: the FIR stage is off (QM treats an "
                           "empty list as 'no filter')."))
    else:
        bad = [i for i, t in enumerate(raw_ff) if _num(t) is None]
        if bad:
            notes.append(_note("block", "ff_not_number",
                               f"feedforward_filter has non-numeric taps at index {bad[:5]}."))
            block = True
        else:
            taps = [float(t) for t in raw_ff]
            if len(taps) > MAX_FIR_TAPS:
                notes.append(_note("block", "ff_too_long",
                                   f"{len(taps)} FIR taps; the LF-FEM FIR has {MAX_FIR_TAPS}."))
                block = True
            out = [i for i, t in enumerate(taps) if not (-FF_TAP_LIMIT < t < FF_TAP_LIMIT)]
            if out:
                notes.append(_note("block", "ff_out_of_range",
                                   f"FIR taps at index {out[:5]} are outside (-2, 2), the hardware range."))
                block = True
            if taps and all(t == 0 for t in taps):
                notes.append(_note("block", "ff_all_zero",
                                   "Every FIR tap is 0: the port would output nothing."))
                block = True
            gain = sum(abs(t) for t in taps)
            if gain >= 1.0:
                notes.append(_note("info", "ff_gain",
                                   f"sum |FIR taps| = {gain:.3f}; QM recommends below 1 to avoid clipping."))

    # ---- port rate / modes ----
    sr = _num(port.get("sampling_rate"))
    sr = 1e9 if sr is None else sr
    if sr not in (1e9, 2e9):
        notes.append(_note("block", "bad_rate", f"sampling_rate = {sr:g}; an LF-FEM port runs at 1e9 or 2e9."))
        block = True
    up = port.get("upsampling_mode")
    up = up if up in ("mw", "pulse") else "mw"
    om = port.get("output_mode")
    om = om if om in OUTPUT_RANGE_V else "direct"

    if not exps and dc_gain == 1.0 and not taps and not block:
        notes.append(_note("info", "no_filters",
                           "This port has no exponential and no FIR filter: the output is the pulse itself."))

    pf = PortFilter(tuple(exps), float(dc_gain), tuple(taps), float(sr), up, om, high_pass)
    if block:
        return None, notes
    # Stability is a property of the whole exponential set -> checked here too.
    if pf.has_iir:
        _, _, _, stab = _correction_analog(pf)
        notes.extend(stab)
        if any(n["level"] == "block" for n in stab):
            return None, notes
    return pf, notes


# ---------------------------------------------------------------------------
# The IIR stage
# ---------------------------------------------------------------------------

def _distortion_rational(exps, dc_gain):
    """H(s) = A_dc + sum_i A_i * s / (s + 1/tau_i), as (num, den) polynomials
    in s (s in 1/ns), highest power first.

    [derived] H is s * Laplace{step response}: the Laplace transform of the
    documented step response ``A_dc + sum A_n exp(-t/tau_n)`` is
    ``A_dc/s + sum A_n/(s + 1/tau_n)``; multiplying by s turns a step response
    into a transfer function. Cross-checked in the tests by the round trip
    H * C == 1 on a step and by the t = 0+ / t -> inf limits.
    """
    poles = [1.0 / tau for _, tau in exps]
    den = np.array([1.0])
    for p in poles:
        den = np.polymul(den, [1.0, p])
    num = dc_gain * den
    for i, (a, _) in enumerate(exps):
        term = np.array([a, 0.0])          # A_i * s
        for j, p in enumerate(poles):
            if j != i:
                term = np.polymul(term, [1.0, p])
        num = np.polyadd(num, term)
    return num, den


def _merge_equal_taus(exps):
    """Sum the amplitudes of exponentials that share a tau; drop a sum of 0.

    [derived] ``A1*s/(s+1/tau) + A2*s/(s+1/tau) == (A1+A2)*s/(s+1/tau)``, so a
    repeated tau is one term. Built unmerged, the line model's numerator and
    denominator share the factor ``(s + 1/tau)`` and one of its "zeros" sits
    exactly on a pole, where the rational H cannot be evaluated. After the
    merge every tau is distinct with a non-zero A, and then no zero of H can
    coincide with a pole: the numerator at ``s = -1/tau_i`` is
    ``A_i * (-1/tau_i) * prod_{j != i} (1/tau_j - 1/tau_i) != 0``.
    Only the QOP >= 3.5 sum form merges; the QOP <= 3.4 cascade keeps every
    stage (two cascaded stages with one tau are NOT one stage).
    """
    out: list[list[float]] = []
    for a, tau in exps:
        for m in out:
            if math.isclose(m[1], tau, rel_tol=1e-12, abs_tol=0.0):
                m[0] += a
                break
        else:
            out.append([a, tau])
    return tuple((a, tau) for a, tau in out if a != 0.0)


def _h_eval(exps, dc_gain, s):
    return dc_gain + sum(a * s / (s + 1.0 / tau) for a, tau in exps)


def _h_resid(exps, dc_gain, s):
    """|H(s)|, or inf when *s* sits exactly on a pole of H."""
    try:
        return abs(_h_eval(exps, dc_gain, s))
    except ZeroDivisionError:
        return math.inf


def _hprime_eval(exps, s):
    return sum(a * (1.0 / tau) / (s + 1.0 / tau) ** 2 for a, tau in exps)


def _correction_analog(pf: PortFilter):
    """Zeros/poles/gain of the correction C(s) = 1/H(s), plus stability notes.

    [derived] The correction's zeros are H's poles (-1/tau_i); its poles are
    H's zeros (roots of the numerator), found with np.roots and then polished
    by Newton steps on the rational H itself, whose evaluation is well
    conditioned even when the taus span six decades. The gain is
    1 / (A_dc + sum A_i), H's value at s -> infinity. Evidence that the
    hardware realizes exactly 1/H: the QOP compiler's warnings on a real
    20-port chip matched these roots to 10 significant digits
    (D:\\work\\study\\2026-09-01_iir-filter-and-flux-waveform, section 3.2).
    """
    notes: list[dict] = []
    exps = _merge_equal_taus(pf.exponential)
    lead = pf.dc_gain + sum(a for a, _ in exps)
    if abs(lead) < 1e-12:
        notes.append(_note("block", "improper",
                           "A_dc + sum A = 0: the line model has no high-frequency response, so its "
                           "correction would need infinite gain. No curve."))
        return None, None, None, notes
    zeros = np.array([-1.0 / tau for _, tau in exps], dtype=complex)
    num, _den = _distortion_rational(exps, pf.dc_gain)
    raw = np.roots(num) if len(num) > 1 else np.array([], dtype=complex)
    poles = []
    for r in raw:
        s = complex(r)
        for _ in range(30):
            try:
                h = _h_eval(exps, pf.dc_gain, s)
                d = _hprime_eval(exps, s)
                if d == 0:
                    break
                step = h / d
            except ZeroDivisionError:     # s landed exactly on a pole of H
                break
            s -= step
            if abs(step) <= 1e-15 * max(abs(s), 1e-12):
                break
        poles.append(s)
    poles = np.array(poles, dtype=complex)
    k = 1.0 / lead
    scale = max([1.0 / tau for _, tau in exps] or [1.0])
    for p in poles:
        resid = _h_resid(exps, pf.dc_gain, p) if abs(p) > 0 else abs(pf.dc_gain)
        if resid > 1e-6 * max(1.0, abs(lead)):
            notes.append(_note("block", "ill_conditioned",
                               "The exponential set is numerically ill-conditioned (a root of the line "
                               "model could not be pinned down). No curve rather than a guessed one."))
            return None, None, None, notes
    unstable = [p for p in poles if p.real > 1e-12 * scale]
    if unstable:
        tau_bad = ", ".join(f"{1.0 / abs(p.real):.4g} ns" for p in unstable[:3])
        notes.append(_note("block", "unstable",
                           "The correction for these exponentials is UNSTABLE (the line model has a zero "
                           f"in the right half-plane, growth time {tau_bad}): the output would diverge. "
                           "Usually an A <= -1, or several A whose sum crosses -A_dc."))
        return None, None, None, notes
    return zeros, poles, k, notes


def correction_zpk(pf: PortFilter, model: str = "sum"):
    """Discrete (z-domain) zeros, poles, gain of the IIR correction at 2 GSa/s.

    ``model="sum"`` (QOP >= 3.5): one correction for the whole sum-form line
    model. ``model="cascade"`` (QOP <= 3.4): each ``(A, tau)`` corrected on its
    own and the stages cascaded -- [paper: output_filter.md "The filters are
    cascaded sequentially, such that the output of one filter is the input of
    the next one."]. [derived] Bilinear transform at fs = 2 GSa/s.
    Returns ``None`` when the set is unusable (parse_port_filter says why).
    """
    fs_per_ns = 1.0 / TS_NS
    stages = _analog_stages(pf, model)
    if stages is None:
        return None
    zs, ps, k = [], [], 1.0
    for z, p, kk in stages:
        zd, pd, kd = signal.bilinear_zpk(z, p, kk, fs_per_ns)
        zs.extend(zd); ps.extend(pd); k *= kd
    return np.array(zs), np.array(ps), k


def _cascade_stage_filters(pf: PortFilter) -> list[PortFilter]:
    """The QOP <= 3.4 cascade, one single-exponential PortFilter per stage.

    Every plain stage has A_dc = 1: the QOP 3.4-and-earlier section of the
    docs configures the IIR stage with two fields only [paper:
    output_filter.md "To configure the IIR filters, set the filter parameters
    in the `exponential` and `high-pass` fields."] -- no
    ``exponential_dc_gain`` -- so a stored dc gain is not applied here
    (``model_notes`` says so on the page).

    The high-pass stage (appended LAST by ``parse_port_filter``) is leaky on
    that QOP [paper: output_filter.md "Note that a decay of 0.5 seconds is
    automatically added to the high-pass filter."], built in the docs' own
    leaky form, A_dc small and A_1 = 1 - A_dc [paper: output_filter.md
    "In most cases, it is better to set"] with A_dc = tau_hp / 0.5 s [paper:
    output_filter.md "the decay time will be 0.5 seconds."].
    """
    exps = list(pf.exponential)
    out = []
    for i, (a, tau) in enumerate(exps):
        if pf.high_pass is not None and i == len(exps) - 1:
            adc = pf.high_pass / HP_AUTO_DECAY_NS
            out.append(PortFilter(((1.0 - adc, tau),), adc))
        else:
            out.append(PortFilter(((a, tau),), 1.0))
    return out


def _analog_stages(pf: PortFilter, model: str):
    """Analog (z, p, k) of each IIR correction stage, or None if unusable."""
    if model == "cascade":
        out = []
        for one in _cascade_stage_filters(pf):
            z, p, kk, _ = _correction_analog(one)
            if z is None:
                return None
            out.append((z, p, kk))
        return out
    z, p, k, _ = _correction_analog(pf)
    if z is None:
        return None
    return [(z, p, k)]


def model_dc_gain(pf: PortFilter, model: str = "sum") -> float:
    """The line model's DC value A_dc as the given model draws it (the
    correction's DC gain is its inverse). Cascade: the product of the stages'
    A_dc (1 for every plain stage)."""
    if model == "cascade":
        g = 1.0
        for one in _cascade_stage_filters(pf):
            g *= one.dc_gain
        return g
    return pf.dc_gain


def model_notes(pf: PortFilter, model: str = "sum") -> list[dict]:
    """Notes that depend on which QOP model is drawn."""
    notes: list[dict] = []
    if model == "cascade":
        if pf.high_pass is None and pf.dc_gain != 1.0:
            notes.append(_note("warn", "dc_gain_not_in_cascade",
                               f"exponential_dc_gain = {pf.dc_gain:g} is a QOP >= 3.5 field; QOP <= 3.4 "
                               "has no such field, so the cascade curve is drawn with A_dc = 1."))
        if pf.high_pass is not None:
            notes.append(_note("info", "hp_cascade_decay",
                               "QOP <= 3.4 adds a 0.5 s decay to the high-pass: drawn as A_dc = "
                               f"tau_hp / 0.5 s = {pf.high_pass / HP_AUTO_DECAY_NS:.3g}."))
    return notes


def correction_sos(pf: PortFilter, model: str = "sum"):
    """The IIR correction as second-order sections (numerically safe for taus
    spanning 1 ns .. 1 ms), or ``None``. Identity when there is no IIR."""
    if not pf.has_iir:
        return np.array([[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]])
    zpk = correction_zpk(pf, model)
    if zpk is None:
        return None
    z, p, k = zpk
    return signal.zpk2sos(z, p, k)


def apply_filters(pf: PortFilter, x, *, iir: bool = True, fir: bool = True,
                  model: str = "sum") -> np.ndarray:
    """Output of the port's filters for 2 GSa/s input samples *x*.

    [derived] Both stages are linear and time-invariant, so the order of IIR
    and FIR does not change the result (only where clipping happens, which is
    checked on the final output). IIR first, then FIR taps spaced 0.5 ns
    [paper: "each FIR tap is 0.5 ns"].
    """
    y = np.asarray(x, dtype=float)
    if iir and pf.has_iir:
        sos = correction_sos(pf, model)
        if sos is None:
            raise ValueError("unusable exponential set")
        y = signal.sosfilt(sos, y)
    if fir and pf.has_fir:
        y = signal.lfilter(np.asarray(pf.feedforward), [1.0], y)
    return y


# ---------------------------------------------------------------------------
# Responses shipped to the page
# ---------------------------------------------------------------------------

def _slowest_correction_ns(pf: PortFilter, model: str) -> float:
    """[derived] The slowest time constant of the CORRECTION's step response.

    C's step response is a sum of C's own modes exp(p t) over C's poles p
    (H's zeros). The line model's taus are C's ZEROS: they do not set how
    long the output takes to settle. A leaky high-pass settles with
    tau_hp / A_dc, far beyond tau_hp [paper: output_filter.md "which would
    lead to a long decay with a time constant of"]. A pole at 0 (the ideal
    integrator) never settles: inf.
    """
    if not pf.has_iir:
        return 0.0
    stages = _analog_stages(pf, model)
    if stages is None:
        return max([tau for _, tau in pf.exponential] or [0.0])
    slow = 0.0
    for _z, p, _k in stages:
        for pole in p:
            re = abs(complex(pole).real)
            slow = max(slow, math.inf if re == 0.0 else 1.0 / re)
    return slow


def _step_horizon_ns(pf: PortFilter, model: str = "sum") -> tuple[float, bool]:
    slow = _slowest_correction_ns(pf, model)
    want = max(STEP_MIN_NS, STEP_TAU_MULT * slow, TS_NS * (len(pf.feedforward) + 50))
    return min(want, STEP_MAX_NS), want > STEP_MAX_NS


def _decimate_idx(n: int) -> np.ndarray:
    dense = int(STEP_DENSE_NS / TS_NS)
    head = np.arange(0, min(n, dense))
    if n <= dense:
        return head
    tail = np.unique(np.geomspace(dense, n - 1, STEP_LOG_POINTS).astype(int))
    return np.unique(np.concatenate([head, tail]))


def step_response(pf: PortFilter, *, model: str = "sum") -> dict:
    """The unit step through the port: ideal vs IIR-only vs FIR-only vs both.

    Time of sample n is ``(n + 1) * 0.5 ns`` -- the END of the sample interval
    it is held for -- so the first output sample sits at 0.5 ns and a log axis
    can show it. Shipped decimated (dense first 100 ns, log-spaced after);
    ``peak``/``first``/``final`` are computed on the FULL-rate output.
    """
    horizon, truncated = _step_horizon_ns(pf, model)
    n = int(round(horizon / TS_NS))
    x = np.ones(n)
    both = apply_filters(pf, x, model=model)
    iir_only = apply_filters(pf, x, fir=False, model=model) if pf.has_iir else None
    fir_only = apply_filters(pf, x, iir=False) if pf.has_fir else None
    idx = _decimate_idx(n)
    t = (idx + 1) * TS_NS

    def pick(a):
        return None if a is None else [float(v) for v in a[idx]]

    ipk = int(np.argmax(np.abs(both)))
    adc = model_dc_gain(pf, model)
    return {
        "t_ns": [float(v) for v in t],
        "ideal": [1.0] * len(idx),
        "both": pick(both),
        "iir_only": pick(iir_only),
        "fir_only": pick(fir_only),
        "horizon_ns": horizon,
        "truncated": truncated,
        "first": float(both[0]),
        "peak": float(both[ipk]),
        "peak_t_ns": float((ipk + 1) * TS_NS),
        "final": float(both[-1]),
        # [derived] the DC limit of the model DRAWN: C(0) * sum(FIR) =
        # sum(FIR) / A_dc(model) -- the cascade's A_dc is not the stored one.
        "dc_limit": (sum(pf.feedforward) if pf.has_fir else 1.0) / adc
        if adc != 0 else None,
    }


def upsample_to_2gs(samples, pf: PortFilter, *, input_rate: float = 1e9
                    ) -> tuple[np.ndarray, list[dict]]:
    """Pulse samples at *input_rate* -> the 2 GSa/s stream the filters see.

    [paper: opx1000_fems.md "`pulse` - In this mode, the upsampling is done by
    doubling the 1 GSa/s samples (essentially, a 0-order interpolation
    filter), which produces a clean step response."] 2 GSa/s input passes
    straight through. The ``mw`` mode's 14-tap filter is unpublished:
    approximated by doubling, and the returned note says so. SM's synthesizer
    draws one sample per ns; on a 2e9 port that is doubled too, with a note.
    """
    x = np.asarray(samples, dtype=float)
    if input_rate == 2e9:
        return x, []
    notes = []
    if pf.sampling_rate == 2e9:
        notes.append(_note("warn", "rate_mismatch",
                           "This port runs at 2 GSa/s, but SM draws the pulse at 1 sample per ns; "
                           "it is shown sample-doubled, so fine structure below 1 ns is not the lab's."))
    elif pf.upsampling_mode == "mw":
        notes.append(_note("warn", "mw_upsampling",
                           "This port upsamples in 'mw' mode (a 14-tap filter QM does not publish). "
                           "The pulse is shown sample-doubled, so its edges are approximate; "
                           "'pulse' mode is the one QM recommends for flux lines."))
    return np.repeat(x, 2), notes


def pulse_response(pf: PortFilter, samples, *, pad_before_ns: float = 10.0,
                   pad_after_ns: float | None = None, model: str = "sum",
                   input_rate: float = 1e9) -> dict:
    """One flux pulse (1 GSa/s, or 2 GSa/s on a 2e9 port) through the port.

    Returns the ideal (upsampled) and filtered traces on a 0.5 ns grid, the
    peak output, and a clipping note against the port's output range
    [paper: output_filter.md "If the output is outside the allowed range, it
    is clipped to the nearest allowed value according to its sign."]. The DC
    offset is not included: the filters run "before the DC Offset".
    """
    x2, notes = upsample_to_2gs(samples, pf, input_rate=input_rate)
    dur = len(x2) * TS_NS
    if pad_after_ns is None:
        pad_after_ns = max(100.0, min(2.0 * dur, 2000.0))
    nb = int(round(pad_before_ns / TS_NS))
    na = int(round(pad_after_ns / TS_NS))
    x = np.concatenate([np.zeros(nb), x2, np.zeros(na)])
    y = apply_filters(pf, x, model=model)
    t = (np.arange(len(x)) - nb) * TS_NS
    ideal_pk = float(np.max(np.abs(x))) if len(x) else 0.0
    out_pk = float(np.max(np.abs(y))) if len(y) else 0.0
    rng = OUTPUT_RANGE_V[pf.output_mode]
    if out_pk > rng:
        notes.append(_note("warn", "clips",
                           f"Peak output {out_pk:.4g} V exceeds the {pf.output_mode} range "
                           f"(+/-{rng:g} V): the OPX clips it."))
    return {
        "t_ns": [float(v) for v in t],
        "ideal": [float(v) for v in x],
        "both": [float(v) for v in y],
        "ideal_peak": ideal_pk,
        "peak": out_pk,
        "ratio": (out_pk / ideal_pk) if ideal_pk > 0 else None,
        "residual_after": float(y[-1]) if len(y) else 0.0,
        "range_v": rng,
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# From the state: which port feeds this z line
# ---------------------------------------------------------------------------

def resolve_zline(merged: dict, channel_path: str) -> dict:
    """Follow ``<channel_path>.opx_output`` through its pointer chain to the
    analog-output port dict. Never raises; a dead end is a ``block`` note."""
    from .pointer_path import _walk, resolve_field_target
    from .pointer_resolver import is_pointer

    out = {"channel_path": channel_path, "port_path": None, "port": None,
           "chain": [], "notes": []}
    found, ch = _walk(merged, channel_path.split("."))
    if not found or not isinstance(ch, dict):
        out["notes"].append(_note("block", "no_channel", f"{channel_path} is not in this state."))
        return out
    raw = ch.get("opx_output")
    if raw is None:
        out["notes"].append(_note("block", "no_output", f"{channel_path} has no opx_output."))
        return out
    if isinstance(raw, (list, tuple)):
        out["notes"].append(_note("block", "tuple_port",
                                  f"{channel_path}.opx_output is a bare port address {list(raw)!r}; "
                                  "a port given that way has no filter entry in the state."))
        return out
    target = resolve_field_target(merged, channel_path + ".opx_output")
    out["chain"] = [h.get("pointer") for h in target.get("chain") or []]
    if not target.get("resolvable"):
        last = out["chain"][-1] if out["chain"] else raw
        out["notes"].append(_note("block", "dangling",
                                  f"{channel_path}.opx_output is DANGLING: {last!r} points at nothing."))
        return out
    path = target.get("resolved_path")
    found, port = _walk(merged, path.split(".")) if path else (False, None)
    if not found or not isinstance(port, dict) or is_pointer(port):
        out["notes"].append(_note("block", "not_port", f"{channel_path}.opx_output resolves to {path!r}, not a port."))
        return out
    if not path.startswith("ports.analog_outputs."):
        out["notes"].append(_note("block", "not_lf_port",
                                  f"{channel_path}.opx_output resolves to {path}; output filters "
                                  "exist on LF analog outputs only."))
        return out
    out["port_path"] = path
    out["port"] = port
    return out


def zline_entities(merged: dict) -> list[dict]:
    """Every flux line on the chip: each qubit's ``z`` and each pair's
    ``coupler`` that has an ``opx_output``. Sorted naturally by id."""
    import re

    def nat(s):
        return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]

    rows = []
    for q in sorted((merged.get("qubits") or {}), key=nat):
        z = (merged["qubits"][q] or {}).get("z") if isinstance(merged["qubits"][q], dict) else None
        if isinstance(z, dict) and "opx_output" in z:
            rows.append({"id": q, "kind": "qubit", "channel_path": f"qubits.{q}.z"})
    for p in sorted((merged.get("qubit_pairs") or {}), key=nat):
        pair = merged["qubit_pairs"][p]
        c = pair.get("coupler") if isinstance(pair, dict) else None
        if isinstance(c, dict) and "opx_output" in c:
            rows.append({"id": p, "kind": "coupler", "channel_path": f"qubit_pairs.{p}.coupler"})
    return rows


def zline_row(merged: dict, ent: dict) -> dict:
    """Cheap per-line summary for the table (no filtering run)."""
    return row_from_resolved(ent, resolve_zline(merged, ent["channel_path"]))


def row_from_resolved(ent: dict, r: dict, parse=None) -> dict:
    """The table row for one line from its ``resolve_zline`` result. *parse*
    (default ``parse_port_filter``) lets a caller hand in a memoized parse."""
    row = dict(ent)
    row.update({"port_path": r["port_path"], "notes": list(r["notes"]),
                "n_exp": None, "n_taps": None, "ff_sum": None, "ok": False})
    if r["port"] is None:
        return row
    pf, notes = (parse or parse_port_filter)(r["port"])
    row["notes"].extend(notes)
    port = r["port"]
    ex = port.get("exponential_filter")
    ff = port.get("feedforward_filter")
    row["n_exp"] = len(ex) if isinstance(ex, (list, tuple)) else 0
    row["n_taps"] = len(ff) if isinstance(ff, (list, tuple)) else 0
    if pf is not None:
        row["ok"] = True
        row["ff_sum"] = sum(pf.feedforward) if pf.has_fir else None
    return row
