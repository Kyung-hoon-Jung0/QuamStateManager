"""Strategy-B interactive plots: re-run the experiment's own ``plotting.py``.

Two halves:
  * the *driver* (:func:`replot_run`) spawns ``generator/run_interactive_replot.py``
    in a user-selected QM env and caches the resulting ``iplot/v1`` JSON;
  * the *converter* (:func:`mpljson_to_plotly`) turns one extracted figure into a
    Plotly ``{data, layout}`` — pure dict work, no numpy/matplotlib in-process.

The State-Manager process never imports the QM stack: the subprocess produces the
structured JSON, this module only reshapes it. See ``iplot_extract.py`` for the
extraction contract and ``docs/`` for the design rationale.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Driver: spawn run_interactive_replot.py in the selected QM env, cache result
# ---------------------------------------------------------------------------

# folder_path(str) -> {"fingerprint": str, "result": dict}. Bounded; newest wins.
# Guarded by _LOCK. _INFLIGHT coalesces concurrent same-run requests onto ONE
# subprocess (the menu call + N lazy tile calls all land here near-simultaneously).
_CACHE: dict[str, dict] = {}
_CACHE_MAX = 12
_LOCK = threading.Lock()
_INFLIGHT: dict[str, threading.Event] = {}


def _script_path() -> Path:
    """Locate ``generator/run_interactive_replot.py`` (dev checkout or frozen)."""
    from quam_state_manager.core.config_generator import _script_path as sp
    return sp("run_interactive_replot.py")


def _derive_util(node_name: str) -> str:
    """Node name -> ``calibration_utils`` submodule (mirror of the subprocess copy).

    Strips an optional graph prefix (``1Q_``/``2Q_``) then the numeric node prefix.
    """
    s = node_name or ""
    s = re.sub(r"^[0-9]+[A-Za-z]?Q_", "", s)
    s = re.sub(r"^[0-9]+[a-z]?_", "", s)
    return s.strip()


def _fingerprint(folder: Path) -> str:
    """Cheap content stamp: mtime+size of the inputs a re-run depends on.

    Note: this tracks the *run's data*, not the analysis code (which lives in the
    selected env's install and can't be stat-ed cheaply here) — code edits are
    picked up via an explicit Regenerate, mirroring the Config Viewer.
    """
    parts = []
    for name in ("node.json", "ds_raw.h5", "ds_fit.h5"):
        p = folder / name
        try:
            st = p.stat()
            parts.append(f"{name}:{int(st.st_mtime)}:{st.st_size}")
        except OSError:
            parts.append(f"{name}:-")
    return "|".join(parts)


def replot_capability(run, instance_path) -> dict:
    """Cheap, no-subprocess check: can this run be reproduced from experiment code?

    Returns ``{"available": bool, "reason": str, "util": str, "env": str|None}``.
    """
    from quam_state_manager.core import config_generator
    node_name = getattr(run, "experiment_name", "") or ""
    util = _derive_util(node_name)
    env = config_generator.get_selected_env(instance_path)
    folder = Path(getattr(run, "folder_path", "") or "")
    if not env:
        return {"available": False, "reason": "No QM environment selected "
                "(set one in Generate Config).", "util": util, "env": None}
    if not (folder / "quam_state").is_dir():
        return {"available": False, "reason": "Run has no quam_state/ snapshot to "
                "rebuild qubits from.", "util": util, "env": env}
    if not util:
        return {"available": False, "reason": "Could not derive a calibration_utils "
                "module from the node name.", "util": util, "env": env}
    return {"available": True, "reason": "", "util": util, "env": env}


def replot_run(run, instance_path, *, source_root: str | None = None,
               force: bool = False, timeout: int = 180) -> dict:
    """Re-run the experiment's plotting in the selected env; return ``iplot/v1`` JSON.

    Cached per run folder + data fingerprint. ``force`` re-runs (used by the
    Regenerate button, e.g. after the user edits the analysis code).
    """
    from quam_state_manager.core import config_generator
    folder = Path(getattr(run, "folder_path", "") or "")
    key = str(folder)
    fp = _fingerprint(folder)

    # Fast path + in-flight coalescing: collapse the menu call and every lazy
    # tile call for the same run onto ONE subprocess instead of N.
    while True:
        with _LOCK:
            hit = _CACHE.get(key)
            if not force and hit and hit["fingerprint"] == fp:
                return hit["result"]
            ev = _INFLIGHT.get(key)
            if ev is None:
                ev = _INFLIGHT[key] = threading.Event()
                owner = True
                break
            owner = False
            force = False  # a waiter never forces a second run; it takes the result
        # someone else is producing this run; wait then re-check the cache
        ev.wait(timeout=300)
        with _LOCK:
            hit = _CACHE.get(key)
            if hit and hit["fingerprint"] == fp:
                return hit["result"]
            # producer finished without a usable cache entry (error/timeout) — retry as owner
            if _INFLIGHT.get(key) is None:
                continue
        ev.wait(timeout=5)

    env = config_generator.get_selected_env(instance_path)
    if not env:
        with _LOCK:
            _INFLIGHT.pop(key, None)
        ev.set()
        return {"schema": "iplot/v1", "figures": [],
                "errors": [{"stage": "env", "trace": "No environment selected"}]}

    out_fd, out_path = tempfile.mkstemp(suffix="_iplot.json", prefix="qsm_")
    os.close(out_fd)
    try:
        args = [env, str(_script_path()), "--run", str(folder), "--out", out_path]
        if source_root:
            args += ["--source-root", source_root]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        try:
            with open(out_path, "r", encoding="utf-8") as fh:
                result = json.load(fh)
        except (OSError, ValueError):
            result = {"schema": "iplot/v1", "figures": [], "errors": [
                {"stage": "subprocess",
                 "trace": (proc.stderr or proc.stdout or "no output")[-2000:]}]}
    except subprocess.TimeoutExpired:
        result = {"schema": "iplot/v1", "figures": [],
                  "errors": [{"stage": "timeout", "trace": f"exceeded {timeout}s"}]}
    except OSError as e:  # bad interpreter path, spawn failure — must still release in-flight
        result = {"schema": "iplot/v1", "figures": [],
                  "errors": [{"stage": "spawn", "trace": str(e)}]}
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass

    with _LOCK:
        # Cache only usable results: a transient timeout/error stays retryable
        # (it would otherwise pin a "failed" envelope until the next Regenerate).
        if result.get("figures"):
            _CACHE[key] = {"fingerprint": fp, "result": result}
            while len(_CACHE) > _CACHE_MAX:
                _CACHE.pop(next(iter(_CACHE)), None)
        _INFLIGHT.pop(key, None)
    ev.set()
    return result


def replot_menu(result: dict) -> list[dict]:
    """``iplot/v1`` -> tile menu ``[{key,title,available}]`` for the template."""
    figs = result.get("figures", [])
    return [{"key": f["key"], "title": f.get("title", f["key"]),
             "available": True} for f in figs]


def replot_figure(result: dict, key: str) -> dict | None:
    """Convert one cached figure to Plotly ``{data, layout, title}``; ``None`` if absent."""
    for f in result.get("figures", []):
        if f["key"] == key:
            return mpljson_to_plotly(f)
    return None


# ---------------------------------------------------------------------------
# Converter: iplot/v1 figure JSON -> Plotly {data, layout}
# ---------------------------------------------------------------------------

_LEGEND_COLORS_FALLBACK = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
                           "#9467bd", "#8c564b", "#e377c2"]


def _axis_id(kind: str, n: int) -> str:
    """Plotly axis name: ``xaxis`` / ``xaxis2`` ... and trace ref ``x`` / ``x2``."""
    return kind if n == 1 else f"{kind}{n}"


def _ref(kind: str, n: int) -> str:
    return kind if n == 1 else f"{kind}{n}"


# Transfer caps so a pathological multi-qubit / large-sweep run can't ship tens
# of MB per figure to the browser. Striding is enough for an overview plot.
_MAX_LINE_PTS = 6000
_MAX_HEATMAP_DIM = 400


def _decimate(x, y, max_pts=_MAX_LINE_PTS):
    n = len(x or [])
    if n <= max_pts:
        return x, y
    step = n // max_pts + 1
    return x[::step], y[::step]


def _line_to_plotly(t, xref, yref):
    x, y = _decimate(t["x"], t["y"])
    d = {
        "type": "scattergl" if len(x) > 400 else "scatter",
        "x": x, "y": y, "mode": t.get("mode", "lines"),
        "xaxis": xref, "yaxis": yref,
        "line": {"color": t.get("color"), "dash": t.get("dash", "solid"),
                 "width": t.get("width") or 1.5},
        "opacity": t.get("opacity", 1.0),
    }
    if t.get("mode", "lines") != "lines":
        d["marker"] = {"color": t.get("color"), "size": t.get("marker_size") or 6,
                       "symbol": t.get("marker_symbol", "circle")}
    if t.get("name"):
        d["name"] = t["name"]
        d["showlegend"] = True
    else:
        d["showlegend"] = False
    return d


def _scatter_to_plotly(t, xref, yref):
    x, y = _decimate(t["x"], t["y"])
    mc = t.get("marker_color")
    # a per-point colour list must match the (decimated) point count or Plotly
    # silently mis-colours; fall back to a single colour on any mismatch.
    if isinstance(mc, list) and len(mc) != len(x):
        mc = mc[0] if mc else None
    d = {
        "type": "scattergl" if len(x) > 400 else "scatter",
        "x": x, "y": y, "mode": "markers",
        "xaxis": xref, "yaxis": yref,
        "marker": {"size": t.get("marker_size") or 6,
                   "color": mc, "opacity": t.get("opacity", 1.0)},
        "showlegend": bool(t.get("name")),
    }
    if t.get("name"):
        d["name"] = t["name"]
    return d


def _heatmap_to_plotly(t, xref, yref):
    x, y, z = t["x"], t["y"], t["z"]
    xs = max(1, len(x) // _MAX_HEATMAP_DIM + (1 if len(x) > _MAX_HEATMAP_DIM else 0))
    ys = max(1, len(y) // _MAX_HEATMAP_DIM + (1 if len(y) > _MAX_HEATMAP_DIM else 0))
    if xs > 1 or ys > 1:
        x = x[::xs]
        y = y[::ys]
        z = [row[::xs] for row in z[::ys]]
    return {
        "type": "heatmap", "x": x, "y": y, "z": z,
        "xaxis": xref, "yaxis": yref,
        "colorscale": t.get("colorscale", "Viridis"),
        "showscale": True,
        "colorbar": {"len": 0.9, "thickness": 12},
    }


def _trace_to_plotly(t, xref, yref):
    if t["type"] == "line":
        return _line_to_plotly(t, xref, yref)
    if t["type"] == "scatter":
        return _scatter_to_plotly(t, xref, yref)
    if t["type"] == "heatmap":
        return _heatmap_to_plotly(t, xref, yref)
    return None


def _axis_props(ax, *, log_key):
    p = {"title": {"text": ax.get("xlabel") if log_key == "x" else ax.get("ylabel")}}
    scale = ax.get("xscale") if log_key == "x" else ax.get("yscale")
    if scale == "log":
        p["type"] = "log"
    return p


def _drawable(axes):
    """Indices worth rendering: not a colorbar strip, and carrying ≥1 trace."""
    return [i for i, a in enumerate(axes)
            if not a.get("is_colorbar") and a.get("traces")]


def _group_panels(axes, drawable):
    """Union drawable axes that share an axis (twinx/twiny) into one panel each.

    Returns a list of panels, each a list of axis indices that occupy the same
    physical subplot cell (a base axis + its secondary right-Y / top-X twins).
    """
    parent = {i: i for i in drawable}

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    dset = set(drawable)
    for i in drawable:
        for rel in ("shares_x_with", "shares_y_with"):
            j = axes[i].get(rel)
            if j is not None and j in dset:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for i in drawable:
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def _pick_base(axes, panel):
    """The base axis of a panel: the one labelled bottom-x + left-y (the original)."""
    for i in panel:
        a = axes[i]
        if a.get("x_side", "bottom") == "bottom" and a.get("y_side", "left") == "left":
            return i
    for i in panel:
        if axes[i].get("y_side", "left") == "left":
            return i
    return panel[0]


def _grid_layout(axes, panels):
    """(nrows, ncols, [(row,col) per panel]) honouring QubitGrid; else 1 column."""
    cells = []
    nrows = ncols = 1
    for p in panels:
        g = axes[_pick_base(axes, p)].get("grid") or {}
        cells.append((g.get("row", 0), g.get("col", 0)))
        nrows = max(nrows, g.get("nrows", 1))
        ncols = max(ncols, g.get("ncols", 1))
    # Fall back to a vertical stack when the grid can't seat every panel distinctly
    # (independent axes that aren't a real QubitGrid all report row0/col0).
    if len(set(cells)) != len(panels) or nrows * ncols < len(panels):
        n = len(panels)
        return n, 1, [(k, 0) for k in range(n)]
    return nrows, ncols, cells


def mpljson_to_plotly(fig_json: dict) -> dict:
    """One extracted figure -> Plotly ``{data, layout, title}``.

    Layout: colorbar strips and trace-less axes are dropped; axes sharing an axis
    (twinx → right Y, twiny → top X) fold into one panel; panels are seated in the
    experiment's QubitGrid R×C (or stacked when there's no real grid).
    """
    axes = fig_json.get("axes", [])
    drawable = _drawable(axes)
    if not drawable:
        return {"data": [], "title": fig_json.get("title", ""),
                "layout": {"annotations": [
                    {"text": "(no interactive content)", "showarrow": False,
                     "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5}]}}

    panels = _group_panels(axes, drawable)
    nrows, ncols, cells = _grid_layout(axes, panels)
    xpad = 0.06 / ncols
    ypad = 0.10 / nrows

    data, layout, notes = [], {}, []
    for p_idx, panel in enumerate(panels):
        pid = p_idx + 1
        row, col = cells[p_idx]
        x0 = col / ncols + xpad
        x1 = (col + 1) / ncols - xpad
        y1 = 1.0 - row / nrows - ypad
        y0 = 1.0 - (row + 1) / nrows + ypad
        base_i = _pick_base(axes, panel)
        base = axes[base_i]
        xref, yref = _ref("x", pid), _ref("y", pid)

        for t in base["traces"]:
            tr = _trace_to_plotly(t, xref, yref)
            if tr:
                data.append(tr)
        layout[_axis_id("xaxis", pid)] = {**_axis_props(base, log_key="x"),
                                          "anchor": yref, "domain": [max(x0, 0), min(x1, 1)]}
        layout[_axis_id("yaxis", pid)] = {**_axis_props(base, log_key="y"),
                                          "anchor": xref, "domain": [max(y0, 0), min(y1, 1)]}

        for i in panel:
            if i == base_i:
                continue
            sec = axes[i]
            if sec.get("shares_y_with") is not None:   # twiny → secondary top X
                xref2 = _ref("x", 100 + pid)
                layout[_axis_id("xaxis", 100 + pid)] = {
                    **_axis_props(sec, log_key="x"), "anchor": yref,
                    "overlaying": xref, "side": sec.get("x_side", "top"),
                    "domain": [max(x0, 0), min(x1, 1)]}
                for t in sec["traces"]:
                    tr = _trace_to_plotly(t, xref2, yref)
                    if tr:
                        data.append(tr)
            else:                                       # twinx → secondary right Y
                yref2 = _ref("y", 100 + pid)
                layout[_axis_id("yaxis", 100 + pid)] = {
                    **_axis_props(sec, log_key="y"), "anchor": xref,
                    "overlaying": yref, "side": sec.get("y_side", "right"),
                    "domain": [max(y0, 0), min(y1, 1)]}
                for t in sec["traces"]:
                    tr = _trace_to_plotly(t, xref, yref2)
                    if tr:
                        data.append(tr)

        if ncols > 1 or nrows > 1:  # per-cell title for a real qubit grid
            ttl = base.get("title") or ""
            if ttl:
                notes.append({"text": ttl, "showarrow": False, "xref": "paper",
                              "yref": "paper", "x": (x0 + x1) / 2, "y": min(y1 + ypad * 0.5, 1),
                              "xanchor": "center", "yanchor": "bottom",
                              "font": {"size": 11}})
        for a in base.get("annotations", []):
            if a.get("xref_frac") and a.get("text"):
                notes.append({"text": a["text"], "showarrow": False, "xref": "paper",
                              "yref": "paper", "x": max(x0, 0) + 0.005, "y": min(y1, 1) - 0.005,
                              "xanchor": "left", "yanchor": "top",
                              "font": {"size": 10}, "align": "left"})

    # One colorbar per figure: overlaid twiny heatmaps would otherwise stack two
    # colorbars (the source plots set add_colorbar=False — there's nothing to dupe).
    seen_cb = False
    for tr in data:
        if tr.get("type") == "heatmap":
            tr["showscale"] = not seen_cb
            seen_cb = True
    has_top_axis = any(k.startswith("xaxis1") for k in layout)  # a twiny secondary top-X

    layout["title"] = {"text": fig_json.get("suptitle") or fig_json.get("title", ""),
                       "y": 0.99, "yanchor": "top"}
    layout["showlegend"] = True
    layout["legend"] = {"orientation": "h", "y": -0.12 / nrows, "x": 0.0}
    layout["margin"] = {"l": 60, "r": 60, "t": 70 if has_top_axis else 44, "b": 50}
    layout["height"] = max(320, 320 * nrows) + (26 if has_top_axis else 0)
    if notes:
        layout["annotations"] = notes
    return {"data": data, "layout": layout, "title": fig_json.get("title", "")}
