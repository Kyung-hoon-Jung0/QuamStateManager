"""Discover the environment's package or check frozen class bytes in a copy."""
from __future__ import annotations

import argparse
import ast
import base64
import hashlib
import importlib
import json
import re
import site
import sys
from pathlib import Path


def _load_chip(req):
    """Load the chip copy with its root class; the error text when it fails."""
    root_class = req.get("root_class") or "quam_config.Quam"
    module, name = root_class.rsplit(".", 1)
    try:
        cls = getattr(importlib.import_module(module), name)
        cls.load(req["chip"])
    except BaseException as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


XY_ROLES = {"qubit_control": "control", "qubit_target": "target"}


def _xy_operations(source):
    """``{role: [operation name, ...]}`` for every ``<pair>.qubit_control.xy.play("name")``
    and ``<pair>.qubit_target.xy.play("name")`` call in *source*. The slot check's
    stand-in qubits get these operations, so a class that plays xy operations
    around the gate is judged on its gate slots, not on this chip's operations."""
    found = {}
    for node in ast.walk(ast.parse(source)):
        func = getattr(node, "func", None)
        if not (isinstance(node, ast.Call) and isinstance(func, ast.Attribute) and func.attr == "play"):
            continue
        xy = func.value
        qubit = xy.value if isinstance(xy, ast.Attribute) and xy.attr == "xy" else None
        role = XY_ROLES.get(qubit.attr) if isinstance(qubit, ast.Attribute) else None
        name = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        if role and isinstance(name, str) and name not in found.setdefault(role, []):
            found[role].append(name)
    return found


# What a class says when it asks a stand-in xy channel for an operation the
# reader above did not hand over: the template's own guard sentence, or the
# channel's lookup error when the guard is gone.
XY_ASKED = (re.compile(r"Sequence step (?P<number>\d+) (?P<where>before|after) the gate plays '(?P<operation>[^']*)', "
                       r"which the (?P<role>control|target) qubit xy channel lacks"),
            re.compile(r"Operation '(?P<operation>[^']*)' not found in channel '(?P<role>control|target)_xy'"))
XY_UNREAD = ("The save check could not tie the xy operation {operation!r} that the class asks of the {role} "
             "qubit{place} to a quoted name in a pair.qubit_control.xy.play(\"name\") or "
             "pair.qubit_target.xy.play(\"name\") call, so the class file was not saved. Write each xy "
             "operation of the class as a quoted name directly in such a call. The check ran on stand-in "
             "qubits and did not look at the chip.")


def _xy_unread(error, line):
    """The sentence for a failed check whose class asked a stand-in xy channel
    for an operation it was not given, or None for any other failure. The
    stand-in has only the names ``_xy_operations`` read, so this failure says
    what the check could not read and nothing about a real qubit."""
    for pattern in XY_ASKED:
        match = pattern.search(str(error))
        if match:
            found = match.groupdict()
            place = [f"sequence step {found['number']} {found['where']} the gate"] if found.get("number") else []
            place += [f"line {line}"] if line else []
            return XY_UNREAD.format(operation=found["operation"], role=found["role"],
                                    place=" (" + ", ".join(place) + ")" if place else "")
    return None


def _check_slot(req):
    """A slot gate class: it imports and every declared slot plays once.
    No spectators; the stand-in qubits get the xy operations the class names
    as quoted names in direct xy play calls."""
    from run_slot_gate_check import check
    root = Path(req["copy_root"])
    source = base64.b64decode(req["source"], validate=True)
    draft = root / "quam_config" / req["filename"]
    draft.write_bytes(source)
    sys.path.insert(0, str(root))
    # Import the public module too: package imports and relative imports matter.
    mod = importlib.import_module("quam_config." + draft.stem)
    getattr(mod, req["class_name"])
    found = check({"file": str(draft), "class_name": req["class_name"], "spectators": {},
                   "xy_operations": _xy_operations(source)})
    result = {"ok": False, "sha256": hashlib.sha256(source).hexdigest(), "proportional": None,
              "slots_declared": found.get("slots_declared", []),
              "slots_played": found.get("slots_played", []), "error": found.get("error")}
    if not found.get("ok") or not found.get("imports", {}).get("ok"):
        unread = _xy_unread(found.get("error"), found.get("line"))
        if unread:
            result["error"] = unread
            return result
        result["error"] = "The slot gate class check failed: " + str(found.get("error"))
        if found.get("line"):
            result["error"] += f" (line {found['line']})"
        return result
    if not result["slots_declared"] or sorted(result["slots_played"]) != sorted(result["slots_declared"]):
        result["error"] = ("The slot gate class must play each declared slot once: declared "
                           f"{result['slots_declared']}, played {result['slots_played']}.")
        return result
    result.update(ok=True, error=None)
    if req.get("chip"):
        error = _load_chip(req)
        if error:
            result.update(ok=False, chip_error=error,
                          error="The chip copy does not load with the draft class: " + error)
        else:
            result["chip_loaded"] = True
    return result


def run(req):
    if req.get("action") == "discover":
        package = importlib.import_module("quam_config")
        return {"ok": True, "folder": str(Path(package.__file__).resolve().parent),
                "site_packages": site.getsitepackages() + [site.getusersitepackages()]}
    if req.get("action") == "load":
        # The copy as it is, before the draft is written into it.
        sys.path.insert(0, str(Path(req["copy_root"])))
        error = _load_chip(req)
        return {"ok": error is None, "chip_loaded": error is None, "error": error}
    if req.get("action") == "check_slot":
        return _check_slot(req)
    from run_pulse_draft import run as preview
    root = Path(req["copy_root"])
    source = base64.b64decode(req["source"], validate=True)
    draft = root / "quam_config" / req["filename"]
    draft.write_bytes(source)
    sys.path.insert(0, str(root))
    # Import the public module too: package imports and relative imports matter.
    mod = importlib.import_module("quam_config." + draft.stem)
    getattr(mod, req["class_name"])
    result = preview({"draft": str(draft), "class_name": req["class_name"]})
    if result.get("ok") and not any(f["name"] == "amplitude" for f in result["fields"]):
        result.update(ok=False, error="The pulse class needs an amplitude field.")
    if result.get("ok") and req.get("chip"):
        error = _load_chip(req)
        if error:
            result.update(ok=False, chip_error=error,
                          error="The chip copy does not load with the draft class: " + error)
        else:
            result["chip_loaded"] = True
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="inp", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    try:
        result = run(json.loads(Path(args.inp).read_text(encoding="utf-8")))
    except BaseException as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(args.out).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
