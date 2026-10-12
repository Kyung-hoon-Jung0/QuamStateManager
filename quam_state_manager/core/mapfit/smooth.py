"""NaN-aware separable smoothing in sample points, in y then x order."""
from __future__ import annotations

import numpy as np
from scipy.ndimage import gaussian_filter1d, uniform_filter1d, median_filter
from scipy.signal import savgol_filter


def window_size(sigma, kernel):
    """[derived] Odd support: box 2 ceil(1.5 sigma)+1; other windows 2 ceil(2 sigma)+1.

    These are design support rules, not exact equal-variance identities.
    """
    return 2 * int(np.ceil((1.5 if kernel == "box" else 2) * sigma)) + 1


def _filter(a, sigma, axis, kernel):
    if sigma == 0:
        return a.copy()
    if kernel == "gaussian":
        return gaussian_filter1d(a, sigma, axis=axis, mode="nearest", truncate=4.0)
    width = window_size(sigma, kernel)
    if kernel == "box":
        return uniform_filter1d(a, width, axis=axis, mode="nearest")
    if kernel == "savgol":
        return savgol_filter(a, max(3, width), 2, axis=axis, mode="nearest")
    sizes = [1] * a.ndim
    sizes[axis] = width
    return median_filter(a, size=sizes, mode="nearest")


def _axis(a, sigma, axis, kernel):
    """[derived] Normalized convolution is K(z m)/K(m), valid where K(m)>=0.5."""
    if sigma == 0:
        return a.copy()
    mask = np.isfinite(a)
    if kernel in ("gaussian", "box"):
        weights = _filter(mask.astype(float), sigma, axis, kernel)
        numerator = _filter(np.where(mask, a, 0), sigma, axis, kernel)
        return np.divide(numerator, weights, out=np.full_like(a, np.nan), where=weights >= .5)
    moved = np.moveaxis(a, axis, -1)
    filled = moved.copy()
    for index in np.ndindex(moved.shape[:-1]):
        row = moved[index]
        finite = row[np.isfinite(row)]
        filled[index] = np.where(np.isfinite(row), row, np.median(finite) if finite.size else 0)
    result = _filter(np.moveaxis(filled, -1, axis), sigma, axis, kernel)
    return np.where(mask, result, np.nan)


def smooth(z, sigma_x=0., sigma_y=0., kernel="gaussian", circular=None, row_sigmas=None):
    """[derived] Circular mean: atan2(K sin(2 pi z/P), K cos(2 pi z/P))*P/(2 pi).

    row_sigmas supplies a distinct x strength per count row; count rows never mix.
    """
    a = np.asarray(z, float)
    if a.ndim != 2 or kernel not in ("gaussian", "box", "savgol", "median"):
        raise ValueError("Expected a two-dimensional map and a supported kernel")
    if not np.isfinite([sigma_x, sigma_y]).all() or min(sigma_x, sigma_y) < 0:
        raise ValueError("Strengths must be finite and nonnegative")
    if circular is not None and (not np.isfinite(circular) or circular <= 0):
        raise ValueError("Circular period must be positive")
    if row_sigmas is not None:
        row_sigmas = np.asarray(row_sigmas, float)
        if row_sigmas.shape != (a.shape[0],) or not np.isfinite(row_sigmas).all() or np.any(row_sigmas < 0):
            raise ValueError("One nonnegative strength is required per row")
        if sigma_y != 0:
            raise ValueError("Count-axis smoothing is locked")

    def apply(values):
        values = _axis(values, sigma_y, 0, kernel)
        if row_sigmas is None:
            return _axis(values, sigma_x, 1, kernel)
        return np.stack([_axis(row[None, :], float(s), 1, kernel)[0] for row, s in zip(values, row_sigmas)])

    if circular is None:
        return apply(a)
    angle = a * (2 * np.pi / circular)
    sine, cosine = apply(np.sin(angle)), apply(np.cos(angle))
    out = np.mod(np.arctan2(sine, cosine) * circular / (2 * np.pi), circular)
    return np.where(np.hypot(sine, cosine) > 1e-12, out, np.nan)
