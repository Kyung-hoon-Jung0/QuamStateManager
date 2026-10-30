"""Evidence for environment changes, restricted to objects in the given state."""
from __future__ import annotations

import re
import json

from quam_state_manager.core import state_env_baseline as baseline
from quam_state_manager.core.state_env_validate import analyze_state

OLDER_REFERENCE = "not checked: reference recorded by an older SM"
CASE_NAMES = {"1": "new optional fields", "2": "new required fields", "3": "fields gone / renamed",
              "4": "classes that no longer import", "5": "override signatures", "6": "waveforms"}
SIGNATURE_HEADING = ("May fail at run time \u2014 signature differs "
                     "(heuristic; the call site is not checked)")
WAVEFORM_SCOPE = "samples written to the QM config \u2014 not measured hardware timing"
NO_REFERENCE = "No reference from before this change \u2014 waveforms not compared."


def objects(state, path=""):
    """Yield every explicit class instance, including instances inside lists."""
    if isinstance(state, dict):
        if isinstance(state.get("__class__"), str):
            yield path, state
        for key, value in state.items():
            yield from objects(value, f"{path}.{key}" if path else key)
    elif isinstance(state, list):
        for index, value in enumerate(state):
            yield from objects(value, f"{path}.{index}" if path else str(index))


def _fields(entry):
    result = {}
    for name, field in (entry.get("fields") or {}).items():
        result[name] = {"type": field.get("t", field.get("type")),
                        "optional": field.get("o", field.get("optional", False)),
                        "has_default": field.get("d", field.get("has_default")),
                        "defining_class": field.get("defining_class"),
                        "default": field.get("default"), "doc": field.get("doc")}
    return result


def _case(number, rows=None, message=None):
    rows = rows or []
    return {"case": number, "rows": rows, "status": "found" if rows else
            "not_checked" if message else "none_found", "message": message or
            (f"{len(rows)} found" if rows else "none found")}


def _signature_shape(signature):
    return signature.get("parameters") if isinstance(signature, dict) else signature


def non_waveform_diff(old, new):
    """Compare values of integration weights and port filter/crosstalk blocks."""
    def relevant(config):
        result = {"integration_weights": config.get("integration_weights", {})}

        def walk(node, path):
            if isinstance(node, dict):
                for name, value in node.items():
                    if name in ("filter", "crosstalk"):
                        result[f"{path}.{name}"] = value
                    else:
                        walk(value, f"{path}.{name}")
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk(value, f"{path}.{index}")

        walk(config.get("controllers", {}), "controllers")
        return result

    before, after = relevant(old), relevant(new)
    return [{"path": path, "old": before.get(path), "new": after.get(path)}
            for path in sorted(before.keys() | after.keys()) if before.get(path) != after.get(path)]


def report(state: dict, current: dict | None, reference: dict | None = None, *,
           catalog: dict | None = None, event: dict | None = None,
           old_config: dict | None = None, new_config: dict | None = None) -> dict:
    """Return case verdicts without importing classes, mutating state, or spawning."""
    instances = list(objects(state))
    used = list(dict.fromkeys(obj["__class__"] for _, obj in instances))
    current = current or {}
    selected = baseline.select_classes(current, used)
    classes = selected.get("classes") or {}
    previous = (baseline.select_classes(reference or {}, used).get("classes") or {})
    unprobed = [path for path in used if path not in classes]
    cases = {str(n): _case(n) for n in range(1, 7)}
    result = {"cases": cases, "classes_not_probed": len(unprobed), "event": event or {},
              "footer": "Not checked: run-time behaviour of macros, hardware timing, "
                        f"classes not probed ({len(unprobed)})",
              "waveform_scope": WAVEFORM_SCOPE, "non_waveform": [], "fyi": [], "banner": False}
    cases["6"] = _case(6, message=NO_REFERENCE)
    if not current.get("classes") or current.get("ok") is False:
        message = "could not check: " + (current.get("error") or "environment probe unavailable")
        for n in range(1, 6):
            cases[str(n)] = _case(n, message=message)
        return result
    validator_manifest = {**selected, "classes": {
        path: {**entry, "fields": _fields(entry) if entry.get("fields") is not None else None}
        for path, entry in classes.items()}}
    analysis = analyze_state(state, validator_manifest, uncapped=True)
    findings = analysis["findings"]
    missing = []
    unknown = []
    for row in findings:
        path = row.get("class")
        field = row.get("field")
        before = _fields(previous.get(path, {})).get(field)
        after = _fields(classes.get(path, {})).get(field)
        if row["kind"] == "missing_required":
            missing.append({**row, "paths": row["example_paths"],
                            "default_removed": bool(before and before["has_default"] and after
                                                    and not after["has_default"])})
        if row["kind"] == "unknown_field":
            owner = before.get("defining_class") if before else None
            old_fields = _fields(previous.get(owner, {})) if owner else {}
            new_fields = _fields(classes.get(owner, {})) if owner else {}
            candidates = [{"field": name, **spec} for name, spec in new_fields.items()
                          if name not in old_fields and spec.get("defining_class") == owner]
            unknown.append({**row, "paths": row["example_paths"],
                "was_declared": before is not None, "defining_class": owner,
                "candidates": candidates, "heuristic": True,
                "label": "rename candidates (heuristic)" if candidates else
                         "field removed" if before else "unknown field"})
    cases["2"] = _case(2, missing)
    cases["3"] = _case(3, unknown)
    imports = []
    for path in used:
        entry = classes.get(path)
        if entry is None or entry.get("importable"):
            continue
        leaf = path.rsplit(".", 1)[-1]
        match = re.search(r"cannot import name ['\"]([^'\"]+)['\"]", entry.get("error") or "")
        leaves = {leaf, match.group(1)} if match else {leaf}
        candidates = [name for name, candidate in (catalog or {}).items()
                      if name.rsplit(".", 1)[-1] in leaves and candidate.get("importable")
                      and name != path]
        imports.append({"class": path, "error": entry.get("error"),
                        "frames": entry.get("error_frames") or [],
                        "candidates": sorted(candidates), "candidate_source": "cached catalog only",
                        "candidate_status": "checked" if catalog is not None else "could not check: catalog not cached",
                        "paths": [p for p, obj in instances if obj["__class__"] == path]})
    cases["4"] = _case(4, imports)
    reference_current = (reference and reference.get("version") == 2 and
                         reference.get("schema_format") == current.get("schema_format") == 4)
    if not reference_current:
        cases["1"] = _case(1, message=OLDER_REFERENCE if reference else "not checked: no schema reference")
        cases["5"] = _case(5, message=OLDER_REFERENCE if reference else "not checked: no reference")
    else:
        additions = []
        signatures = []
        unreferenced = []
        rename_candidates = {(row["defining_class"], c["field"])
                             for row in unknown for c in row["candidates"]}
        for path, entry in classes.items():
            old_entry = previous.get(path)
            if old_entry is None:
                continue
            before, after = _fields(old_entry), _fields(entry)
            for field, spec in after.items():
                if (field not in before and spec.get("has_default") and spec.get("defining_class") == path
                        and (path, field) not in rename_candidates):
                    additions.append({"class": path, "field": field, **spec})
            if entry.get("origin") != "lab":
                continue
            for method, sig in (entry.get("signatures") or {}).items():
                if sig.get("error"):
                    continue
                old_sig = (old_entry.get("signatures") or {}).get(method) or {}
                reference_parent = old_sig.get("parent")
                if reference_parent is None or old_sig.get("error"):
                    unreferenced.append(f"{path}.{method}")
                    continue
                own, parent = _signature_shape(sig.get("own")), _signature_shape(sig.get("parent"))
                if parent != _signature_shape(reference_parent):
                    # the upgrade changed the call contract under the lab's override
                    signatures.append({"class": path, "method": method, **sig,
                        "reference_parent": reference_parent,
                        "label": "parent signature changed since the reference", "heuristic": True})
                elif own != parent:
                    result["fyi"].append(f"lab override signature differs from parent (unchanged since "
                                         f"reference): {path}.{method}")
        cases["1"] = _case(1, additions)
        cases["5"] = _case(5, signatures)
        if unreferenced and not signatures:
            cases["5"] = _case(5, message=f"not checked: no reference signature for {len(unreferenced)} methods")
        if any("bases" not in e or "signatures" not in e for e in previous.values()):
            cases["1"] = _case(1, message=OLDER_REFERENCE)
            cases["5"] = _case(5, message=OLDER_REFERENCE)
        lab_changed = sum(1 for path, entry in classes.items() if entry.get("origin") == "lab"
                          and previous.get(path) != entry)
        if lab_changed:
            result["fyi"].append(f"your own classes changed: {lab_changed}")
    schema_unknown = [path for path, entry in classes.items()
                      if path in used and entry.get("importable") and entry.get("fields") is None]
    if schema_unknown:
        for n in (1, 2, 3):
            case = cases[str(n)]
            if not case["rows"]:
                cases[str(n)] = _case(n, message="could not check: class field introspection failed")
    signature_unknown = any(sig.get("error") for entry in classes.values()
                            for sig in (entry.get("signatures") or {}).values())
    if signature_unknown and not cases["5"]["rows"]:
        cases["5"] = _case(5, message="could not check: method signature introspection failed")
    if unprobed:
        for n in range(1, 6):
            if cases[str(n)]["status"] == "none_found":
                cases[str(n)] = _case(n, message=f"could not check: {len(unprobed)} classes not probed")
    if any(cases[str(n)]["rows"] for n in (2, 3, 4)):
        cases["6"] = _case(6, message="blocked by the items above")
        result["banner"] = bool(event and event.get("official_changed") and not event.get("first"))
    if old_config is not None and new_config is not None:
        result["non_waveform"] = non_waveform_diff(old_config, new_config)
    result["findings_signature"] = baseline._hash({k: v["rows"] for k, v in cases.items()})
    return result


def verdict_lines(result: dict) -> list[str]:
    """Stable case lines shared by offline drivers and presentation code."""
    return [f"VERDICT case {n}: {case['status']} - {case['message']}"
            for n, case in result["cases"].items()]


def text_report(result: dict, python_path: str, versions: dict) -> str:
    """Plain-text evidence shared by Copy as text and the command line."""
    lines = [f"Checked in: {python_path} ({json.dumps(versions, sort_keys=True)})",
             result.get("event", {}).get("direction") or "", WAVEFORM_SCOPE]
    for line, case in zip(verdict_lines(result), result["cases"].values()):
        lines.append(line)
        lines.extend(json.dumps(row, sort_keys=True, ensure_ascii=True) for row in case["rows"])
    lines.extend(json.dumps(row, sort_keys=True, ensure_ascii=True) for row in result.get("non_waveform", []))
    lines.extend(result.get("fyi", []))
    lines.append(result["footer"])
    return "\n".join(lines)
