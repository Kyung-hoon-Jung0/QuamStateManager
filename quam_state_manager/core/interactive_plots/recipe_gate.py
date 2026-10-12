"""Per-run recipe proof and analysis fingerprint retirement."""
from __future__ import annotations

import copy
import hashlib
import math
import sys
import threading
from collections import OrderedDict
from pathlib import Path

from . import learn_click

_VERIFIED = OrderedDict()
_LOCK = threading.Lock()


def analysis_fingerprint(recipe):
    """Hash the recipe and shared arithmetic helpers, including formula edits."""
    root = Path(__file__).parent
    paths = [Path(recipe.__file__), root / "recipes" / "base.py"]
    paths.extend(root / name for name in ("contracts.py", "models.py", "fitcheck.py", "plotbuild.py"))
    pending, visited = [recipe], set()
    while pending:
        module = pending.pop()
        filename = getattr(module, "__file__", None)
        if filename is None or filename in visited:
            continue
        visited.add(filename)
        paths.append(Path(filename))
        for value in vars(module).values():
            name = getattr(value, "__module__", "") or getattr(value, "__name__", "")
            if name.startswith("quam_state_manager.core.interactive_plots."):
                dependency = sys.modules.get(name)
                if dependency is not None:
                    pending.append(dependency)
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        try:
            digest.update(path.name.encode("utf-8"))
            digest.update(path.read_bytes())
        except OSError:
            digest.update(b"missing")
    return digest.hexdigest()


def _recipe_markers(spec, entity):
    fig = spec.figure or {}
    explicit = getattr(spec, "verify_points", None) or fig.get("verify_points") or []
    markers = []
    for index, point in enumerate(explicit):
        if "coordinate" in point:
            markers.append({"id": f"verify:{index}", "entity": entity, **point})
        else:
            for axis in ("x", "y"):
                if axis in point:
                    markers.append({"id": f"verify:{index}:{axis}", "entity": entity, "axis": axis,
                                    "coordinate": point[axis], "path": point.get("path")})
    for index, shape in enumerate(fig.get("layout", {}).get("shapes", [])):
        for axis in ("x", "y"):
            value = shape.get(axis + "0")
            ref = shape.get(axis + "ref", axis)
            if shape.get("type", "line") == "line" and value is not None and value == shape.get(axis + "1") and ref != "paper" and "domain" not in ref:
                markers.append({"id": f"shape:{index}:{axis}", "entity": entity, "axis": axis,
                                "plotly_axis": ref, "coordinate": value})
    for index, trace in enumerate(fig.get("data", [])):
        name = str(trace.get("name", "")).lower()
        if not name.startswith(("fit", "opt", "pick")):
            continue
        x, y = trace.get("x", []), trace.get("y", [])
        if len(x) != 1 or len(y) != 1:
            continue
        for axis, value in (("x", x[0]), ("y", y[0])):
            markers.append({"id": f"trace:{index}:{axis}", "entity": entity, "axis": axis,
                            "plotly_axis": trace.get(axis + "axis", axis), "coordinate": value})
    return markers


def _evaluate(target, coordinate):
    c = learn_click._number(coordinate)
    if c is None:
        return None
    transform = target.get("transform")
    try:
        if not transform:
            return c * target.get("scale", 1) + target.get("offset", 0)
        kind = transform.get("type")
        if kind == "prefactor":
            return transform["old"] * (c * transform.get("coordinate_scale", 1) + transform.get("coordinate_offset", 0))
        if kind == "ceil4":
            return math.ceil(c / 4) * 4 + transform.get("add", 0)
        if kind == "wrap01":
            return (transform["a"] * c + transform["b"]) % 1
        if kind == "dbm_to_amp":
            return transform.get("scale", 1) * 10 ** ((c - transform["ref_dbm"]) / 20)
        if kind == "dbm_gridfs":
            fs = min(max(math.ceil(c - 20 * math.log10(transform["max_amp"])), transform["fs_min"]), transform["fs_max"])
            return fs if transform["part"] == "fs" else 10 ** ((c - fs) / 20)
    except (TypeError, ValueError, KeyError, OverflowError):
        return None
    return None


def gate(bundle, spec, analysis_fingerprint):
    """Verify the recipe's own formula before accepting any learned replacement."""
    if spec.clickable is None:
        return spec
    spec = copy.copy(spec)
    spec.clickable = copy.deepcopy(spec.clickable)
    fig = spec.figure or {}
    entries = learn_click.evidence(bundle.run, bundle.node_meta or None)
    key = (str(getattr(bundle.run, "folder_path", "")), spec.key)
    with _LOCK:
        previous = _VERIFIED.get(key)
    if previous is not None and previous != analysis_fingerprint:
        spec.clickable = None
        spec.reason = "recipe analysis fingerprint changed since verification: click disabled"
        return spec
    if learn_click._defaults(fig):
        spec.clickable = None
        spec.reason = "defaulted fields feed a marker: recipe click disabled"
        return spec
    if not entries:
        note = "recipe formula, not verified on this run (the node wrote no state)"
        spec.reason = note
        spec.clickable["verification_note"] = note
        for target in spec.clickable.get("targets", []):
            provenance = target.setdefault("provenance", {"formula": "", "inputs": []})
            provenance["formula"] = "; ".join(filter(None, [provenance.get("formula"), note]))
        return spec
    entity = spec.clickable.get("qubit") or spec.key.split("::")[-1]
    points = _recipe_markers(spec, entity)
    verified_targets = []
    for target in spec.clickable.get("targets", []):
        path = target.get("path", "").replace("{q}", entity).replace("{pair}", entity).replace("{name}", entity)
        writes = [entry for entry in entries if entry["path"] == path]
        axis = target.get("axis", spec.clickable.get("axis", "x"))
        matches = [point for point in points if point.get("axis") == axis and point.get("path") in (None, path, target.get("path"))]
        proof = next((point for point in matches if writes and all(entry["old"] is not None and
                      learn_click._agrees(_evaluate(target, point["coordinate"]), entry["value"]) for entry in writes)), None)
        if proof is None:
            spec.clickable = None
            spec.reason = "the node's write differs from the recipe's formula (analysis changed or no proof marker): click disabled"
            return spec
        tier = writes[0]["tier"]
        target.update(evidence_tier=tier, evidence_label="proposed, not applied" if tier == "proposed" else "applied",
                      marker_id=proof["id"], verified_on=str(getattr(bundle.run, "uid", None) or key[0]))
        label = target["evidence_label"]
        provenance = target.setdefault("provenance", {"formula": "", "inputs": []})
        provenance["formula"] = "; ".join(filter(None, [provenance.get("formula"), f"verified on this run ({label})"]))
        verified_targets.append(target)
    if not verified_targets:
        spec.clickable = None
        spec.reason = "recipe has no verifiable targets: click disabled"
        return spec
    spec.clickable.update(analysis_fingerprint=analysis_fingerprint, n_verified=len(verified_targets),
                          verified_against="patches" if entries[0]["tier"] == "applied" else "proposals")
    with _LOCK:
        _VERIFIED[key] = analysis_fingerprint
        _VERIFIED.move_to_end(key)
        while len(_VERIFIED) > 256:
            _VERIFIED.popitem(last=False)
    learned_figure = {"markers": points, "bind_report": fig.get("bind_report", {}),
                      "marker_defaults": fig.get("marker_defaults", [])}
    learned = learn_click.learn(bundle.run, learned_figure)
    if learned:
        learned["analysis_fingerprint"] = analysis_fingerprint
        spec.clickable = learned
    return spec
