"""Full-array map analysis and bounded, run-scoped replay of displayed results."""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import json
import math
import re
import threading

from quam_state_manager.core.mapfit import api
from quam_state_manager.core.mapfit.contour import centers
from quam_state_manager.core.mapfit.compare import saved_comparisons

# Map calculation failures are request errors, including numerical domain failures.
REQUEST_ERRORS = (ValueError, TypeError, KeyError, IndexError, ArithmeticError)

_RESULTS = OrderedDict()
_LOCK = threading.Lock()
_BYTES = 64 * 1024 * 1024


def _frequency_scale(run, values, label):
    """Resolve plotted frequency units, verifying a mislabeled scale against source coordinates."""
    import numpy as np
    from pathlib import Path
    from quam_state_manager.core import ndview
    for unit, scale in (("ghz", 1e9), ("mhz", 1e6), ("khz", 1e3)):
        if unit in label.lower():
            return scale
    # A copied xarray coordinate can retain its old Hz label after division. Only a full
    # match against the saved frequency grid proves the scale; magnitude alone is not evidence.
    values = np.asarray(values, float)
    for filename in ("ds_raw.h5", "ds_fit.h5"):
        path = Path(run.folder_path) / filename
        if not path.is_file():
            continue
        with ndview._h5_lock_for(str(path)):
            with ndview._open_reader(path) as reader:
                for name in ("full_freq", "RF_frequency", "frequency"):
                    dataset = reader.get(name)
                    if dataset is None or math.prod(dataset.shape) > 100_000 or not dataset.shape or dataset.shape[-1] != len(values):
                        continue
                    rows = np.asarray(reader.read(dataset), float).reshape(-1, len(values))
                    for scale in (1e9, 1e6, 1e3):
                        if any(np.allclose(values * scale, row, rtol=1e-10, atol=1e-4) for row in rows):
                            return scale
    return 1.


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
            "signal_label": (trace.get("colorbar") or {}).get("label", ""),
            "map_label": figure.get("key", "")}
    if "power" in (axis.get("xlabel", "") + axis.get("ylabel", "")).lower():
        meta["summary"] = "asymptote"
    def entity_of(candidate):
        return candidate.get("entity") or candidate.get("title", "").split(":", 1)[0].split(" ", 1)[0].strip()
    sibling_axes = figure.get("axes", []) + figure.get("map_sibling_axes", [])
    declared_levels = {t.get("level") for candidate in sibling_axes
        if not candidate.get("is_colorbar") and entity_of(candidate) == entity_of(axis)
        for t in candidate.get("traces", []) if t.get("type") == "contour"}
    # Same-grid phase sibling supplies the landscape target contour.
    siblings = choices + [{"axis": candidate, "trace": sibling} for candidate in figure.get("map_sibling_axes", [])
        if not candidate.get("is_colorbar") for sibling in candidate.get("traces", [])
        if sibling.get("type") == "heatmap" and sibling.get("full_resolution")]
    for sibling in siblings:
        label = (sibling["trace"].get("colorbar") or {}).get("label", "").lower()
        if (sibling is not panel and any(word in label for word in ("phase", "phi_2q"))
                and entity_of(sibling["axis"]) == entity_of(axis)
                and sibling["trace"]["x"] == trace["x"] and sibling["trace"]["y"] == trace["y"]):
            meta["phase"] = sibling["trace"]["z"]
            meta["phase_units"] = (sibling["trace"].get("colorbar") or {}).get("units", "")
            if "phase error" in label and 0 in declared_levels:
                meta["target_deg"] = 0.
    from quam_state_manager.core.interactive_plots.learn_click import _read, _owner
    saved = _read(run, "data.json").get("fit_results", {})
    entity = _owner(axis, set(saved)) if isinstance(saved, dict) else None
    records = [saved[entity]] if isinstance(saved, dict) and entity in saved else []
    record = records[0] if len(records) == 1 and isinstance(records[0], dict) and body.get("source") != "ndview" else {}
    if record.get("b_resolved") is False:
        meta["b_resolved"] = False
    if "l1_at_pick" in record or "l1_amp_at_pick" in record:
        import numpy as np
        parameters = getattr(run,"parameters",{})
        shots = parameters.get("num_shots",0) * parameters.get("num_frame_rotations",1)
        floor = record.get("readout_f_floor")
        finite = np.asarray(trace["z"],float)
        finite = finite[np.isfinite(finite)]
        if shots > 0 and isinstance(floor,(int,float)) and 0 <= floor <= 1 and finite.size:
            on = min(1.,max(floor,floor+2*float(np.min(finite))))
            # Zero observed counts still have finite binomial uncertainty (Jeffreys half count).
            on = on if on > 0 else .5/(shots+1)
            floor = floor if floor > 0 else .5/(shots+1)
            meta["objective_sigma"] = math.sqrt(on*(1-on)+floor*(1-floor))/(2*math.sqrt(shots))
            meta["objective_shots"] = shots
            meta["objective_floor"] = floor
    operation = record.get("operation") or getattr(run, "parameters", {}).get("operation")
    if operation == "x90":
        meta["theta"] = math.pi / 2
    x, y = centers(trace["x"], len(trace["z"][0])), centers(trace["y"], len(trace["z"]))
    classification = api.classify(x, y, meta, trace["z"])
    time_scale, control_scale, frequency_scale, lobe = 1., 1., 1., None
    if classification["type"] == "chevron":
        time_label = axis.get("xlabel" if classification["feature_axis"] == "x" else "ylabel", "")
        unit = re.search(r"\b(ns|us|ms|s)\b", time_label.lower())
        if unit:
            meta["time_units"] = unit.group(1)
            time_scale = {"s": 1., "ms": 1e-3, "us": 1e-6, "ns": 1e-9}[unit.group(1)]
        control_label = axis.get("ylabel" if classification["feature_axis"] == "x" else "xlabel", "")
        control_scale = .001 if "mv" in control_label.lower() else 1.
        match = re.search(r"\blobe\s+([+-])", axis.get("title", ""))
        lobe = match.group(1) if match else None
    elif classification["type"] == "ridge":
        feature = classification["feature_axis"]
        frequency_scale = _frequency_scale(run, x if feature == "x" else y,
            axis.get("xlabel" if feature == "x" else "ylabel", ""))
        if isinstance(record.get("fwhm"), (int, float)) and record["fwhm"] > 0:
            meta["fwhm"] = record["fwhm"] / frequency_scale
    names = {"erroramp": ("opt_amp_prefactor", "a_pi"), "ridge": ("frequency", "y"),
             "landscape": ("optimal_a_scan", "x"), "chevron": ("v_res_plus_v", "dv0")}
    node_name, value_key = names.get(classification["type"], ("x", "x"))
    if classification["type"] == "erroramp" and "optimal_a_scan" in record:
        node_name = "optimal_a_scan"
    if classification["type"] == "ridge" and meta.get("summary") == "asymptote":
        value_key = "asymptote"
    if classification["type"] == "landscape" and any(
            label in axis.get("ylabel", "").lower() for label in ("a_scan", "scan amplitude")):
        value_key = "y"
    node_value = record.get(node_name)
    if isinstance(node_value, (float, int)) and math.isfinite(node_value):
        meta.update(node_value=node_value, node_name=node_name, value_key=value_key)
    landscape_fields = []
    if classification["type"] == "landscape":
        for key, label in (("x", axis.get("xlabel", "").lower()), ("y", axis.get("ylabel", "").lower())):
            name = ("optimal_b_over_a" if "b_over_a" in label else "optimal_t_phi" if "t_phi" in label
                else "optimal_neg_offset_v" if "neg_offset" in label else "optimal_a_scan"
                if "a_scan" in label or "scan amplitude" in label else None)
            if name:
                landscape_fields.append((name, key))
        landscape_fields.sort(key=lambda item: item[0] == "optimal_b_over_a")
    array, coordinates = trace["z"], {"x": trace["x"], "y": trace["y"]}
    coordinate_maps = {}
    omitted = 0
    if classification["type"] == "erroramp":
        import numpy as np
        count_axis = classification["feature_axis"]
        count = centers(coordinates[count_axis], len(array) if count_axis == "y" else len(array[0]))
        positive = count > 0
        omitted = int((~positive).sum())
        if omitted:
            values = np.asarray(array, float)
            array = values[positive, :] if count_axis == "y" else values[:, positive]
            coordinates[count_axis] = count[positive].tolist()
        amplitude_axis = "x" if count_axis == "y" else "y"
        for secondary in axis.get("secondary_axes", []):
            if secondary.get("axis") != amplitude_axis or "prefactor" not in secondary.get("label", "").lower():
                continue
            mapping = secondary.get("map") or {}
            parent, forward = mapping.get("parent", []), mapping.get("forward", [])
            if len(parent) < 2 or len(parent) != len(forward) or parent[-1] == parent[0]:
                continue
            scale = (forward[-1] - forward[0]) / (parent[-1] - parent[0])
            offset = forward[0] - scale * parent[0]
            if scale and all(abs(scale * p + offset - v) < 1e-9 for p, v in zip(parent, forward)):
                coordinate_maps[amplitude_axis] = {"scale": scale, "offset": offset}
                coordinates[amplitude_axis] = [v * scale + offset for v in coordinates[amplitude_axis]]
                break
    fit_request = {"z": array, "x": coordinates["x"], "y": coordinates["y"], "meta": meta,
                   **{k: body[k] for k in ("smooth", "contour", "fit") if k in body}}
    result = api.analyze(fit_request)
    # A single slider's cap is the tightest row cap, not the least restrictive row.
    if classification["type"] in {"erroramp", "chevron"}:
        import numpy as np
        a = np.asarray(array, float)
        xc, yc = centers(coordinates["x"], a.shape[1]), centers(coordinates["y"], a.shape[0])
        if np.any(np.diff(xc) < 0):
            xc, a = xc[::-1], a[:, ::-1]
        if np.any(np.diff(yc) < 0):
            yc, a = yc[::-1], a[::-1, :]
        if result["caps_applied"]["transposed"]:
            a, xc, yc = a.T, yc, xc
        _, _, row_caps, _ = api._cap_info(classification["type"], a, xc, yc,
            {**meta, "circular": classification["circular"]}, result["raw_fit"])
        if row_caps is not None:
            tightest = float(np.min(row_caps))
            smooth = body.get("smooth", {})
            if not smooth.get("override_cap") and result["caps_applied"]["sigma_x"] > tightest:
                key = "sigma_y" if result["caps_applied"]["transposed"] else "sigma_x"
                fit_request["smooth"] = {**smooth, key: tightest}
                result = api.analyze(fit_request)
            result["caps_applied"]["cap_x"] = tightest
            if smooth.get("override_cap") and result["caps_applied"]["sigma_x"] > tightest:
                result["caps_applied"].update(override=True, warning="cap overridden")
                if "cap overridden" not in result["fit"]["verdict_line"]:
                    result["fit"]["verdict_line"] += "; cap overridden"
    for dim, mapping in coordinate_maps.items():
        result["smoothed"][dim] = [(v - mapping["offset"]) / mapping["scale"] for v in result["smoothed"][dim]]
        for contour in result["contours"]:
            for line in contour["lines"]:
                for point in line:
                    point_index = 0 if dim == "x" else 1
                    point[point_index] = (point[point_index] - mapping["offset"]) / mapping["scale"]
    if omitted:
        result["fit"]["verdict_line"] += f"; {omitted} nonpositive-count baseline rows excluded from fit"
    import numpy as np
    dx, dy = float(np.median(np.abs(np.diff(x)))), float(np.median(np.abs(np.diff(y))))
    if result["caps_applied"]["transposed"]:
        dx, dy = dy, dx
    references = saved_comparisons(classification["type"], record, result["fit"], result["raw_fit"],
        dx, dy, frequency_scale, time_scale, control_scale, lobe, landscape_fields)
    if references:
        result["comparisons"] = references
        result["compare"] = references[0]
    result["node_verdict"] = {k: record[k] for k in ("success", "reasons", "reason", "note") if k in record}
    # Scope the hash to the run, figure and exact source revision as well as arrays/options.
    from quam_state_manager.core.interactive_plots.replot import _fingerprint
    from pathlib import Path
    result["request_hash"] = api.request_hash({"analysis": result["request_hash"], "run": uid,
        "fig": body.get("fig"), "panel": index, "source": body.get("source", "replot"),
        "revision": figure.get("revision"), "fingerprint": figure.get("code_fingerprint"), "settings": body,
        "inputs": _fingerprint(Path(run.folder_path)), "contract": converted.get("clickable")})
    chip = converted.get("status_chip") or "not verified on this run"
    comparison = result["compare"]
    run_id = getattr(run, "id", getattr(run, "run_id", "?"))
    applied = result["caps_applied"]
    display_caps = dict(applied)
    if applied["transposed"]:
        for prefix in ("sigma_", "cap_"):
            display_caps[prefix + "x"], display_caps[prefix + "y"] = applied[prefix + "y"], applied[prefix + "x"]
    model = body.get("fit", {}).get("model", "auto")
    model = (classification["models"] or ["none"])[0] if model == "auto" else model
    contour_note = ""
    if body.get("contour", {}).get("on"):
        contour_note = (" [contour level " + ", ".join(f"{c['level']:.4g}" for c in result["contours"]) + "]"
                        if result["contours"] else " [contour: no automatic levels]")
    revision = figure.get("revision") or {}
    code = revision.get("rung", "unverified") + ("/" + revision["sha"][:7] if revision.get("sha") else "")
    def compact(value):
        return "n/a" if value is None else f"{value:.6g}"
    text = (f"derived from run #{run_id}: {model} on smoothed map; code {code}; "
            f"figure {body.get('fig', '')[:32]}, panel {index} "
            f"({body.get('smooth', {}).get('kernel', 'gaussian')}, sigma x={display_caps['sigma_x']:.1f} pt, "
            f"y={display_caps['sigma_y']:.1f}; cap x={display_caps['cap_x']:.3g}, y={display_caps['cap_y']:.3g} pt"
            + ("; cap overridden" if applied["override"] else "") + f"){contour_note}; "
            f"node value {compact(comparison['node_value'])}, delta {compact(comparison['delta'])}, {comparison['verdict']}")
    result["provenance_text"] = text[:300]
    origin = {"src": "map_fit", "run_uid": uid, "request_hash": result["request_hash"], "text": text[:300]}
    updates = []
    for target in (converted.get("clickable") or {}).get("targets", []):
        if target.get("evidence_tier", (converted.get("clickable") or {}).get("evidence_tier")) != "applied":
            continue
        target_axis = target.get("axis", "x")
        if target.get("plotly_axis", panel[target_axis + "ref"]) != panel[target_axis + "ref"]:
            continue
        coordinate = _coordinate(result, target_axis, body.get("fit", {}).get("model"))
        if coordinate is None:
            continue
        if target_axis in coordinate_maps:
            mapping = coordinate_maps[target_axis]
            coordinate = (coordinate - mapping["offset"]) / mapping["scale"]
        transform = target.get("transform")
        if transform and transform.get("type") != "prefactor":
            continue
        value = coordinate * target.get("scale", 1) + target.get("offset", 0)
        if transform:
            value = transform["old"] * (coordinate * transform.get("coordinate_scale", 1) + transform.get("coordinate_offset", 0))
        path = target.get("path", "")
        if math.isfinite(value) and path and "{" not in path:
            details = {"figure": body.get("fig", ""), "panel": index,
                "run_id": run_id, "experiment_name": getattr(run, "experiment_name", ""),
                "source": body.get("source", "replot"), "settings": deepcopy(body),
                "caps_applied": deepcopy(result["caps_applied"]),
                "contour_levels": [c["level"] for c in result["contours"]],
                "model": model, "revision": deepcopy(revision),
                "code_fingerprint": figure.get("code_fingerprint"), "target": deepcopy(target)}
            updates.append({"dot_path": path, "value": value, "provenance": {**origin, "map": details}})
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
            converted = {"data": [trace], "clickable": None,
                         "status_chip": "raw data", "display_only_reason": "no node fit to compare; raw coordinates have no verified map write contract"}
            return figure, converted


def replay(uid, digest):
    with _LOCK:
        entry = _RESULTS.get((uid, digest))
        return deepcopy(entry[0]) if entry else None
