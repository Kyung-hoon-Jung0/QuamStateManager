"""Dump quam/quam-builder class field schemas for a chip's ``__class__`` set.

Runs under the *external* interpreter the user picked (like
``probe_capabilities.py``), so at import time it uses ONLY the Python standard
library — the heavy QM imports happen inside :func:`dump_schemas`, never at
module load.

Input (``--classes`` JSON file, written by the SM side)::

    {"classes": ["quam_builder...CZGate", ...], "pulse_roster": true}

Output (``--out`` JSON envelope) — the ONE manifest document shared by the
type-policy layer and the state↔env validator (their contract test pins it)::

    {"status": "ok", "mode": "state_schema", "python": "3.11.9",
     "versions": {"quam": "0.6.0", ...},
     "classes": {"<requested path>": {
         "importable": true, "canonical": "<defining module.QualName>",
         "bases": ["quam..."], "error": null, "is_dataclass": true,
         "fields": {"<name>": {"type": <TypeSpec>, "optional": false,
                               "has_default": true, "default": 0.0,
                               "default_repr": null,
                               "default_is_reference": false,
                               "raw": "float"}} | null}},
     "pulse_roster": {"<LeafName>": {"homes": [...], "canonical": "...",
                                     "readout": false, "deprecated": false,
                                     "inferred_length": true,
                                     "fields": {...}}},
     "error": null, "traceback": null}

``fields: null`` (vs ``{}``) means "schema unknown — abstain": non-dataclass
classes, wholly failed dumps. The validator NEVER flags children of an
abstained class.

TypeSpec (nested, closed vocabulary)::

    {"base": "int|float|str|bool|list|dict|component|union|any",
     "optional": bool, "item": TypeSpec|null, "enum": [..]|null,
     "union": [TypeSpec,..]|null, "class": "path"|null, "raw": "<display>"}

Mapping decisions (grounded in the corpus + critique):
  - quam ``Scalar*`` aliases (``ScalarInt``/``ScalarFloat`` — really
    Union[num, str-reference]) map to plain int/float BY NAME: the pointer
    bypass covers the str arm globally, and a bare non-pointer string in a
    duration field is exactly the crash class being caught.
  - ``Literal[...]`` → base of the value type + ``enum`` (enforcement tier is
    the SM side's decision — warning in v1).
  - ``Dict[K, V]`` → key types ignored (JSON keys are strings; multi-DUC
    ``upconverters`` uses "1"/"2" keys), ``item`` = V's spec.
  - a class whose MRO passes through quam/quam_builder modules →
    ``component`` (its own fields are dumped separately if requested).
  - anything unresolvable → ``any`` (never omit the field, never crash).

Run standalone::  python probe_state_schema.py --classes c.json --out r.json
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import re
import sys
import traceback
import typing
from pathlib import Path

# Make the shared stdlib helpers importable in every launch mode (mirrors
# run_build.py's defensive sys.path insert before importing _script_common).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_common import library_versions as _library_versions  # noqa: E402
from probe_capabilities import _PULSE_HOMES  # noqa: E402  (single-source triplet)

# Third-party lab packages subclass quam components, so their MRO hits a
# quam.* base — the quam/quam_builder prefixes cover them transitively.
_COMPONENT_MODULE_RE = re.compile(r"^(quam|quam_builder)")
_SCALAR_ALIAS_RE = re.compile(r"\bScalar(Int|Float|Bool)\b")


# --------------------------------------------------------------------------
# annotation → TypeSpec
# --------------------------------------------------------------------------

def _spec(base: str, **extra) -> dict:
    out = {"base": base, "optional": False, "item": None, "enum": None,
           "union": None, "class": None, "raw": extra.pop("raw", base)}
    out.update(extra)
    return out


def _is_component_class(tp) -> bool:
    if not isinstance(tp, type):
        return False
    for b in getattr(tp, "__mro__", ()):
        if _COMPONENT_MODULE_RE.match(getattr(b, "__module__", "") or ""):
            return True
    return False


def _map_type(tp, raw: str, _depth: int = 0) -> dict:
    """Map a RESOLVED annotation object to a TypeSpec. Never raises."""
    if _depth > 6:                       # armor against pathological nesting
        return _spec("any", raw=raw)
    try:
        # Scalar* aliases collapse to their numeric base BY NAME (see module doc).
        m = _SCALAR_ALIAS_RE.search(raw or "")
        if m:
            return _spec({"Int": "int", "Float": "float", "Bool": "bool"}[m.group(1)], raw=raw)

        origin = typing.get_origin(tp)
        args = typing.get_args(tp)

        if tp is type(None):
            return _spec("any", optional=True, raw=raw)
        if tp is int:
            return _spec("int", raw=raw)
        if tp is float:
            return _spec("float", raw=raw)
        if tp is bool:
            return _spec("bool", raw=raw)
        if tp is str:
            return _spec("str", raw=raw)
        if tp in (dict, typing.Dict):
            return _spec("dict", raw=raw)
        if tp in (list, tuple, typing.List, typing.Tuple):
            return _spec("list", raw=raw)
        if tp is typing.Any or tp is object:
            return _spec("any", raw=raw)

        # Annotated[X, ...] → unwrap
        if origin is not None and str(origin).endswith("Annotated"):
            return _map_type(args[0], raw, _depth + 1) if args else _spec("any", raw=raw)

        # Literal[...] → enum on the value type's base
        if origin is typing.Literal:
            vals = list(args)
            base = "str"
            if vals and all(isinstance(v, bool) for v in vals):
                base = "bool"
            elif vals and all(isinstance(v, int) for v in vals):
                base = "int"
            return _spec(base, enum=vals, raw=raw)

        # Union / Optional
        if origin is typing.Union:
            none_arm = type(None) in args
            arms = [a for a in args if a is not type(None)]
            # quam's Scalar* aliases resolve to Union[QuaVariable[int], ...,
            # int] — the qm.qua runtime-expression arms are QUA-program-time
            # types that never serialize into state.json. Drop them so
            # ScalarInt collapses to plain int even after alias expansion.
            non_qua = [a for a in arms if "qm.qua._expressions" not in str(a)]
            if non_qua and len(non_qua) < len(arms):
                arms = non_qua
            if not arms:
                return _spec("any", optional=True, raw=raw)
            if len(arms) == 1:
                inner = _map_type(arms[0], raw, _depth + 1)
                inner["optional"] = inner["optional"] or none_arm
                inner["raw"] = raw
                return inner
            mapped = [_map_type(a, str(a), _depth + 1) for a in arms]
            # Union[Component, str] — quam's "component or reference" idiom:
            # the str arm is the POINTER form, covered by the global pointer
            # bypass → collapse to the component arm. ONLY for components:
            # Union[int, str] (qubit/pair ids) really means str values too —
            # collapsing that blocked every real 'q0'-style id (corpus-caught).
            non_str = [s for s in mapped if s["base"] != "str"]
            if (len(non_str) == 1 and len(mapped) == 2
                    and non_str[0]["base"] == "component"):
                one = dict(non_str[0])
                one["optional"] = one["optional"] or none_arm
                one["raw"] = raw
                return one
            return _spec("union", union=mapped, optional=none_arm, raw=raw)

        # containers
        if origin in (list, typing.Sequence, tuple):
            item = None
            if args:
                # Tuple[X, ...] / homogeneous → X; heterogeneous → no item type
                if origin is tuple and len(args) == 2 and args[1] is Ellipsis:
                    item = _map_type(args[0], str(args[0]), _depth + 1)
                elif origin is tuple and len(set(args)) > 1:
                    item = None
                else:
                    item = _map_type(args[0], str(args[0]), _depth + 1)
            return _spec("list", item=item, raw=raw)
        if origin is dict:
            item = _map_type(args[1], str(args[1]), _depth + 1) if len(args) == 2 else None
            return _spec("dict", item=item, raw=raw)

        # quam component classes
        if _is_component_class(tp):
            return _spec("component",
                         **{"class": f"{tp.__module__}.{tp.__qualname__}"}, raw=raw)

        return _spec("any", raw=raw)
    except Exception:  # noqa: BLE001 — an exotic annotation must never kill the probe
        return _spec("any", raw=raw)


# --------------------------------------------------------------------------
# per-class dump
# --------------------------------------------------------------------------

def _import_class(path: str):
    """Import ``module.QualName``, retrying the split further left for dotted
    qualnames (``mod.Outer.Inner``). Raises on total failure."""
    parts = path.split(".")
    last_exc: Exception | None = None
    for cut in range(len(parts) - 1, 0, -1):
        mod_name = ".".join(parts[:cut])
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            continue
        obj = mod
        try:
            for attr in parts[cut:]:
                obj = getattr(obj, attr)
        except AttributeError as exc:
            last_exc = exc
            continue
        return obj
    raise last_exc or ImportError(f"cannot import {path!r}")


def _json_safe(value):
    """(is_json_safe, value_or_None, repr_or_None)."""
    try:
        json.dumps(value)
        return True, value, None
    except (TypeError, ValueError):
        return False, None, repr(value)[:200]


def _dump_fields(cls) -> dict | None:
    """Field schemas for a dataclass-based class, or None (= abstain)."""
    if dataclasses.is_dataclass(cls):
        dc_fields = list(dataclasses.fields(cls))
    else:
        raw_fields = getattr(cls, "__dataclass_fields__", None)
        if not raw_fields:
            return None
        dc_fields = list(raw_fields.values())
    try:
        hints = typing.get_type_hints(cls)
    except Exception:  # noqa: BLE001 — old forks carry unresolvable forward refs
        hints = {}

    # Per-field salvage namespace: one bad forward ref must not poison the
    # WHOLE class into `any` (get_type_hints is all-or-nothing).
    mod = sys.modules.get(cls.__module__)
    globalns = dict(getattr(mod, "__dict__", {}) or {})

    out: dict[str, dict] = {}
    for f in dc_fields:
        name = f.name
        raw_ann = f.type if isinstance(f.type, str) else str(f.type)
        if "ClassVar" in raw_ann:      # defensive; dataclasses.fields excludes them
            continue
        resolved = hints.get(name)
        if resolved is None:
            if not isinstance(f.type, str):
                resolved = f.type              # already an annotation object
            else:
                try:                           # child-env eval — same trust level
                    resolved = eval(f.type, globalns, dict(vars(typing)))  # noqa: S307
                except Exception:  # noqa: BLE001
                    resolved = None
        ts = (_map_type(resolved, raw_ann) if resolved is not None
              else _spec("any", raw=raw_ann))

        has_default = f.default is not dataclasses.MISSING
        has_factory = f.default_factory is not dataclasses.MISSING  # type: ignore[misc]
        default = None
        default_repr = None
        if has_default:
            ok, default, default_repr = _json_safe(f.default)
            if not ok:
                default = None
        elif has_factory:
            try:
                produced = f.default_factory()  # type: ignore[misc]
                ok, default, default_repr = _json_safe(produced)
                if not ok:
                    default, default_repr = None, default_repr or "<factory>"
            except Exception:  # noqa: BLE001
                default, default_repr = None, "<factory>"

        out[name] = {
            "type": ts,
            "optional": bool(ts.get("optional")),
            "has_default": bool(has_default or has_factory),
            "default": default,
            "default_repr": default_repr,
            "default_is_reference": isinstance(default, str) and default.startswith("#"),
            "raw": raw_ann,
        }
    return out


def _dump_class(path: str) -> dict:
    entry = {"importable": False, "canonical": None, "bases": [],
             "is_dataclass": False, "fields": None, "error": None}
    try:
        cls = _import_class(path)
    except Exception as exc:  # noqa: BLE001 — third-party lab packages absent etc.
        entry["error"] = f"{type(exc).__name__}: {exc}"
        return entry
    if not isinstance(cls, type):
        entry["error"] = f"{path!r} resolves to {type(cls).__name__}, not a class"
        return entry
    entry["importable"] = True
    entry["canonical"] = f"{cls.__module__}.{cls.__qualname__}"
    entry["bases"] = [
        f"{b.__module__}.{b.__qualname__}"
        for b in cls.__mro__[1:]
        if _COMPONENT_MODULE_RE.match(getattr(b, "__module__", "") or "")
    ][:8]
    entry["is_dataclass"] = bool(
        dataclasses.is_dataclass(cls) or getattr(cls, "__dataclass_fields__", None))
    try:
        entry["fields"] = _dump_fields(cls)
    except Exception as exc:  # noqa: BLE001
        entry["error"] = f"field dump: {type(exc).__name__}: {exc}"
        entry["fields"] = None
    return entry


# --------------------------------------------------------------------------
# pulse roster
# --------------------------------------------------------------------------

def _dump_pulse_roster() -> dict:
    """Subclass walk of the env's Pulse base across every known pulse home."""
    roster: dict[str, dict] = {}
    try:
        base_mod = importlib.import_module("quam.components.pulses")
        pulse_base = getattr(base_mod, "Pulse", None)
        readout_base = getattr(base_mod, "BaseReadoutPulse",
                               getattr(base_mod, "ReadoutPulse", None))
    except Exception:  # noqa: BLE001 — no quam at all
        return roster
    if pulse_base is None:
        return roster

    for home in _PULSE_HOMES:
        try:
            mod = importlib.import_module(home)
        except Exception:  # noqa: BLE001 — home absent in this generation
            continue
        for name in dir(mod):
            obj = getattr(mod, name, None)
            if not (isinstance(obj, type) and issubclass(obj, pulse_base)):
                continue
            leaf = obj.__name__
            rec = roster.get(leaf)
            if rec is None:
                rec = {
                    "homes": [],
                    "canonical": f"{obj.__module__}.{obj.__qualname__}",
                    "readout": bool(readout_base and issubclass(obj, readout_base)),
                    "deprecated": leaf.startswith("_"),
                    "inferred_length": bool(
                        hasattr(obj, "inferred_length")
                        or hasattr(obj, "inferred_total_length")),
                    "fields": None,
                }
                try:
                    rec["fields"] = _dump_fields(obj)
                except Exception:  # noqa: BLE001
                    rec["fields"] = None
                roster[leaf] = rec
            if home not in rec["homes"]:
                rec["homes"].append(home)
    return roster


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def dump_schemas(class_paths: list[str], pulse_roster: bool = True) -> dict:
    """Never raises — a broken env still returns a per-class-annotated manifest."""
    classes = {}
    for path in class_paths:
        try:
            classes[path] = _dump_class(path)
        except Exception as exc:  # noqa: BLE001 — belt and braces
            classes[path] = {"importable": False, "canonical": None, "bases": [],
                             "is_dataclass": False, "fields": None,
                             "error": f"{type(exc).__name__}: {exc}"}
    result = {
        "python": sys.version.split()[0],
        "versions": _library_versions(),
        "classes": classes,
        "pulse_roster": _dump_pulse_roster() if pulse_roster else {},
    }
    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--classes", required=True, help="input JSON: {classes:[...], pulse_roster:bool}")
    ap.add_argument("--out", required=True, help="write the schema manifest JSON here")
    args = ap.parse_args()
    result = {"status": "error", "mode": "state_schema", "python": "",
              "versions": {}, "classes": {}, "pulse_roster": {},
              "error": None, "traceback": None}
    try:
        spec = json.loads(Path(args.classes).read_text(encoding="utf-8"))
        result.update(dump_schemas(
            [str(c) for c in spec.get("classes", [])],
            pulse_roster=bool(spec.get("pulse_roster", True)),
        ))
        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 — surface as a structured error
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "result_file": args.out}))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
