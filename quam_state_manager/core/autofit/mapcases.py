"""From a measured map to a SIGNAL — the family-neutral half of reading.

The reader measures; the manual names. ``signal_for`` returns semantic keys
("the ridge arches and its turning point is inside the window", "the claim
sits off the visible feature") together with the numbers behind them, and the
knowledge pack maps those keys onto ITS case vocabulary and prescriptions
(``signal_map`` in ``cases.json``).

Keeping the two apart is what lets the manual be revised — cases renamed,
split, merged, a prescription rewritten after a lab disagrees — without
touching the code that reads pixels, and conversely lets the reader improve
without renaming anything a human wrote. It is also the seam a vision judge
slots into: a judge returns a case id, exactly what ``signal_map`` produces,
and the numbers stay on this side of the line.

Signals are deliberately few and blunt. A reader that cannot tell returns
``None``, which never adopts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from quam_state_manager.core.autofit import mapshapes as MS

# ---------------------------------------------------------------------------
# semantic vocabulary
# ---------------------------------------------------------------------------

# 1-D spectroscopy
LINE_CLEAN = "line_clean"
LINE_EMPTY = "line_empty"
LINE_EDGE = "line_edge_clipped"
LINE_MULTI = "line_multi_feature"
LINE_WEAK = "line_weak_broad"
LINE_SPLIT = "line_split_flat_top"
LINE_FANO = "line_fano_asymmetric"

# a swept curve (flux, coupler flux)
CURVE_ARCH = "curve_arch_vertex_inside"
CURVE_MONOTONIC = "curve_monotonic_vertex_outside"
CURVE_FLAT = "curve_flat_no_response"
CURVE_PARTIAL = "curve_partial_ridge"
CURVE_BROKEN = "curve_broken_ridge"
CURVE_FULL_SWING = "curve_full_swing"
CURVE_MULTI = "curve_multi_period"
CURVE_EMPTY = "curve_empty"

# flags, all cross-family
FLAG_OFF_FEATURE = "fit_off_feature"
FLAG_OUT_OF_WINDOW = "value_outside_swept_range"
FLAG_EDGE_VALUE = "value_at_window_edge"
FLAG_EXTRAPOLATED = "record_admits_extrapolation"
FLAG_REFUSED_READABLE = "refused_a_readable_map"
FLAG_ACCEPTED_EMPTY = "accepted_an_empty_map"
FLAG_BATCH_MOSTLY_FAILED = "batch_mostly_failed"
FLAG_UNSTABLE = "unstable_across_identical_runs"
FLAG_LOW_COVERAGE = "ridge_lost_over_much_of_the_sweep"
FLAG_FIT_ON_WRONG_SIDE = "fit_on_the_companion_not_the_notch"
FLAG_OVER_BROADENED = "line_far_wider_than_the_node_asked_for"
# How many times its own declared target width a line may be before its
# centre stops being worth adopting. The node states the target it is trying
# to reach (``target_peak_width``), so this is the node's own yardstick and
# not a constant invented here; the factor is deliberately generous, because
# the failure it catches is 5-16x, not 3.1x.
BROADEN_FACTOR = 3.0

# Which fit field carries the frequency answer, and which carries the swept
# value the node picked. Both are family facts, taken from the records.
VALUE_FIELDS = {
    "resonator_spectroscopy": ("frequency", None),
    "qubit_spectroscopy": ("frequency", None),
    "qubit_spectroscopy_vs_flux": ("qubit_frequency", "idle_offset"),
    "resonator_spectroscopy_vs_flux": ("resonator_frequency", "idle_offset"),
    "resonator_spectroscopy_vs_coupler_flux": ("resonator_frequency", "idle_offset"),
}
SWEEP_PARAM_RANGE = {
    "qubit_spectroscopy_vs_flux": ("min_flux_offset_in_v", "max_flux_offset_in_v"),
    "resonator_spectroscopy_vs_flux": ("min_flux_offset_in_v", "max_flux_offset_in_v"),
    "resonator_spectroscopy_vs_coupler_flux": ("min_flux_offset_in_v",
                                               "max_flux_offset_in_v"),
}


# Where the PHYSICS fixes which way the feature points, it is not measured.
# A readout resonator watched in |I+iQ| is a transmission notch, always — and
# on a Fano-asymmetric trace the tallest excursion is the COMPANION peak, not
# the resonance, so letting the orientation probe choose puts the reader on
# the wrong feature (measured: one lab's entire 1-D readout set read as
# "peak"). Families whose value is a rotated projection keep measuring their
# sign, because there it really is a convention — and it differs between labs
# and between qubits within one run.
FAMILY_SIGN: dict[str, int] = {
    "resonator_spectroscopy": -1,
}


@dataclass
class ShapeSignal:
    key: str | None
    flags: list[str] = field(default_factory=list)
    confidence: str = "med"
    reasons: list[str] = field(default_factory=list)
    measured: dict = field(default_factory=dict)
    corrected: dict = field(default_factory=dict)


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(float(v)) else None


def signal_for(family: str, folder, target: str, *, fit: dict | None = None,
               params: dict | None = None, outcomes: dict | None = None,
               prior_keys: list[str] | None = None) -> ShapeSignal:
    """Read one run for one target and say what shape it carries."""
    fit = fit or {}
    params = params or {}
    cube = MS.read_cube(folder, target)
    if cube is None:
        return ShapeSignal(key=None, confidence="low",
                           reasons=["raw map unreadable — nothing to read"])
    forced = FAMILY_SIGN.get(family)
    sign = forced if forced is not None else MS.orient(cube)
    sig = ShapeSignal(key=None)
    sig.measured["feature"] = "dip" if sign < 0 else "peak"
    sig.measured["sign_source"] = "physics" if forced is not None else "measured"

    if cube.n_sweep <= 1:
        _line(cube, sign, fit, sig)
        if forced is not None:
            # The companion check belongs to the families whose feature
            # direction is fixed by physics — that is where "the tallest
            # excursion is not the resonance" is a KNOWN hazard. On a rotated
            # projection an opposite-sign excursion nearby is ordinary
            # background structure, and treating it as a Fano partner turned
            # clean qubit lines into an unnameable case.
            _companion(cube, sign, fit, sig)
    else:
        _curve(cube, sign, fit, params, family, sig)

    _common_flags(family, cube, fit, params, outcomes, sig)
    if prior_keys and sig.key and prior_keys and prior_keys[-1] and \
            _disagrees(prior_keys[-1], sig.key):
        sig.flags.append(FLAG_UNSTABLE)
        sig.reasons.append(f"the previous run of this target read "
                           f"{prior_keys[-1]!r} and this one reads "
                           f"{sig.key!r} — the feature is flickering, not the "
                           f"settings")
    return sig


def _disagrees(a: str, b: str) -> bool:
    """A readable/unreadable flip between consecutive runs is instability;
    two shades of readable are not."""
    good = {LINE_CLEAN, LINE_FANO, CURVE_ARCH, CURVE_FULL_SWING,
            CURVE_MONOTONIC, CURVE_MULTI}
    bad = {LINE_EMPTY, CURVE_EMPTY, CURVE_FLAT, LINE_WEAK}
    return (a in good and b in bad) or (a in bad and b in good)


# ---------------------------------------------------------------------------

def _line(cube, sign, fit, sig: ShapeSignal) -> None:
    ln = MS.shape_line(cube, sign=sign)
    step = cube.freq_step()
    sig.measured.update({"depth_z": round(ln.depth_z, 2),
                         "width_px": ln.width_px,
                         "n_significant": ln.n_significant,
                         "near_edge": ln.near_edge})
    if ln.pos_px is None:
        sig.key, sig.confidence = LINE_EMPTY, "high"
        sig.reasons.append("no feature clears the noise anywhere in the window")
        return
    seen_hz = cube.freq_at(ln.pos_px)
    sig.measured["feature_hz"] = seen_hz
    if ln.near_edge and ln.truncated:
        sig.key, sig.confidence = LINE_EDGE, "high"
        sig.reasons.append("the feature runs off a window edge, so its centre "
                           "is not measurable here")
        return
    if ln.n_significant >= 2:
        sig.key, sig.confidence = LINE_MULTI, "med"
        sig.reasons.append(f"{ln.n_significant} separate features clear the "
                           f"noise — which one is the qubit is not decided by "
                           f"this map alone")
        return
    if ln.flat_top:
        sig.key, sig.confidence = LINE_SPLIT, "med"
        sig.reasons.append("the line is flat-topped rather than peaked — the "
                           "shape of an over-driven or split transition")
        return
    if ln.depth_z < 2.0 * MS.z_bar(cube.n_freq):
        sig.key, sig.confidence = LINE_WEAK, "med"
        sig.reasons.append("a feature is present but shallow against the noise")
        return
    sig.key, sig.confidence = LINE_CLEAN, "high"
    sig.reasons.append("one resolved feature standing clear of the noise")
    claim = _num(fit.get("frequency"))
    if claim is not None and seen_hz is not None and step:
        off = abs(claim - seen_hz)
        sig.measured["claim_offset_hz"] = off
        if off > 3.0 * max(step * ln.width_px, step):
            sig.flags.append(FLAG_OFF_FEATURE)
            sig.reasons.append("the claimed frequency sits several linewidths "
                               "off the feature the map carries")
            sig.corrected["frequency"] = seen_hz


def _companion(cube, sign, fit, sig: ShapeSignal) -> None:
    """A Fano-asymmetric line: the resonance notch with a comparable or larger
    excursion of the OPPOSITE sense immediately beside it.

    Worth its own name rather than folding into "multi-feature": it is ONE
    resonance, not two features, and the danger it carries is specific — the
    companion is frequently the taller thing in the panel, so any rule that
    reaches for the largest excursion lands on it. Recording where the
    companion sits is what lets the flag below say whether the record fell
    for it, and hand back the notch instead.
    """
    ln = MS.shape_line(cube, sign=sign)
    other = MS.shape_line(cube, sign=-sign)
    if ln.pos_px is None or other.pos_px is None:
        return
    gap = abs(other.pos_px - ln.pos_px)
    if not (gap <= max(4.0, 4.0 * max(1.0, ln.width_px))
            and other.depth_z >= 0.5 * ln.depth_z):
        return
    sig.measured["companion_px"] = other.pos_px
    sig.measured["companion_depth_z"] = round(other.depth_z, 2)
    if sig.key in (LINE_CLEAN, LINE_MULTI, LINE_WEAK):
        sig.key = LINE_FANO
        sig.reasons.append("a notch with a comparable excursion of the "
                           "opposite sense immediately beside it — one "
                           "asymmetric resonance, not two features")
    claim = _num(fit.get("frequency"))
    comp_hz = cube.freq_at(other.pos_px)
    seen_hz = cube.freq_at(ln.pos_px)
    if (claim is not None and comp_hz is not None and seen_hz is not None
            and abs(claim - comp_hz) < abs(claim - seen_hz)):
        sig.flags.append(FLAG_FIT_ON_WRONG_SIDE)
        sig.reasons.append("the claimed frequency sits closer to the "
                           "companion than to the notch — the taller "
                           "excursion is not the resonance")
        sig.corrected["frequency"] = seen_hz


def _curve(cube, sign, fit, params, family, sig: ShapeSignal) -> None:
    tr = MS.track_ridge(cube, sign=sign)
    sh = MS.shape_curve(cube, tr)
    sig.measured.update({"coverage": round(tr.coverage, 3),
                         "background": tr.background,
                         "span_px": round(sh.span_px, 1),
                         "width_px": tr.width_px,
                         "moves": sh.moves, "breaks": sh.breaks,
                         "vertex_inside": sh.vertex_inside,
                         "extremum_inside": sh.extremum_inside,
                         "curvature_significant": sh.curvature_significant,
                         "periods": sh.periods,
                         "turns": sh.turns})
    if tr.n_traceable < max(3, int(round(0.10 * cube.n_sweep))):
        sig.key, sig.confidence = CURVE_EMPTY, "high"
        sig.reasons.append("no feature is traceable across the sweep")
        return
    if not sh.moves:
        # a feature the MOVING background cannot see did not move: that is a
        # flat line, which is a different problem from an empty window and
        # takes a different knob
        sig.key = CURVE_FLAT
        sig.confidence = "high" if tr.background == "static" else "med"
        sig.reasons.append("the feature holds its frequency across the whole "
                           "sweep — it is not responding to this knob")
        return
    if tr.coverage < 0.5:
        sig.key, sig.confidence = CURVE_PARTIAL, "med"
        sig.flags.append(FLAG_LOW_COVERAGE)
        sig.reasons.append("the ridge is visible over only part of the sweep, "
                           "so its shape is asserted rather than measured")
        return
    if sh.breaks >= 2:
        sig.key, sig.confidence = CURVE_BROKEN, "med"
        sig.reasons.append("the ridge is interrupted mid-sweep — the signature "
                           "of a crossing rather than of a clean response")
        return
    # more than ONE full period needs three turns: a single arch turns once,
    # a whole visible period turns twice
    if sh.turns >= 3:
        sig.key, sig.confidence = CURVE_MULTI, "med"
        sig.reasons.append("the ridge turns more than once across the window, "
                           "so 'the' operating point is ambiguous")
        return
    if sh.turns >= 2:
        # apex AND trough inside the window: the modulation's own scale is
        # measured here, which several manuals treat as a distinct (and
        # stronger) case than a single turning point
        sig.key, sig.confidence = CURVE_FULL_SWING, "high"
        sig.reasons.append("the ridge turns twice inside the window — both "
                           "extremes of the modulation are measured, not "
                           "extrapolated")
        sig.measured["vertex_sweep"] = sh.vertex_sweep
    elif sh.extremum_inside or (sh.vertex_inside and sh.curvature_significant):
        sig.key, sig.confidence = CURVE_ARCH, "high"
        sig.reasons.append("a continuous ridge that curves and turns inside "
                           "the swept range")
        sig.measured["vertex_sweep"] = sh.vertex_sweep
        sig.measured["vertex_hz"] = cube.freq_at(sh.vertex_px)
    else:
        sig.key, sig.confidence = CURVE_MONOTONIC, "high"
        sig.reasons.append("the ridge runs across the window without turning "
                           "— any turning point is outside what was swept")

    value_field, sweep_field = VALUE_FIELDS.get(family, (None, None))
    claim = _num(fit.get(value_field)) if value_field else None
    step = cube.freq_step()
    if claim is not None and step and tr.n_traceable:
        pos = tr.pos[tr.ok]
        seen = [cube.freq_at(p) for p in pos]
        seen = [s for s in seen if s is not None]
        if seen:
            near = min(abs(claim - s) for s in seen)
            sig.measured["claim_offset_hz"] = near
            if near > 3.0 * max(step * tr.width_px, step):
                sig.flags.append(FLAG_OFF_FEATURE)
                sig.reasons.append("the claimed frequency does not lie on the "
                                   "ridge the map carries at any point of the "
                                   "sweep")


def _common_flags(family, cube, fit, params, outcomes, sig: ShapeSignal) -> None:
    value_field, sweep_field = VALUE_FIELDS.get(family, (None, None))

    # a swept value the node picked, checked against what was actually swept
    if sweep_field and cube.sweep is not None:
        v = _num(fit.get(sweep_field))
        lo, hi = float(cube.sweep.min()), float(cube.sweep.max())
        if v is not None and hi > lo:
            sig.measured[sweep_field] = v
            if v < lo or v > hi:
                sig.flags.append(FLAG_OUT_OF_WINDOW)
                sig.reasons.append(f"the chosen {sweep_field} lies outside the "
                                   f"range that was swept — the field is "
                                   f"invalid, not merely unlucky")
            elif min(v - lo, hi - v) / (hi - lo) <= 0.03:
                sig.flags.append(FLAG_EDGE_VALUE)
                sig.reasons.append(f"the chosen {sweep_field} sits on the very "
                                   f"edge of the swept range, where the sweep "
                                   f"could not see past it")

    # A line far wider than the node's own declared target is power-broadened,
    # and a broadened Lorentzian's centre is worth a fraction of what a
    # resolved one's is. Measured on a real session: at full drive every qubit
    # on the chip came back 5-16x the target width, and one of those centres
    # was 30 MHz from where two low-drive sweeps agreed. The node states the
    # target, so this compares the run against its own intent.
    fwhm = _num(fit.get("fwhm"))
    target = _num(params.get("target_peak_width"))
    if fwhm is not None and target and fwhm > BROADEN_FACTOR * target:
        sig.flags.append(FLAG_OVER_BROADENED)
        sig.measured["fwhm_over_target"] = round(fwhm / target, 1)
        sig.reasons.append(f"the fitted line is {fwhm / target:.0f}x the width "
                           f"this node was asked to reach — power-broadened, "
                           f"and its centre is worth correspondingly less")

    # the record's own admission
    if fit.get("vertex_extrapolated") is True:
        sig.flags.append(FLAG_EXTRAPOLATED)
        sig.reasons.append("the record itself says the turning point was "
                           "fitted outside the swept range")

    success = fit.get("success")
    readable = sig.key in (LINE_CLEAN, LINE_FANO, CURVE_ARCH,
                           CURVE_FULL_SWING, CURVE_MONOTONIC, CURVE_MULTI)
    if success is False and readable:
        sig.flags.append(FLAG_REFUSED_READABLE)
        sig.reasons.append("the node refused a map that carries a readable "
                           "feature")
    elif success is True and sig.key in (LINE_EMPTY, CURVE_EMPTY):
        sig.flags.append(FLAG_ACCEPTED_EMPTY)
        sig.reasons.append("the node accepted a map that supports nothing")

    # "Most of the batch failed" needs a denominator big enough for "most" to
    # mean something. The rule was derived from an eight-qubit run that failed
    # seven; applied to a PAIR it fires whenever one of two qubits fails,
    # which is an ordinary outcome and vetoed clean, well-fitted lines on the
    # qubit that worked.
    if params.get("multiplexed") and outcomes and len(outcomes) >= 4:
        failed = sum(1 for o in outcomes.values() if o != "successful")
        if failed / float(len(outcomes)) > 0.5:
            sig.flags.append(FLAG_BATCH_MOSTLY_FAILED)
            sig.reasons.append(f"{failed} of {len(outcomes)} targets in this "
                               f"multiplexed run failed — a shared budget that "
                               f"missed most of its targets is not evidence "
                               f"for the rest")
