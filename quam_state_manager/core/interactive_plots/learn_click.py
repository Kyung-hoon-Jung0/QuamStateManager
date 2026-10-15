"""Learn a closed click transform from a run's own numeric state writes.

Marker coordinates prove the mapping; fit-result names only describe it.
Every frozen input comes from the evidence, never from the post-update state.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from pathlib import Path

from quam_state_manager.core import safe_io


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _read(run, filename):
    path = Path(getattr(run, "folder_path", run)) / filename
    try:
        # An absent run file is absent: the retry ladder is for a live file caught mid-replace,
        # and waiting it out on a run that never wrote state_updates.json cost ~0.9 s per read,
        # five reads per reproduced figure request.
        return safe_io.read_json(path, attempts=None if path.exists() else 1)
    except (OSError, ValueError, TypeError):
        return {}


def evidence(run, node=None):
    """Patches win as a whole tier, including an unusable patch list."""
    node = _read(run, "node.json") if node is None else node
    patches = node.get("patches") if isinstance(node, dict) else None
    if patches:
        entries, tier = patches, "applied"
    else:
        proposed = _read(run, "state_updates.json")
        entries = proposed.get("operations", []) if isinstance(proposed, dict) else []
        tier = "proposed"
    output = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict) or entry.get("op") != "replace":
            continue
        path = entry.get("path", "")
        value = _number(entry.get("value"))
        if not isinstance(path, str) or not path.startswith("/quam/") or value is None:
            continue
        parts = [p.replace("~1", "/").replace("~0", "~") for p in path[6:].split("/")]
        if len(parts) < 3 or parts[0] not in {"qubits", "qubit_pairs"}:
            continue
        # Dot-path targets cannot represent literal dots in pointer components.
        if any("." in p or not p for p in parts):
            continue
        entity = parts[1]
        template = ".".join([parts[0], "{q}" if parts[0] == "qubits" else "{pair}", *parts[2:]])
        output.append({"path": ".".join(parts), "template": template,
                       "entity": entity, "value": value, "old": _number(entry.get("old")),
                       "tier": tier})
    return output


def _agrees(actual, expected):
    return actual is not None and abs(actual - expected) <= 1e-6 * max(abs(expected), 1e-12)


def _owner(panel, entities):
    explicit = panel.get("entity") or panel.get("qubit") or panel.get("pair")
    if explicit in entities:
        return explicit
    title = str(panel.get("title", ""))
    found = [q for q in entities if re.search(r"(?<![\w-])" + re.escape(q) + r"(?![\w-])", title)]
    return found[0] if len(found) == 1 else None


def markers(figure, entities):
    """Return panel-owned markers from v2 artists or explicit verify markers."""
    output = []
    from .replot import _drawable, _group_panels, _pick_base, _ref

    axes_payload = figure.get("axes", [])
    references = {}
    for panel_index, group in enumerate(_group_panels(axes_payload, _drawable(axes_payload)), 1):
        base = _pick_base(axes_payload, group)
        for index in group:
            panel = axes_payload[index]
            references[index] = {axis: _ref(axis, panel_index + (100 if index != base and
                                  ((axis == "x" and panel.get("shares_y_with") is not None) or
                                   (axis == "y" and panel.get("shares_y_with") is None)) else 0))
                                 for axis in ("x", "y")}
    for original_index, panel in enumerate(figure.get("axes", [])):
        if original_index not in references:
            continue
        owner = _owner(panel, entities)
        if owner is None:
            continue
        anchors = dict(panel.get("anchors") or {})
        for trace in panel.get("traces", []):
            if trace.get("type") == "heatmap":
                for axis in ("x", "y"):
                    coords = trace.get(axis) or []
                    if coords:
                        anchors.setdefault(axis, coords[0])
        for index, trace in enumerate(panel.get("traces", [])):
            role = trace.get("role")
            x, y = trace.get("x") or [], trace.get("y") or []
            label = str(trace.get("label") or trace.get("name") or "")
            single = len(x) == len(y) == 1 and (trace.get("type") == "scatter" or
                                               "markers" in trace.get("mode", ""))
            named = trace.get("type") == "scatter" and re.search(r"fit|opt|pick", label, re.I)
            axes = ["x"] if role == "vline" else ["y"] if role == "hline" else ["x", "y"] if single or named else []
            for axis in axes:
                values = x if axis == "x" else y
                for point, coordinate in enumerate(values[:1] if role in {"vline", "hline"} else values):
                    coordinate = _number(coordinate)
                    if coordinate is None:
                        continue
                    marker = {"id": f"{original_index}:{index}:{point}:{axis}", "entity": owner,
                              "axis": axis, "plotly_axis": references[original_index][axis], "role": role or "point",
                              "coordinate": coordinate, "anchor": _number(anchors.get(axis)),
                              "coordinate_scale": 1.0, "coordinate_offset": 0.0}
                    output.append(marker)
                    for secondary in panel.get("secondary_axes", []):
                        if secondary.get("axis") != axis:
                            continue
                        mapping = secondary.get("map") or {}
                        parent, forward = mapping.get("parent", []), mapping.get("forward", [])
                        if len(parent) < 2 or len(parent) != len(forward):
                            continue
                        try:
                            scale = (forward[-1] - forward[0]) / (parent[-1] - parent[0])
                            offset = forward[0] - scale * parent[0]
                            linear = all(abs(scale * p + offset - f) <= 1e-9 * max(abs(f), 1e-12)
                                         for p, f in zip(parent, forward))
                        except (TypeError, ZeroDivisionError):
                            continue
                        if linear:
                            output.append({**marker, "id": marker["id"] + ":secondary",
                                           "coordinate": scale * coordinate + offset,
                                           "anchor": None, "coordinate_scale": scale,
                                           "coordinate_offset": offset})
    for index, marker in enumerate(figure.get("markers", [])):
        if marker.get("entity") in entities and _number(marker.get("coordinate")) is not None:
            output.append({"id": str(index), "role": "point", "anchor": None,
                           "coordinate_scale": 1.0, "coordinate_offset": 0.0,
                           "plotly_axis": marker.get("axis", "x"), **marker})
    return output


def _defaults(figure):
    defaults = list(figure.get("marker_defaults") or [])
    report = figure.get("bind_report") or {}
    for decision in report.values():
        if not isinstance(decision, dict):
            continue
        defaults.extend(decision.get("marker_defaults") or [])
        if decision.get("status") == "defaulted" and decision.get("feeds_marker"):
            defaults.append(decision.get("field", "argument"))
        for record in decision.get("records", []):
            defaults.extend(record.get("marker_defaults") or [])
    return defaults


def _result_names(run, marker):
    results = getattr(run, "fit_results", None) or (_read(run, "data.json") or {}).get("fit_results", {})
    if not isinstance(results, dict):
        return []
    names = []

    def visit(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                visit(item, path + [str(key)])
        else:
            number = _number(value)
            if number is not None and abs(number - marker["coordinate"]) <= 1e-9 * max(abs(number), 1e-12):
                names.append(".".join(path))

    visit(results.get(marker["entity"], {}), [marker["entity"]])
    return names


def _candidates(entry, marker):
    if entry["old"] is None:
        return []
    c, old = marker["coordinate"], entry["old"]
    candidates = []
    for exponent in range(-12, 13):
        scale = 10.0 ** exponent
        if _agrees(c * scale, entry["value"]):
            candidates.append(("identity" if exponent == 0 else "scale", scale, 0.0))
    if _agrees(old * c, entry["value"]):
        candidates.append(("prefactor", old, 0.0))
    anchor = _number(marker.get("anchor"))
    if anchor is not None and _agrees(old + c - anchor, entry["value"]):
        candidates.append(("increment", 1.0, old - anchor))
    return [(kind, scale * marker["coordinate_scale"],
             scale * marker["coordinate_offset"] + offset) for kind, scale, offset in candidates]


def _unique(candidates):
    # Identity and scale(1), or old=1 prefactor, describe the same affine map.
    unique = []
    for candidate in candidates:
        marker, kind, scale, offset = candidate
        if not any(m["axis"] == marker["axis"] and
                   abs(s - scale) <= 1e-12 * max(abs(scale), 1e-12) and
                   abs(o - offset) <= 1e-12 * max(abs(offset), 1e-12)
                   for m, _, s, o in unique):
            unique.append(candidate)
    return unique


def learn(run, figure_payload):
    """Return a verified clickable contract, or None with a display-only reason.

    Only common (path template, axis, marker role, transform) tuples survive
    across every entity with that write. Unknown/default-derived markers fail
    closed. The figure receives the reason even when only some paths survive.
    """
    figure_payload["display_only_reason"] = ""
    entries = evidence(run)
    if not entries:
        figure_payload["display_only_reason"] = "this run wrote no state: click-to-apply off"
        return None
    defaults = _defaults(figure_payload)
    if defaults:
        figure_payload["display_only_reason"] = "defaulted fields feed a marker: " + ", ".join(map(str, defaults))
        return None
    entities = {e["entity"] for e in entries}
    points = markers(figure_payload, entities)
    groups = defaultdict(list)
    for entry in entries:
        groups[entry["template"]].append(entry)
    learned = {}
    reasons = []
    direct_paths = set()
    for template, group in groups.items():
        matches = []
        for entry in group:
            found = [(m, kind, scale, offset) for m in points if m["entity"] == entry["entity"]
                     for kind, scale, offset in _candidates(entry, m)]
            if found:
                direct_paths.add(entry["path"])
            matches.append(_unique(found))
        common = None
        for found in matches:
            signatures = {(m["axis"], m["role"], kind) for m, kind, _, _ in found}
            common = signatures if common is None else common & signatures
        selected = [[c for c in found if (c[0]["axis"], c[0]["role"], c[1]) in (common or set())]
                    for found in matches]
        if not all(len(found) == 1 for found in selected):
            reason = "ambiguous transforms" if any(len(found) > 1 for found in selected) else "no common verified marker transform"
            if any(e["old"] is None for e in group):
                reason = "missing numeric pre-update value"
            reasons.append(f"{template}: {reason}")
            continue
        for entry, found in zip(group, selected):
            learned[entry["path"]] = (entry, *found[0])
    # A half/double chain uses another independently verified write's mapping.
    seeds = list(learned.values())
    for template, group in groups.items():
        if any(e["path"] in direct_paths or e["old"] is None for e in group):
            continue
        chain_matches = []
        for entry in group:
            chains = []
            for other, marker, _, scale, offset in seeds:
                if other["entity"] != entry["entity"]:
                    continue
                for ratio in (0.5, 2.0):
                    if _agrees(other["value"] * ratio, entry["value"]):
                        chains.append((marker, "chain", scale * ratio, offset * ratio,
                                       other["template"], ratio))
            unique = _unique([c[:4] for c in chains])
            chain_matches.append([c for c in chains if c[:4] in unique])
        signatures = [{(c[0]["axis"], c[0]["role"], c[4], c[5]) for c in found}
                      for found in chain_matches]
        common = set.intersection(*signatures) if signatures else set()
        selected = [[c for c in found if (c[0]["axis"], c[0]["role"], c[4], c[5]) in common]
                    for found in chain_matches]
        if all(len(found) == 1 for found in selected):
            for entry, found in zip(group, selected):
                learned[entry["path"]] = (entry, *found[0][:4])
    targets = []
    for entry, marker, kind, scale, offset in learned.values():
        # Recompute from the displayed parent coordinate, independently of names.
        parent = (marker["coordinate"] - marker["coordinate_offset"]) / marker["coordinate_scale"] if marker["coordinate_scale"] else None
        if parent is None or not _agrees(parent * scale + offset, entry["value"]):
            continue
        tier = entry["tier"]
        tier_label = "proposed, not applied" if tier == "proposed" else "applied"
        target = {"path": entry["path"], "path_template": entry["template"], "axis": marker["axis"],
                  "plotly_axis": marker["plotly_axis"], "entity": entry["entity"],
                  "scale": scale, "offset": offset, "transform_kind": kind,
                  "marker_role": marker["role"],
                  "evidence_tier": tier, "evidence_label": tier_label, "marker_id": marker["id"],
                  "verified_on": str(getattr(run, "uid", None) or getattr(run, "folder_path", run)),
                  "provenance": {"formula": f"{kind}: clicked * {scale:g} + {offset:g}; {tier_label}; fit marker verified on this run",
                                 "result_names": _result_names(run, marker),
                                 "inputs": [{"label": "pre-update value from evidence", "path": entry["path"],
                                             "frozen_value": entry["old"]}]}}
        if kind == "prefactor":
            target["transform"] = {"type": "prefactor", "old": entry["old"],
                                   "coordinate_scale": marker["coordinate_scale"],
                                   "coordinate_offset": marker["coordinate_offset"]}
        # Repeated panels may show the same verified mapping. Bind each target
        # to its actual Plotly axis, so a click cannot write another panel.
        seen_axes = set()
        for point in points:
            if point["entity"] != entry["entity"] or point["axis"] != marker["axis"] or point["role"] != marker["role"]:
                continue
            parent_value = (point["coordinate"] - point["coordinate_offset"]) / point["coordinate_scale"] if point["coordinate_scale"] else None
            if parent_value is None or not _agrees(parent_value * scale + offset, entry["value"]):
                continue
            if point["plotly_axis"] in seen_axes:
                continue
            seen_axes.add(point["plotly_axis"])
            targets.append({**target, "plotly_axis": point["plotly_axis"], "marker_id": point["id"]})
    surviving = {t["path_template"] for t in targets}
    reasons = [reason for reason in reasons if reason.split(": ", 1)[0] not in surviving]
    figure_payload["display_only_reason"] = "; ".join(reasons) if reasons else ""
    if not targets:
        figure_payload["display_only_reason"] = figure_payload["display_only_reason"] or "no entity-owned fit marker"
        return None
    tier = entries[0]["tier"]
    label = f"Learned from this run's {len({t['path'] for t in targets})} state writes; the fit marker reproduces them exactly"
    label += " (proposed, not applied)" if tier == "proposed" else " (applied)"
    for target in targets:
        target["provenance"]["formula"] += "; " + label
        names = target["provenance"]["result_names"]
        if names:
            target["provenance"]["formula"] += "; marker matches " + ", ".join(names)
    return {"axis": targets[0]["axis"], "qubit": next(iter(entities)) if len(entities) == 1 else None,
            "label": label,
            "targets": targets, "learned": True, "verified_against": "patches" if tier == "applied" else "proposals",
            "evidence_tier": tier, "n_verified": len({t["path"] for t in targets})}


def gate(bundle, spec, analysis_fingerprint):
    """Check a recipe formula against its marker and analysis revision."""
    from .recipe_gate import gate as verify_recipe
    return verify_recipe(bundle, spec, analysis_fingerprint)
