"""Shared CZ flux formulas. Frequencies Hz, amplitudes V, flux Phi0.

Pure numerical functions with no state-manager dependencies.
"""


import math

import numpy as np


def node30_target(fc, ft, alpha_c, alpha_t, moving=None, partner_hz=None,
                  rf_control=None, rf_target=None):
    """Signed CZ mover and shift [derived], check S2-N30 official-code parity.

    Roles use idle f_01; detuning uses the supplied frequency frame. The
    high qubit always carries the leakage level, including when low moves.
    """
    high = "control" if fc >= ft else "target"
    low = "target" if high == "control" else "control"
    ah = abs(alpha_c if high == "control" else alpha_t)
    gap = abs(fc - ft)
    official = high if gap > ah else low
    role = moving or official
    frequencies = (fc if rf_control is None else rf_control, ft if rf_target is None else rf_target)
    fm, fs = frequencies if role == "control" else frequencies[::-1]
    fs = fs if partner_hz is None else partner_hz
    target = fs + ah if role == high else fs - ah
    return {"mover": official, "target_hz": target, "delta_hz": target - fm,
            "rule": "high moves down if delta > |alpha_high|; otherwise low moves down"}


def quad_inverse(delta, coefficient, evidence_sign=None, polarity=1, qubit="qubit"):
    """Signed quadratic inverse [derived], check S2-Q and S2-N30.

    Evidence takes precedence; contradictory signed coefficients are refused.
    """
    sign = evidence_sign if evidence_sign is not None else (1 if coefficient > 0 else -1)
    if evidence_sign is not None and coefficient * evidence_sign < 0:
        raise ValueError("official node 30 would refuse")
    if delta * sign < 0:
        need, actual = ("up" if delta > 0 else "down"), ("up" if sign > 0 else "down")
        raise ValueError(f"the target needs {qubit} to move {need} but its sweet spot only moves it {actual}")
    return math.copysign(math.sqrt(abs(delta) / abs(coefficient)), polarity or 1)


def model_inverse(model, anchor, f01, target, polarity=1):
    """Exact anchored arc inverse [derived], F9/D4b and check S2-M.

    Enumerate k in {-1,0,1}; measured amplitude window has 1 percent tolerance.
    Return the smallest magnitude on the stored side, then the opposite side.
    """
    fstar = target - f01 + float(arc_frequency(model, anchor))
    r = (fstar + model["ec_hz"]) / (model["f_max_hz"] + model["ec_hz"])
    if r < 0 or not model["d"]**2 - 1e-14 <= r**4 <= 1 + 1e-14:
        offset = f01 - float(arc_frequency(model, anchor))
        fmin = float(arc_frequency(model, 0.5)) + offset
        fmax = float(arc_frequency(model, 0)) + offset
        raise ValueError(f"target {target / 1e9:g} GHz is outside the fitted arc ({fmin / 1e9:g}..{fmax / 1e9:g} GHz)")
    phi = math.asin(math.sqrt(np.clip((1 - r**4) / (1 - model["d"]**2), 0, 1))) / math.pi
    candidates = [model["phi0_v"] * (k + side * phi - anchor)
                  for k in (-1, 0, 1) for side in (-1, 1)]
    lo, hi = model["amp_min_v"], model["amp_max_v"]
    tol = 0.01 * max(abs(lo), abs(hi))
    allowed = [a for a in candidates if lo - tol <= a <= hi + tol]
    if not allowed:
        best = min(candidates, key=abs)
        raise ValueError(f"target needs {best:g} V, outside the measured flux range [{lo:g}, {hi:g}] V")
    same = [a for a in allowed if a * (polarity or 1) >= 0]
    return min(same or allowed, key=abs), not bool(same)


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



def coupler_frequency(model, amplitude):
    """[derived], S3-L: F8 at phi_idle + A_c/phi0_v.

    Q16 [paper: arXiv:1803.09813v1, p.7, supplement Eq. (1)]
    "is the junction asymmetry". The coupler uses the same fitted arc.
    """
    return float(arc_frequency(dict(f_max_hz=model["fc_max_hz"],
        ec_hz=model["ec_c_hz"], d=model["d_c"]),
        model["phi_idle"] + amplitude / model["phi0_v"]))


def lamb_shift(fq, fc, g, alpha_q=0.0, alpha_c=0.0):
    """[derived], S3L-bound: exact one-excitation RWA plus dressed CR.

    Leading RWA only [paper: arXiv:1803.09813v1, p.2, Eq. (2)]:
    `w~j = wj + gj^2/Delta_j` (LITERATURE.md section 3).
    The signed 2x2 eigenvalue resums -g^4/Delta^3 and higher terms.
    CR is second order between RWA eigenstates: eliminate the N=3
    block at the dressed energy and the N=2 block at ground energy 0.
    Check: checks/s3l_bound.py and test_cz_s3l_bound.py, <=0.16 percent
    on the documented finite grid; not a bound outside that domain.
    Anharmonicities are accepted signed or as positive magnitudes.
    """
    delta = fq - fc
    rwa = (2 * g**2 / delta) / (math.sqrt(1 + 4 * (g / delta)**2) + 1)
    aq, ac = -abs(alpha_q), -abs(alpha_c)
    mix = rwa / g if g else 0.0
    u = 1 / math.sqrt(1 + mix**2)
    v = mix * u
    # N=3 basis |21>, |12>; CR source sqrt(2)*g*(u,v).
    x = fq + rwa - (2 * fq + fc + aq)
    y = fq + rwa - (fq + 2 * fc + ac)
    excited_cr = 2 * g**2 * (u*u*y + 4*g*u*v + v*v*x) / (x*y - 4*g*g)
    # N=2 basis |20>, |11>, |02>; |00> couples to |11>.
    ground_cr = -g**2 / (fq + fc - 2*g*g/(2*fq + aq) - 2*g*g/(2*fc + ac))
    return rwa + excited_cr - ground_cr


def dressed_energies(frequencies, anharmonicities, couplings, states):
    """[derived], S3-L/S3-O: F1 charge coupling, three levels per mode.

    Return each maximum-overlap energy, probability and eigenindex. Couplings
    are (mode index, mode index, Hz); all energies are in Hz.
    """
    n = len(frequencies)
    a = np.diag(np.sqrt([1., 2.]), 1)
    number = a.T @ a
    def embed(operator, mode):
        """[derived], S3-L: tensor placement in the truncated product basis."""
        result = np.ones((1, 1))
        for k in range(n):
            result = np.kron(result, operator if k == mode else np.eye(3))
        return result
    h = sum(embed(f * number - abs(alpha) / 2 * number @ (number - np.eye(3)), k)
            for k, (f, alpha) in enumerate(zip(frequencies, anharmonicities)))
    x = [embed(a + a.T, k) for k in range(n)]
    for i, j, g in couplings:
        h += g * x[i] @ x[j]
    energy, vectors = np.linalg.eigh(h)
    result = []
    for state in states:
        index = int(np.ravel_multi_index(state, (3,) * n))
        weights = abs(vectors[index])**2
        chosen = int(np.argmax(weights))
        result.append((float(energy[chosen]), float(weights[chosen]), chosen))
    return result


def coupler_shift(fq, fc, alpha, ec, g):
    """[derived], S3-L/S3-O: regime and maximum-overlap refusal."""
    if fq != fc and abs(g / (fq - fc)) <= 0.1:
        return {"coupler_shift_hz": lamb_shift(fq, fc, g, alpha, ec),
                "coupler_method": "dispersive estimate (f_01 shift only)"}
    states = dressed_energies([fq, fc], [alpha, ec], [(0, 1, g)], [(0, 0), (1, 0)])
    if min(s[1] for s in states) < 0.9 or len({s[2] for s in states}) != 2:
        return {"coupler_shift_hz": None,
                "coupler_method": "qubit and coupler hybridised: no frequency assigned"}
    return {"coupler_shift_hz": states[1][0] - states[0][0] - fq,
            "coupler_method": "diagonalised"}


def dressed_resonances(fc, ft, wc, ac, at, ec, gc, gt, gct):
    """[derived], S3-O/C-RES: dressed distances, never twice an f_01 shift.

    Preserve resonances()' signed distance convention; also expose E_leak-E_11.
    """
    states = dressed_energies([fc, wc, ft], [ac, ec, at],
        [(0, 1, gc), (1, 2, gt), (0, 2, gct)],
        [(1, 0, 1), (2, 0, 0), (0, 0, 2), (1, 0, 0), (0, 0, 1)])
    valid = min(s[1] for s in states) >= 0.9 and len({s[2] for s in states}) == len(states)
    e11, e20, e02, e10, e01 = [s[0] for s in states]
    distances = [e20 - e11, e11 - e02, e10 - e01]
    return [{"name": name, "distance_hz": distance if valid else None,
             "dressed_energy_difference_hz": raw if valid else None,
             "method": "diagonalised" if valid else "qubit and coupler hybridised: no frequency assigned"}
            for name, distance, raw in zip(["|11>-|20>", "|11>-|02>", "iSWAP"],
                distances, [e20-e11, e02-e11, e10-e01])]



#: A dressed computational state below this overlap with its bare state is not assigned
#: [derived, check S3-O: overlap 0.93 still at g/Delta 0.3].
ZZ_OVERLAP_MIN = 0.9
#: A pi phase that needs a longer flat top than this is "no usable ZZ" [derived, REVIEW r1:
#: t_flat > ~2 us].
ZZ_T_MAX_NS = 2000.0
#: Coupler closer than this many max(g) to a qubit: a warning, not a refusal [derived,
#: dispersive bound, S3-L/S3-O; REVIEW r1 makes the overlap check the refusal].
COLLISION_G_FACTOR = 5.0
HYBRIDISED = "coupler hybridised with a qubit at this bias"


def zz_three_mode(fc, ft, wc, ac, at, ec, gc, gt, gct):
    """Static ZZ of the qubit-coupler-qubit chain, Hz [derived, check D5a/D5b].

    Hamiltonian F1 [paper: arXiv:2101.07746v1, p.1, Eqs. (1)-(3)]: three Duffing modes,
    charge coupling (a+a^dagger)(x)(a+a^dagger), three levels per mode (27 states).
    ZZ [paper: arXiv:2101.07746v1, p.2, Eq. (4)] "With these parameters we diagonalize
    the system and extract ZZ as" ZZ = (E11 - E01 - E10 + E00)/h.
    Each dressed state is the eigenvector of maximum overlap with |q1, 0_c, q2>; the
    four must be distinct eigenvectors and each overlap >= ZZ_OVERLAP_MIN.
    """
    states = dressed_energies([fc, wc, ft], [ac, ec, at],
                              [(0, 1, gc), (1, 2, gt), (0, 2, gct)],
                              [(0, 0, 0), (1, 0, 0), (0, 0, 1), (1, 0, 1)])
    overlap = min(s[1] for s in states)
    distinct = len({s[2] for s in states}) == 4
    if overlap < ZZ_OVERLAP_MIN or not distinct:
        return {"zz_hz": None, "ok": False, "min_overlap": overlap, "reason": HYBRIDISED}
    e00, e10, e01, e11 = (s[0] for s in states)
    return {"zz_hz": e11 - e10 - e01 + e00, "ok": True, "min_overlap": overlap, "reason": None}


def zz_flat_time_ns(zz_hz):
    """Starting flat-top length for a pi conditional phase, ns, rounded up to 4 ns.

    [derived, check D5b] from the c-Phase rule Q28 [paper: arXiv:0903.2030v2, p.2]
    "implements a c-Phase, because φ11 = φ01+ φ10−" (integral of zeta dt = pi) and the
    linear-in-length phase of a square pulse, Q4 [paper: arXiv:2005.08863v1, p.2]
    "The linear scaling is a direct consequence of the chosen square pulse shape and allows us to easily chose any targeted conditional phase by adjusting the pulse length accordingly."
    t_flat = 1/(2|ZZ|). A starting value only: ramps add phase.
    Returns (t_ns or None, reason or None).
    """
    if zz_hz is None or not math.isfinite(zz_hz) or zz_hz == 0:
        return None, "no usable ZZ at this bias"
    t = 1e9 / (2 * abs(zz_hz))
    if t > ZZ_T_MAX_NS:
        return None, f"no usable ZZ: a pi phase needs {t:.0f} ns (> {ZZ_T_MAX_NS / 1e3:g} us)"
    return 4.0 * math.ceil(t / 4.0 - 1e-9), None


def coupler_cz_plan(model, fc, ft, ac, at, amplitude, n_scan=201):
    """A4 plan at coupler amplitude A_c (V relative to the idle offset) [derived].

    Coupler frequency from the coupler arc; ZZ by diagonalisation at that bias; the path
    0 -> A_c is scanned (n_scan points) and refused where any dressed assignment fails;
    a coupler within COLLISION_G_FACTOR*max(g) of a qubit, or |11> within 2*max(g) of
    |20>/|02>, is a warning. Frequencies Hz, anharmonicities read as magnitudes.
    """
    gmax = max(model["g_c_hz"], model["g_t_hz"], model["g_ct_hz"])
    ec = model["ec_c_hz"]
    out = {"coupler_frequency_hz": None, "zz_hz": None, "t_flat_ns": None,
           "min_overlap": None, "warnings": [], "refusal": None, "method": "diagonalised"}
    if amplitude is None:
        out["refusal"] = "coupler amplitude unknown"
        return out
    for a in np.linspace(0.0, amplitude, n_scan):
        wc = coupler_frequency(model, float(a))
        step = zz_three_mode(fc, ft, wc, ac, at, ec, model["g_c_hz"], model["g_t_hz"], model["g_ct_hz"])
        if not step["ok"]:
            out["refusal"] = f"{HYBRIDISED} (A_c = {float(a) * 1e3:+.1f} mV on the way)"
            out["coupler_frequency_hz"] = wc
            return out
    wc = coupler_frequency(model, amplitude)
    here = zz_three_mode(fc, ft, wc, ac, at, ec, model["g_c_hz"], model["g_t_hz"], model["g_ct_hz"])
    out.update(coupler_frequency_hz=wc, zz_hz=here["zz_hz"], min_overlap=here["min_overlap"])
    t, reason = zz_flat_time_ns(here["zz_hz"])
    out["t_flat_ns"], out["refusal"] = t, reason
    near = min(abs(wc - fc), abs(wc - ft))
    if near < COLLISION_G_FACTOR * gmax:
        out["warnings"].append(f"coupler within {near / 1e6:.0f} MHz of a qubit "
                               f"(< {COLLISION_G_FACTOR:g} x max g = {COLLISION_G_FACTOR * gmax / 1e6:.0f} MHz): leakage risk")
    delta = fc - ft
    for name, gap in (("|11>-|20>", delta - abs(ac)), ("|11>-|02>", delta + abs(at))):
        if abs(gap) < 2 * gmax:
            out["warnings"].append(f"{name} within {abs(gap) / 1e6:.0f} MHz (< 2 x max g)")
    return out


def zz_curve(model, fc, ft, ac, at, amplitudes):
    """ZZ (Hz, None where unassigned) and coupler frequency along amplitudes [derived]."""
    rows = []
    for a in amplitudes:
        wc = coupler_frequency(model, float(a))
        z = zz_three_mode(fc, ft, wc, ac, at, model["ec_c_hz"],
                          model["g_c_hz"], model["g_t_hz"], model["g_ct_hz"])
        rows.append({"amplitude_v": float(a), "coupler_frequency_hz": wc, "zz_hz": z["zz_hz"]})
    return rows
