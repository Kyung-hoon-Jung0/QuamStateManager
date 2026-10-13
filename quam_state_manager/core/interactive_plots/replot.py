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
from collections import OrderedDict
import math
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
_CACHE: dict[str, dict] = OrderedDict()
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
    """Run inputs, including saved results and state, with nanosecond stats."""
    parts = []
    for name in ("node.json", "state_updates.json", "data.json", "ds_raw.h5", "ds_fit.h5", "quam_state/state.json", "quam_state/wiring.json"):
        p = folder / name
        try:
            st = p.stat()
            # st_mtime_ns, not int(st_mtime): second-truncation made a
            # same-second same-size rewrite invisible → stale cached replot.
            parts.append(f"{name}:{st.st_mtime_ns}:{st.st_size}")
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


_PLOT_WORKER = None


def _warm_request(env, folder, source_root, timeout):
    global _PLOT_WORKER
    from quam_state_manager.core import lab_worker
    if not Path(env).is_file():
        return None
    from quam_state_manager.generator.iplot_sources import snapshot
    context = str(source_root or "")
    w = _PLOT_WORKER
    # fresh(): alive, same env, every imported file unedited since import; the
    # snapshot adds files added to or removed from the imported packages
    if w is not None and (w.context != context or w.python_path != env or not w.fresh()
                          or (getattr(w, "manifest", None) and snapshot(*w.manifest) != w.snapshot)):
        w.kill()
        w = None
    try:
        if w is None:
            w = lab_worker.Worker(env, str(_script_path()), "@@SM-IPLOT@@", context=context)
            _PLOT_WORKER = w
        lab_worker.retire_except(w)
        result = w.ask({"run": str(folder), "source_root": context}, timeout)
        if result is not None:
            w.manifest = ([m["path"] for m in result.get("module_files", [])], result.get("module_trees", []))
            w.snapshot = snapshot(*w.manifest)
            return result
        w.kill()
        _PLOT_WORKER = None
    except (OSError, ValueError):
        if w is not None:
            w.kill()
        _PLOT_WORKER = None
    return None


def _invoke(env, folder, source_root, timeout):
    from quam_state_manager.core import lab_worker
    total = 0
    for name in ("ds_raw.h5", "ds_fit.h5"):
        try:
            total += (folder / name).stat().st_size
        except OSError:
            pass
    if total > 300 * 1024**2:
        return {"schema": "iplot/v2", "figures": [], "errors": [{"stage": "datasets",
                "trace": "Run data exceeds 300 MiB; use saved figures or the raw data viewer"}]}
    with lab_worker.machine_lock:
        result = _warm_request(env, folder, source_root, timeout)
        if result is not None:
            return result
        lab_worker.retire_except()
        out_fd, out_path = tempfile.mkstemp(suffix="_iplot.json", prefix="qsm_")
        os.close(out_fd)
        try:
            args = [env, "-B", str(_script_path()), "--run", str(folder), "--out", out_path]
            if source_root:
                args += ["--source-root", source_root]
            proc = lab_worker.run(args, capture_output=True, text=True, timeout=min(timeout, 180),
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
            try:
                return json.loads(Path(out_path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return {"schema": "iplot/v2", "figures": [], "errors": [{"stage": "subprocess",
                        "trace": (proc.stderr or proc.stdout or "no output")[-2000:]}]}
        except subprocess.TimeoutExpired:
            return {"schema": "iplot/v2", "figures": [],
                    "errors": [{"stage": "timeout", "trace": f"exceeded {timeout}s"}]}
        except OSError as exc:
            return {"schema": "iplot/v2", "figures": [],
                    "errors": [{"stage": "spawn", "trace": str(exc)}]}
        finally:
            Path(out_path).unlink(missing_ok=True)


def _remember(key, meta, result, size):
    from . import replot_cache as cache
    if size > cache.RAM_BYTES:
        return
    _CACHE.pop(key, None)
    _CACHE[key] = {"meta": meta, "result": result, "size": size}
    while len(_CACHE) > _CACHE_MAX or sum(v["size"] for v in _CACHE.values()) > cache.RAM_BYTES:
        _CACHE.pop(next(iter(_CACHE)))


def replot_run(run, instance_path, *, source_root: str | None = None,
               force: bool = False, timeout: int = 180) -> dict:
    """Warm replay with a content-validated RAM LRU and instance disk cache."""
    from quam_state_manager.core import config_generator
    from . import replot_cache as cache
    folder = Path(getattr(run, "folder_path", "") or "").resolve()
    key = str(folder)
    env = config_generator.get_selected_env(instance_path)
    if not env:
        return {"schema": "iplot/v2", "figures": [],
                "errors": [{"stage": "env", "trace": "No environment selected"}]}
    context = {"folder": key, "inputs": _fingerprint(folder), "env": env,
               "env_sig": cache.digest(config_generator._env_signature(env)),
               "pythonpath": os.environ.get("PYTHONPATH", ""),
               "source_root": str(Path(source_root).resolve()) if source_root else "live",
               "instance": str(Path(instance_path).resolve()), "schema": cache.SCHEMA}
    disk = cache.DiskCache(instance_path)
    request_key = cache.digest(context)
    while True:
        with _LOCK:
            hit = _CACHE.get(key)
            if not force and hit and cache.valid(hit["meta"], context):
                _CACHE.move_to_end(key)
                return _with_click_contracts(hit["result"], run)
            if not force:
                found = disk.load(context)
                if found:
                    meta, result, size = found
                    _remember(key, meta, result, size)
                    return _with_click_contracts(result, run)
            ev = _INFLIGHT.get(request_key)
            if ev is None:
                ev = _INFLIGHT[request_key] = threading.Event()
                break
            force = False
        ev.wait(timeout=timeout + 10)
    try:
        from . import replot_revision
        # DESIGN 2.5 ladder: current code -> run-time revision (git trees only)
        # -> the saved figure; the chosen rung is labelled in the payload
        result = replot_revision.reproduce(
            folder, instance_path, source_root,
            lambda root: _invoke(env, folder, root, timeout))
        with _LOCK:
            if result.get("figures") or result.get("not_produced") or result.get("saved_figures"):
                try:
                    stored = disk.store(context, result)
                    if stored:
                        meta, size = stored
                        _remember(key, meta, result, size)
                except (OSError, ValueError):
                    logger.debug("replay disk cache unavailable", exc_info=True)
        return _with_click_contracts(result, run)
    finally:
        with _LOCK:
            _INFLIGHT.pop(request_key, None)
        ev.set()


def _with_click_contracts(result, run):
    """Attach fresh evidence proof even when the extracted artists are cached."""
    from .learn_click import learn
    figures = []
    for original in result.get("figures", []):
        figure = dict(original)
        figure["clickable"] = learn(run, figure)
        figures.append(figure)
    return {**result, "figures": figures}


def replot_menu(result: dict) -> list[dict]:
    """``iplot/v1`` -> tile menu ``[{key,title,available}]`` for the template."""
    figs = result.get("figures", [])
    return [{"key": f["key"], "title": f.get("title", f["key"]),
             "available": True, "bind_report": f.get("bind_report", {})} for f in figs]


def replot_outcome(result: dict) -> dict:
    """Sort an ``iplot/v1`` result into what the user must be told (datasets-r2-22).

    ``state`` is one of:

    * ``ok`` -- at least one figure came back (any errors are partial failures);
    * ``failed`` -- no figures and a stage BEFORE the plot loop failed (env,
      spawn, subprocess, timeout, import, qubits, datasets): the lab code never
      ran, so nothing may read as "reproduced" or "the code ran";
    * ``all_plots_failed`` -- no figures, the module loaded, every ``plot_*`` raised;
    * ``no_plot_fns`` -- no figures and no errors: the module has no ``plot_*``.

    ``fatal`` is the first pre-plot error (``{stage, line}``, ``line`` = the
    last line of its trace), else ``None``. Figures are keyed first, so a result
    carrying both figures and a fatal stage (the runner cannot produce one)
    still reads ``ok``.
    """
    figs = result.get("figures") or []
    errs = [e for e in (result.get("errors") or []) if isinstance(e, dict)]

    def _line(e):
        lines = [ln for ln in str(e.get("trace") or "").strip().splitlines() if ln.strip()]
        return lines[-1].strip() if lines else ""

    fatal = next((e for e in errs
                  if not str(e.get("stage") or "").startswith("plot:")), None)
    if figs:
        state = "ok"
    elif fatal is not None:
        state = "failed"
    elif errs:
        state = "all_plots_failed"
    elif result.get("not_produced"):
        state = "no_figures"
    else:
        state = "no_plot_fns"
    return {"state": state, "n_errors": len(errs),
            "rung": (result.get("revision") or {}).get("rung"),
            "status_chip": result.get("status_chip"),
            "fatal": ({"stage": str(fatal.get("stage") or ""), "line": _line(fatal)}
                      if fatal is not None else None)}


def replot_figure(result: dict, key: str) -> dict | None:
    """Convert one cached figure to Plotly ``{data, layout, title}``; ``None`` if absent."""
    for f in result.get("figures", []):
        if f["key"] == key:
            converted = mpljson_to_plotly(f)
            converted["bind_report"] = f.get("bind_report", {})
            converted["revision"] = f.get("revision")
            converted["status_chip"] = f.get("status_chip")
            converted["clickable"] = f.get("clickable")
            converted["display_only_reason"] = f.get("display_only_reason", "")
            if converted["display_only_reason"]:
                converted.setdefault("notices", []).append(converted["display_only_reason"])
            return converted
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
_MAP_BYTE_BUDGET = 64 * 1024 * 1024


def _decimate(x, y, max_pts=_MAX_LINE_PTS):
    n = len(x or [])
    if n <= max_pts:
        return x, y
    step = n // max_pts + 1
    return x[::step], y[::step]


def _line_to_plotly(t, xref, yref):
    x, y = (t["x"], t["y"]) if "zorder" in t else _decimate(t["x"], t["y"])
    d = {
        "type": "scattergl" if len(x) > 400 and "zorder" not in t else "scatter",
        "x": x, "y": y, "mode": t.get("mode", "lines"),
        "xaxis": xref, "yaxis": yref,
        "line": {"color": t.get("color"), "dash": t.get("dash", "solid"),
                 "width": t.get("width", 1.5),
                 "shape": {"steps-pre": "vh", "steps-post": "hv", "steps-mid": "hvh"}.get(t.get("drawstyle"), "linear")},
        "opacity": t.get("opacity", 1.0),
    }
    if "zorder" in t:
        d["zorder"] = int(t.get("zorder") or 0)
    if t.get("visible") is False:
        d["visible"] = False
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
    x, y = t["x"], t["y"]
    mc = t.get("marker_color")
    # a per-point colour list must match the (decimated) point count or Plotly
    # silently mis-colours; fall back to a single colour on any mismatch.
    if isinstance(mc, list) and len(mc) != len(x):
        mc = mc[0] if mc else None
    d = {
        "type": "scattergl" if len(x) > 400 and "zorder" not in t else "scatter",
        "x": x, "y": y, "mode": "markers",
        "xaxis": xref, "yaxis": yref,
        "marker": {"size": t.get("marker_size") or 6,
                   "color": mc, "opacity": t.get("opacity", 1.0)},
        "showlegend": bool(t.get("name")),
    }
    if t.get("name"):
        d["name"] = t["name"]
    if "zorder" in t:
        d["zorder"] = int(t["zorder"] or 0)
    return d


def _heatmap_to_plotly(t, xref, yref):
    x, y, z = t["x"], t["y"], t["z"]
    full = t.get("full_resolution", False)
    size = len(json.dumps(t, separators=(",", ":"), allow_nan=False).encode("utf-8"))
    limited = full and size > _MAP_BYTE_BUDGET
    xs = 1 if full and not limited else max(1, (len(x) + _MAX_HEATMAP_DIM - 1) // _MAX_HEATMAP_DIM)
    ys = 1 if full and not limited else max(1, (len(y) + _MAX_HEATMAP_DIM - 1) // _MAX_HEATMAP_DIM)
    if xs > 1 or ys > 1:
        x = x[::xs]
        y = y[::ys]
        z = [row[::xs] for row in z[::ys]]
    result = {
        "type": "heatmap", "x": x, "y": y, "z": z,
        "xaxis": xref, "yaxis": yref,
        "colorscale": t.get("colorscale", "Viridis"),
        "showscale": True,
        "colorbar": {"len": 0.9, "thickness": 12},
        "meta": {"cmap": t.get("cmap"), "norm": t.get("norm"),
                 "colorbar": t.get("colorbar"), "full_shape": [len(t["y"]), len(t["x"])],
                 "preview_reduced": limited, "source_bytes": size,
                 "full_array_server_side": full},
    }
    if full and not limited:
        result["x"] = t.get("x_edges", x)
        result["y"] = t.get("y_edges", y)
    norm = t.get("norm") or {}
    lo, hi, center = norm.get("vmin"), norm.get("vmax"), norm.get("vcenter")
    result["opacity"] = t.get("opacity", 1)
    cb = t.get("colorbar")
    if cb is not None:
        result["showscale"] = cb.get("visible", False)
        result["colorbar"]["title"] = {"text": cb.get("label", "")}
    if lo is not None and hi is not None:
        result.update(zmin=lo, zmax=hi, zauto=False)
    if norm.get("log") or center is not None:
        def normal(v):
            if v is None:
                return None
            if norm.get("log"):
                return math.log10(v) if v > 0 else None
            if v <= center:
                return .5 * (v - lo) / (center - lo) if center != lo else .5
            return .5 + .5 * (v - center) / (hi - center) if hi != center else .5
        result["customdata"] = z
        result["z"] = [[normal(v) for v in row] for row in z]
        result["hovertemplate"] = "x=%{x}<br>y=%{y}<br>value=%{customdata}<extra></extra>"
        if norm.get("log"):
            result.update(zmin=math.log10(lo), zmax=math.log10(hi))
            vals = [lo * (hi / lo) ** (i / 4) for i in range(5)]
        else:
            result.update(zmin=0, zmax=1)
            vals = [lo, (lo + center) / 2, center, (center + hi) / 2, hi]
        result["colorbar"].update(tickvals=[normal(v) for v in vals],
                                 ticktext=[f"{v:.6g}" for v in vals])
    return result


def full_map(result: dict, key: str, axes_index: int, trace_index: int) -> dict | None:
    """Full map retained in the replay cache for subsequent server map tools."""
    for fig in result.get("figures", []):
        if fig["key"] == key:
            try:
                trace = fig["axes"][axes_index]["traces"][trace_index]
            except (IndexError, KeyError):
                return None
            return trace if trace.get("type") == "heatmap" else None
    return None


def _trace_to_plotly(t, xref, yref):
    if t["type"] in ("line", "contour", "polygon"):
        if t["type"] == "polygon":
            return {"type": "scatter", "x": t["x"], "y": t["y"],
                    "xaxis": xref, "yaxis": yref, "mode": "lines",
                    "fill": "toself", "fillcolor": t.get("color"),
                    "line": {"width": 0}, "opacity": t.get("opacity", 1),
                    "showlegend": False, "zorder": int(t.get("zorder") or 0)}
        if t["type"] == "contour":
            t = {**t, "name": f"level {t.get('level')}", "mode": "lines"}
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
    limits = ax.get(log_key + "lim")
    if limits and all(v is not None for v in limits):
        if scale != "log":
            p["range"] = limits
        elif all(v > 0 for v in limits):
            p["range"] = [math.log10(v) for v in limits]
    return p


def _drawable(axes):
    """Indices worth rendering: not a colorbar strip, and carrying ≥1 trace."""
    return [i for i, a in enumerate(axes)
            if not a.get("is_colorbar") and (a.get("traces") or a.get("annotations") or a.get("skipped"))]


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
    if not drawable and not fig_json.get("texts") and not fig_json.get("skipped"):
        return {"data": [], "title": fig_json.get("title", ""),
                "layout": {"annotations": [
                    {"text": "(no interactive content)", "showarrow": False,
                     "xref": "paper", "yref": "paper", "x": 0.5, "y": 0.5}]}}

    panels = _group_panels(axes, drawable)
    nrows, ncols, cells = _grid_layout(axes, panels)
    xpad = 0.06 / ncols
    has_secondary_top = any(
        a.get("shares_y_with") is not None or any(s.get("axis") == "x" and s.get("side", "top") == "top"
                                                  for s in a.get("secondary_axes", [])) for a in axes)
    ypad = (0.20 if has_secondary_top else 0.10) / nrows

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

        _add_artists(base, xref, yref, data, layout, notes)
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
                _add_artists(sec, xref2, yref, data, layout, notes)
            else:                                       # twinx → secondary right Y
                yref2 = _ref("y", 100 + pid)
                layout[_axis_id("yaxis", 100 + pid)] = {
                    **_axis_props(sec, log_key="y"), "anchor": xref,
                    "overlaying": yref, "side": sec.get("y_side", "right"),
                    "domain": [max(y0, 0), min(y1, 1)]}
                _add_artists(sec, xref, yref2, data, layout, notes)

        for j, sec in enumerate(base.get("secondary_axes", [])):
            kind = sec["axis"]
            ref = xref if kind == "x" else yref
            mapping = sec.get("map") or {}
            values = mapping.get("parent", [])
            labels = mapping.get("forward", [])
            step = max(1, (len(values) - 1) // 4)
            sid = 200 + pid + j * len(panels)
            layout[_axis_id(kind + "axis", sid)] = {
                **_axis_props(base, log_key=kind), "title": {"text": sec.get("label", "")},
                "overlaying": ref, "matches": ref,
                "anchor": yref if kind == "x" else xref, "side": sec.get("side", "top"),
                "tickmode": "array", "tickvals": values[::step],
                "ticktext": [f"{v:.6g}" if v is not None else "" for v in labels[::step]],
                "showgrid": False, "zeroline": False}
            if values:
                endpoints = [values[0], values[-1]]
                other = base.get("ylim" if kind == "x" else "xlim", [0, 1])[0]
                data.append({"type": "scatter", "mode": "markers", "opacity": 0,
                             "x": endpoints if kind == "x" else [other, other],
                             "y": [other, other] if kind == "x" else endpoints,
                             "xaxis": _ref("x", sid) if kind == "x" else xref,
                             "yaxis": yref if kind == "x" else _ref("y", sid),
                             "showlegend": False, "hoverinfo": "skip"})

        if ncols > 1 or nrows > 1:  # per-cell title for a real qubit grid
            ttl = base.get("title") or ""
            if ttl:
                notes.append({"text": ttl, "showarrow": False, "xref": "paper",
                              "yref": "paper", "x": (x0 + x1) / 2, "y": y1,
                              "yshift": 64 if has_secondary_top else 12,
                              "xanchor": "center", "yanchor": "bottom",
                              "font": {"size": 11}})

    # One colorbar per figure: overlaid twiny heatmaps would otherwise stack two
    # colorbars (the source plots set add_colorbar=False — there's nothing to dupe).
    seen_cb = False
    for tr in data:
        if tr.get("type") == "heatmap":
            cb = (tr.get("meta") or {}).get("colorbar")
            if cb is None:
                tr["showscale"] = not seen_cb
                seen_cb = True
            elif tr.get("showscale"):
                xdomain = layout[_axis_id("xaxis", int(tr["xaxis"][1:] or 1))]["domain"]
                ydomain = layout[_axis_id("yaxis", int(tr["yaxis"][1:] or 1))]["domain"]
                tr["colorbar"].update(x=xdomain[1], y=sum(ydomain) / 2,
                                      len=ydomain[1] - ydomain[0])
    has_top_axis = has_secondary_top

    for text in fig_json.get("texts", []):
        notes.append(_text_note(text, "paper", "paper"))
    skipped = fig_json.get("skipped", []) + [s for a in axes if not a.get("is_colorbar") for s in a.get("skipped", [])]
    notices = [f"{s['artist']} x{s['n']} ({s.get('reason', 'unsupported')})" for s in skipped]
    if notices:
        notices = ["Not shown: " + "; ".join(notices)]
    if any((t.get("meta") or {}).get("preview_reduced") for t in data):
        notices.append("Map preview reduced to the byte budget; full array retained server-side for map tools.")
    layout["title"] = {"text": "" if fig_json.get("texts") else (fig_json.get("suptitle") or fig_json.get("title", "")),
                       "y": 0.99, "yanchor": "top"}
    layout["showlegend"] = True
    layout["legend"] = {"orientation": "h", "y": -0.12 / nrows, "x": 0.0}
    layout["margin"] = {"l": 60, "r": 60, "t": 92 if has_top_axis else 44, "b": 50}
    layout["height"] = max(320, 320 * nrows) + (64 * nrows if has_top_axis else 0)
    if notes:
        layout["annotations"] = notes
    from .mathtext import plotly_text
    return plotly_text({"data": data, "layout": layout, "title": fig_json.get("title", ""),
                       "skipped": skipped, "notices": notices})


def _text_note(a, xref, yref):
    result = {"text": a["text"], "showarrow": False, "x": a.get("x", 0), "y": a.get("y", 0),
              "xref": xref, "yref": yref, "font": {"size": a.get("size", 10)},
              "xanchor": {"center": "center", "right": "right"}.get(a.get("ha"), "left"),
              "yanchor": {"center": "middle", "top": "top"}.get(a.get("va"), "bottom")}
    if a.get("color") and a["color"] != "#000000":
        result["font"]["color"] = a["color"]
    return result


def _add_artists(ax, xref, yref, data, layout, notes):
    for t in sorted(ax.get("traces", []), key=lambda t: t.get("zorder") or 0):
        coord = t.get("coord")
        if coord and t["type"] == "polygon":
            layout.setdefault("shapes", []).append({"type": "rect", "x0": min(t["x"]), "x1": max(t["x"]),
                "y0": min(t["y"]), "y1": max(t["y"]),
                "xref": xref + (" domain" if coord == "x-domain-y-data" else ""),
                "yref": yref + (" domain" if coord == "x-data-y-domain" else ""),
                "fillcolor": t.get("color"), "line": {"width": 0},
                "opacity": t.get("opacity", 1), "layer": "below"})
        elif coord and t["type"] == "line":
            layout.setdefault("shapes", []).append({"type": "line", "x0": t["x"][0], "x1": t["x"][-1],
                "y0": t["y"][0], "y1": t["y"][-1], "xref": xref + (" domain" if coord == "x-domain-y-data" else ""),
                "yref": yref + (" domain" if coord == "x-data-y-domain" else ""),
                "line": {"color": t.get("color"), "width": t.get("width", 1), "dash": t.get("dash", "solid")},
                "opacity": t.get("opacity", 1)})
        else:
            trace = _trace_to_plotly(t, xref, yref)
            if trace:
                data.append(trace)
    for a in ax.get("annotations", []):
        frac = a.get("xref_frac", False)
        note = _text_note(a, xref + (" domain" if frac else ""), yref + (" domain" if frac else ""))
        if not a.get("arrow") and a.get("textcoords") == "offset points":
            frac = a.get("xycoords") == "axes fraction"
            note.update(x=a["xy"][0], y=a["xy"][1],
                        xref=xref + (" domain" if frac else ""),
                        yref=yref + (" domain" if frac else ""),
                        xshift=a["x"] * 96 / 72, yshift=a["y"] * 96 / 72)
        if a.get("arrow"):
            xycoords = a.get("xycoords", "data")
            textcoords = a.get("textcoords", "data")
            note.update(showarrow=True, arrowhead=2, x=a["xy"][0], y=a["xy"][1],
                        xref=xref + (" domain" if xycoords == "axes fraction" else ""),
                        yref=yref + (" domain" if xycoords == "axes fraction" else ""))
            if xycoords == "axes fraction":
                note.update(x=_fraction_data(ax, a["xy"][0], "x"), y=_fraction_data(ax, a["xy"][1], "y"),
                            xref=xref, yref=yref)
            if textcoords == "offset points":
                note.update(ax=a["x"] * 96 / 72, ay=-a["y"] * 96 / 72)
            else:
                note.update(ax=_fraction_data(ax, a["x"], "x") if textcoords == "axes fraction" else a["x"],
                            ay=_fraction_data(ax, a["y"], "y") if textcoords == "axes fraction" else a["y"],
                            axref=xref, ayref=yref)
        for field, ref, kind in (("x", "xref", "x"), ("y", "yref", "y"),
                                 ("ax", "axref", "x"), ("ay", "ayref", "y")):
            if field in note and " domain" not in note.get(ref, " domain") and ax.get(kind + "scale") == "log":
                value = note[field]
                note[field] = math.log10(value) if value is not None and value > 0 else None
        notes.append(note)


def _fraction_data(ax, value, kind):
    lo, hi = ax.get(kind + "lim", [0, 1])
    if ax.get(kind + "scale") == "log" and lo > 0 and hi > 0:
        return lo * (hi / lo) ** value
    return lo + (hi - lo) * value
