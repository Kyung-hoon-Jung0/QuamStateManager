"""CZ gate plan: where each CZ operation of a pair moves its qubits in frequency.

Read-only. Every number comes from the chip's stored fields, resolved through
SM's one pointer resolver; nothing here writes.

Flux -> frequency, per qubit (decided by the product owner, 2026-10-15):

* **quad-term estimate** (the default, every chip): ``f = f_01 + s * |q| * A**2``
  with ``q = freq_vs_flux_01_quad_term`` and ``A`` the operation's amplitude.
  [derived] -- the second-order Taylor expansion of an even flux arc about its
  sweet spot; no paper states it for this use, so it is checked numerically
  against the fitted model (:func:`quadratic_range`). The sign ``s`` is NOT the
  coefficient's sign (labs store it both ways); it is decided from evidence the
  chip carries (:func:`frame`) and refused when the evidence disagrees.
* **lab flux model** (when the qubit carries ``extras.lffem_flux_model``): the
  fitted asymmetric-transmon arc, transcribed from the lab's own evaluator and
  pinned to its output (``tests/golden/cz_flux_model.json``). Exact when the
  lab's own acceptance rule holds (:func:`lab_rejection`); when the qubit now
  sits at the OTHER sweet spot of the same arc, the arc is evaluated about that
  spot instead ([derived], labelled so).

Resonances of the |11> manifold for a pair (control c, target t), with
``Delta = f_c - f_t`` and ``|alpha|`` the anharmonicity magnitudes:
|11>-|20> at ``Delta = +|alpha_c|``, |11>-|02> at ``Delta = -|alpha_t|``,
|10>-|01> (iSWAP) at ``Delta = 0``. [paper: arXiv:1903.02492v1, p.1] "of
resonance between |01⟩and |10⟩for iSWAP" ... "|11⟩and |02⟩or |20⟩for
conditional-phase (CZ)"; the signed condition is [paper: arXiv:1903.02492v1,
p.8] "by deﬁnition ϵ(t) = ω|02⟩(t) −ω|11⟩(t) = ωqH(t) + ηqH −ωqM" (the qubit
carrying |2> is qH: epsilon = 0 at f_H - f_M = -eta_H = |alpha_H|).
"""
from __future__ import annotations

import json
import math

import numpy as np

from quam_state_manager.core import value_history

QUAD_LABEL = "quad-term estimate"
MODEL_LABEL = "lab flux model"
OTHER_SPOT_LABEL = "lab flux model, about the other sweet spot [derived]"

#: the lab's acceptance rule for a stored fit (transcribed from its resolver)
F01_TOLERANCE_HZ = 5e6
MIN_R2 = 0.99
#: tolerance of the quad-vs-model validity band (stated, product decision)
ERROR_TOLERANCE_HZ = 10e6
#: f_01 within this fraction of the sweet-spot separation counts as "at" that
#: spot of the fitted arc [derived]
SPOT_FRACTION = 0.10
#: |quad| this far from the model's local curvature is reported [derived]
CURVATURE_MISMATCH = 0.25
#: quad-term validity for a qubit without a fitted arc, by the sweet spot it sits at:
#: the smallest shift at which a quadratic with the arc's OWN local curvature is off
#: by ERROR_TOLERANCE_HZ, measured over 29 fitted arcs at their sweet spots when this
#: view was built (upper: 193-431 MHz over 15 arcs; lower: 120-142 MHz over 14; an
#: arc fitted off its sweet spot excluded). Rounded down. [derived, measured]
GENERIC_LIMIT_HZ = {"upper": 190e6, "lower": 120e6}
#: a dependency changed this long after the operation's amplitude counts as newer
STALE_SLACK_US = 1_000_000


def number(value):
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def read(doc, path):
    """Resolve scalars AND objects through the one resolver (field history's)."""
    return value_history.target(doc, path, container=True)


def current(doc, path, default=None):
    t = read(doc, path)
    return t["current"] if t.get("has_current") else default


def resolved_tree(doc, path, seen=()):
    """Resolve each consumed descendant, including leaves of referenced containers."""
    target = read(doc, path)
    holder = target.get("holder_path")
    if holder in seen or len(seen) >= 16:
        return None
    value = target.get("current") if target.get("has_current") else None
    if isinstance(value, dict):
        return {k: resolved_tree(doc, path + "." + k, (*seen, holder)) for k in value}
    if isinstance(value, list):
        return [resolved_tree(doc, path + "." + str(i), (*seen, holder)) for i in range(len(value))]
    return value


# ---------------------------------------------------------------- lab model
_MODEL_KEYS = ("phi0_v", "phi_park", "d", "f_max_hz", "ec_hz", "amp_min_v", "amp_max_v",
               "f_01_at_fit_hz")


def fitted_frequency(model, phi):
    """The lab's ``LffemFluxModel.frequency_hz`` (flux in units of Phi0), transcribed."""
    phi_array = np.asarray(phi, dtype=float)
    inside = np.cos(np.pi * phi_array) ** 2 + model["d"]**2 * np.sin(np.pi * phi_array) ** 2
    return (model["f_max_hz"] + model["ec_hz"]) * inside**0.25 - model["ec_hz"]


def fitted_shift(model, amplitude):
    """The lab's ``FluxConversion.shift_hz`` for a model source, array form, transcribed:
    outside the measured amplitude range (+1 % of its larger bound) is NaN."""
    amp = np.asarray(amplitude, dtype=float)
    tolerance = 0.01 * max(abs(model["amp_min_v"]), abs(model["amp_max_v"]))
    invalid = (~np.isfinite(amp) | (amp < model["amp_min_v"] - tolerance)
               | (amp > model["amp_max_v"] + tolerance))
    result = (fitted_frequency(model, model["phi_park"] + amp / model["phi0_v"])
              - fitted_frequency(model, model["phi_park"]))
    return np.where(invalid, np.nan, result)


def model_of(qubit):
    """The stored fit as numbers, or None when it cannot be evaluated at all."""
    raw = (qubit.get("extras") or {}).get("lffem_flux_model")
    if not isinstance(raw, dict) or any(number(raw.get(k)) is None for k in _MODEL_KEYS):
        return None
    m = {k: float(raw[k]) for k in _MODEL_KEYS}
    for k in ("model_r2", "a_half_v", "f_opposite_hz", "quad_small_hz_per_v2"):
        m[k] = number(raw.get(k))
    m["created_at"] = raw.get("created_at") if isinstance(raw.get("created_at"), str) else None
    if m["phi0_v"] <= 0 or not 0 < m["d"] < 1 or m["amp_min_v"] >= m["amp_max_v"]:
        return None
    return m


def lab_rejection(model, f01):
    """The lab's ``_model_rejection_reason`` (no required-shift check), transcribed:
    None when the lab's own resolver would use this fit, else its reason."""
    r2 = model.get("model_r2")
    if r2 is None or r2 < MIN_R2:
        return f"model_r2 is missing or below {MIN_R2:g}"
    if model.get("a_half_v") is None:
        return "a_half_v is not finite"
    if f01 is None:
        return "f_01_at_fit_hz or qubit.f_01 is missing"
    if abs(f01 - model["f_01_at_fit_hz"]) > F01_TOLERANCE_HZ:
        return f"qubit.f_01 moved by more than {F01_TOLERANCE_HZ:g} Hz since the fit"
    return None


def spot_kind(phi):
    """``upper`` where cos(2 pi phi) >= 0 (the lab's arc rule), else ``lower``."""
    return "upper" if math.cos(2 * math.pi * phi) >= 0 else "lower"


def sweet_spot_phi(phi):
    """The lab's ``sweet_spot_phi``, transcribed: the nearest multiple of half a flux quantum."""
    return float(np.round(2.0 * float(phi)) / 2.0)


def model_spots(model):
    """Both sweet spots of the fitted arc, in the qubit's frequency frame.

    The fit's own park is ``f_01_at_fit_hz`` at ``phi_park``; the other spot is the
    arc's extremum half a flux quantum from the park's sweet spot, carried by the
    fit's offset from ``f_01_at_fit_hz`` [derived]. ``stored_other_hz`` is the lab's
    own ``f_opposite_hz`` beside it."""
    offset = model["f_01_at_fit_hz"] - float(fitted_frequency(model, model["phi_park"]))
    phi_other = sweet_spot_phi(model["phi_park"]) + 0.5
    other = float(fitted_frequency(model, phi_other)) + offset
    return {"fit": {"phi": model["phi_park"], "kind": spot_kind(model["phi_park"]),
                    "f_hz": model["f_01_at_fit_hz"]},
            "other": {"phi": phi_other, "kind": spot_kind(phi_other), "f_hz": other},
            "stored_other_hz": model.get("f_opposite_hz")}


def _at_spot(model, f01):
    """Which spot of the fitted arc the qubit sits at now: ``(name, spot, distance)``."""
    if f01 is None:
        return None
    spots = model_spots(model)
    sep = abs(spots["fit"]["f_hz"] - spots["other"]["f_hz"])
    name = min(("fit", "other"), key=lambda k: abs(spots[k]["f_hz"] - f01))
    dist = abs(spots[name]["f_hz"] - f01)
    if dist <= max(F01_TOLERANCE_HZ, SPOT_FRACTION * sep):
        return name, spots[name], dist
    return None


# ---------------------------------------------------------------- the sign
def frame(qubit):
    """Where the qubit is parked and which way flux moves it, from evidence only.

    The coefficient's own sign is never used (labs store it both ways). Each
    source names a direction; agreement gives the sign, disagreement gives
    "sign convention unclear", nothing gives "no sweet-spot evidence"."""
    z, extras = qubit.get("z") or {}, qubit.get("extras") or {}
    f = number(qubit.get("f_01"))
    m = model_of(qubit)
    evidence, notes = [], []
    point = z.get("flux_point")
    if point == "joint":
        evidence.append(("z.flux_point is joint (an upper sweet spot)", -1))
    elif point == "min":
        evidence.append(("z.flux_point is min (a lower sweet spot)", 1))
    at = _at_spot(m, f) if m else None
    if m and at:
        name, spot, dist = at
        where = "its fit" if name == "fit" else "the other sweet spot of its fit"
        evidence.append((f"lab flux model: f_01 sits at {where} ({spot['kind']})",
                         -1 if spot["kind"] == "upper" else 1))
    elif m and f is not None:
        notes.append("the lab flux model's sweet spots are both far from f_01")
    for label, direction in (("upper", -1), ("lower", 1)):
        sweet = number(extras.get(label + "_sweet_spot_frequency"))
        if f is not None and sweet is not None and abs(sweet - f) <= F01_TOLERANCE_HZ:
            evidence.append((f"extras.{label}_sweet_spot_frequency equals f_01", direction))
    opposite = number(extras.get("opposite_sweet_spot_qubit_frequency"))
    if f is not None and opposite is not None:
        here = at[1]["f_hz"] if at else None
        stale = abs(opposite - f) <= F01_TOLERANCE_HZ or (
            here is not None and m is not None
            and abs(opposite - here) <= SPOT_FRACTION * abs(model_spots(m)["fit"]["f_hz"]
                                                            - model_spots(m)["other"]["f_hz"]))
        if stale:
            notes.append("extras.opposite_sweet_spot_qubit_frequency names the spot the qubit sits at now"
                         " (recorded from the other spot); not used")
        else:
            evidence.append(("extras.opposite_sweet_spot_qubit_frequency is "
                             + ("above" if opposite > f else "below") + " f_01", 1 if opposite > f else -1))
    directions = {d for _, d in evidence}
    sign = next(iter(directions)) if len(directions) == 1 else None
    reason = ("sign convention unclear" if len(directions) > 1 else
              "no sweet-spot evidence" if not directions else
              "upper sweet spot: flux moves it down" if sign == -1 else
              "lower sweet spot: flux moves it up")
    if extras.get("at_sweet_spot") is False:
        sign, reason = None, "extras.at_sweet_spot is false: no sweet spot to expand about"
    return {"sign": sign, "reason": reason, "notes": notes,
            "spot": ("upper" if sign == -1 else "lower" if sign == 1 else None),
            "model_anchor": at[0] if at else None,
            "evidence": [{"source": k, "direction": "down" if d < 0 else "up"} for k, d in evidence]}


# ---------------------------------------------------------------- one qubit
class Conversion:
    """One qubit's flux -> frequency, decided once (frame, model, validity)."""

    def __init__(self, qubit):
        self.qubit = qubit
        self.f01 = number(qubit.get("f_01"))
        quad = number(qubit.get("freq_vs_flux_01_quad_term"))
        self.quad = abs(quad) if quad else None
        self.quad_raw = quad
        self.frame = frame(qubit)
        self.sign = self.frame["sign"]
        self.notes = list(self.frame["notes"])
        self.model = model_of(qubit)
        self.anchor = None          # phi the model is evaluated about
        self.model_kind = None
        self.rejection = None
        m = self.model
        if m is not None:
            self.rejection = lab_rejection(m, self.f01)
            spot = self.frame["model_anchor"]
            quality_ok = lab_rejection(m, m["f_01_at_fit_hz"]) is None
            if spot == "fit" and quality_ok:
                self.anchor, self.model_kind = m["phi_park"], (MODEL_LABEL if self.rejection is None
                                                             else MODEL_LABEL + ", re-anchored to f_01 [derived]")
            elif spot == "other" and quality_ok:
                self.anchor, self.model_kind = model_spots(m)["other"]["phi"], OTHER_SPOT_LABEL
            if self.anchor is not None:
                gap = abs(self.f01 - model_spots(m)[spot]["f_hz"])
                if gap > F01_TOLERANCE_HZ:
                    self.notes.append(f"f_01 is {gap / 1e6:.0f} MHz from that sweet spot of the fit;"
                                      " the curve is drawn from f_01")
            else:
                self.notes.append("lab flux model not drawn: " + (self.rejection if not quality_ok else
                                  "f_01 is at neither sweet spot of the fit"))
            if self.rejection and self.anchor is not None:
                self.notes.append("the lab's own resolver would reject this fit (" + self.rejection
                                  + ") and use the quad term")
        elif (qubit.get("extras") or {}).get("lffem_flux_model") is not None:
            self.rejection = "invalid or non-finite model parameters"
            self.notes.append("lab flux model not drawn: " + self.rejection)
        self.curvature = self._curvature()
        self.range = self._range()

    # the model about its anchor, in the qubit's frame (shift added to f_01)
    def _model_window(self):
        m = self.model
        tol = 0.01 * max(abs(m["amp_min_v"]), abs(m["amp_max_v"])) / m["phi0_v"]
        return (m["phi_park"] + m["amp_min_v"] / m["phi0_v"] - tol,
                m["phi_park"] + m["amp_max_v"] / m["phi0_v"] + tol)

    def model_shift(self, amplitude):
        """Model shift at amplitude(s) about the anchor; NaN outside the measured flux."""
        a = np.asarray(amplitude, dtype=float)
        if self.anchor is None:
            return np.full(a.shape, np.nan)
        m = self.model
        if self.anchor == m["phi_park"]:
            return fitted_shift(m, a)       # the lab's own guard, unchanged
        phi = self.anchor + a / m["phi0_v"]
        lo, hi = self._model_window()
        # the other spot is the measured window shifted by whole periods (the arc is periodic)
        k = np.round((phi - (lo + hi) / 2))
        inside = (phi - k >= lo) & (phi - k <= hi)
        shift = fitted_frequency(m, phi) - fitted_frequency(m, self.anchor)
        return np.where(np.isfinite(a) & inside, shift, np.nan)

    def quad_shift(self, amplitude):
        a = np.asarray(amplitude, dtype=float)
        if self.quad is None or self.sign is None:
            return np.full(a.shape, np.nan)
        return self.sign * self.quad * a**2

    def _curvature(self):
        """The model's own local quad coefficient at the anchor (Hz/V^2), and how the
        stored quad term compares with it [derived]."""
        if self.anchor is None:
            return None
        m = self.model
        h = 1e-3 * m["phi0_v"]
        def k_at(phi):
            f = fitted_frequency(m, [phi - h / m["phi0_v"], phi, phi + h / m["phi0_v"]])
            return float((f[0] + f[2] - 2 * f[1]) / (2 * h * h))
        here, other = k_at(self.anchor), k_at(self.anchor + 0.5)
        out = {"here_hz_per_v2": here, "other_hz_per_v2": other, "ratio": None, "note": None}
        if self.quad:
            out["ratio"] = self.quad / abs(here) if here else None
            near = lambda k: k and abs(self.quad - abs(k)) <= CURVATURE_MISMATCH * abs(k)
            if not near(here):
                if near(other):
                    out["note"] = ("freq_vs_flux_01_quad_term matches the fitted arc's curvature at the OTHER"
                                   " sweet spot (likely measured before the qubit moved spots)")
                else:
                    out["note"] = (f"freq_vs_flux_01_quad_term is {out['ratio']:.2f}x the fitted arc's local curvature")
                self.notes.append(out["note"])
        return out

    def _range(self):
        """Measured validity of the quad estimate: the first shift where |quad - model|
        exceeds ERROR_TOLERANCE_HZ, per amplitude lobe; the smaller lobe wins."""
        out = {"tolerance_hz": ERROR_TOLERANCE_HZ, "limit_hz": None, "source": None, "branches": []}
        if self.quad is None or self.sign is None:
            return out
        if self.anchor is None:
            out.update(limit_hz=GENERIC_LIMIT_HZ[self.frame["spot"]],
                       source=f"measured on fitted arcs at {self.frame['spot']} sweet spots")
            return out
        m = self.model
        for side in (1, -1):
            amps = np.linspace(0, side * 0.5 * m["phi0_v"], 2001)
            shifts = self.model_shift(amps)
            ok = np.isfinite(shifts)
            if ok.sum() < 2:
                continue
            amps, shifts = amps[ok], shifts[ok]
            errors = np.abs(self.quad_shift(amps) - shifts)
            bad = np.flatnonzero(errors > ERROR_TOLERANCE_HZ)
            limit = abs(float(shifts[max(0, int(bad[0]) - 1)])) if len(bad) else None
            out["branches"].append({"side": side, "limit_hz": limit,
                                    "checked_hz": float(np.max(np.abs(shifts))),
                                    "checked_quad_hz": float(np.max(np.abs(self.quad_shift(amps)))),
                                    "amplitude_min_v": float(np.min(np.abs(amps))),
                                    "amplitude_max_v": float(np.max(np.abs(amps))),
                                    "max_error_hz": float(np.max(errors))})
        limits = [b["limit_hz"] for b in out["branches"]]
        if out["branches"]:
            known = [x for x in limits if x is not None]
            # no crossing on a lobe: valid as far as it was measured (that lobe's checked span)
            spans = [b["checked_hz"] for b in out["branches"] if b["limit_hz"] is None]
            out["limit_hz"] = min(known) if known else None
            out["source"] = "this qubit's fitted arc"
            out["within_checked_hz"] = min(spans) if spans and not known else None
        return out

    def point(self, amplitude):
        """The frequency this qubit reaches at *amplitude* (V), every tier shown."""
        a = number(amplitude)
        res = {"amplitude_v": a, "frequency_hz": None, "kind": None, "quad_hz": None,
               "model_hz": None, "error_hz": None, "validity": None, "reason": None}
        if self.f01 is None or a is None:
            res["reason"] = "f_01 or the operation amplitude is missing"
            return res
        if a == 0:
            res.update(frequency_hz=self.f01, kind="idle f_01", validity="idle")
            return res
        if self.sign is None:
            res["reason"] = self.frame["reason"]
            return res
        if self.quad is not None:
            res["quad_hz"] = self.f01 + float(self.quad_shift(a))
        ms = float(self.model_shift(a)) if self.anchor is not None else float("nan")
        if math.isfinite(ms):
            res["model_hz"] = self.f01 + ms
        if res["model_hz"] is not None:
            res.update(frequency_hz=res["model_hz"], kind=self.model_kind)
        elif res["quad_hz"] is not None:
            res.update(frequency_hz=res["quad_hz"], kind=QUAD_LABEL)
            if self.anchor is not None:
                res["reason"] = "amplitude outside the flux range the lab model was measured on"
        else:
            res["reason"] = "no flux-frequency model: freq_vs_flux_01_quad_term is missing"
        if res["quad_hz"] is not None and res["model_hz"] is not None:
            res["error_hz"] = abs(res["quad_hz"] - res["model_hz"])
        limit = self.range["limit_hz"]
        if res["quad_hz"] is not None:
            shift = abs(res["quad_hz"] - self.f01)
            if self.range["branches"]:
                branch = next((b for b in self.range["branches"] if b["side"] == (1 if a > 0 else -1)), None)
                checked = (branch and branch["amplitude_min_v"] <= abs(a) <= branch["amplitude_max_v"]
                           and shift <= branch["checked_quad_hz"])
                if not checked:
                    res["validity"] = "beyond the fitted arc's measured range"
                elif branch["limit_hz"] is not None and shift > branch["limit_hz"]:
                    res["validity"] = "outside the quadratic range"
                else:
                    res["validity"] = "within the measured quadratic range"
            elif self.anchor is not None:
                res["validity"] = "beyond the fitted arc's measured range"
            elif limit is None:
                res["validity"] = "quadratic range unknown"
            else:
                res["validity"] = ("outside the quadratic range" if shift > limit
                                   else "within the quadratic range")
        return res

    def curve(self, amax, n=241):
        """f vs amplitude over [-amax, amax] for both tiers (None where a tier has no value)."""
        amps = np.linspace(-amax, amax, n)
        def clean(arr):
            return [None if not np.isfinite(v) else float(v) for v in arr]
        f0 = self.f01 if self.f01 is not None else float("nan")
        return {"amplitude_v": [float(a) for a in amps],
                "quad_hz": clean(f0 + np.where(amps == 0, 0.0, self.quad_shift(amps))),
                "model_hz": clean(f0 + np.where(amps == 0, 0.0,
                                   self.model_shift(amps) if self.sign is not None else np.nan))
                            if self.anchor is not None else None}

    def trajectory(self, samples):
        """Each waveform sample (V) mapped through the same tiers (1 sample per ns)."""
        a = np.asarray([number(s) if number(s) is not None else np.nan for s in samples], dtype=float)
        f0 = self.f01 if self.f01 is not None else float("nan")
        def clean(arr):
            return [None if not np.isfinite(v) else float(v) for v in arr]
        quad = f0 + np.where(a == 0, 0.0, self.quad_shift(a))
        model = (f0 + np.where(a == 0, 0.0, self.model_shift(a) if self.sign is not None else np.nan)
                 if self.anchor is not None else None)
        main = model if model is not None else quad
        if model is not None:
            main = np.where(np.isfinite(model), model, quad)
        return {"frequency_hz": clean(main), "quad_hz": clean(quad),
                "model_hz": clean(model) if model is not None else None}

    def summary(self):
        m = self.model
        return {"f01_hz": self.f01, "quad_hz_per_v2": self.quad_raw, "sign": self.sign,
                "frame": self.frame, "notes": self.notes, "model_kind": self.model_kind,
                "model_rejection": self.rejection, "curvature": self.curvature, "range": self.range,
                "model": ({"created_at": m["created_at"], "r2": m["model_r2"],
                           "amp_range_v": [m["amp_min_v"], m["amp_max_v"]],
                           "spots": model_spots(m)} if m else None),
                "anharmonicity_hz": number(self.qubit.get("anharmonicity"))}


def resonances(fc, ft, alpha_c, alpha_t):
    """The three |11>-manifold conditions on Delta = f_c - f_t (see module docstring)."""
    delta = fc - ft if fc is not None and ft is not None else None
    conditions = (("|11>-|20>", abs(alpha_c) if alpha_c is not None else None),
                  ("|11>-|02>", -abs(alpha_t) if alpha_t is not None else None),
                  ("iSWAP", 0.0))
    return [{"name": n, "delta_hz": d,
             "distance_hz": delta - d if delta is not None and d is not None else None}
            for n, d in conditions]


def tls_bands(qubit):
    """Defect bands (centre +- FWHM/2) the lab measured for this qubit, absolute Hz."""
    raw = (qubit.get("extras") or {}).get("population_loss_defects_json")
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return []
    out = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        f, w = number(r.get("frequency_hz")), number(r.get("fwhm_hz")) or 0.0
        if f is not None:
            out.append({"lo_hz": f - w / 2, "hi_hz": f + w / 2, "peak_loss": number(r.get("peak_loss"))})
    return out


# ---------------------------------------------------------------- the chip
_QUBIT_FIELDS = ("f_01", "anharmonicity", "freq_vs_flux_01_quad_term", "phi0_voltage")
_EXTRAS_USED = ("lffem_flux_model", "upper_sweet_spot_frequency", "lower_sweet_spot_frequency",
                "opposite_sweet_spot_qubit_frequency", "at_sweet_spot", "population_loss_defects_json")


def qubit_at(doc, path):
    """Only the fields this view reads, each resolved through the resolver."""
    raw = current(doc, path, {})
    if not isinstance(raw, dict):
        return {}
    out = {k: current(doc, path + "." + k) for k in _QUBIT_FIELDS}
    out["z"] = {k: current(doc, path + ".z." + k) for k in
                ("flux_point", "joint_offset", "min_offset", "independent_offset")}
    out["extras"] = {k: resolved_tree(doc, path + ".extras." + k) for k in _EXTRAS_USED
                     if read(doc, path + ".extras." + k).get("has_current")}
    return out


def dependency_paths(qubit_path, qubit):
    """The values an operation's frequency depends on (for staleness)."""
    paths = [qubit_path + "." + k for k in ("f_01", "anharmonicity", "freq_vs_flux_01_quad_term")]
    point = (qubit.get("z") or {}).get("flux_point")
    if point in ("joint", "min", "independent"):
        paths.append(qubit_path + ".z." + point + "_offset")
    if "lffem_flux_model" in (qubit.get("extras") or {}):
        paths += [qubit_path + ".extras.lffem_flux_model." + k for k in _MODEL_KEYS]
    return paths


def _is_cz(name, macro):
    cls = str(macro.get("__class__", ""))
    return "CZ" in cls.split(".")[-1] or name.lower().startswith("cz")


def operations(doc, conversions=None, unavailable=None):
    """Every CZ macro of every pair (aliases included), with what each qubit plays.

    Pulse-to-qubit mapping follows the gate classes: a single-mover CZ plays
    ``flux_pulse_qubit`` on ``qubit_pair.moving_qubit``; a two-flux gate plays
    ``flux_pulse_qubit`` on the control and ``flux_pulse_target`` on the target;
    a both-movers gate plays ``flux_pulse_high`` on the higher-f_01 qubit (ties go
    to the control) and ``flux_pulse_low`` on the other; a spectator entry whose
    qubit is a pair member also moves that member."""
    conversions = {} if conversions is None else conversions
    unavailable = [] if unavailable is None else unavailable

    def mapping(path):
        raw = current(doc, path, {})
        if isinstance(raw, dict):
            return raw
        unavailable.append(path + ": unavailable (expected an object; unresolved pointer or malformed value)")
        return {}

    rows = []
    for pair_name in mapping("qubit_pairs"):
        pp = "qubit_pairs." + pair_name
        if not mapping(pp):
            continue
        roles = {}
        for role in ("control", "target"):
            t = read(doc, pp + ".qubit_" + role)
            path = t.get("holder_path") or ""
            q = qubit_at(doc, path) if path else {}
            if path and path not in conversions:
                conversions[path] = Conversion(q)
            roles[role] = {"path": path, "name": path.split(".")[-1] if path else "?", "qubit": q}
        plan = resolved_tree(doc, pp + ".extras.cz_plan")
        moving = current(doc, pp + ".moving_qubit")
        for name in mapping(pp + ".macros"):
            path = pp + ".macros." + name
            macro = mapping(path)
            cls = current(doc, path + ".__class__", "")
            if not macro or not _is_cz(name, {"__class__": cls}):
                continue
            alias = read(doc, path).get("via") or []
            fields, notes = {}, []
            cls = str(cls).split(".")[-1]
            if "flux_pulse_high" in macro or "flux_pulse_low" in macro:
                fc, ft = (number(roles[k]["qubit"].get("f_01")) for k in ("control", "target"))
                if fc is not None and ft is not None:
                    high = "control" if fc >= ft else "target"
                    low = "target" if high == "control" else "control"
                    fields = {high: "flux_pulse_high", low: "flux_pulse_low"}
                else:
                    notes.append("cannot tell the high and low qubit: an idle f_01 is missing")
                kind = "both movers"
            elif "flux_pulse_target" in macro:
                fields = {"control": "flux_pulse_qubit", "target": "flux_pulse_target"}
                kind = "two-flux"
            elif moving in ("control", "target"):
                fields = {moving: "flux_pulse_qubit"}
                kind = "single mover"
            else:
                notes.append("qubit_pair.moving_qubit is missing or not control/target")
                kind = "single mover"
            duration_field = "duration" if kind == "both movers" else "duration_qubit" if kind == "single mover" else None
            raw_duration = current(doc, path + "." + duration_field) if duration_field else None
            cycles = number(raw_duration)
            duration_ns = (4 * cycles if cycles is not None and cycles > 0 and cycles.is_integer()
                           and math.isfinite(4 * cycles) else None)
            duration_note = (f"{cycles:g} clock cycles x 4 ns = {duration_ns:g} ns (play-time override)"
                             if duration_ns is not None else
                             "unknown: play-time duration override cannot be resolved" if raw_duration is not None else
                             "unknown: no play-time duration override is stored; stored duration is a runtime property")
            spectators = mapping(path + ".spectator_qubits_control")
            for key in spectators:
                target = read(doc, path + ".spectator_qubits." + key)
                who = target.get("holder_path") if target.get("has_current") else None
                for role, info in roles.items():
                    if info["path"] == who and role not in fields:
                        fields[role] = "spectator_qubits_control." + key
            lines = []
            for role in ("control", "target"):
                info = roles[role]
                conv = conversions.get(info["path"])
                field = fields.get(role)
                pulse_path = path + "." + field if field else None
                pulse = current(doc, pulse_path) if pulse_path else None
                if isinstance(pulse, str) and not pulse.startswith("#"):
                    pulse_path = info["path"] + ".z.operations." + pulse
                if pulse_path and isinstance(current(doc, pulse_path), dict):
                    pulse_path = read(doc, pulse_path)["holder_path"]
                amp_path = pulse_path + ".amplitude" if pulse_path else None
                amp = number(current(doc, amp_path)) if amp_path else 0.0
                if amp_path and amp is None:
                    notes.append(f"{info['name']}: the pulse amplitude cannot be read")
                pulse = current(doc, pulse_path, {}) if pulse_path else {}
                pt = conv.point(amp) if conv else {"frequency_hz": None, "reason": "qubit not found"}
                lines.append({"role": role, "name": info["name"], "qubit_path": info["path"],
                              "moves": bool(field), "pulse_path": pulse_path, "amplitude_path": amp_path,
                              "play_duration_ns": duration_ns if field in ("flux_pulse_qubit", "flux_pulse_high", "flux_pulse_low") else None,
                              "duration_unknown": (raw_duration is not None and duration_ns is None
                                                   and field in ("flux_pulse_qubit", "flux_pulse_high", "flux_pulse_low")),
                              "pulse_class": (str(current(doc, pulse_path + ".__class__", "")).split(".")[-1]
                                              if pulse_path and isinstance(pulse, dict) else None),
                              "amplitude_v": amp, "point": pt})
            fq = [ln["point"].get("frequency_hz") for ln in lines]
            alphas = [number(roles[k]["qubit"].get("anharmonicity")) for k in ("control", "target")]
            res = resonances(fq[0], fq[1], alphas[0], alphas[1])
            known = [r for r in res if r["distance_hz"] is not None]
            nearest = min(known, key=lambda r: abs(r["distance_hz"])) if known else None
            coupler = current(doc, path + ".coupler_flux_pulse")
            if coupler:
                notes.append("a coupler pulse also plays: these bare-qubit frequencies omit the coupler's shift")
            rows.append({
                "pair": pair_name, "operation": name, "path": path, "class": cls, "kind": kind,
                "alias_of": alias[-1]["to_path"] if alias else None,
                "lines": lines, "resonances": res, "nearest": nearest,
                "detuning_hz": (fq[0] - fq[1]) if None not in fq else None,
                "idle_detuning_hz": (lambda a, b: a - b if a is not None and b is not None else None)(
                    number(roles["control"]["qubit"].get("f_01")), number(roles["target"]["qubit"].get("f_01"))),
                "duration_ns": duration_ns, "duration_note": duration_note,
                "phases": {k: number(current(doc, path + ".phase_shift_" + k)) for k in ("control", "target")},
                "fidelity": _fidelity(resolved_tree(doc, path + ".fidelity")),
                "plan": _plan(plan, lines),
                "interaction_frequency_hz": number(current(doc, path + ".interaction_frequency")),
                "coupler_amplitude_v": number(current(doc, path + ".coupler_flux_pulse.amplitude")) if coupler else None,
                "amplitudes": [ln["amplitude_path"] for ln in lines if ln["amplitude_path"]],
                "dependencies": [p for r in ("control", "target") if roles[r]["path"]
                                 for p in dependency_paths(roles[r]["path"], roles[r]["qubit"])],
                "notes": notes,
            })
    return rows, conversions


def _fidelity(raw):
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k in ("StandardRB", "InterleavedRB"):
        v = number(raw.get(k))
        if v is not None:
            out[k] = {"value": v, "load_id": raw.get(k + "_load_id")}
    # an IRB run's own load id is sometimes stored only beside the RB one
    return out


def _plan(plan, lines):
    """The lab planner's interaction point beside what the operation plays now."""
    if not isinstance(plan, dict):
        return None
    out = {"omega_int_hz": number(plan.get("omega_int_hz")), "path_ok": plan.get("path_ok"),
           "leakage_qubit": plan.get("leakage_qubit"), "two_j_hz": number(plan.get("two_j_hz")),
           "reasons": [], "roles": {}}
    try:
        reasons = json.loads(plan.get("reasons_json") or "[]")
        out["reasons"] = [str(r) for r in reasons] if isinstance(reasons, list) else []
    except (TypeError, ValueError):
        pass
    for ln in lines:
        v = number(plan.get("v_" + ln["role"] + "_v"))
        out["roles"][ln["role"]] = {"plan_v": v, "difference_v": (
            ln["amplitude_v"] - v if v is not None and ln["moves"] and ln["amplitude_v"] is not None else None)}
    return out


# ---------------------------------------------------------------- when set
def calibration(row, history):
    """When each value this operation uses was last set (effective change points).

    ``last_set`` is the NEWEST amplitude change; ``last_calibrated`` is that point
    only when a run is PROVEN to have written it. A dependency (f_01, the flux
    model, the park offset) changed after an amplitude is a staleness warning.
    Unknown stays unknown: no amplitude history -> ``unknown``."""
    rows = history.get("rows") or {}
    fields, changes, observations, notes = {}, {}, {}, []

    def setting(p):
        return (p.get("provenance") in ("sm", "run_proven", "run_saved", "run_uncertain_chip")
                and not p.get("removed") and not p.get("undone")
                and ("old" not in p or "value" not in p or p["old"] != p["value"]))

    def view(p):
        return {"t": p.get("t"), "t_us": p.get("t_us"), "run_id": p.get("run_id"),
                "provenance": p.get("provenance"),
                "proven": bool(p.get("proven")) and p.get("provenance") == "run_proven",
                "kind": p.get("kind")}

    for path in row["amplitudes"] + row["dependencies"]:
        series = rows.get(path) or {}
        points = series.get("effective") or series.get("points") or []
        usable = [p for p in points if not p.get("undone")]
        p = usable[-1] if usable else {}
        path_notes = (history.get("notes") or {}).get(path) or []
        differs = any(n.get("code") == "current_differs" for n in path_notes)
        fields[path] = view(p if setting(p) and not differs else {})
        events = [p for p in usable if setting(p)] if not differs else []
        changes[path] = view(events[-1] if events else {})
        if path in row["amplitudes"]:
            if differs:
                notes.extend(n["text"] for n in path_notes if n.get("code") == "current_differs" and n.get("text"))
            if usable and fields[path]["t_us"] is None:
                first = usable[0]
                observations[path] = view(first)
                if first.get("run_id") is not None:
                    notes.append(f"{path}: first recorded in run #{first['run_id']}; setting time unknown")
                elif first.get("t"):
                    notes.append(f"{path}: first recorded {first['t']}; setting time unknown")
    amps = [fields[p] for p in row["amplitudes"]]
    known = [a for a in amps if a["t_us"] is not None]
    complete = bool(amps) and len(known) == len(amps)
    newest = max(known, key=lambda a: a["t_us"]) if complete else {}
    oldest = min(known, key=lambda a: a["t_us"]) if complete else None
    warnings = []
    if oldest is not None:
        for path in row["dependencies"]:
            dep = changes[path]
            if dep["t_us"] is not None and dep["t_us"] > oldest["t_us"] + STALE_SLACK_US:
                warnings.append({"path": path, "t": dep["t"], "run_id": dep["run_id"],
                                 "text": "calibrated against an older "
                                         + (path[len("qubits."):] if path.startswith("qubits.") else path)})
    return {"fields": fields, "last_set": newest,
            "last_calibrated": newest if newest and newest.get("proven") else {},
            "warnings": warnings, "unknown": not complete,
            "observations": observations,
            "status": history.get("mode", "unavailable"),
            "reason": "; ".join(notes) or history.get("unavailable_note") or history.get("reason")}
