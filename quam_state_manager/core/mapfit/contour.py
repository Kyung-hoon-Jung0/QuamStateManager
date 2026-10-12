"""Contour levels, full server polylines and bounded transport polylines."""
from __future__ import annotations

import contourpy
import numpy as np


def centers(coordinates, size):
    """[derived] Cell centers from edges: c_i=(e_i+e_(i+1))/2."""
    a = np.asarray(coordinates, float)
    if a.ndim != 1 or a.size not in (size, size+1) or not np.isfinite(a).all():
        raise ValueError("Expected finite centers or cell edges")
    if not (np.all(np.diff(a) > 0) or np.all(np.diff(a) < 0)):
        raise ValueError("Coordinates must be strictly monotonic")
    return (a[:-1]+a[1:])/2 if a.size == size+1 else a


def levels(z, kind, meta=None, mode="auto", level=None, band=None):
    """[derived] Objective half contrast is min+0.5*(p98-min); phase targets use metadata."""
    meta = meta or {}
    if mode not in ("auto", "target", "custom"):
        raise ValueError("Unknown contour mode")
    if mode == "custom":
        if level is None or not np.isfinite(level):
            raise ValueError("Custom contour requires a finite level")
        return [float(level)]
    if kind == "erroramp" and mode == "auto":
        return []
    period = meta.get("circular")
    if period:
        target = meta.get("target_deg", meta.get("contour_level", 180.))
        if "target_deg" in meta or "contour_level" not in meta:
            target *= period/360
        width = (5 * period/360 if band is None else band)
        return [float(target)] if width == 0 else [float(target-width), float(target), float(target+width)]
    threshold = meta.get("l1_amp_max", meta.get("threshold"))
    if threshold is not None:
        return [float(threshold)]
    a = np.asarray(z, float)
    finite = a[np.isfinite(a)]
    if not finite.size:
        return []
    low = float(np.min(finite))
    high = float(np.max(finite) if kind == "ridge" else np.percentile(finite, 98))
    return [low + .5 * (high-low)]


def simplify(line, x_step=1., y_step=1., tolerance=.25):
    """[derived] Douglas-Peucker distance is measured in axis sample units."""
    line = np.asarray(line, float)
    if len(line) <= 2:
        return line.copy()
    scaled = line / [x_step, y_step]
    keep, stack = {0, len(line)-1}, [(0, len(line)-1)]
    while stack:
        lo, hi = stack.pop()
        if hi-lo <= 1:
            continue
        delta = scaled[hi]-scaled[lo]
        offsets = scaled[lo+1:hi]-scaled[lo]
        den = np.dot(delta, delta)
        t = np.clip(offsets @ delta / den, 0, 1) if den else np.zeros(len(offsets))
        distance = np.linalg.norm(offsets-t[:, None]*delta, axis=1)
        pos = int(np.argmax(distance))
        if distance[pos] > tolerance:
            mid = lo+1+pos
            keep.add(mid)
            stack.extend(((lo, mid), (mid, hi)))
    return line[sorted(keep)]


def contours(z, x, y, contour_levels, circular=None, max_vertices=2000):
    """Contour at cell centers, preserving full lines; use shortest arcs for circular targets.

    [derived] Wrapped residual d=(z-target+P/2) mod P-P/2; mask the opposite half-circle
    so its discontinuity cannot create a false target contour.
    """
    a = np.asarray(z, float)
    if a.ndim != 2 or min(a.shape) < 2 or max_vertices < 2:
        raise ValueError("Contour maps need two cells on each axis")
    xc, yc = centers(x, a.shape[1]), centers(y, a.shape[0])
    dx, dy = float(np.median(np.abs(np.diff(xc)))), float(np.median(np.abs(np.diff(yc))))
    out = []
    for level in contour_levels:
        values = a
        query = float(level)
        if circular:
            values = (a-level+circular/2) % circular-circular/2
            values = np.where(np.abs(values) <= circular/4, values, np.nan)
            query = 0.
        generator = contourpy.contour_generator(x=xc, y=yc, z=np.ma.masked_invalid(values),
                                               name="serial", corner_mask=False, line_type="Separate")
        full = generator.lines(query)
        transport = [simplify(line, dx, dy) for line in full]
        # Increase tolerance until the total transport budget is met. Full geometry stays intact.
        tolerance = .25
        while sum(len(line) for line in transport) > max_vertices and len(transport)*2 <= max_vertices:
            tolerance *= 2
            transport = [simplify(line, dx, dy, tolerance) for line in full]
        truncated = len(transport)*2 > max_vertices
        if truncated:
            transport = transport[:max_vertices//2]
            transport = [line[[0, -1]] for line in transport]
        out.append({"level": float(level), "lines": transport, "full_lines": full,
                    "transport_truncated": truncated})
    return out
