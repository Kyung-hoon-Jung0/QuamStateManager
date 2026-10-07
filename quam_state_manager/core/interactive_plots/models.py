"""Closed-form model functions used to reconstruct fit-overlay curves.

These are the experiment code's own analysis models, so the Interactive plots
can redraw the curve the node fitted from the parameters already stored in
``ds_fit`` / ``data.json``. We only *evaluate* stored parameters here -- we
never fit.

Every model below quotes the line it reproduces. "qualibration_libs" means the
library version installed in the lab environments (0.2.1; the ``models.py``
file is byte-identical across every environment checked, including 0.3.0).
A recipe must not draw a curve whose model it cannot pin to the run (see
``fitcheck.py`` and docs/300): a wrong curve shown as "the fit" is worse than
no curve.
"""
from __future__ import annotations

import numpy as np


def oscillation(t, a, f, phi, offset):
    """Cosine oscillation -- the model behind ``fit_oscillation`` (power Rabi).

    [source: qualibration_libs analysis/models.py, line 67]
    ``return a * np.cos(2 * np.pi * f * t + phi) + offset``
    and analysis/fitting.py, line 310 (inside ``fit_oscillation``):
    ``model = Model(oscillation, independent_vars=["t"])``.
    A sine here (the previous overlay) shifts the curve by a quarter period.
    """
    t = np.asarray(t, dtype=float)
    return a * np.cos(2.0 * np.pi * f * t + phi) + offset


def oscillation_decay_exp(t, a, f, phi, offset, decay):
    """Exponentially damped cosine -- the Ramsey model.

    [source: qualibration_libs analysis/models.py, line 102]
    ``return a * np.exp(-t * decay) * np.cos(2 * np.pi * f * t + phi) + offset``
    and analysis/fitting.py, lines 138-139 (``fit_oscillation_decay_exp``):
    ``curve_fit(`` / ``oscillation_decay_exp, x, y, p0=[a, f, phi, offset, decay]``.
    ``decay`` is a positive RATE (1/T2*). The node stores
    ``t2_star = 1e-9 * mean(1 / decay)`` (seconds), which every archived run
    with a finite ``t2_star`` satisfies -- the per-run check in ``fitcheck``.
    """
    t = np.asarray(t, dtype=float)
    return a * np.exp(-t * decay) * np.cos(2.0 * np.pi * f * t + phi) + offset


def decay_exp(t, a, offset, decay):
    """Plain exponential -- the model behind ``fit_decay_exp`` (legacy 1Q RB).

    [source: qualibration_libs analysis/models.py, line 118]
    ``return a * np.exp(t * decay) + offset``
    Note the sign: unlike ``oscillation_decay_exp``, ``decay`` multiplies +t
    here, so a decaying fit stores a NEGATIVE ``decay``.
    """
    t = np.asarray(t, dtype=float)
    return a * np.exp(t * decay) + offset


def power_law(x, p, A, B):
    """RB power law ``A * p**x + B`` (the newer 1Q RB generation).

    calibration_utils/common_utils/fitting_tools.py, lines 67-68:
    ``def power_law(x, p, A, B):`` / ``return A * (p**x) + B``.
    """
    x = np.asarray(x, dtype=float)
    return A * (p ** x) + B


def lorentzian_peak(x, amplitude, center, width, offset):
    """Plain Lorentzian peak (half width ``width``) -- legacy qubit-spectroscopy overlay.

    [source: qualibration_libs analysis/models.py, line 30]
    ``return offset + amplitude * (1 / (1 + ((x - center) / width) ** 2))``
    """
    x = np.asarray(x, dtype=float)
    return offset + amplitude * (1.0 / (1.0 + ((x - center) / width) ** 2))


def lorentzian_peak_linbg(f, f0, fwhm, amp, bg0, bg1):
    """Lorentzian peak on a linear background, referenced to the mean of ``f``.

    The node generation that stores ``popt`` = [f0, fwhm, amp, bg0, bg1]
    (calibration_utils/qubit_spectroscopy/analysis.py, lines 69-70):
    ``fc = float(np.mean(f))`` /
    ``return (bg0 + bg1 * (f - fc)) + amp / (1.0 + ((f - f0) / (fwhm / 2.0)) ** 2)``.
    ``fc`` is the mean of whatever ``f`` is passed, so the caller must pass the
    fit window, not the whole sweep (a whole-sweep ``f`` shifts and tilts the
    background).
    """
    f = np.asarray(f, dtype=float)
    fc = float(np.mean(f)) if f.size else 0.0
    return (bg0 + bg1 * (f - fc)) + amp / (1.0 + ((f - f0) / (fwhm / 2.0)) ** 2)


def multiexp_decay(t, a_dc, components):
    """Multi-exponential step response (flux distortion 19a fitted_data).

    ``y(t) = a_dc + Σ a_i * exp(-t/τ_i)`` for ``components = [(a_i, τ_i), ...]``.
    """
    t = np.asarray(t, dtype=float)
    y = np.full(t.shape, float(a_dc))
    for amp, tau in components:
        if tau == 0:
            continue
        y = y + float(amp) * np.exp(-t / float(tau))
    return y


def multiexp_finite_pulse(t, a_dc, components, t_pulse):
    """Finite-pulse multi-exponential (flux distortion 19b fitted_data).

    ``y(t) = a_dc + Σ a_i * (1 - exp(-T/τ_i)) * exp(-t/τ_i)``, T = pulse length.
    """
    t = np.asarray(t, dtype=float)
    y = np.full(t.shape, float(a_dc))
    for amp, tau in components:
        if tau == 0:
            continue
        y = y + float(amp) * (1.0 - np.exp(-float(t_pulse) / float(tau))) * np.exp(-t / float(tau))
    return y


def detrend_phase_poly(phase, axis, center=0.0, halfwidth=0.0, deg=3):
    """Subtract a degree-``deg`` polynomial fit of ``phase`` vs ``axis``.

    The polynomial is fit only to points *outside* ``±halfwidth`` of ``center``
    (so the resonance feature doesn't bias the background), matching
    ``plot_detrended_phase``. Falls back to fitting all points when too few
    remain outside the exclusion window.
    """
    phase = np.asarray(phase, dtype=float)
    axis = np.asarray(axis, dtype=float)
    mask = np.abs(axis - center) > halfwidth
    if int(mask.sum()) < deg + 1:
        mask = np.ones_like(axis, dtype=bool)
    coeffs = np.polyfit(axis[mask], phase[mask], deg)
    return phase - np.polyval(coeffs, axis)
