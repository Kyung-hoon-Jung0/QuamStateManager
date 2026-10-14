"""Generic matplotlib ``Figure`` -> structured JSON extractor (``iplot/v2``).

Runs inside the QM-stack subprocess (where numpy + matplotlib are importable),
never in the State-Manager process. The State-Manager side
(``core/interactive_plots/replot.py``) turns this JSON into a Plotly figure, so
the contract here is intentionally renderer-agnostic and stdlib-JSON-clean.

Why re-extract instead of shipping a PNG: the goal is an *interactive* (zoom /
hover / click-to-apply) reproduction that tracks whatever the experiment's own
``plotting.py`` draws. We run the real plot function, then read its artists back
out of the Axes — so when the analysis changes, this output changes with it and
no per-experiment porting is needed.

Lines, scatter, scalar maps, contours, fills, segments, text, annotations and
secondary axes are retained. Unsupported content is counted in ``skipped``.
"""
from __future__ import annotations

import numpy as np
from matplotlib.collections import LineCollection, PathCollection, QuadMesh, PolyCollection
from matplotlib.contour import ContourSet
from matplotlib.text import Annotation
from matplotlib.axes._secondary_axes import SecondaryAxis
from matplotlib.path import Path
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
    has_line = ln.get_linestyle() not in ("None", " ", "", None) and ln.get_linewidth() > 0
    has_marker = ln.get_marker() not in ("None", " ", "", None)
    mode = ("lines" if has_line else "") + ("+markers" if has_marker and has_line else "")
    if not mode:
        mode = "markers" if has_marker else "none"
    label = ln.get_label()
    return {
        "type": "line",
        "x": _flist(x), "y": _flist(y),
        "mode": mode,
        "name": None if (label is None or label.startswith("_")) else str(label),
        "color": _hex(ln.get_color()),
        "dash": _DASH.get(ln.get_linestyle(), "solid"),
        "width": _f(ln.get_linewidth()),
        "linestyle": ln.get_linestyle(),
        "drawstyle": ln.get_drawstyle(),
        "zorder": _f(ln.get_zorder()),
        "visible": ln.get_visible(),
        "marker_symbol": _mpl_marker(ln.get_marker()),
        "marker_size": _f((ln.get_markersize() or 6)),
        "opacity": (float(ln.get_alpha()) if ln.get_alpha() is not None
                    else _opacity(ln.get_color())),
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
    coll.update_scalarmappable()
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
        "marker_size": (_flist(np.sqrt(sizes)) if len(sizes) > 1
                        else (_f(np.sqrt(sizes[0])) if len(sizes) else 6.0)),
        "name": None if (label is None or str(label).startswith("_")) else str(label),
        "zorder": _f(coll.get_zorder()),
        "opacity": float(coll.get_alpha()) if coll.get_alpha() is not None else 1.0,
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
    if nx1 < 2 or ny1 < 2:
        return None
    # cell-corner centers -> Plotly x/y (length nx, ny)
    xc = coords[0, :, 0]
    yc = coords[:, 0, 1]
    if not (np.allclose(coords[:, :, 0], xc[None, :]) and
            np.allclose(coords[:, :, 1], yc[:, None]) and
            _monotonic(xc) and _monotonic(yc)):
        return None
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
        "x_edges": _flist(xc), "y_edges": _flist(yc),
        "z": [[(v if np.isfinite(v) else None) for v in row] for row in z],
        "colorscale": _CMAP.get(cmap, "Viridis"),
        **_map_metadata(qm),
    }


def _monotonic(values):
    delta = np.diff(values)
    return bool(np.all(delta > 0) or np.all(delta < 0))


def _map_metadata(artist):
    norm = artist.norm
    cmap = artist.get_cmap()
    cb = getattr(artist, "colorbar", None)
    label = ""
    if cb is not None:
        label = cb.ax.get_ylabel() or cb.ax.get_xlabel()
    import re
    units = re.search(r"[\[(]([^\])]+)[\])]\s*$", label)
    return {"cmap": cmap.name,
            "colorscale": [[float(v), _hex(cmap(v))] for v in np.linspace(0, 1, 257)],
            "norm": {"type": type(norm).__name__, "vmin": _f(norm.vmin),
                     "vmax": _f(norm.vmax), "log": type(norm).__name__ == "LogNorm",
                     "vcenter": _f(getattr(norm, "vcenter", None)), "clip": bool(norm.clip)},
            "colorbar": {"label": label, "units": units.group(1) if units else None,
                         "id": str(id(cb)) if cb is not None else None,
                         "visible": cb is not None},
            "full_resolution": True,
            "zorder": _f(artist.get_zorder()),
            "opacity": artist.get_alpha() if artist.get_alpha() is not None else 1}


# matplotlib cmap -> Plotly colorscale (common subset)
_CMAP = {"viridis": "Viridis", "plasma": "Plasma", "inferno": "Inferno",
         "magma": "Magma", "cividis": "Cividis", "jet": "Jet", "hot": "Hot",
         "coolwarm": "RdBu", "RdBu": "RdBu", "RdBu_r": "RdBu", "Blues": "Blues",
         "gray": "Greys", "Greys": "Greys"}


def _is_colorbar_axes(ax, traces, skipped=()):
    """A decorative strip: produced no usable traces yet carries mesh/line-strip art.

    Decided *after* extraction so a real ``pcolormesh`` heatmap (which yields a
    heatmap trace) is never mistaken for the narrow power colorbar beside it.
    """
    if getattr(ax, "_colorbar", None) is not None:
        return True
    if traces or skipped:
        return False
    strips = [c for c in ax.collections if isinstance(c, (QuadMesh, LineCollection))]
    return bool(strips)


def _annotations(ax):
    out = []
    for t in ax.texts:
        s = t.get_text()
        if isinstance(t, Annotation) and (t.xycoords not in ("data", "axes fraction") or
                                         t.anncoords not in ("data", "axes fraction", "offset points")):
            continue
        if not isinstance(t, Annotation) and t.get_transform() not in (ax.transData, ax.transAxes):
            continue
        x, y = t.get_position()
        a = {
            "text": s,
            "x": _f(x), "y": _f(y),
            "xref_frac": (t.anncoords == "axes fraction" if isinstance(t, Annotation)
                          else t.get_transform() == ax.transAxes),
            "color": _hex(t.get_color()), "size": _f(t.get_fontsize()),
            "ha": t.get_ha(), "va": t.get_va(),
            "wrap": t.get_wrap(),
        }
        if isinstance(t, Annotation):
            a.update(arrow=t.arrow_patch is not None, xy=_flist(t.xy),
                     xycoords=t.xycoords if isinstance(t.xycoords, str) else "unsupported",
                     textcoords=t.anncoords if isinstance(t.anncoords, str) else "unsupported")
        out.append(a)
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
    skipped = []
    def skip(artist, reason):
        name = type(artist).__name__
        for entry in skipped:
            if entry["artist"] == name and entry["reason"] == reason:
                entry["n"] += 1
                return
        skipped.append({"artist": name, "n": 1, "reason": reason})
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
            else:
                skip(coll, "mesh coordinates cannot be represented")
        elif isinstance(coll, ContourSet):
            if coll.filled:
                skip(coll, "filled contour regions; levels retained")
            traces.extend(_contour_traces(coll))
        elif isinstance(coll, LineCollection):
            colors, widths = coll.get_colors(), coll.get_linewidths()
            for j, segment in enumerate(coll.get_segments()):
                if segment.size == 0:
                    continue
                traces.append({"type": "line", "x": _flist(segment[:, 0]),
                               "y": _flist(segment[:, 1]), "mode": "lines",
                               "color": _hex(colors[j % len(colors)]) if len(colors) else None,
                               "width": _f(widths[j % len(widths)]) if len(widths) else 1,
                               "zorder": _f(coll.get_zorder()),
                               "opacity": coll.get_alpha() if coll.get_alpha() is not None else 1})
        elif isinstance(coll, PolyCollection):
            for path in coll.get_paths():
                for vertices in _path_parts(path):
                    traces.append(_polygon_trace(vertices, coll))
        else:
            skip(coll, "unsupported collection")
    for image in ax.images:
        arr = np.ma.filled(image.get_array().astype(float), np.nan)
        if arr.ndim != 2:
            skip(image, "RGB images are not scalar maps")
            continue
        left, right, bottom, top = image.get_extent()
        ny, nx = arr.shape
        xe = np.linspace(left, right, nx + 1)
        ye = np.linspace(top, bottom, ny + 1) if image.origin == "upper" else np.linspace(bottom, top, ny + 1)
        traces.append({"type": "heatmap", "x": _flist((xe[1:] + xe[:-1]) / 2),
                       "y": _flist((ye[1:] + ye[:-1]) / 2),
                       "x_edges": _flist(xe), "y_edges": _flist(ye),
                       "z": [_flist(row) for row in arr], "origin": image.origin,
                       "extent": _flist(image.get_extent()), **_map_metadata(image)})
    for patch in ax.patches:
        transform = patch.get_transform()
        branches = transform.contains_branch_seperately(ax.transData)
        if any(branches):
            vertices = transform.transform(patch.get_path().vertices)
            vertices = ax.transData.inverted().transform(vertices)
            trace = _polygon_trace(vertices, patch)
            if not all(branches):
                frac = ax.transAxes.inverted().transform(transform.transform(patch.get_path().vertices))
                trace.update(coord="x-data-y-domain" if branches[0] else "x-domain-y-data")
                trace["y" if branches[0] else "x"] = _flist(frac[:, 1 if branches[0] else 0])
            traces.append(trace)
        else:
            skip(patch, "unsupported patch transform")
    secondary = []
    for child in ax.child_axes:
        if isinstance(child, SecondaryAxis):
            which = child._orientation
            limits = ax.get_xlim() if which == "x" else ax.get_ylim()
            values = np.linspace(*limits, 33)
            forward, inverse = child._functions
            mapped = np.asarray(forward(values))
            secondary.append({"axis": which, "side": child._loc,
                              "label": child.get_xlabel() if which == "x" else child.get_ylabel(),
                              "map": {"parent": _flist(values), "forward": _flist(mapped),
                                      "inverse": _flist(inverse(mapped))}})
        else:
            skip(child, "unsupported child axes")
    for artist in list(ax.artists) + list(ax.tables):
        skip(artist, "unsupported artist")
    for text in ax.texts:
        if isinstance(text, Annotation):
            if text.xycoords not in ("data", "axes fraction") or text.anncoords not in ("data", "axes fraction", "offset points"):
                skip(text, "unsupported annotation coordinates")
        elif text.get_transform() not in (ax.transData, ax.transAxes):
            skip(text, "unsupported text transform")
    for artist in list(ax.collections) + list(ax.images):
        if isinstance(artist, QuadMesh) or artist in ax.images:
            if type(artist.norm).__name__ not in ("Normalize", "LogNorm", "TwoSlopeNorm"):
                skip(artist, "unsupported norm " + type(artist.norm).__name__)
    for ln, trace in zip(ax.lines, [t for t in traces if t["type"] == "line"]):
        if ln.get_transform() == ax.get_xaxis_transform():
            trace.update(role="vline", coord="x-data-y-domain")
        elif ln.get_transform() == ax.get_yaxis_transform():
            trace.update(role="hline", coord="x-domain-y-data")
        else:
            trace["role"] = "markers" if trace.get("mode") == "markers" else "curve"
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
        "is_colorbar": _is_colorbar_axes(ax, traces, skipped),
        "show_legend": show_legend,
        "traces": traces,
        "secondary_axes": secondary,
        "skipped": skipped,
        "annotations": _annotations(ax),
        "grid": _grid_pos(ax),
    }


def _grid_pos(ax):
    try:
        ss = ax.get_subplotspec().get_topmost_subplotspec()
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
        "schema": "iplot/v2",
        "key": key,
        "title": title or sup or key,
        "suptitle": sup,
        "size_inches": _flist(f.get_size_inches()),
        "axes": [extract_axes(ax, axes) for ax in axes],
        "texts": [{"text": t.get_text(), "x": _f(t.get_position()[0]),
                   "y": _f(t.get_position()[1]), "color": _hex(t.get_color()),
                   "size": _f(t.get_fontsize()), "ha": t.get_ha(), "va": t.get_va(), "wrap": t.get_wrap()}
                  for t in f.texts if t.get_transform() == f.transFigure],
        "skipped": [{"artist": type(a).__name__, "n": 1, "reason": "unsupported figure artist"}
                    for a in list(f.artists) + list(f.images) + list(f.patches) + list(f.lines)] +
                   [{"artist": "FigureText", "n": 1, "reason": "unsupported text transform"}
                    for t in f.texts if t.get_transform() != f.transFigure],
    }


def _path_parts(path):
    vertices = path.vertices
    if path.codes is None:
        return [vertices]
    starts = np.flatnonzero(path.codes == Path.MOVETO)
    return [vertices[start:end] for start, end in zip(starts, list(starts[1:]) + [len(vertices)])]


def _polygon_trace(vertices, artist):
    color = artist.get_facecolor()
    if np.asarray(color).ndim > 1:
        color = color[0] if len(color) else "none"
    return {"type": "polygon", "x": _flist(vertices[:, 0]), "y": _flist(vertices[:, 1]),
            "color": _hex(color), "opacity": artist.get_alpha() if artist.get_alpha() is not None else _opacity(color),
            "zorder": _f(artist.get_zorder())}


def _contour_traces(coll):
    out = []
    colors = coll.get_edgecolors()
    widths = coll.get_linewidths()
    for j, path in enumerate(coll.get_paths()):
        for vertices in _path_parts(path):
            out.append({"type": "contour", "level": _f(coll.levels[j]),
                        "levels": _flist(coll.levels), "x": _flist(vertices[:, 0]),
                        "y": _flist(vertices[:, 1]), "mode": "lines",
                        "color": _hex(colors[j % len(colors)]) if len(colors) else None,
                        "width": _f(widths[j % len(widths)]) if len(widths) else 1,
                        "zorder": _f(coll.get_zorder()),
                        "opacity": coll.get_alpha() if coll.get_alpha() is not None else 1})
    return out
