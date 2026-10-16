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


# The SM code a cached replay was made with: the scripts the lab worker runs and the
# revision ladder that composes their result. The cache key carries their digest, so
# an upgrade that changes how figures are reproduced never serves an older result.
_REPLAY_CODE = ("run_interactive_replot.py", "iplot_extract.py", "iplot_markers.py", "iplot_sources.py")
_replay_code_digest_value: str | None = None


def _replay_code_digest() -> str:
    global _replay_code_digest_value
    if _replay_code_digest_value is None:
        import hashlib
        from quam_state_manager import __version__
        from quam_state_manager.core.config_generator import _script_path as sp
        h = hashlib.sha256()
        paths = [(name, lambda name=name: sp(name)) for name in _REPLAY_CODE]
        paths.append(("replot_revision.py", lambda: Path(__file__).with_name("replot_revision.py")))
        for name, locate in paths:
            try:
                h.update(name.encode() + b"\0" + Path(locate()).read_bytes())
            except OSError:
                h.update(name.encode() + b"\0" + __version__.encode())  # frozen build: the release
        _replay_code_digest_value = h.hexdigest()[:16]
    return _replay_code_digest_value


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
    names = ["node.json", "state_updates.json", "data.json", "ds_raw.h5", "ds_fit.h5", "quam_state/state.json", "quam_state/wiring.json"]
    names += sorted(p.name for p in folder.glob("*.h5") if p.name not in names)
    for name in names:
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
               "instance": str(Path(instance_path).resolve()), "schema": cache.SCHEMA,
               "code": _replay_code_digest()}
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


def _spans_row(fig: dict) -> bool:
    """A lab figure drawn wide -- several panel columns, or a canvas 10 in or wider --
    takes a whole row of the tile grid. Squeezed into one of two or three columns,
    its panel titles, shared axis labels and colorbar titles overlap."""
    panels = [a for a in fig.get("axes", []) if not a.get("is_colorbar")]
    columns = max(((a.get("grid") or {}).get("ncols") or 1 for a in panels), default=1)
    size = fig.get("size_inches") or ()
    wide = (len(size) == 2 and isinstance(size[0], (int, float)) and math.isfinite(size[0])
            and size[0] >= 10)
    return columns >= 2 or wide


def replot_menu(result: dict) -> list[dict]:
    """``iplot/v1`` -> tile menu ``[{key,title,available,span}]`` for the template."""
    figs = result.get("figures", [])
    return [{"key": f["key"], "title": f.get("title", f["key"]),
             "available": True, "bind_report": f.get("bind_report", {}),
             "span": _spans_row(f)} for f in figs]


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
    if t.get("name") and not any(v is not None for v in (x or [])):
        # v1.5.1 (D3 class P): a label-only line (``ax.plot([], [], " ", label="T2* = ...")``,
        # how a lab puts a fit value in a legend) has no point, and Plotly leaves a trace
        # without points out of the legend. One null point keeps the entry; a line with
        # neither line nor marker keeps an empty symbol, as matplotlib draws it.
        x, y = [None], [None]
        if t.get("mode") == "none":
            # see-through paint, not opacity 0: a legend still draws a faint symbol at 0
            t = {**t, "mode": "markers", "marker_face": "none", "marker_edge": "none"}
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
        symbol = t.get("marker_symbol", "circle")
        d["marker"] = {"color": t.get("color"), "size": t.get("marker_size") or 6, "symbol": symbol}
        if t.get("mode") == "markers" and d["opacity"] < 1:
            # Matplotlib applies alpha to each marker, so a cloud's dense core builds up to
            # the full colour; a TRACE opacity fades the whole layer at once and the core
            # never does (an IQ cloud read as one dim mass on the dark theme).
            d["marker"]["opacity"] = d.pop("opacity")
        _marker_paint(d["marker"], t)
        if symbol in _STROKE_SYMBOLS:
            # Plotly draws these symbols with the marker outline only.
            d["marker"]["line"] = {"color": _paint(t.get("marker_edge")) or t.get("color"),
                                   "width": max(1.5, t.get("width") or 1.5)}
    if t.get("name"):
        d["name"] = t["name"]
        d["showlegend"] = True
    else:
        d["showlegend"] = False
    return d


_HOLLOW = "rgba(0,0,0,0)"


def _with_alpha(color, alpha):
    """A "#rrggbb" colour with ``alpha`` folded in (an rgba() string); other values unchanged."""
    if isinstance(color, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", color) and isinstance(alpha, (int, float)):
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        return f"rgba({r},{g},{b},{max(0.0, min(1.0, float(alpha))):g})"
    return color


def _paint(color):
    """An extracted marker colour for Plotly: "none" is see-through, a list stays a list."""
    if isinstance(color, list):
        return [_HOLLOW if c == "none" else c for c in color]
    return _HOLLOW if color == "none" else color


def _marker_paint(marker, t):
    """Face and edge as the lab drew them (a hollow ring, a star with a black edge).
    Results extracted before these fields existed keep the trace colour."""
    if "marker_face" not in t and "marker_edge" not in t:
        return
    if t.get("marker_face") is not None:
        marker["color"] = _paint(t["marker_face"])
    if t.get("marker_edge") is not None:
        marker["line"] = {"color": _paint(t["marker_edge"]), "width": t.get("marker_edge_width") or 1.0}


_STROKE_SYMBOLS = {"line-ew", "line-ns", "x-thin", "cross-thin", "y-down", "y-up", "y-left", "y-right"}


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
    _marker_paint(d["marker"], t)
    if t.get("name"):
        d["name"] = t["name"]
    if "zorder" in t:
        d["zorder"] = int(t["zorder"] or 0)
    if "marker_values" in t:
        mapped = _heatmap_to_plotly({**t, "z": [t["marker_values"]], "y": [0]}, xref, yref)
        d["marker"].update(color=mapped["z"][0], colorscale=mapped["colorscale"],
                           showscale=mapped["showscale"], colorbar=mapped["colorbar"])
        for zkey, ckey in (("zmin", "cmin"), ("zmax", "cmax"), ("zauto", "cauto")):
            if zkey in mapped:
                d["marker"][ckey] = mapped[zkey]
        d["meta"] = mapped["meta"]
        if "customdata" in mapped:
            d.update(customdata=t["marker_values"],
                     hovertemplate="x=%{x}<br>y=%{y}<br>value=%{customdata}<extra></extra>")
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
        result["colorbar"]["title"] = {"text": cb.get("label", ""), "side": "right"}
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
            trace = {"type": "scatter", "x": t["x"], "y": t["y"],
                     "xaxis": xref, "yaxis": yref, "mode": "lines",
                     "fill": "toself", "fillcolor": t.get("color"),
                     "line": {"width": 0}, "opacity": t.get("opacity", 1),
                     "showlegend": False, "zorder": int(t.get("zorder") or 0)}
            if t.get("edge_color") and (t.get("edge_width") or 0) > 0:
                # v1.5.1 (D3 class P): the patch's own outline. An unfilled patch (a step
                # histogram, an outline box) is only its edge and was invisible; the edge
                # carries its own alpha, the fill keeps the fill's.
                trace["line"] = {"color": t["edge_color"], "width": t["edge_width"],
                                 "dash": t.get("edge_dash", "solid")}
                if t.get("fill") is False:
                    trace.update(fill="none", opacity=1.0)
                    trace.pop("fillcolor")
                else:
                    trace.update(fillcolor=_with_alpha(t.get("color"), t.get("opacity", 1)), opacity=1.0)
            return trace
        if t["type"] == "contour":
            # The level names the line on hover; a contour carries no legend label of its own.
            t = {**t, "name": f"level {t.get('level')}", "mode": "lines"}
            return {**_line_to_plotly(t, xref, yref), "showlegend": False}
        return _line_to_plotly(t, xref, yref)
    if t["type"] == "scatter":
        return _scatter_to_plotly(t, xref, yref)
    if t["type"] == "heatmap":
        return _heatmap_to_plotly(t, xref, yref)
    return None


def _axis_props(ax, *, log_key):
    p = {"title": {"text": ax.get("xlabel") if log_key == "x" else ax.get("ylabel")}}
    ticks = ax.get(log_key + "_ticks") or {}
    if "values" in ticks:
        p.update(tickmode="array", tickvals=ticks["values"], ticktext=ticks["labels"],
                 tickangle=ticks.get("angle", 0))
    if ticks.get("color"):
        p["tickfont"] = {"color": ticks["color"]}
    if ticks.get("label_color"):
        p["title"]["font"] = {"color": ticks["label_color"]}
    # the lab's tick and axis-title sizes (a fixed 10 pt drew labelsize-14 ticks at 70%)
    if isinstance(ticks.get("size"), (int, float)) and ticks["size"] > 0:
        p.setdefault("tickfont", {})["size"] = ticks["size"]
    if isinstance(ticks.get("label_size"), (int, float)) and ticks["label_size"] > 0:
        p["title"].setdefault("font", {})["size"] = ticks["label_size"]
    if "axison" in ax:
        spines = ax.get("spines_visible", {})
        sides = ("bottom", "top") if log_key == "x" else ("left", "right")
        p["visible"] = bool(ax["axison"] and (ax.get("ticks_visible", {}).get(log_key, True)
                                             or any(spines.get(side, False) for side in sides)
                                             or p["title"]["text"]))
    scale = ax.get("xscale") if log_key == "x" else ax.get("yscale")
    if scale == "log":
        p["type"] = "log"
    limits = ax.get(log_key + "lim")
    if limits and all(v is not None for v in limits):
        if scale != "log":
            p["range"] = limits
        elif all(v > 0 for v in limits):
            p["range"] = [math.log10(v) for v in limits]
            if "values" not in ticks and abs(math.log10(limits[1]) - math.log10(limits[0])) >= 1:
                # v1.5.1 (D3 class T): matplotlib labels a log axis that spans a decade
                # or more at the decades only (10^k); Plotly's default added "2" and "5"
                # between them, which crowded small panels (log histograms)
                p.update(dtick=1, exponentformat="power")
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
                a, b = axes[i].get("position"), axes[j].get("position")
                if a and b and any(abs(u - v) > 1e-7 for u, v in zip(a, b)):
                    continue
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


def _position_bounds(position):
    if not isinstance(position, (list, tuple)) or len(position) != 4:
        return None
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in position):
        return None
    x, y, width, height = position
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > 1.000001 or y + height > 1.000001:
        return None
    return x, y, x + width, y + height


def _position_domains(axes, panels):
    """Keep figure fractions, including space reserved for headers and labels."""
    bounds = [_position_bounds(axes[_pick_base(axes, p)].get("position")) for p in panels]
    if not bounds or any(b is None for b in bounds):
        return None
    def domain(b):
        return b[0], b[2], b[1], b[3]
    return domain, [domain(b) for b in bounds]


def _legend_edge(lo, hi, p_lo, p_hi, names, axis):
    """Anchor one direction of a lab legend box [lo, hi] against its panel [p_lo, p_hi]:
    outside the panel, the edge next to it; inside, the half the box lies in (centred ->
    its centre). Returns {axis: position, axis + "anchor": name}."""
    low, mid, high = names
    span = max(p_hi - p_lo, 1e-9)
    centre, p_centre = (lo + hi) / 2, (p_lo + p_hi) / 2
    if lo >= p_hi - 1e-6:                       # beyond the high side: the edge facing it
        return {axis: lo, axis + "anchor": low}
    if hi <= p_lo + 1e-6:                       # beyond the low side
        return {axis: hi, axis + "anchor": high}
    if abs(centre - p_centre) <= 0.1 * span:
        return {axis: centre, axis + "anchor": mid}
    return {axis: hi, axis + "anchor": high} if centre > p_centre else {axis: lo, axis + "anchor": low}


def _legend_place(legend: dict, bounds) -> dict:
    """Plotly placement of one lab legend: the box matplotlib drew it in (figure fractions),
    else its location code inside its panel ``bounds`` (x0, x1, y0, y1). A framed lab legend
    keeps a frame (the theme fills it, so it reads over the data as the lab's does)."""
    x0, x1, y0, y1 = bounds
    box = legend.get("box")
    # a legend the lab laid out in several columns reads across, not down
    orientation = "h" if (legend.get("ncol") or 1) > 1 else "v"
    # a legend the lab put beside or below the axes (bbox_to_anchor) may reach past the
    # figure edge; its box still says where it is (Plotly accepts positions outside 0..1)
    if isinstance(box, list) and len(box) == 4 \
            and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box) \
            and box[0] < box[2] and box[1] < box[3] and box[2] > -0.5 and box[0] < 1.5 \
            and box[3] > -0.5 and box[1] < 1.5:
        # v1.5.1 (D3 class T): hold the legend by the edge of its box that faces the panel
        # border it sits against. A Plotly legend is often larger than the lab's (a row is at
        # least ~16 px): hung from its top-left corner a lower legend grew down over the
        # tick labels and axis title; held by that edge it grows into the panel instead.
        out = {"orientation": orientation}
        out.update(_legend_edge(box[0], box[2], x0, x1, ("left", "center", "right"), "x"))
        out.update(_legend_edge(box[1], box[3], y0, y1, ("bottom", "middle", "top"), "y"))
    else:
        # matplotlib location codes: 1 upper right, 2 upper left, 3 lower left, 4 lower right,
        # 5 right, 6 center left, 7 center right, 8 lower center, 9 upper center, 10 center
        loc = legend.get("loc", 1)
        across = "left" if loc in (2, 3, 6) else "center" if loc in (8, 9, 10) else "right"
        down = "bottom" if loc in (3, 4, 8) else "middle" if loc in (5, 6, 7, 10) else "top"
        out = {"orientation": orientation,
               "x": {"left": x0, "center": (x0 + x1) / 2, "right": x1}[across],
               "y": {"bottom": y0, "middle": (y0 + y1) / 2, "top": y1}[down],
               "xanchor": across, "yanchor": down}
    if legend.get("frame"):
        out.update(borderwidth=1, bordercolor="rgba(128,128,128,0.6)")
    if (legend.get("markerscale") or 1) > 1:
        # the lab enlarged the legend symbols (markerscale): a cloud of 1 pt dots would
        # otherwise show 1 pt dots in its legend
        out["itemsizing"] = "constant"
    return out


def _legend_box_ok(box) -> bool:
    return isinstance(box, list) and len(box) == 4 \
        and all(isinstance(v, (int, float)) and math.isfinite(v) for v in box) \
        and box[0] < box[2] and box[1] < box[3]


def _split_legend_rows(data: list, layout: dict, grids: dict) -> None:
    """A lab legend laid out in ``ncol`` columns over several rows, as one Plotly legend per row.

    Plotly lays a horizontal legend out in one row as wide as the whole figure, so a lab legend
    of 2 columns x 3 rows became one long row that ran into the legend beside it (v1.5.1, D3
    class T). Each row of the lab's grid becomes its own horizontal legend at the lab's row
    pitch, held by the same edge as the whole legend; matplotlib fills a legend's columns
    first, so entry k sits in row k % rows."""
    used = {k for k in layout if re.fullmatch(r"legend\d*", k)}

    def free():
        k = 2
        while f"legend{k}" in used:
            k += 1
        used.add(f"legend{k}")
        return f"legend{k}"

    for name, lab in list(grids.items()):
        own, box = layout.get(name), (lab or {}).get("box")
        ncol = int((lab or {}).get("ncol") or 1)
        if ncol < 2 or not isinstance(own, dict) or not _legend_box_ok(box):
            continue
        entries = [t for t in data if t.get("showlegend") and t.get("name")
                   and t.get("legend", "legend") == name]
        rows = -(-len(entries) // ncol)
        if rows < 2:
            continue
        pitch = (box[3] - box[1]) / rows
        anchor, y = own.get("yanchor", "top"), own.get("y", box[3])
        names = [name] + [free() for _ in range(rows - 1)]
        for r, row_name in enumerate(names):
            step = {"bottom": rows - 1 - r, "middle": (rows - 1) / 2 - r}.get(anchor, -r)
            # every row is held by the same edge as the whole legend; Plotly sizes each entry
            # by its text (a fixed entry width ran long labels into the next entry, and wide
            # legends moved the colorbars), and a frame per row would overlap its neighbours,
            # so the rows are unframed
            row = {k: v for k, v in own.items() if k not in ("borderwidth", "bordercolor", "bgcolor")}
            layout[row_name] = {**row, "orientation": "h", "y": y + step * pitch,
                                "font": dict(own.get("font") or {})}
        for k, trace in enumerate(entries):
            if k % rows:
                trace["legend"] = names[k % rows]


def _thin_ticks(values, labels, width_pt, size_pt):
    """Every k-th of the drawn ticks, so the labels fit side by side in ``width_pt``: Plotly
    rotates labels that do not fit, which matplotlib never does, and a crowded top axis then
    climbed into the panel caption. Keeps the "0" tick when there is one."""
    if not values or not (width_pt > 0):
        return values, labels
    widest = max(len(str(t)) for t in labels) * size_pt * 0.6 + size_pt
    step = 1
    while step < len(values) and -(-len(values) // step) * widest > width_pt:
        step += 1
    if step == 1:
        return values, labels
    zero = next((i for i, t in enumerate(labels) if str(t).replace("\u2212", "-") in ("0", "-0", "0.0")), 0)
    keep = list(range(zero % step, len(values), step))
    return [values[i] for i in keep], [labels[i] for i in keep]


def _legend_once(data: list) -> None:
    """One legend entry per distinct artist kind: a lab figure that labels the same line
    in every panel draws one legend per panel, but Plotly has one legend for the figure,
    so the entry repeated once per panel. Same name and same look share a legend group,
    so the one entry still shows or hides that kind in every panel."""
    groups: dict = {}
    for trace in data:
        if not trace.get("showlegend") or not trace.get("name"):
            continue
        line, marker = trace.get("line") or {}, trace.get("marker") or {}
        # one entry per kind PER LEGEND: a panel that drew its own legend keeps its entries
        key = (trace.get("legend"), trace["name"], trace.get("mode"), trace.get("type"), str(line.get("color")),
               str(line.get("dash")), str(marker.get("color")), str(marker.get("symbol")))
        if key in groups:
            trace["showlegend"] = False
        trace["legendgroup"] = groups.setdefault(key, f"lg{len(groups)}")


def mpljson_to_plotly(fig_json: dict) -> dict:
    """One extracted figure -> Plotly ``{data, layout, title}``.

    Layout: colorbar strips are handled by their artists; text-only axes survive.
    Axes sharing an axis
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
    positions = _position_domains(axes, panels)
    # Reserve a title plus tick labels between columns at the nominal pane width.
    xpad = min(42 / 1080, .22 / ncols) if ncols > 1 else .06
    has_secondary_top = any(
        a.get("shares_y_with") is not None or any(s.get("axis") == "x" and s.get("side", "top") == "top"
                                                  for s in a.get("secondary_axes", [])) for a in axes)
    ypad = (0.20 if has_secondary_top else 0.10) / nrows
    if nrows == 1 and has_secondary_top:
        # The outer top margin already holds this row's secondary labels.
        ypad = .04

    data, layout, notes = [], {}, []
    spans = []   # per panel: the [start, end) of its traces in `data`
    for p_idx, panel in enumerate(panels):
        spans.append(len(data))
        pid = p_idx + 1
        row, col = cells[p_idx]
        x0 = col / ncols + xpad
        x1 = (col + 1) / ncols - xpad
        y1 = 1.0 - row / nrows - ypad
        y0 = 1.0 - (row + 1) / nrows + ypad
        if positions:
            x0, x1, y0, y1 = positions[1][p_idx]
        base_i = _pick_base(axes, panel)
        base = axes[base_i]
        xref, yref = _ref("x", pid), _ref("y", pid)

        panel_width = 1080 * (x1 - x0)
        _add_artists(base, xref, yref, data, layout, notes, panel_width)
        layout[_axis_id("xaxis", pid)] = {**_axis_props(base, log_key="x"),
                                          "anchor": yref, "domain": [max(x0, 0), min(x1, 1)]}
        layout[_axis_id("yaxis", pid)] = {**_axis_props(base, log_key="y"),
                                          "anchor": xref, "domain": [max(y0, 0), min(y1, 1)]}
        if base.get("aspect"):
            layout[_axis_id("xaxis", pid)]["constrain"] = "domain"
            layout[_axis_id("yaxis", pid)].update(scaleanchor=xref, scaleratio=base["aspect"], constrain="domain")
        if col > 0 and any(cells[k] == (row, 0) and axes[_pick_base(axes, p)].get("ylabel") == base.get("ylabel")
                           for k, p in enumerate(panels)):
            layout[_axis_id("yaxis", pid)]["title"] = {"text": ""}

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
                _add_artists(sec, xref2, yref, data, layout, notes, panel_width)
            else:                                       # twinx → secondary right Y
                yref2 = _ref("y", 100 + pid)
                layout[_axis_id("yaxis", 100 + pid)] = {
                    **_axis_props(sec, log_key="y"), "anchor": xref,
                    "overlaying": yref, "side": sec.get("y_side", "right"),
                    "domain": [max(y0, 0), min(y1, 1)]}
                _add_artists(sec, xref, yref2, data, layout, notes, panel_width)

        for j, sec in enumerate(base.get("secondary_axes", [])):
            kind = sec["axis"]
            ref = xref if kind == "x" else yref
            mapping = sec.get("map") or {}
            values = mapping.get("parent", [])
            labels = mapping.get("forward", [])
            step = max(1, (len(values) - 1) // 4)
            tick_indices = list(range(0, len(values), step))
            if kind == "x" and ncols > 1 and len(values) >= 5:
                tick_indices = [round((len(values) - 1) * fraction) for fraction in (.1, .5, .9)]
            sid = 200 + pid + j * len(panels)
            tickvals = [values[k] for k in tick_indices]
            ticktext = [f"{labels[k]:.6g}" if labels[k] is not None else "" for k in tick_indices]
            drawn = sec.get("ticks") or {}
            if len(drawn.get("parent") or []) >= 2 and len(drawn["parent"]) == len(drawn.get("labels") or []):
                # v1.5.1 (D3 class P): the ticks matplotlib drew (round values, the lab's
                # labels), not samples at the axis ends (bin-edge values such as -20.25)
                tickvals, ticktext = list(drawn["parent"]), [str(v) for v in drawn["labels"]]
                size = fig_json.get("size_inches")
                if kind == "x" and isinstance(size, list) and len(size) == 2 \
                        and isinstance(size[0], (int, float)) and size[0] > 0:
                    font_pt = (base.get("x_ticks") or {}).get("size") or 10
                    tickvals, ticktext = _thin_ticks(tickvals, ticktext, (x1 - x0) * size[0] * 72, font_pt)
            layout[_axis_id(kind + "axis", sid)] = {
                **_axis_props(base, log_key=kind), "title": {"text": sec.get("label", "")},
                "overlaying": ref, "matches": ref,
                "anchor": yref if kind == "x" else xref, "side": sec.get("side", "top"),
                "tickmode": "array", "tickvals": tickvals, "ticktext": ticktext,
                "showgrid": False, "zeroline": False}
            if drawn.get("parent") and kind == "x":
                layout[_axis_id(kind + "axis", sid)]["tickangle"] = 0   # matplotlib's upright labels
            if values:
                endpoints = [values[0], values[-1]]
                other = base.get("ylim" if kind == "x" else "xlim", [0, 1])[0]
                data.append({"type": "scatter", "mode": "markers", "opacity": 0,
                             "x": endpoints if kind == "x" else [other, other],
                             "y": [other, other] if kind == "x" else endpoints,
                             "xaxis": _ref("x", sid) if kind == "x" else xref,
                             "yaxis": yref if kind == "x" else _ref("y", sid),
                             "showlegend": False, "hoverinfo": "skip"})

        if base.get("title"):  # axes captions also belong to single-panel figures
            ttl = base.get("title") or ""
            if ttl:
                note = {"text": ttl, "showarrow": False, "xref": "paper",
                        "yref": "paper", "x": (x0 + x1) / 2, "y": y1,
                        "yshift": 64 if has_secondary_top else 6 if positions else 12,
                        "xanchor": "center", "yanchor": "bottom",
                        "font": {"size": 10 if positions else 11}}
                box = base.get("title_box")
                if positions and _position_bounds(box and [box[0], box[1], box[2] - box[0], box[3] - box[1]]):
                    # v1.5.1 (D3 class T): where the lab drew it. A fixed lift (64 px over a
                    # top axis) put a caption on the figure heading.
                    note.update(x=(box[0] + box[2]) / 2, y=box[1], yshift=0)
                if positions and base.get("title_size"):
                    size = base["title_size"]
                    # Plotly sets lines 1.3 em apart, matplotlib 1.2 em (as _text_note)
                    note["font"]["size"] = round(size * 1.2 / 1.3, 3) if "\n" in ttl else size
                if base.get("title_color"):
                    # v1.5.1 (D3 class P): a crimson warning title kept its colour only in the PNG
                    note["font"]["color"] = base["title_color"]
                notes.append(note)

    spans.append(len(data))
    for scale in fig_json.get("colorbars", []):
        norm = scale.get("norm") or {}
        data.append(_scatter_to_plotly({**scale, "type": "scatter", "x": [None, None],
                    "y": [None, None], "marker_values": [norm.get("vmin"), norm.get("vmax")],
                    "marker_size": 0, "opacity": 0}, "x", "y"))
    # One colorbar per figure: overlaid twiny heatmaps would otherwise stack two
    # colorbars (the source plots set add_colorbar=False — there's nothing to dupe).
    seen_cb = False
    for tr in data:
        if tr.get("type") == "heatmap" or (tr.get("marker") or {}).get("showscale"):
            owner = tr if tr.get("type") == "heatmap" else tr["marker"]
            cb = (tr.get("meta") or {}).get("colorbar")
            if cb is None:
                tr["showscale"] = not seen_cb
                seen_cb = True
            elif owner.get("showscale"):
                xdomain = layout[_axis_id("xaxis", int(tr["xaxis"][1:] or 1))]["domain"]
                ydomain = layout[_axis_id("yaxis", int(tr["yaxis"][1:] or 1))]["domain"]
                owner["colorbar"].update(x=xdomain[1], y=sum(ydomain) / 2,
                                      len=ydomain[1] - ydomain[0])
                bounds = _position_bounds(cb.get("position"))
                if positions and bounds:
                    cx0, cx1, cy0, cy1 = positions[0](bounds)
                    horizontal = cb.get("orientation") == "horizontal"
                    owner["colorbar"].update(orientation="h" if horizontal else "v",
                        x=(cx0 + cx1) / 2 if horizontal else cx0,
                        y=cy0 if horizontal else (cy0 + cy1) / 2,
                        xanchor="center" if horizontal else "left", yanchor="bottom" if horizontal else "middle",
                        xpad=0, ypad=0, len=cx1 - cx0 if horizontal else cy1 - cy0,
                        thicknessmode="fraction", thickness=cy1 - cy0 if horizontal else cx1 - cx0)
    has_top_axis = has_secondary_top

    heading = fig_json.get("suptitle") or ""
    heading_in_texts = heading_as_note = False
    heading_size = None
    for text in fig_json.get("texts", []):
        if heading and text.get("text") == heading and text.get("y", 0) >= .9:
            heading_in_texts = True
            fitted = heading
            if positions:
                fitted, heading_size = _fit_heading(heading, text.get("size") or 12, fig_json.get("size_inches"))
            # A Plotly title centres extra lines on its y, so a multi-line lab heading ran
            # off the top; as a note its whole block hangs from y like the lab's.
            heading_as_note = bool(positions) and "\n" in fitted
            if not heading_as_note:
                continue
            text = {**text, "text": fitted, "size": heading_size}
        notes.append(_text_note(text, "paper", "paper"))
    skipped = fig_json.get("skipped", []) + [s for a in axes if not a.get("is_colorbar") for s in a.get("skipped", [])]
    notices = [f"{s['artist']} x{s['n']} ({s.get('reason', 'unsupported')})" for s in skipped]
    if notices:
        notices = ["Not shown: " + "; ".join(notices)]
    if any((t.get("meta") or {}).get("preview_reduced") for t in data):
        notices.append("Map preview reduced to the byte budget; full array retained server-side for map tools.")
    fallback_heading = fig_json.get("title", "")
    keys = [fig_json.get(name, "") for name in ("key", "source_key", "function_key")]
    if any(fallback_heading in (key, key.replace("_", " ")) for key in keys if key) \
            and any(a.get("title") for a in axes):
        fallback_heading = ""
    layout["title"] = {"text": "" if heading_as_note else heading if heading_in_texts else ""
                       if fig_json.get("texts") else (heading or fallback_heading),
                       "y": 0.99, "yanchor": "top"}
    layout["showlegend"] = True
    layout["legend"] = {"orientation": "h", "y": -0.12 / nrows, "x": 0.0}
    layout["margin"] = {"l": 60, "r": 60, "t": 92 if has_top_axis else 44, "b": 50}
    figure_links = []
    grids = {}   # legend name -> the lab legend (its column count and drawn box)
    if positions:
        # Source fractions already contain the margins around axes and headers.
        layout["margin"] = {"l": 0, "r": 0, "t": 0, "b": 0}
        layout["legend"].update(y=0, yanchor="bottom")
        # The one panel that carries the lab's legend keeps it; several legends share the footer.
        holders = [i for i, p in enumerate(panels) if axes[_pick_base(axes, p)].get("legend")]
        source_legend = axes[_pick_base(axes, panels[holders[0]])].get("legend") if len(holders) == 1 else None
        if source_legend:
            layout["legend"].update(_legend_place(source_legend, positions[1][holders[0]]))
            grids["legend"] = source_legend
        elif len(holders) > 1:
            # v1.5.1 (D3 classes P/T): several panels each drew their OWN legend (a T2* value,
            # an RB summary per qubit). One footer legend merged them: per-panel numbers lost
            # their panel, and the footer sat on the bottom row's axis titles. Each panel now
            # keeps its legend where the lab drew it, with that panel's entries only.
            for n, i in enumerate(holders):
                name = "legend" if n == 0 else f"legend{n + 1}"
                own = axes[_pick_base(axes, panels[i])]["legend"]
                layout[name] = {"font": {"size": own.get("size") or 10},
                                **_legend_place(own, positions[1][i])}
                grids[name] = own
                for trace in data[spans[i]:spans[i + 1]]:
                    if trace.get("showlegend") and n:
                        trace["legend"] = name
            held = {k for i in holders for k in range(spans[i], spans[i + 1])}
            for k, trace in enumerate(data[:spans[-1]]):
                if k not in held:
                    trace["showlegend"] = False   # the lab drew no legend for that panel
        figure_legends = fig_json.get("figure_legends") or [fig_json.get("figure_legend") or {}]
        if any(legend.get("entries") for legend in figure_legends) and not holders:
            for trace in data:
                trace["showlegend"] = False
        for index, figure_legend in enumerate(figure_legends):
            if not figure_legend:
                continue
            # v1.5.1 (D3 class T): a figure-level legend (fig.legend) went to the footer's
            # left corner, onto the first panel's axis title. Keep its own entries too:
            # custom figure handles need not occur in any axes, and axes legends may coexist.
            number = len(holders) + index + 1
            name = "legend" if number == 1 else f"legend{number}"
            own = _legend_place(figure_legend, (0, 1, 0, 1))
            own.update(orientation="h" if (figure_legend.get("ncol") or 1) > 1 else "v",
                       font={"size": figure_legend.get("size") or 10})
            layout[name] = own
            grids[name] = figure_legend
            for entry in figure_legend.get("entries", []):
                proxy = (_scatter_to_plotly(entry, "x", "y") if entry.get("type") == "scatter"
                         else _line_to_plotly(entry, "x", "y"))
                proxy["legend"] = name
                figure_links.append((proxy, [trace for trace in data if trace.get("name") == entry.get("name")
                                              and not trace.get("showlegend")]))
                data.append(proxy)
    layout["height"] = max(320, 320 * nrows) + (64 * nrows if has_top_axis else 0)
    size = fig_json.get("size_inches")
    source_aspect = None
    if size and len(size) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in size):
        # Height at a nominal 1200 px width; the browser re-derives it from the
        # width the pane really has (source_aspect), so a narrow pane keeps the
        # lab figure's shape instead of drawing it too tall.
        source_aspect = round(size[1] / size[0], 4)
        layout["height"] = round(max(320, min(2200, 1200 * source_aspect)))
    if notes:
        layout["annotations"] = notes
    heading_style = next((t for t in fig_json.get("texts", []) if t.get("text") == heading), {})
    if heading_style.get("color"):
        # the heading's colour stands with or without the lab's panel positions
        layout["title"].setdefault("font", {})["color"] = heading_style["color"]
    if positions and source_aspect:
        layout["font"] = {"size": 10}
        # v1.5.1 (D3 class T): the heading hangs where the lab put it (a suptitle sits at
        # y ~0.99, top-anchored). Capped at 0.98 it sat on the first row's panel titles.
        layout["title"]["y"] = min(1.0, heading_style.get("y", .98))
        layout["title"]["yanchor"] = {"center": "middle", "bottom": "bottom",
                                      "baseline": "bottom"}.get(heading_style.get("va"), "top")
        layout["title"]["font"] = {"size": heading_size or heading_style.get("size", 12)}
        if heading_style.get("color"):
            layout["title"]["font"]["color"] = heading_style["color"]
        layout["legend"]["font"] = {"size": (layout["legend"].get("font") or {}).get("size") or 10}
        if source_legend:
            layout["legend"]["font"]["size"] = source_legend.get("size") or 10
        for name, axis in layout.items():
            if re.fullmatch(r"[xy]axis\d*", name):
                axis.setdefault("tickfont", {}).setdefault("size", 10)
                axis["title"].setdefault("font", {}).setdefault("size", 10)
                axis["title"].update(standoff=4 if name.startswith("y") else 0)
    _legend_once(data)
    _split_legend_rows(data, layout, grids)
    for proxy, linked in figure_links:
        for trace in linked:
            trace["legendgroup"] = proxy.get("legendgroup", "")
    from .mathtext import plotly_text
    return _hover_guides(plotly_text({"data": data, "layout": layout, "title": fig_json.get("title", ""),
                                      "skipped": skipped, "notices": notices, "source_aspect": source_aspect,
                                      "source_size_inches": size if positions and source_aspect else None}))


#: a dashed crosshair that follows the pointer (neutral grey reads on both themes)
_SPIKE = {"showspikes": True, "spikemode": "across", "spikesnap": "cursor", "spikedash": "dash",
          "spikethickness": 1, "spikecolor": "rgba(128,128,128,0.75)"}


_TAG = re.compile(r"</?(?:sup|sub|b|i|em|strong|span)(?:\s[^>]*)?>", re.I)


def _plain(text) -> str:
    """An axis or colorbar title as hover text: the converter's own markup tags dropped (never a
    bare '<' that is part of the label), a template brace kept literal."""
    text = re.sub(r"<br\s*/?>", " ", str(text or ""), flags=re.I)
    return _TAG.sub("", text).replace("%{", "% {").strip()


def _hover_guides(fig: dict) -> dict:
    """Hover names each value by its axis title (not x/y/z), and every panel draws a
    dashed horizontal + vertical guide through the pointer."""
    layout = fig.get("layout") or {}
    titles, axes = {}, {}
    for name, axis in layout.items():
        if re.fullmatch(r"[xy]axis\d*", name) and isinstance(axis, dict):
            title = axis.get("title")
            titles[name[0] + name[5:]] = _plain(title.get("text") if isinstance(title, dict) else title)
            axes[name[0] + name[5:]] = axis
            if axis.get("visible", True) is not False:
                for key, value in _SPIKE.items():
                    axis.setdefault(key, value)
    # A shared label is drawn once (the left panel of a row, the bottom panel of a column): an
    # untitled axis borrows the title of a titled axis of the same letter, side and domain, and
    # only when both show the same range (a shared axis); an unshared one keeps "x"/"y".
    def place(ref):
        a = axes[ref]
        rng = a.get("range")
        return (ref[0], a.get("side"), bool(a.get("overlaying")), a.get("type"),
                tuple(round(v, 6) for v in a.get("domain") or ()),
                tuple(float(f"{v:.9g}") for v in rng) if isinstance(rng, list) and len(rng) == 2
                and all(isinstance(v, (int, float)) for v in rng) else None)
    for ref in [r for r, t in titles.items() if not t]:
        donor = next((r for r, t in titles.items() if t and place(r) == place(ref)), None)
        if donor:
            titles[ref] = titles[donor]
    if titles:
        layout.setdefault("hovermode", "closest")
        layout.setdefault("spikedistance", -1)
    for trace in fig.get("data") or []:
        if trace.get("hoverinfo") == "skip" or trace.get("type") not in ("heatmap", "scatter", "scattergl"):
            continue
        xl = titles.get(trace.get("xaxis", "x")) or "x"
        yl = titles.get(trace.get("yaxis", "y")) or "y"
        if trace["type"] == "heatmap":
            cb = (trace.get("colorbar") or {}).get("title")
            zl = _plain(cb.get("text") if isinstance(cb, dict) else cb) or "value"
            z = "%{customdata}" if "customdata" in trace else "%{z}"
            trace["hovertemplate"] = f"{xl}: %{{x}}<br>{yl}: %{{y}}<br>{zl}: {z}<extra></extra>"
        elif "hovertemplate" in trace and "customdata" in trace:
            cb = ((trace.get("marker") or {}).get("colorbar") or {}).get("title")
            zl = _plain(cb.get("text") if isinstance(cb, dict) else cb) or "value"
            trace["hovertemplate"] = f"{xl}: %{{x}}<br>{yl}: %{{y}}<br>{zl}: %{{customdata}}<extra></extra>"
        else:
            name = "" if trace.get("name") else "<extra></extra>"
            trace["hovertemplate"] = f"{xl}: %{{x}}<br>{yl}: %{{y}}{name}"
    return fig


_HEADING_EM = 0.54        # mean glyph width of the browser plot font, in em (measured)
_HEADING_MIN_SCALE = 0.6  # below this a heading wraps instead of shrinking further


def _fit_heading(text, size_pt, size_inches):
    """``(text, size)`` of a figure heading that fits the lab figure's width.

    A lab that saves with a tight bounding box gets a PNG that grows to hold a long heading;
    the reproduced figure has the lab's own width, so one long line was cut off at both
    sides (v1.5.1, D3 class T). The heading shrinks to the width -- staying on the line the
    lab reserved for it, so it never runs into the panel captions below -- and wraps only
    below 60 % of the lab size. Text scales with the canvas, so a width in figure points
    holds at every display width. Lines the lab broke itself are kept."""
    size_pt = float(size_pt or 12)
    if not (isinstance(size_inches, (list, tuple)) and len(size_inches) == 2
            and isinstance(size_inches[0], (int, float)) and size_inches[0] > 0):
        return text, size_pt
    width_pt = size_inches[0] * 72 * 0.98
    longest = max(len(line) for line in text.split("\n"))
    needed = longest * size_pt * _HEADING_EM
    if needed <= width_pt:
        return text, size_pt
    if width_pt / needed >= _HEADING_MIN_SCALE:
        return text, round(size_pt * width_pt / needed, 3)
    import textwrap
    size = round(size_pt * _HEADING_MIN_SCALE, 3)
    columns = max(12, int(width_pt / (size * _HEADING_EM)))
    lines = [textwrap.wrap(line, width=columns, break_long_words=False, break_on_hyphens=False) or [""]
             for line in text.split("\n")]
    return "\n".join(part for line in lines for part in line), size


def _text_note(a, xref, yref, width=1080):
    text = a["text"]
    if a.get("wrap"):
        import textwrap
        columns = max(12, int(width * .95 / (max(1, a.get("size", 10)) * .6)))
        text = "\n".join("\n".join(textwrap.wrap(line, width=columns, break_long_words=False,
                           break_on_hyphens=False)) for line in text.split("\n"))
    size = a.get("size", 10)
    if "\n" in text:
        # Plotly sets lines 1.3 em apart, Matplotlib 1.2 em: scale a block so it fills
        # the height the lab reserved for it instead of running into the panels.
        size = round(size * 1.2 / 1.3, 3)
    result = {"text": text, "showarrow": False, "x": a.get("x", 0), "y": a.get("y", 0),
              "xref": xref, "yref": yref, "font": {"size": size},
              "xanchor": {"center": "center", "right": "right"}.get(a.get("ha"), "left"),
              "yanchor": {"center": "middle", "top": "top"}.get(a.get("va"), "bottom")}
    if a.get("color"):
        result["font"]["color"] = a["color"]
    box = a.get("box") or {}
    if box.get("face") or box.get("edge"):
        # v1.5.1 (D3 classes P/T): the box the lab drew behind the text (a fit summary, a T1
        # value): without it the text ran over the curve it sits on
        result.update(bgcolor=box.get("face") or "rgba(0,0,0,0)",
                      bordercolor=box.get("edge") or "rgba(0,0,0,0)",
                      borderwidth=(box.get("width") or 0) if box.get("edge") else 0,
                      borderpad=round(box.get("pad") or 0, 2))
    return result


def _add_artists(ax, xref, yref, data, layout, notes, width=1080):
    first_trace = len(data)
    for t in sorted(ax.get("traces", []), key=lambda t: t.get("zorder") or 0):
        coord = t.get("coord")
        if coord and t["type"] == "polygon":
            layout.setdefault("shapes", []).append({"type": "rect", "x0": min(t["x"]), "x1": max(t["x"]),
                "y0": min(t["y"]), "y1": max(t["y"]),
                "xref": xref + (" domain" if coord == "x-domain-y-data" else ""),
                "yref": yref + (" domain" if coord == "x-data-y-domain" else ""),
                "fillcolor": t.get("color"), "line": {"width": 0},
                "opacity": t.get("opacity", 1), "layer": "below"})
            if t.get("edge_color") and (t.get("edge_width") or 0) > 0:
                # the span's own outline (an unfilled box is only its edge)
                layout["shapes"][-1].update(
                    line={"color": t["edge_color"], "width": t["edge_width"], "dash": t.get("edge_dash", "solid")},
                    fillcolor=(_with_alpha(t.get("color"), t.get("opacity", 1)) if t.get("fill") is not False
                               else "rgba(0,0,0,0)"), opacity=1.0)
        elif coord and t["type"] == "line":
            layout.setdefault("shapes", []).append({"type": "line", "x0": t["x"][0], "x1": t["x"][-1],
                "y0": t["y"][0], "y1": t["y"][-1], "xref": xref + (" domain" if coord == "x-domain-y-data" else ""),
                "yref": yref + (" domain" if coord == "x-data-y-domain" else ""),
                "line": {"color": t.get("color"), "width": t.get("width", 1), "dash": t.get("dash", "solid")},
                "opacity": t.get("opacity", 1)})
            if t.get("name"):
                data.append(_line_to_plotly({**t, "x": [None], "y": [None]}, xref, yref))
        else:
            trace = _trace_to_plotly(t, xref, yref)
            if trace:
                data.append(trace)
    # Plotly only instantiates Cartesian pairs referenced by a trace. Null points
    # keep text-only and shape-only panels alive without affecting their ranges.
    if len(data) == first_trace:
        data.append({"type": "scatter", "x": [None], "y": [None],
                     "xaxis": xref, "yaxis": yref, "mode": "markers", "opacity": 0,
                     "showlegend": False, "hoverinfo": "skip"})
    for a in ax.get("annotations", []):
        frac = a.get("xref_frac", False)
        note = _text_note(a, xref + (" domain" if frac else ""), yref + (" domain" if frac else ""), width)
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
