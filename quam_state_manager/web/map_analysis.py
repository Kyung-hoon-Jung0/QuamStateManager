"""Full-array map analysis and bounded, run-scoped replay of displayed results."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import math
import threading

from quam_state_manager.core.mapfit import api
from quam_state_manager.core.mapfit.contour import centers

_RESULTS = OrderedDict()
_LOCK = threading.Lock()
_BYTES = 64 * 1024 * 1024


def panels(figure, converted):
    """Associate source heatmaps with converter axis references, retaining full z."""
    originals = [(axis, trace) for axis in figure.get("axes", [])
                 if not axis.get("is_colorbar") for trace in axis.get("traces", [])
                 if trace.get("type") == "heatmap"]
    rendered = [t for t in converted.get("data", []) if t.get("type") == "heatmap"]
    return [{"axis": axis, "trace": trace, "xref": view.get("xaxis", "x"),
             "yref": view.get("yaxis", "y")} for (axis, trace), view in zip(originals, rendered)]


def preview(result):
    """Bound browser transfer; full-resolution fitting has already finished."""
    out = deepcopy(result)
    grid = out["smoothed"]
    sx = max(1, math.ceil(len(grid["x"]) / 400))
    sy = max(1, math.ceil(len(grid["y"]) / 400))
    grid.update(x=grid["x"][::sx], y=grid["y"][::sy], z=[r[::sx] for r in grid["z"][::sy]])
    return out


def _coordinate(result, axis):
    model_axis = next(k for k, v in result["model_axes"].items() if v == axis)
    values = result["fit"].get("values") or {}
    kind = result["classification"]["type"]
    key = {"erroramp": {"x": "a_pi", "y": "y"},
           "chevron": {"x": "t_pi", "y": "dv0"}}.get(kind, {}).get(model_axis, model_axis)
    return values.get(key)


def analyze(run, uid, figure, converted, body):
    choices = panels(figure, converted)
    index = body.get("panel", 0)
    if index >= len(choices):
        raise ValueError("Map panel not available")
    panel = choices[index]
    axis, trace = panel["axis"], panel["trace"]
    if not trace.get("full_resolution", False):
        raise ValueError("Full-resolution map not available for server analysis")
    meta = {"x": {"label": axis.get("xlabel", "")}, "y": {"label": axis.get("ylabel", "")},
            "signal_label": (trace.get("colorbar") or {}).get("label", "")}
    # Same-grid phase sibling supplies the landscape target contour.
    for sibling in choices:
        label = (sibling["trace"].get("colorbar") or {}).get("label", "").lower()
        if sibling is not panel and "phase" in label and sibling["trace"]["x"] == trace["x"] and sibling["trace"]["y"] == trace["y"]:
            meta["phase"] = sibling["trace"]["z"]
    from quam_state_manager.core.interactive_plots.learn_click import _read
    saved = _read(run, "data.json").get("fit_results", {})
    entity = axis.get("entity") or axis.get("title", "").strip()
    records = [v for k, v in saved.items() if k == entity or k in entity] if isinstance(saved, dict) else []
    record = records[0] if len(records) == 1 and isinstance(records[0], dict) else {}
    x, y = centers(trace["x"], len(trace["z"][0])), centers(trace["y"], len(trace["z"]))
    classification = api.classify(x, y, meta, trace["z"])
    names = {"erroramp": ("opt_amp_prefactor", "a_pi"), "ridge": ("frequency", "y"),
             "landscape": ("optimal_a_scan", "x"), "chevron": ("v_res_plus_v", "dv0")}
    node_name, value_key = names.get(classification["type"], ("x", "x"))
    node_value = record.get(node_name)
    if isinstance(node_value, (float, int)) and math.isfinite(node_value):
        meta.update(node_value=node_value, node_name=node_name, value_key=value_key)
    result = api.analyze({"z": trace["z"], "x": trace["x"], "y": trace["y"], "meta": meta,
                          **{k: body[k] for k in ("smooth", "contour", "fit") if k in body}})
    # Scope the hash to the run, figure and exact source revision as well as arrays/options.
    result["request_hash"] = api.request_hash({"analysis": result["request_hash"], "run": uid,
        "fig": body.get("fig"), "panel": index, "source": body.get("source", "replot"),
        "revision": figure.get("revision"), "fingerprint": figure.get("code_fingerprint")})
    chip = converted.get("status_chip") or "not verified on this run"
    comparison = result["compare"]
    run_id = getattr(run, "id", getattr(run, "run_id", "?"))
    text = (f"derived from run #{run_id} {getattr(run, 'experiment_name', '')}: "
            f"{result['provenance_text'].removeprefix('derived from ')}; "
            f"node value {comparison['node_value']}, delta {comparison['delta']}, {comparison['verdict']}; code {chip}")
    if body.get("contour", {}).get("on"):
        text += "; contour level " + ", ".join(str(c["level"]) for c in result["contours"])
    result["provenance_text"] = text[:300]
    origin = {"src": "map_fit", "run_uid": uid, "request_hash": result["request_hash"], "text": text[:300]}
    updates = []
    for target in (converted.get("clickable") or {}).get("targets", []):
        target_axis = target.get("axis", "x")
        if target.get("plotly_axis", panel[target_axis + "ref"]) != panel[target_axis + "ref"]:
            continue
        coordinate = _coordinate(result, target_axis)
        if coordinate is None:
            continue
        transform = target.get("transform")
        if transform and transform.get("type") != "prefactor":
            continue
        value = coordinate * target.get("scale", 1) + target.get("offset", 0)
        if transform:
            value = transform["old"] * (coordinate * transform.get("coordinate_scale", 1) + transform.get("coordinate_offset", 0))
        path = target.get("path", "")
        if math.isfinite(value) and path and "{" not in path:
            updates.append({"dot_path": path, "value": value, "provenance": origin})
    result.update(updates=updates, status_chip=chip, xref=panel["xref"], yref=panel["yref"],
                  display_only_reason=converted.get("display_only_reason") or ("not verified on this run" if not updates else ""))
    out = preview(result)
    key = (uid, out["request_hash"])
    size = len(api.canonical_json(out).encode("utf-8"))
    with _LOCK:
        _RESULTS[key] = (deepcopy(out), size)
        _RESULTS.move_to_end(key)
        while len(_RESULTS) > 64 or sum(v[1] for v in _RESULTS.values()) > _BYTES:
            _RESULTS.popitem(last=False)
    return out


def replay(uid, digest):
    with _LOCK:
        entry = _RESULTS.get((uid, digest))
        return deepcopy(entry[0]) if entry else None
