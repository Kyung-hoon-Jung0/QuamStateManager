"""Closed-form model functions used to reconstruct fit-overlay curves.

These mirror the experiment code's analysis models (qualibration_graphs /
qualibration_libs) so the Interactive plots can redraw the same fit curves
from the parameters already stored in ``ds_fit`` / ``data.json``. We only
*evaluate* stored parameters here — we never fit.
"""
from __future__ import annotations

import numpy as np


def lorentzian_dip_linbg(f, f0, fwhm, amp, bg0, bg1):
    """Inverted Lorentzian with linear background (resonator spectroscopy).

    Matches ``resonator_spectroscopy/analysis.py``:
    ``R(f) = (bg0 + bg1*(f - f.mean())) - amp / (1 + ((f - f0)/(fwhm/2))**2)``.
    """
    f = np.asarray(f, dtype=float)
    fc = f - f.mean()
    return (bg0 + bg1 * fc) - amp / (1.0 + ((f - f0) / (fwhm / 2.0)) ** 2)


def sin_osc(x, offset, a, freq, phi):
    """Cosine/sine oscillation overlay (power Rabi 1D): ``offset + a*sin(2πfx+φ)``."""
    x = np.asarray(x, dtype=float)
    return offset + a * np.sin(2.0 * np.pi * freq * x + phi)


def osc_decay(t, a, f, phi, offset, decay):
    """Damped oscillation (Ramsey): ``offset + a*exp(decay*t)*cos(2π f t + phi)``.

    Matches ``qualibration_libs`` ``oscillation_decay_exp`` (``decay`` is the
    stored decay-rate value, typically negative).
    """
    t = np.asarray(t, dtype=float)
    return offset + a * np.exp(decay * t) * np.cos(2.0 * np.pi * f * t + phi)


def lorentzian_peak_linbg(f, f0, fwhm, amp, base):
    """Lorentzian peak on a (flat) baseline: ``base + amp/(1+((f-f0)/(fwhm/2))**2)``.

    Used for qubit-spectroscopy's rotated-I peak when the saved schema gives a
    baseline array + (position, width, amplitude) instead of a full ``popt``.
    """
    f = np.asarray(f, dtype=float)
    base = np.asarray(base, dtype=float)
    return base + amp / (1.0 + ((f - f0) / (fwhm / 2.0)) ** 2)


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
