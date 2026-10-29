"""Shared CZ flux formulas. Frequencies Hz, amplitudes V, flux Phi0.

Pure numerical functions with no state-manager dependencies.
"""


import math

import numpy as np


def quad_shift(amplitude, magnitude, sign):
    """Forward shift in Hz: sign * |q| * amplitude squared (V squared).

    coefficient of an arc: -(fmax+EC)(1-d^2)pi^2/4 per Phi0^2 [derived, D4].
    The caller supplies its existing sign convention.
    """
    return sign * magnitude * np.asarray(amplitude, dtype=float)**2


def arc_quad_coefficient(fmax, ec, d):
    """Upper-spot coefficient of an arc: -(fmax+EC)(1-d^2)pi^2/4
    per Phi0^2 [derived, D4]. No production caller is added by extraction.
    """
    return -(fmax + ec) * (1 - d**2) * np.pi**2 / 4


def local_quad_coefficient(model, phi):
    """Existing finite-difference local coefficient in Hz/V^2 [derived]."""
    h = 1e-3 * model["phi0_v"]
    f = arc_frequency(model, [phi - h / model["phi0_v"], phi, phi + h / model["phi0_v"]])
    return float((f[0] + f[2] - 2 * f[1]) / (2 * h * h))


def model_window(model):
    """Measured flux window with the transcribed ±1 % amplitude tolerance."""
    tol = 0.01 * max(abs(model["amp_min_v"]), abs(model["amp_max_v"])) / model["phi0_v"]
    return (model["phi_park"] + model["amp_min_v"] / model["phi0_v"] - tol,
            model["phi_park"] + model["amp_max_v"] / model["phi0_v"] + tol)


def model_shift(model, anchor, amplitude):
    """Arc difference about an anchor with the existing periodic window guard."""
    a = np.asarray(amplitude, dtype=float)
    if anchor is None:
        return np.full(a.shape, np.nan)
    if anchor == model["phi_park"]:
        return fitted_shift(model, a)
    phi = anchor + a / model["phi0_v"]
    lo, hi = model_window(model)
    k = np.round((phi - (lo + hi) / 2))
    inside = (phi - k >= lo) & (phi - k <= hi)
    shift = arc_frequency(model, phi) - arc_frequency(model, anchor)
    return np.where(np.isfinite(a) & inside, shift, np.nan)


def arc_frequency(model, phi):
    """Asymmetric transmon arc, flux in Phi0, result Hz.

    [derived from cond-mat/0703002v2 p.6 Eq. 2.11 + p.7 Eq. 2.18; checks D2, D3]
    use with fitted parameters only (D3: up to 23 MHz absolute offset vs exact diagonalisation at EJ/EC = 50).
    """
    phi_array = np.asarray(phi, dtype=float)
    inside = np.cos(np.pi * phi_array) ** 2 + model["d"]**2 * np.sin(np.pi * phi_array) ** 2
    return (model["f_max_hz"] + model["ec_hz"]) * inside**0.25 - model["ec_hz"]


def fitted_shift(model, amplitude):
    """[derived] difference of the arc about the park, window ±1 % = lab rule
    (transcribed, golden). Outside the window the result is NaN.
    """
    amp = np.asarray(amplitude, dtype=float)
    tolerance = 0.01 * max(abs(model["amp_min_v"]), abs(model["amp_max_v"]))
    invalid = (~np.isfinite(amp) | (amp < model["amp_min_v"] - tolerance)
               | (amp > model["amp_max_v"] + tolerance))
    result = (arc_frequency(model, model["phi_park"] + amp / model["phi0_v"])
              - arc_frequency(model, model["phi_park"]))
    return np.where(invalid, np.nan, result)


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
    offset = model["f_01_at_fit_hz"] - float(arc_frequency(model, model["phi_park"]))
    phi_other = sweet_spot_phi(model["phi_park"]) + 0.5
    other = float(arc_frequency(model, phi_other)) + offset
    return {"fit": {"phi": model["phi_park"], "kind": spot_kind(model["phi_park"]),
                    "f_hz": model["f_01_at_fit_hz"]},
            "other": {"phi": phi_other, "kind": spot_kind(phi_other), "f_hz": other},
            "stored_other_hz": model.get("f_opposite_hz")}


def resonances(fc, ft, alpha_c, alpha_t):
    """The three |11>-manifold conditions on Delta = f_c - f_t Hamiltonian cross-check: S7 C-RES."""
    delta = fc - ft if fc is not None and ft is not None else None
    conditions = (("|11>-|20>", abs(alpha_c) if alpha_c is not None else None),
                  ("|11>-|02>", -abs(alpha_t) if alpha_t is not None else None),
                  ("iSWAP", 0.0))
    return [{"name": n, "delta_hz": d,
             "distance_hz": delta - d if delta is not None and d is not None else None}
            for n, d in conditions]


def zero_frequencies(
    f_c: float, f_t: float, alpha_c: float, alpha_t: float, sweep_role: str,
) -> list[tuple[str, float]]:
    """The swept qubit's frequency at which each detuning vanishes.

        D(11-20) = f_t - f_c + A_c = 0
        D(11-02) = f_c - f_t + A_t = 0
        D(10-01) = f_c - f_t      = 0

    solved for whichever of f_c / f_t is the one being swept.  These are
    exactly ``cz_branch``'s two branch conditions plus the 1Q resonance.
    """
    if sweep_role == "control":
        return [
            ("Δ(11−20)=0", f_t + alpha_c),
            ("Δ(11−02)=0", f_t - alpha_t),
            ("Δ(10−01)=0", f_t),
        ]
    return [
        ("Δ(11−20)=0", f_c - alpha_c),
        ("Δ(11−02)=0", f_c + alpha_t),
        ("Δ(10−01)=0", f_c),
    ]


def zeros_on_voltage(
    targets: list[tuple[str, float]],
    f_idle: float, quad: float, v_idle: float,
) -> list[dict]:
    """Invert ``f = f_idle + quad*(V - V_idle)^2`` for each zero frequency.

    A target on the side the parabola does not bend towards is simply not
    reachable by flux and is left out — never bent onto the axis anyway.
    """
    zeros: list[dict] = []
    for label, f_target in targets:
        ratio = (f_target - f_idle) / quad
        if ratio < 0:
            continue
        root = math.sqrt(ratio)
        if root == 0.0:
            zeros.append({"label": label, "voltage": v_idle,
                          "frequency": f_target})
            continue
        for sign in (1.0, -1.0):
            zeros.append({"label": label, "voltage": v_idle + sign * root,
                          "frequency": f_target})
    return zeros


def voltage_range(
    targets: list[tuple[str, float]],
    zeros: list[dict],
    f_idle: float, quad: float, v_idle: float,
) -> tuple[float, float]:
    """A voltage window centred on the idle bias that shows the physics.

    Reachable crossings set the width.  When none is reachable the window
    still spans the voltage that WOULD reach the furthest of them, so the
    plot keeps a physical scale instead of collapsing to a hairline around
    the operating point (which is what an empty crossing list used to do).
    """
    half = max((abs(z["voltage"] - v_idle) for z in zeros), default=0.0)
    if half <= 0.0:
        reach = max((abs(f - f_idle) for _, f in targets), default=0.0)
        half = math.sqrt(reach / abs(quad)) if reach > 0 and quad else 0.0
    if half <= 0.0:
        half = 0.05
    margin = half * 0.2
    return v_idle - half - margin, v_idle + half + margin
