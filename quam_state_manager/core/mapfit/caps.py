"""Design section 4.2 strength caps, with numerical checks in test_mapfit_caps."""
from __future__ import annotations

import numpy as np

MAX_CONTRAST_LOSS = .10
AGREE_TOL = .003
CAP_FACTOR = float(np.sqrt(-np.log1p(-MAX_CONTRAST_LOSS) / (2 * np.pi**2)))


def attenuation(sigma, period):
    """[derived] Fourier Gaussian attenuation exp(-2 pi^2 sigma^2/P^2)."""
    if np.any(np.asarray(period) <= 0):
        raise ValueError("Period must be positive")
    return np.exp(-2 * np.pi**2 * np.asarray(sigma)**2 / np.asarray(period)**2)


def fringe_cap(period, loss=MAX_CONTRAST_LOSS):
    """[derived] Invert attenuation: sigma=P sqrt(-ln(1-L)/(2 pi^2)).

    L=0.10 is a reference code guideline; no paper provenance is claimed.
    """
    if not 0 < loss < 1 or np.any(np.asarray(period) <= 0):
        raise ValueError("Positive period and loss strictly between zero and one required")
    return np.asarray(period) * np.sqrt(-np.log1p(-loss) / (2 * np.pi**2))


def erroramp_caps(counts, a_pi, step, theta=np.pi, k=1.):
    """[derived] P_N=2 pi |a_pi|/(|N| theta k step); cap_N=CAP_FACTOR P_N.

    Saved slope protects dense fringes. k=1 explicitly selects reference compatibility.
    """
    if min(abs(a_pi), step, theta, k) <= 0 or not np.isfinite([a_pi, step, theta, k]).all():
        raise ValueError("Finite positive scales required")
    counts = np.abs(np.asarray(counts, float))
    if not np.isfinite(counts).all() or np.any(counts <= 0):
        raise ValueError("Pulse counts must be finite and positive")
    return fringe_cap(2 * np.pi * abs(a_pi) / (counts * theta * k * step))


def phase_slope_cap(slope, step):
    """[derived] A phase slope in degrees/control gives P_pts=360/(|slope| step)."""
    if step <= 0 or not np.isfinite(step) or not np.isfinite(slope):
        raise ValueError("Finite slope and positive step required")
    return float(fringe_cap(360 / (abs(slope) * step))) if slope else float("inf")


def ridge_caps(hwhm, frequency_step, slope, control_step):
    """[derived] cap_f=HWHM/df; cap_control=max(1,HWHM/(|dF/dc| dc))."""
    if min(hwhm, frequency_step, control_step) <= 0:
        raise ValueError("Widths and steps must be positive")
    return {"frequency": hwhm / frequency_step,
            "control": max(1., hwhm / (abs(slope) * control_step)) if slope else float("inf")}


def chevron_caps(frequencies, dt, f0, slope, control_step):
    """[derived] cap_t=CAP_FACTOR/(f dt); cap_c=0.5 f0/(|slope| dc).

    f(c)^2=f0^2+slope^2(c-c0)^2 reaches 2 f0^2 at |c-c0|=f0/|slope|.
    """
    if min(dt, f0, abs(slope), control_step) <= 0 or np.any(np.asarray(frequencies) <= 0):
        raise ValueError("Positive chevron scales required")
    return {"time": fringe_cap(1 / (np.asarray(frequencies) * dt)),
            "control": .5 * f0 / (abs(slope) * control_step)}


def landscape_cap(shape, extremum_index, contour_spacing=float("inf")):
    """[derived] cap=min(nearest edge distance/4, target contour spacing/4).

    The 4-sigma support avoids boundaries. For an even normalized kernel,
    K*(a x^2+b x+c)=a x^2+b x+c+a E[u^2], preserving the quadratic vertex.
    """
    distances = [min(i, n-1-i) for i, n in zip(extremum_index, shape)]
    if len(distances) != 2 or min(distances) < 0 or contour_spacing <= 0:
        raise ValueError("Interior two-dimensional location and positive spacing required")
    return min(min(distances), contour_spacing) / 4


def apply_caps(sigma_x, sigma_y, cap_x, cap_y, override=False, count_axis=None):
    """Clamp strengths and retain an explicit override warning; counts stay locked."""
    if min(sigma_x, sigma_y, cap_x, cap_y) < 0 or not np.isfinite([sigma_x, sigma_y]).all():
        raise ValueError("Nonnegative finite strengths required")
    used_x = sigma_x if override else min(sigma_x, cap_x)
    used_y = sigma_y if override else min(sigma_y, cap_y)
    if count_axis == "x":
        used_x = 0.
    if count_axis == "y":
        used_y = 0.
    exceeded = (sigma_x > cap_x and count_axis != "x") or (sigma_y > cap_y and count_axis != "y")
    return {"sigma_x": used_x, "sigma_y": used_y, "cap_x": cap_x, "cap_y": cap_y,
            "override": bool(override and exceeded), "warning": "cap overridden" if override and exceeded else ""}
