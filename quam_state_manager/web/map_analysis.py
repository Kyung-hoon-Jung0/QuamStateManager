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


def _coordinate(result, axis, model=None):
    model_axis = next(k for k, v in result["model_axes"].items() if v == axis)
    values = result["fit"].get("values") or {}
    kind = result["classification"]["type"] if model != "extremum" else "unknown"
    key = {"erroramp": {"x": "a_pi", "y": "y"},
           "chevron": {"x": "t_pi", "y": "dv0"}}.get(kind, {}).get(model_axis, model_axis)
    if kind == "ridge" and model_axis == "y":
        return values.get("y", values.get("asymptote"))
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
    if "power" in (axis.get("xlabel", "") + axis.get("ylabel", "")).lower():
        meta["summary"] = "asymptote"
    # Same-grid phase sibling supplies the landscape target contour.
    for sibling in choices:
        label = (sibling["trace"].get("colorbar") or {}).get("label", "").lower()
        if sibling is not panel and "phase" in label and sibling["trace"]["x"] == trace["x"] and sibling["trace"]["y"] == trace["y"]:
            meta["phase"] = sibling["trace"]["z"]
    from quam_state_manager.core.interactive_plots.learn_click import _read
    saved = _read(run, "data.json").get("fit_results", {})
    entity = axis.get("entity") or axis.get("title", "").strip()
    records = [v for k, v in saved.items() if k == entity or k in entity] if isinstance(saved, dict) else []
    record = records[0] if len(records) == 1 and isinstance(records[0], dict) and body.get("source") != "ndview" else {}
    x, y = centers(trace["x"], len(trace["z"][0])), centers(trace["y"], len(trace["z"]))
    classification = api.classify(x, y, meta, trace["z"])
    names = {"erroramp": ("opt_amp_prefactor", "a_pi"), "ridge": ("frequency", "y"),
             "landscape": ("optimal_a_scan", "x"), "chevron": ("v_res_plus_v", "dv0")}
    node_name, value_key = names.get(classification["type"], ("x", "x"))
    if classification["type"] == "erroramp" and "optimal_a_scan" in record:
        node_name = "optimal_a_scan"
    if classification["type"] == "ridge" and meta.get("summary") == "asymptote":
        value_key = "asymptote"
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
        coordinate = _coordinate(result, target_axis, body.get("fit", {}).get("model"))
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


def raw_figure(run, body, candidates):
    """Read a selected raw plane without using the browser's decimated cube."""
    from pathlib import Path
    import numpy as np
    from quam_state_manager.core import ndview
    which, var = body.get("which"), body.get("var")
    if which not in ndview.list_h5_files(Path(run.folder_path)):
        raise ValueError("Data file not available")
    with ndview._h5_lock_for(str(Path(run.folder_path) / which)):
        with ndview._open_reader(Path(run.folder_path) / which) as reader:
            ds = reader.get(var)
            if ds is None or math.prod(ds.shape) > 4_000_000:
                raise ValueError("Map too large for server analysis or variable unavailable")
            dims = reader.dim_meta(ds)
            names = [d["name"] for d in dims]
            xd, yd = body.get("x_dim"), body.get("y_dim")
            if xd == yd or xd not in names or yd not in names:
                raise ValueError("Map axes do not match the raw variable")
            selection = body.get("selection", {})
            if set(selection) - set(names):
                raise ValueError("Unknown selected dimension")
            data = reader.read(ds)
            component = body.get("component", "base")
            if component not in {"base", "mag", "phase"}:
                raise ValueError("Unknown map component")
            if component != "base":
                partner = reader.get(ndview._iq_partner(var, set(reader.keys())))
                if partner is None or partner.shape != ds.shape or reader.dim_meta(partner) != dims:
                    raise ValueError("Matching IQ partner not available")
                second = reader.read(partner)
                first, second = (second, data) if str(var).startswith("Q") else (data, second)
                data = np.hypot(first, second) if component == "mag" else np.arctan2(second, first)
            entity = None
            # Match the viewer: repetition dimensions are averaged before slicing.
            descriptor = [{"name": name, "size": ds.shape[i], "kind": ndview._classify_dim(
                name, ds.shape[i], reader.read_coord(name) if dims[i]["has_coord"] else None, dims[i]["has_coord"])} for i, name in enumerate(names)]
            reductions = [item["name"] for item in ndview._default_view(descriptor)["reduced"]]
            if reductions:
                data, descriptor = ndview._reduce_dims(data, descriptor, reductions)
                names = [item["name"] for item in descriptor]
            shape = data.shape
            fixed = []
            for index, name in enumerate(names):
                if name in (xd, yd):
                    fixed.append(slice(None))
                else:
                    pos = selection.get(name, 0)
                    if pos >= shape[index]:
                        raise ValueError("Selected index outside the map")
                    fixed.append(pos)
                    if name in ("qubit", "qubit_pair"):
                        coord = reader.read_coord(name)
                        entity = str(ndview._decode(coord[pos])) if coord is not None else None
            plane = np.asarray(data[tuple(fixed)], float)
            if names.index(xd) < names.index(yd):
                plane = plane.T
            coords = {}
            for name in (xd, yd):
                coord = reader.read_coord(name)
                coords[name] = np.asarray(coord if coord is not None else np.arange(shape[names.index(name)]), float).tolist()
            trace = {"type": "heatmap", "full_resolution": True, "x": coords[xd], "y": coords[yd],
                     "z": api._json_value(plane), "colorbar": {"label": "phase rad" if component == "phase" else str(var)}}
            axis = {"xlabel": xd, "ylabel": yd, "title": entity or "", "traces": [trace]}
            figure = {"axes": [axis]}
            targets = []
            for candidate in candidates:
                if not entity or candidate.get("dim") not in (xd, yd):
                    continue
                path = candidate.get("path_by_entity", {}).get(entity) or candidate["path"]
                path = path.replace("{q}", entity).replace("{p}", entity)
                targets.append({"path": path, "axis": "x" if candidate["dim"] == xd else "y"})
            converted = {"data": [trace], "clickable": {"targets": targets} if targets else None,
                         "status_chip": "raw data", "display_only_reason": "no node fit to compare; existing coordinate candidates only"}
            return figure, converted


def replay(uid, digest):
    with _LOCK:
        entry = _RESULTS.get((uid, digest))
        return deepcopy(entry[0]) if entry else None
