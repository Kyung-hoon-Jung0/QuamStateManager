"""Per-run evidence that a reconstructed fit curve IS the node's fit (docs/300).

A fit overlay is redrawn from stored parameters, so it is only as right as
SM's guess of the model, its sign convention and its reference point. Where
the run itself stores a number the node computed FROM its own curve (an R^2,
an oscillation SNR, a T2* derived from the decay rate), SM recomputes that
number from its reconstruction on the plotted data. Agreement proves the
reconstruction; disagreement means SM would be drawing a different curve, and
the overlay is withheld with a note instead (an honest absence beats a wrong
curve).

The tolerances are not tuned to make archives pass: on every archived run the
recomputation agrees with the stored value to rounding (docs/300 section 3),
and a wrong convention misses by orders of magnitude more than the tolerance.
"""
from __future__ import annotations

import numpy as np

# Recomputed-vs-stored agreement. The archives agree to ~1e-12; float32 storage
# (NetCDF-classic runs) needs a little slack. A wrong model misses by > 0.5.
R2_ABS_TOL = 1e-3
SNR_REL_TOL = 1e-3
T2_REL_TOL = 1e-4


def r2(y, f) -> float:
    """Coefficient of determination of curve ``f`` against data ``y`` (NaN-safe).

    Same expression as the nodes' own quality metrics:
    ``1 - sum((y - f)**2) / (sum((y - mean(y))**2) + 1e-30)``.
    Returns NaN with fewer than 3 finite pairs.
    """
    y = np.asarray(y, dtype=float).ravel()
    f = np.asarray(f, dtype=float).ravel()
    if y.shape != f.shape:
        return float("nan")
    m = np.isfinite(y) & np.isfinite(f)
    if int(m.sum()) < 3:
        return float("nan")
    y, f = y[m], f[m]
    ss_res = float(np.sum((y - f) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2)) + 1e-30
    return 1.0 - ss_res / ss_tot


def osc_amp_snr(y, model_y, a) -> float:
    """Fitted amplitude over the robust (MAD) scatter of the residual.

    The power-Rabi node's stored ``osc_amp_snr`` (calibration_utils/power_rabi/
    analysis.py, lines 170-171):
    ``scatter = 1.4826 * float(np.median(np.abs(resid - np.median(resid)))) + 1e-15`` /
    ``rec["osc_amp_snr"] = abs(a) / scatter``.
    """
    resid = np.asarray(y, dtype=float) - np.asarray(model_y, dtype=float)
    resid = resid[np.isfinite(resid)]
    if resid.size < 3:
        return float("nan")
    scatter = 1.4826 * float(np.median(np.abs(resid - np.median(resid)))) + 1e-15
    return abs(float(a)) / scatter


def agrees_abs(recomputed: float, stored: float, tol: float) -> bool:
    """``|recomputed - stored| <= tol``, scaled up for huge magnitudes.

    A runaway fit stores an R^2 like -1e17; two evaluations of the same curve
    then differ by float rounding far above an absolute 1e-3, so the tolerance
    is ``tol * max(1, |stored|)`` (absolute near R^2 = 1, relative far from it).
    """
    if not (np.isfinite(recomputed) and np.isfinite(stored)):
        return False
    return abs(recomputed - stored) <= tol * max(1.0, abs(stored))


def agrees_rel(recomputed: float, stored: float, tol: float) -> bool:
    if not (np.isfinite(recomputed) and np.isfinite(stored)):
        return False
    return abs(recomputed - stored) <= tol * max(abs(stored), 1e-300)


RUNAWAY_SPANS = 10.0
RUNAWAY_NOTE = "The node's fit runs away outside the data: y axis held to the data."


def runs_away(curve, *data) -> bool:
    """True when the curve leaves the data's span by more than ``RUNAWAY_SPANS`` spans.

    Such a curve is still the node's (a fit that diverged); drawn as-is, Plotly's
    autorange would squash the data to a line, so the caller holds the y axis to
    the data (``data_range``) and says so -- the node figures' rule.
    """
    c = np.asarray(curve, dtype=float).ravel()
    c = c[np.isfinite(c)]
    d = np.concatenate([np.asarray(a, dtype=float).ravel() for a in data]) if data else np.array([])
    d = d[np.isfinite(d)]
    if c.size == 0 or d.size == 0:
        return False
    lo, hi = float(d.min()), float(d.max())
    span = (hi - lo) or (abs(hi) * 0.1 + 1e-12)
    return bool(c.max() > hi + RUNAWAY_SPANS * span or c.min() < lo - RUNAWAY_SPANS * span)


def data_range(*arrays, pad_frac: float = 0.12):
    """``[lo - pad, hi + pad]`` over the finite values of the DATA arrays.

    Mirrors the node figures' rule for a fit that runs away: the y axis is held
    to the measurement so the data stays readable and the curve leaving the
    frame is itself the warning (calibration_utils/ramsey/plotting.py,
    ``_clip_to_data``, line 186: ``pad = 0.12 * (hi - lo)``).
    """
    vals = [np.asarray(a, dtype=float).ravel() for a in arrays if a is not None]
    if not vals:
        return None
    v = np.concatenate(vals)
    v = v[np.isfinite(v)]
    if v.size == 0:
        return None
    lo, hi = float(v.min()), float(v.max())
    pad = pad_frac * (hi - lo) if hi > lo else (abs(hi) * 0.1 + 1e-9)
    return [lo - pad, hi + pad]
