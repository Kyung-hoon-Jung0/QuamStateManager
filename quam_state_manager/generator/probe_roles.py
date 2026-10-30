"""Role manifest of the quam classes a selected env offers (lab additions, phase 1).

Runs INSIDE the user's selected interpreter. Stdlib only at import time; the
quam imports happen per module, each under a timeout, and a module that fails
or hangs is REPORTED, never fatal.

Candidate classes come from three sources only:

1. curated official homes (``OFFICIAL_HOMES``),
2. the modules of classes named by ``__class__`` in a given state.json,
3. an explicit user module list.

A lab package is NEVER walked (``pkgutil.walk_packages``): lab trees hold
executable scripts next to their classes, and importing every module runs
them (a walk was measured to hang for minutes).

Input JSON (``--spec``): ``{"state_path": str|null, "classes": [str],
"modules": [str], "timeout_s": float}``. Output JSON (``--out``)::

    {"status", "mode": "roles", "versions": {...}, "modules": {name: "ok"|"error: ..."|"timeout"},
     "classes": {"<module>.<Name>": {"role", "origin", "bases", "mro", "fields": {name: {
         "raw", "default_repr", "has_default", "kind", "optional", "targets"}}}},
     "error"}
"""
from __future__ import annotations

import argparse
import dataclasses
import importlib
import importlib.util
import inspect
import json
import os
import sys
import threading
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_common import library_versions as _library_versions  # noqa: E402

_FIELD_KINDS_PATH = Path(__file__).resolve().parent.parent / "core" / "field_kinds.py"

OFFICIAL_HOMES = (
    "quam.core.quam_classes",
    "quam.core.macro.quam_macro",
    "quam.components.pulses",
    "quam.components.channels",
    "quam.components.hardware",
    "quam.components.octave",
    "quam.components.ports.analog_outputs",
    "quam.components.ports.analog_inputs",
    "quam.components.ports.digital_outputs",
    "quam.components.ports.digital_inputs",
    "quam.components.ports.ports_containers",
    "quam.components.macro.qubit_macros",
    "quam.components.macro.qubit_pair_macros",
    "quam.components.quantum_components.qubit",
    "quam.components.quantum_components.qubit_pair",
    "quam_builder.architecture.superconducting.components.pulses",
    "quam_builder.architecture.superconducting.components.flux_line",
    "quam_builder.architecture.superconducting.components.readout_resonator",
    "quam_builder.architecture.superconducting.components.tunable_coupler",
    "quam_builder.architecture.superconducting.components.xy_drive",
    "quam_builder.architecture.superconducting.components.cross_resonance",
    "quam_builder.architecture.superconducting.components.zz_drive",
    "quam_builder.architecture.superconducting.components.twpa",
    "quam_builder.architecture.superconducting.custom_gates.single_qubit_gates",
    "quam_builder.architecture.superconducting.custom_gates.flux_tunable_transmon_pair.two_qubit_gates",
    "quam_builder.architecture.superconducting.custom_gates.fixed_transmon_pair.two_qubit_gates",
    "quam_builder.architecture.superconducting.qubit.base_transmon",
    "quam_builder.architecture.superconducting.qubit.fixed_frequency_transmon",
    "quam_builder.architecture.superconducting.qubit.flux_tunable_transmon",
    "quam_builder.architecture.superconducting.qubit_pair.fixed_frequency_transmon_pair",
    "quam_builder.architecture.superconducting.qubit_pair.flux_tunable_transmon_pair",
    "quam_builder.architecture.superconducting.qpu.base_quam",
    "quam_builder.architecture.superconducting.qpu.fixed_frequency_quam",
    "quam_builder.architecture.superconducting.qpu.flux_tunable_quam",
)
OFFICIAL_PACKAGES = ("quam", "quam_builder", "qualang_tools")

# role -> base class path; checked in this order (first match wins)
ROLE_BASES = (
    ("root", "quam.core.quam_classes.QuamRoot"),
    ("macro_pair", "quam.components.macro.qubit_pair_macros.QubitPairMacro"),
    ("macro_qubit", "quam.components.macro.qubit_macros.QubitMacro"),
    ("pulse", "quam.components.pulses.Pulse"),
    ("pair", "quam.components.quantum_components.qubit_pair.QubitPair"),
    ("qubit", "quam.components.quantum_components.qubit.Qubit"),
    ("twpa", "quam_builder.architecture.superconducting.components.twpa.TWPA"),
)
_CHANNEL_BASE = "quam.components.channels.Channel"
_COMPONENT_BASE = "quam.core.quam_classes.QuamComponent"


def _load_field_kinds():
    spec = importlib.util.spec_from_file_location("_sm_field_kinds", _FIELD_KINDS_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def classes_in_state(state) -> list[str]:
    """Every distinct ``__class__`` string in a state dict (document order)."""
    seen: dict[str, None] = {}
    stack = [state]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            c = node.get("__class__")
            if isinstance(c, str) and "." in c:
                seen.setdefault(c, None)
            stack.extend(reversed(list(node.values())))
        elif isinstance(node, list):
            stack.extend(reversed(node))
    return list(seen)


def import_with_timeout(name: str, timeout_s: float) -> str:
    """Import *name* in a daemon thread; ``ok`` / ``error: ...`` / ``timeout``."""
    box: dict = {}

    def run():
        try:
            importlib.import_module(name)
            box["r"] = "ok"
        except BaseException as exc:  # noqa: BLE001 -- includes SystemExit from a script
            box["r"] = f"error: {type(exc).__name__}: {exc}"

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return "timeout"
    return box.get("r", "error: no result")


def _resolve(path: str):
    mod, _, name = path.rpartition(".")
    m = sys.modules.get(mod)
    return getattr(m, name, None) if m is not None else None


def _path_of(cls) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def role_of(cls, bases: dict) -> str:
    for role, path in ROLE_BASES:
        b = bases.get(path)
        if b is not None and issubclass(cls, b):
            return role
    return "other"


def origin_of(cls) -> str:
    top = cls.__module__.split(".")[0]
    return "official" if top in OFFICIAL_PACKAGES else "lab"


def dump_fields(cls, fk, names: dict) -> dict:
    fields = getattr(cls, "__dataclass_fields__", None) or {}
    out = {}
    for f in fields.values():
        if getattr(f, "_field_type", None) is not dataclasses._FIELD:  # type: ignore[attr-defined]
            continue
        raw = f.type if isinstance(f.type, str) else str(f.type)
        has_default = f.default is not dataclasses.MISSING
        has_factory = f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        default = f.default if has_default else None
        info = fk.classify(raw, default, has_default=has_default,
                           pulse_names=names["pulse"], channel_names=names["channel"],
                           component_names=names["component"])
        out[f.name] = {"raw": raw, "has_default": bool(has_default or has_factory),
                       "default_repr": repr(default) if has_default else (
                           "<factory>" if has_factory else None),
                       **info}
    return out


def probe(spec: dict) -> dict:
    timeout_s = float(spec.get("timeout_s") or 30)
    wanted = [c for c in (spec.get("classes") or []) if isinstance(c, str)]
    if spec.get("state_path"):
        state = json.loads(Path(spec["state_path"]).read_text(encoding="utf-8"))
        wanted += classes_in_state(state)
    wanted = list(dict.fromkeys(wanted))
    user_mods = [m for m in (spec.get("modules") or []) if isinstance(m, str)]
    state_mods = [c.rpartition(".")[0] for c in wanted]
    modules: dict[str, str] = {}
    for name in list(dict.fromkeys(list(OFFICIAL_HOMES) + state_mods + user_mods)):
        modules[name] = import_with_timeout(name, timeout_s)

    bases = {p: _resolve(p) for _, p in ROLE_BASES}
    bases[_CHANNEL_BASE] = _resolve(_CHANNEL_BASE)
    bases[_COMPONENT_BASE] = _resolve(_COMPONENT_BASE)
    comp = bases[_COMPONENT_BASE]

    found: dict[str, type] = {}
    for name, status in modules.items():
        if status != "ok":
            continue
        m = sys.modules.get(name)
        for _, obj in inspect.getmembers(m, inspect.isclass):
            if obj.__module__ != name:
                continue
            if comp is not None and not issubclass(obj, comp) and not (
                    bases.get(ROLE_BASES[0][1]) and issubclass(obj, bases[ROLE_BASES[0][1]])):
                continue
            found[_path_of(obj)] = obj
    missing = []
    for c in wanted:
        obj = _resolve(c)
        if isinstance(obj, type):
            found.setdefault(c, obj)
        else:
            missing.append(c)

    names = {"pulse": [], "channel": [], "component": []}
    pb, cb = bases[ROLE_BASES[3][1]], bases[_CHANNEL_BASE]
    for obj in found.values():
        if pb is not None and issubclass(obj, pb):
            names["pulse"].append(obj.__name__)
        elif cb is not None and issubclass(obj, cb):
            names["channel"].append(obj.__name__)
        else:
            names["component"].append(obj.__name__)

    fk = _load_field_kinds()
    classes = {}
    for path, obj in sorted(found.items()):
        try:
            classes[path] = {
                "role": role_of(obj, bases), "origin": origin_of(obj),
                "is_channel": bool(cb is not None and issubclass(obj, cb)),
                "abstract": bool(inspect.isabstract(obj)),
                "bases": [_path_of(b) for b in obj.__bases__],
                "mro": [_path_of(b) for b in obj.__mro__[1:] if b is not object],
                "fields": dump_fields(obj, fk, names),
            }
        except Exception as exc:  # noqa: BLE001
            classes[path] = {"role": "other", "origin": origin_of(obj),
                             "error": f"{type(exc).__name__}: {exc}"}
    return {"versions": _library_versions(), "modules": modules,
            "classes": classes, "missing_classes": missing}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    result = {"status": "error", "mode": "roles", "python": sys.executable, "error": None}
    try:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        result.update(probe(spec))
        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    Path(args.out).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"status": result["status"], "result_file": args.out}))
    sys.stdout.flush()
    os._exit(0 if result["status"] == "ok" else 1)   # a hung import thread must not keep us alive


if __name__ == "__main__":
    main()
