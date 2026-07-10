"""Shared Plotly construction helpers for the interactive-plot recipes.

Recipes build their figure ``{"data": [...], "layout": {...}}`` dicts directly
(for full control over twin axes, heatmaps, shapes, etc.). These helpers cover
the cross-cutting concerns: JSON-safe serialization (numpy + NaN/Inf), and a
shared color palette aligned with the app's Plotly ``colorway``.
"""
from __future__ import annotations

import math

import numpy as np

# Aligned with UI_CONFIG.plotly.colorway in web/static/app.js.
COLORWAY = [
    "#4e79a7", "#f28e2b", "#59a14f", "#e15759",
    "#76b7b2", "#edc948", "#b07aa1", "#ff9da7",
]
FIT_COLOR = "#e15759"      # red — fit overlays / markers
ACCENT = "#59a14f"         # green — "optimal" vertical lines
GROUP_DELAY = "#f28e2b"    # orange — group-delay (secondary y)


def jsonable(x):
    """Recursively convert numpy/python values to JSON-safe values.

    Arrays become (nested) lists; non-finite floats (NaN/Inf) become ``None``
    so ``jsonify`` emits valid JSON (Plotly renders ``null`` as a gap).
    """
    if isinstance(x, np.ndarray):
        return [jsonable(v) for v in x.tolist()]
    if isinstance(x, dict):
        return {k: jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (np.floating, float)):
        xf = float(x)
        return None if (math.isnan(xf) or math.isinf(xf)) else xf
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, bytes):
        return x.decode()
    return x


def clean(arr):
    """1-D / N-D numeric array → nested JSON-safe lists (NaN/Inf → None)."""
    return jsonable(np.asarray(arr))


# --- trace / layout builders -------------------------------------------

def line(x, y, name=None, color=None, dash=None, width=None, mode="lines",
         customdata=None, xaxis=None, yaxis=None, opacity=None, showlegend=None):
    """A scatter (line/marker) trace with JSON-safe x/y."""
    tr = {"x": clean(x), "y": clean(y), "type": "scatter", "mode": mode}
    if name is not None:
        tr["name"] = name
    ln = {}
    if color:
        ln["color"] = color
    if dash:
        ln["dash"] = dash
    if width is not None:
        ln["width"] = width
    if ln:
        tr["line"] = ln
    if customdata is not None:
        tr["customdata"] = customdata
    if xaxis:
        tr["xaxis"] = xaxis
    if yaxis:
        tr["yaxis"] = yaxis
    if opacity is not None:
        tr["opacity"] = opacity
    if showlegend is not None:
        tr["showlegend"] = showlegend
    return tr


def heatmap(x, y, z, colorscale="Viridis", zmin=None, zmax=None,
            colorbar_title=None, xaxis=None, yaxis=None, robust=False):
    """A heatmap trace with JSON-safe x/y/z.

    With ``robust=True`` and no explicit ``zmin``/``zmax``, the color range is
    clipped to the 2nd–98th percentile of the finite ``z`` values (mirrors
    xarray/matplotlib ``robust=True``), giving the same contrast as the
    experiment figures instead of letting a few outliers wash out the map.
    """
    if robust and zmin is None and zmax is None:
        finite = np.asarray(z, dtype=float)
        finite = finite[np.isfinite(finite)]
        if finite.size:
            lo, hi = (float(v) for v in np.percentile(finite, [2, 98]))
            if hi > lo:
                zmin, zmax = lo, hi
    tr = {"x": clean(x), "y": clean(y), "z": clean(z),
          "type": "heatmap", "colorscale": colorscale}
    if zmin is not None:
        tr["zmin"] = zmin
    if zmax is not None:
        tr["zmax"] = zmax
    if colorbar_title:
        tr["colorbar"] = {"title": {"text": colorbar_title}}
    if xaxis:
        tr["xaxis"] = xaxis
    if yaxis:
        tr["yaxis"] = yaxis
    return tr


def axis(text, log=False, **extra):
    """An axis dict with a title (and optional log type)."""
    a = {"title": {"text": text}}
    if log:
        a["type"] = "log"
    a.update(extra)
    return a


def vline(x, color=ACCENT, dash="solid", width=1.5):
    """A full-height vertical line shape at data-x ``x`` (paper-y)."""
    return {"type": "line", "xref": "x", "yref": "paper",
            "x0": x, "x1": x, "y0": 0, "y1": 1,
            "line": {"color": color, "dash": dash, "width": width}}


def hline(y, color="gray", dash="dot", width=0.5):
    """A full-width horizontal line shape at data-y ``y`` (paper-x)."""
    return {"type": "line", "xref": "paper", "yref": "y",
            "x0": 0, "x1": 1, "y0": y, "y1": y,
            "line": {"color": color, "dash": dash, "width": width}}


# Cap markers per scatter trace. IQ blobs hold thousands of single-shot points;
# we render with SVG (not WebGL) so it works in every browser/webview, and a few
# thousand SVG markers stay responsive. A uniform subsample preserves blob shape.
_SCATTER_MAX_POINTS = 3000


def scatter(x, y, name=None, color=None, size=5, opacity=None, customdata=None, webgl=False):
    """A markers-only scatter trace (e.g. IQ blobs).

    Uses SVG ``scatter`` by default — it renders without WebGL (the embedded
    webview may not provide it, in which case ``scattergl`` shows only a "WebGL
    not supported" message) — and uniformly downsamples to ``_SCATTER_MAX_POINTS``
    so a few-thousand-shot blob stays fast in SVG. Pass ``webgl=True`` for the GL
    renderer (faster for huge clouds, but requires WebGL).
    """
    x = np.asarray(x)
    y = np.asarray(y)
    n = int(min(x.shape[0] if x.ndim else 0, y.shape[0] if y.ndim else 0))
    if n > _SCATTER_MAX_POINTS:
        step = -(-n // _SCATTER_MAX_POINTS)  # ceil division → ≤ _SCATTER_MAX_POINTS pts
        x = x[::step]
        y = y[::step]
        if customdata is not None and hasattr(customdata, "__getitem__"):
            try:
                customdata = customdata[::step]
            except (TypeError, KeyError, IndexError):
                pass
    tr = {"x": clean(x), "y": clean(y),
          "type": "scattergl" if webgl else "scatter", "mode": "markers",
          "marker": {"size": size}}
    if color is not None:
        tr["marker"]["color"] = color
    if name is not None:
        tr["name"] = name
    if opacity is not None:
        tr["marker"]["opacity"] = opacity
    if customdata is not None:
        tr["customdata"] = customdata
    return tr


def bar(x, y, name=None, color=None):
    """A bar trace (histograms via precomputed counts, or categorical values)."""
    tr = {"x": clean(x) if not _is_str_seq(x) else list(x),
          "y": clean(y), "type": "bar"}
    if name is not None:
        tr["name"] = name
    if color is not None:
        tr["marker"] = {"color": color}
    return tr


def confusion_matrix(z, labels=("g", "e"), colorscale="Viridis"):
    """A confusion-matrix heatmap trace + text annotations (prepared vs measured).

    ``z`` is a 2-D list/array indexed [prepared][measured]. Returns
    ``(trace, annotations)`` ready to drop into figure data + layout. Defaults to
    Viridis (matching the saved experiment figures, and reading well on both the
    light and dark theme — unlike a white-low scale, whose near-zero cells look
    like blank holes on the dark background). The annotation text contrast assumes
    a dark-low → bright-high scale: white text on the dark cells, black on the
    bright (high-P) cells, with the crossover near Viridis's mid green-yellow.
    """
    import numpy as np
    z = np.asarray(z, dtype=float)
    x = list(labels)            # measured
    y = list(labels)            # prepared
    trace = {"x": x, "y": y, "z": clean(z), "type": "heatmap",
             "colorscale": colorscale, "zmin": 0, "zmax": 1,
             "colorbar": {"title": {"text": "P"}}}
    annotations = []
    for i in range(z.shape[0]):
        for j in range(z.shape[1]):
            v = z[i, j]
            # Viridis is light (green→yellow) only above ~0.6; below that it's
            # dark (purple→teal) and needs white text.
            text_color = "#000" if (np.isfinite(v) and v > 0.6) else "#fff"
            annotations.append({
                "x": x[j], "y": y[i], "text": f"{v:.3f}" if np.isfinite(v) else "—",
                "showarrow": False, "font": {"color": text_color},
            })
    return trace, annotations


def _is_str_seq(x):
    try:
        return len(x) > 0 and isinstance(x[0], str)
    except (TypeError, IndexError):
        return False
