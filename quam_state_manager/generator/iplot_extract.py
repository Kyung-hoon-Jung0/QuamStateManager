"""Generic matplotlib ``Figure`` -> structured JSON extractor (``iplot/v1``).

Runs inside the QM-stack subprocess (where numpy + matplotlib are importable),
never in the State-Manager process. The State-Manager side
(``core/interactive_plots/replot.py``) turns this JSON into a Plotly figure, so
the contract here is intentionally renderer-agnostic and stdlib-JSON-clean.

Why re-extract instead of shipping a PNG: the goal is an *interactive* (zoom /
hover / click-to-apply) reproduction that tracks whatever the experiment's own
``plotting.py`` draws. We run the real plot function, then read its artists back
out of the Axes — so when the analysis changes, this output changes with it and
no per-experiment porting is needed.

Supported artists (the bounded set QM calibration plots use): ``Line2D`` (lines
+ markers), ``PathCollection`` (scatter), ``QuadMesh`` (``pcolormesh`` ->
heatmap), ``LineCollection`` (colorbar strips), and Axes-level ``Text``
annotations. Anything unrecognised is skipped, never guessed.
"""
from __future__ import annotations

import numpy as np
from matplotlib.collections import LineCollection, PathCollection, QuadMesh
from matplotlib.colors import to_hex


def _f(v):
    """A finite python float, or ``None`` (NaN/inf are not JSON-valid)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if np.isfinite(v) else None


def _flist(arr):
    """1-D float list with NaN/inf -> ``None`` (Plotly reads null as a gap)."""
    out = []
    for v in np.asarray(arr, dtype=float).ravel():
        out.append(v if np.isfinite(v) else None)
    return out


def _hex(c):
    try:
        return to_hex(c, keep_alpha=False)
    except (ValueError, TypeError):
        return None


def _opacity(c, default=1.0):
    try:
        from matplotlib.colors import to_rgba
        return float(to_rgba(c)[3])
    except (ValueError, TypeError):
        return default


# matplotlib linestyle -> Plotly dash
_DASH = {"-": "solid", "--": "dash", "-.": "dashdot", ":": "dot",
         "solid": "solid", "dashed": "dash", "dashdot": "dashdot", "dotted": "dot"}


def _line_trace(ln):
    x = ln.get_xdata(orig=False)
    y = ln.get_ydata(orig=False)
    if len(x) == 0:
        return None
    has_line = ln.get_linestyle() not in ("None", " ", "", None) and ln.get_linewidth() > 0
    has_marker = ln.get_marker() not in ("None", " ", "", None)
    mode = ("lines" if has_line else "") + ("+markers" if has_marker and has_line else "")
    if not mode:
        mode = "markers" if has_marker else "lines"
    label = ln.get_label()
    return {
        "type": "line",
        "x": _flist(x), "y": _flist(y),
        "mode": mode,
        "name": None if (label is None or label.startswith("_")) else str(label),
        "color": _hex(ln.get_color()),
        "dash": _DASH.get(ln.get_linestyle(), "solid"),
        "width": _f(ln.get_linewidth()),
        "marker_symbol": _mpl_marker(ln.get_marker()),
        "marker_size": _f((ln.get_markersize() or 6)),
        "opacity": _opacity(ln.get_color(), ln.get_alpha() or 1.0),
    }


# matplotlib marker -> Plotly symbol (common subset; unknown -> circle)
_MARKER = {"o": "circle", ".": "circle", "s": "square", "^": "triangle-up",
           "v": "triangle-down", "D": "diamond", "d": "diamond", "x": "x",
           "+": "cross", "*": "star", "<": "triangle-left", ">": "triangle-right"}


def _mpl_marker(m):
    if m in ("None", " ", "", None):
        return "circle"
    return _MARKER.get(m, "circle")


def _scatter_trace(coll):
    offs = np.asarray(coll.get_offsets())
    if offs.ndim != 2 or offs.shape[0] == 0:
        return None
    fc = coll.get_facecolors()
    colors = [_hex(c) for c in fc] if len(fc) else None
    sizes = coll.get_sizes()
    label = coll.get_label()
    return {
        "type": "scatter",
        "x": _flist(offs[:, 0]), "y": _flist(offs[:, 1]),
        "marker_color": (colors if colors and len(colors) > 1 else (colors[0] if colors else None)),
        "marker_size": (_f(np.sqrt(sizes[0])) if len(sizes) else 6.0),
        "name": None if (label is None or str(label).startswith("_")) else str(label),
        "opacity": float(coll.get_alpha() or 1.0),
    }


def _heatmap_trace(qm):
    """``QuadMesh`` (pcolormesh) -> Plotly heatmap. Skips degenerate 1xN colorbars."""
    arr = qm.get_array()
    if arr is None:
        return None
    coords = qm.get_coordinates()  # (ny+1, nx+1, 2) cell corners
    try:
        ny1, nx1, _ = coords.shape
    except (ValueError, AttributeError):
        return None
    if nx1 < 3 or ny1 < 3:   # 1xN strip == colorbar, not data
        return None
    # cell-corner centers -> Plotly x/y (length nx, ny)
    xc = coords[0, :, 0]
    yc = coords[:, 0, 1]
    xcen = 0.5 * (xc[:-1] + xc[1:])
    ycen = 0.5 * (yc[:-1] + yc[1:])
    z = np.ma.getdata(arr).astype(float)
    z = z.reshape(ny1 - 1, nx1 - 1) if z.size == (ny1 - 1) * (nx1 - 1) else z
    zmask = np.ma.getmaskarray(arr).reshape(z.shape) if np.ma.isMaskedArray(arr) else None
    if zmask is not None:
        z = np.where(zmask, np.nan, z)
    cmap = qm.get_cmap().name
    return {
        "type": "heatmap",
        "x": _flist(xcen), "y": _flist(ycen),
        "z": [[(v if np.isfinite(v) else None) for v in row] for row in z],
        "colorscale": _CMAP.get(cmap, "Viridis"),
    }


# matplotlib cmap -> Plotly colorscale (common subset)
_CMAP = {"viridis": "Viridis", "plasma": "Plasma", "inferno": "Inferno",
         "magma": "Magma", "cividis": "Cividis", "jet": "Jet", "hot": "Hot",
         "coolwarm": "RdBu", "RdBu": "RdBu", "RdBu_r": "RdBu", "Blues": "Blues",
         "gray": "Greys", "Greys": "Greys"}


def _is_colorbar_axes(ax, traces):
    """A decorative strip: produced no usable traces yet carries mesh/line-strip art.

    Decided *after* extraction so a real ``pcolormesh`` heatmap (which yields a
    heatmap trace) is never mistaken for the narrow power colorbar beside it.
    """
    if traces:
        return False
    strips = [c for c in ax.collections if isinstance(c, (QuadMesh, LineCollection))]
    return bool(strips)


def _annotations(ax):
    out = []
    for t in ax.texts:
        s = t.get_text()
        if not s or not s.strip():
            continue
        x, y = t.get_position()
        out.append({
            "text": s,
            "x": _f(x), "y": _f(y),
            "xref_frac": t.get_transform() == ax.transAxes,
        })
    return out


def _shared_index(ax, axes, which):
    """Index of a sibling sharing this axes' ``which`` ('x'|'y') axis, else ``None``.

    ``twinx`` shares X (paired Y-axes); ``twiny`` shares Y (paired X-axes). Used by
    the converter to fold a twin pair into one panel with a secondary right-Y / top-X
    axis instead of two stacked panels.
    """
    grp = ax.get_shared_x_axes() if which == "x" else ax.get_shared_y_axes()
    sibs = grp.get_siblings(ax)
    if len(sibs) <= 1:
        return None
    for j, other in enumerate(axes):
        if other is ax:
            continue
        if other in sibs:
            return j
    return None


def extract_axes(ax, axes):
    leg = ax.get_legend()
    show_legend = leg is not None
    traces = []
    for ln in ax.get_lines():
        t = _line_trace(ln)
        if t:
            traces.append(t)
    for coll in ax.collections:
        if isinstance(coll, PathCollection):
            t = _scatter_trace(coll)
            if t:
                traces.append(t)
        elif isinstance(coll, QuadMesh):
            t = _heatmap_trace(coll)
            if t:
                traces.append(t)
    return {
        "xlabel": ax.get_xlabel(), "ylabel": ax.get_ylabel(),
        "title": ax.get_title(),
        "xscale": ax.get_xscale(), "yscale": ax.get_yscale(),
        "xlim": [_f(v) for v in ax.get_xlim()],
        "ylim": [_f(v) for v in ax.get_ylim()],
        "x_side": ax.xaxis.get_label_position(),   # 'bottom' | 'top'
        "y_side": ax.yaxis.get_label_position(),   # 'left' | 'right'
        "shares_x_with": _shared_index(ax, axes, "x"),  # twinx sibling (paired Y)
        "shares_y_with": _shared_index(ax, axes, "y"),  # twiny sibling (paired X)
        "is_colorbar": _is_colorbar_axes(ax, traces),
        "show_legend": show_legend,
        "traces": traces,
        "annotations": _annotations(ax),
        "grid": _grid_pos(ax),
    }


def _grid_pos(ax):
    try:
        ss = ax.get_subplotspec()
        g = ss.get_gridspec()
        return {"row": ss.rowspan.start, "col": ss.colspan.start,
                "nrows": g.nrows, "ncols": g.ncols}
    except (AttributeError, ValueError):
        return {"row": 0, "col": 0, "nrows": 1, "ncols": 1}


def extract_figure(fig, key, title=None):
    """Whole Figure -> ``{key, title, suptitle, axes:[...]}``."""
    f = getattr(fig, "fig", fig)  # unwrap QubitGrid-like wrappers
    sup = ""
    if getattr(f, "_suptitle", None) is not None:
        sup = f._suptitle.get_text()
    axes = f.get_axes()
    return {
        "key": key,
        "title": title or sup or key,
        "suptitle": sup,
        "axes": [extract_axes(ax, axes) for ax in axes],
    }
