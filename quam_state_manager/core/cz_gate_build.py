"""Plan the creation of one new CZ gate on one qubit pair (pure).

``plan`` turns "create gate G of class C on pair P" into the ordered list of
state paths and values to create, the same result shape as
``gaussian_cz.plan``: a route writes the creates under ONE change group. It
is the single place that decides

* which way the structure points (the macro owns the pulse and the channel
  operation references it, or the operation owns the pulse and the macro
  names the operation),
* how the channel operation's ``length`` is linked to the macro pulse,
* the pulse ids and operation keys.

Gate classes whose structure SM knows are listed in ``GATE_CLASSES``. Any
other gate class can be planned only by copying the layout of an existing
gate of the same class on the chip.

Pure: no file I/O, no subprocess, no QM-stack import, no store mutation.
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any

from quam_state_manager.core import pulse_catalog
from quam_state_manager.core.add_names import (
    ADDITION_NAME_RE, OPERATION_NAME_RE, pulse_name)
from quam_state_manager.core.pointer_path import resolve_field_target
from quam_state_manager.core.pointer_resolver import _SENTINEL, _get, _walk_relative

CZ_GATE = ("quam_builder.architecture.superconducting.custom_gates."
           "flux_tunable_transmon_pair.two_qubit_gates.CZGate")
COUPLER_CZ_GATE = "qsm_quam_ext.CouplerCZGate"

MACRO_OWNS = "macro"
OPERATION_OWNS = "operation"
MIXED = "mixed"

# Roles a pulse can have in a gate; the naming function accepts every one.
ROLES = ("pulse", "coupler_pulse", "control_pulse", "target_pulse",
         "spectator_pulse")

GIVEN, COPIED, DEFAULT = "given", "copied", "default"
FROM_MODEL = "copied from the model gate"
KEPT_REF = "kept reference"
FROM_MODEL_RENAMED = "copied from the model gate, renamed"

_OP_REF = re.compile(r"^#/.+/operations/[^/]+$")


@dataclass(frozen=True)
class Slot:
    """One pulse field of a gate class.

    ``line`` says which channel plays it: ``moving`` (the pair's moving
    qubit z line), ``control`` / ``target`` (that qubit's z line) or
    ``coupler`` (the pair's coupler)."""
    field: str
    role: str
    line: str
    required: bool


def _cz_skeleton() -> dict[str, Any]:
    return {
        "id": "#./inferred_id",
        "fidelity": {},
        "duration": "#./inferred_duration",
        "flux_pulse_qubit": None,
        "coupler_flux_pulse": None,
        "phase_shift_control": 0.0,
        "phase_shift_target": 0.0,
        "spectator_qubits": {},
        "spectator_qubits_control": {},
        "spectator_qubits_phase_shift": {},
        "extras": {},
        "duration_qubit": None,
        "__class__": CZ_GATE,
    }


def _coupler_cz_skeleton() -> dict[str, Any]:
    return {
        "coupler_flux_pulse": None,
        "phase_shift_control": 0.0,
        "phase_shift_target": 0.0,
        "duration": None,
        "extras": {},
        "fidelity": {},
        "__class__": COUPLER_CZ_GATE,
    }


# The known gate classes: their macro skeleton and pulse slots. A later
# slot class adds one row with roles control_pulse / target_pulse /
# coupler_pulse on lines control / target / coupler.
GATE_CLASSES: dict[str, dict[str, Any]] = {
    CZ_GATE: {
        "skeleton": _cz_skeleton,
        "slots": (Slot("flux_pulse_qubit", "pulse", "moving", True),
                  Slot("coupler_flux_pulse", "coupler_pulse", "coupler", False)),
    },
    COUPLER_CZ_GATE: {
        "skeleton": _coupler_cz_skeleton,
        "slots": (Slot("coupler_flux_pulse", "coupler_pulse", "coupler", True),),
    },
}


def _ref(*segments: str) -> str:
    """An absolute reference built from path segments."""
    return "#/" + "/".join(segments)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def name_rule(macros: dict, gate_name: Any) -> str | None:
    """A gate name starts with ``cz_``, passes the operation-name patterns
    and does not exist on the pair."""
    if not isinstance(gate_name, str) or not gate_name.startswith("cz_"):
        return f"The gate name {gate_name!r} must start with cz_. Pick a name like cz_square."
    if not (OPERATION_NAME_RE.fullmatch(gate_name)
            and ADDITION_NAME_RE.fullmatch(gate_name)):
        return (f"The gate name {gate_name} is not a valid name. Use letters, "
                "digits and _ only, at most 64 characters.")
    if gate_name in macros:
        return f"The pair already has a gate named {gate_name}. Pick another name."
    return None


def operation_name(gate: str, role: str, control: str, target: str) -> str:
    """Pulse id and operation key: ``<gate>_<role>_<control>_<target>``.

    Raises ValueError with a plain reason for an unknown role or a name the
    operation-name rule rejects."""
    if role not in ROLES:
        raise ValueError(f"the pulse role {role} is not one SM knows")
    return pulse_name(gate, role, control, target)


def is_cz_macro(name: str, macro: Any) -> bool:
    """A macro is a CZ gate when it is a dict (not an alias pointer) and its
    class name contains CZ or its name starts with cz."""
    if not isinstance(macro, dict):
        return False
    cls = str(macro.get("__class__", ""))
    return "CZ" in cls.split(".")[-1] or name.lower().startswith("cz")


def _is_pulse_dict(value: Any) -> bool:
    return isinstance(value, dict) and (
        "__class__" not in value
        or pulse_catalog.is_pulse_class(value.get("__class__")))


def pulse_fields(macro: dict, op_keys: set | frozenset = frozenset()) -> list[str]:
    """A macro's pulse fields: a key containing "pulse" whose value is a
    pulse, an operation key on one of the pair's lines, or a reference to an
    ``.../operations/<key>`` entry."""
    out = []
    for k, v in macro.items():
        if "pulse" not in k or k.startswith("spectator"):
            continue
        if _is_pulse_dict(v) or (isinstance(v, str) and (
                v in op_keys or _OP_REF.match(v))):
            out.append(k)
    return out


def gate_direction(macro: dict, op_keys: set | frozenset = frozenset()) -> str | None:
    """A gate is macro-owns-pulse when one of its pulse fields holds a pulse,
    operation-owns-pulse when its pulse fields only name operations, None
    when it has no pulse field."""
    fields = pulse_fields(macro, op_keys)
    if not fields:
        return None
    if any(isinstance(macro[f], dict) for f in fields):
        return MACRO_OWNS
    return OPERATION_OWNS


def _op_keys(lines: dict) -> set:
    keys: set = set()
    for name in ("control", "target", "coupler"):
        ln = lines.get(name)
        if isinstance(ln, tuple):
            keys.update(ln[1])
    return keys


def choose_direction(merged: dict, pair_id: str) -> str:
    """Operation-owns-pulse only when every CZ gate of the pair is, or the
    pair has none and every CZ gate on the chip is (at least one); otherwise
    macro-owns-pulse. Alias pointers are not gates."""
    def directions(pairs):
        out = []
        for pid in pairs:
            p = (merged.get("qubit_pairs") or {}).get(pid)
            macros = p.get("macros") if isinstance(p, dict) else None
            if not isinstance(macros, dict):
                continue
            keys = _op_keys(_lines(merged, pid))
            for name, m in macros.items():
                if is_cz_macro(name, m):
                    d = gate_direction(m, keys)
                    if d is not None:
                        out.append(d)
        return out

    own = directions([pair_id])
    if own:
        return OPERATION_OWNS if all(d == OPERATION_OWNS for d in own) else MACRO_OWNS
    chip = directions(list((merged.get("qubit_pairs") or {}).keys()))
    if chip and all(d == OPERATION_OWNS for d in chip):
        return OPERATION_OWNS
    return MACRO_OWNS


def operation_length(macro_pulse: dict, macro_ref: str) -> Any:
    """With the macro owning the pulse, an explicit macro ``length`` becomes
    a reference on the operation; an inferred-length reference stays the
    operation's own inferred-length reference."""
    length = macro_pulse.get("length")
    if isinstance(length, str) and length.startswith("#./"):
        return length
    return macro_ref + "/length"


def length_rule(pulse: dict) -> str | None:
    """An explicit pulse ``length`` is a whole number of ns (an int, or a
    float with an integral value, which the plan writes as an int), a
    multiple of 4 ns and at least 16 ns. A string that is not a reference is
    refused. A reference (an inferred length) is not computed here: the
    lab-side check owns the real length."""
    length = pulse.get("length")
    if length is None or (isinstance(length, str) and length.startswith("#")):
        return None
    if isinstance(length, bool) or not isinstance(length, (int, float)):
        return (f"The pulse length {length!r} must be a number of ns. "
                "Type a whole number such as 52.")
    if isinstance(length, float):
        if not length.is_integer():
            return (f"The pulse length {length} ns must be a whole number of ns. "
                    "Change the length.")
        pulse["length"] = int(length)
        length = int(length)
    if length < 16 or length % 4:
        return (f"The pulse length {length} ns must be a multiple of 4 ns and "
                "at least 16 ns. Change the length.")
    return None


def same_kind(a: Any, b: Any) -> bool:
    """Two values have the same type: bool, int, float (an int counts as a
    float), str, list or dict; None matches nothing."""
    def kind(v):
        if v is None:
            return None
        if isinstance(v, bool):
            return "bool"
        if isinstance(v, (int, float)):
            return "number"
        if isinstance(v, str):
            return "pointer" if v.startswith("#") else "str"
        if isinstance(v, (list, tuple)):
            return "list"
        if isinstance(v, dict):
            return "dict"
        return type(v).__name__
    ka, kb = kind(a), kind(b)
    return ka is not None and ka == kb


def _plain_scalar(v: Any) -> bool:
    return isinstance(v, (bool, int, float)) or (
        isinstance(v, str) and not v.startswith("#"))


def copyable_fields(source: dict, defaults: dict) -> dict[str, Any]:
    """Initial values copied from another gate: only plain values of fields
    present in both pulses with the same name and the same type; a reference
    is never copied; a field whose default is empty takes any plain scalar."""
    skip = {"__class__", "id"}
    out = {}
    for k, v in source.items():
        if k in skip or k not in defaults:
            continue
        if isinstance(v, str) and v.startswith("#"):
            continue
        d = defaults[k]
        if (d is None and _plain_scalar(v)) or same_kind(v, d):
            out[k] = copy.deepcopy(v)
    return out


def linked_operation(macro_pulse: dict, macro_ref: str) -> dict[str, Any]:
    """The channel operation of a macro-owned pulse: every calibrated value
    references the macro pulse; class, marker, sample rate and empty values
    stay as they are, the id is left out and the length follows the length
    link."""
    keep = {"__class__", "digital_marker", "sample_rate"}
    op: dict[str, Any] = {}
    for k, v in macro_pulse.items():
        if k == "id":
            op[k] = None
        elif k == "length":
            op[k] = operation_length(macro_pulse, macro_ref)
        elif k in keep or v is None or (isinstance(v, str) and v.startswith("#./")):
            op[k] = copy.deepcopy(v)
        else:
            op[k] = f"{macro_ref}/{k}"
    return op


# ---------------------------------------------------------------------------
# Chip lookups
# ---------------------------------------------------------------------------

def pair_qubit(merged: dict, pair_id: str, role_key: str) -> str | None:
    """The qubit name a pair's ``qubit_control`` / ``qubit_target`` points at;
    when the chain does not resolve, the id written in a ``#/qubits/<id>``
    reference."""
    try:
        ft = resolve_field_target(merged, f"qubit_pairs.{pair_id}.{role_key}")
        parts = (ft.get("resolved_path") or "").split(".")
        if len(parts) == 2 and parts[0] == "qubits":
            return parts[1]
    except Exception:  # noqa: BLE001 -- a broken chain falls back to the text
        pass
    pair = (merged.get("qubit_pairs") or {}).get(pair_id)
    raw = pair.get(role_key) if isinstance(pair, dict) else None
    m = re.fullmatch(r"#/qubits/([^/]+)", raw) if isinstance(raw, str) else None
    return m.group(1) if m else None


def _follow(merged: dict, ref: str) -> tuple[tuple, Any] | None:
    """(segments, object) an absolute reference names, one hop; else None."""
    if not ref.startswith("#/"):
        return None
    segs = tuple(s for s in ref[2:].split("/") if s)
    node: Any = merged
    for s in segs:
        if not isinstance(node, dict) or s not in node:
            return None
        node = node[s]
    return segs, node


def _lines(merged: dict, pair_id: str) -> dict[str, Any]:
    """Each line role of the pair -> (path segments of its operations, the
    operations dict, the channel's name for people) or a plain reason why
    it has none. A reason matters only when a pulse plays on that line."""
    p = merged["qubit_pairs"][pair_id]
    control = pair_qubit(merged, pair_id, "qubit_control")
    target = pair_qubit(merged, pair_id, "qubit_target")
    qubits = merged.get("qubits")
    if not isinstance(qubits, dict):
        qubits = {}
    out: dict[str, Any] = {}

    def z_of(q, what, key):
        if not q:
            return (f"The {what} of pair {pair_id} does not point at a qubit, and "
                    "this gate plays on that qubit's flux line. Set the pair's "
                    f"{key} to a qubit first.")
        entry = qubits.get(q)
        if not isinstance(entry, dict):
            return (f"The pair's {what} points at {p.get(key)}, which this chip "
                    "does not have; this gate plays on that qubit's flux line. "
                    f"Fix the {key} of pair {pair_id} or pick a gate that does "
                    "not play there.")
        z = entry.get("z")
        if not isinstance(z, dict) or not isinstance(z.get("operations"), dict):
            return (f"{q}.z has no operations to hold the pulse (a DC-biased flux "
                    "line cannot play pulses). Pick a gate that does not play on "
                    f"{q}.z, or give {q} a pulsed flux line.")
        return (("qubits", q, "z", "operations"), z["operations"], f"{q}.z")

    out["control"] = z_of(control, "control", "qubit_control")
    out["target"] = z_of(target, "target", "qubit_target")
    role = p.get("moving_qubit")
    if role in ("control", "target"):
        out["moving"] = out[role]
    else:
        out["moving"] = (f"Pair {pair_id} records no moving qubit, so SM does not "
                         "know which flux line plays the gate. Set moving_qubit "
                         "on the pair to control or target first.")
    coupler = p.get("coupler")
    csegs: tuple = ("qubit_pairs", pair_id, "coupler")
    clabel = f"the coupler of pair {pair_id}"
    if isinstance(coupler, str) and coupler:
        hop = _follow(merged, coupler)
        if hop is None or not isinstance(hop[1], dict):
            coupler = None
            out["coupler"] = (f"The coupler reference {p['coupler']} of pair "
                              f"{pair_id} could not be followed. Fix the reference "
                              "or pick a gate that does not play on the coupler.")
        else:
            csegs, coupler = hop
            clabel = f"coupler {csegs[-1]}"
    if "coupler" not in out:
        if isinstance(coupler, dict) and isinstance(coupler.get("operations"), dict):
            out["coupler"] = (csegs + ("operations",), coupler["operations"], clabel)
        else:
            out["coupler"] = (f"Pair {pair_id} has no coupler that can hold "
                              "operations. Remove the coupler pulse, or pick a "
                              "gate class that does not play on a coupler.")
    out["_ids"] = (control, target)
    return out


def _dot(segs: tuple, key: str | None = None) -> str:
    return ".".join(segs + ((key,) if key is not None else ()))


def _resolve_slot_pulse(macro: dict, field: str, lines: dict
                        ) -> tuple[dict | None, str | None, str | None]:
    """(pulse dict, line role, operation key) a gate's pulse field plays."""
    v = macro.get(field)
    key = None
    if isinstance(v, dict):
        key = v.get("id") if isinstance(v.get("id"), str) and not v["id"].startswith("#") else None
        pulse = v
    elif isinstance(v, str):
        key = v.rstrip("/").rsplit("/", 1)[-1] if v.startswith("#/") else v
        pulse = None
    else:
        return None, None, None
    for line in ("control", "target", "coupler"):
        ln = lines.get(line)
        if isinstance(ln, tuple) and key and key in ln[1]:
            op = ln[1][key]
            return (pulse if pulse is not None else (op if isinstance(op, dict) else None)), line, key
    return pulse, None, key


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def _fields_for(qclass: str, given: dict, copy_src: dict | None
                ) -> tuple[dict, dict] | str:
    """The pulse dict (without id) and per-field provenance."""
    spec = pulse_catalog.by_qclass(qclass)
    if spec is None:
        return (f"SM does not know the fields of the pulse class {qclass}. "
                "Pick a pulse class from the list.")
    defaults: dict[str, Any] = {}
    for p in spec.params:
        covered = [n for n in (p.name, *p.aliases) if n in given]
        name = covered[0] if covered else p.name
        if copy_src is not None and not covered:
            alias_in_src = [n for n in (p.name, *p.aliases) if n in copy_src]
            if alias_in_src:
                name = alias_in_src[0]
        d = p.default
        defaults[name] = list(d) if isinstance(d, tuple) else copy.deepcopy(d)
    if spec.length_mode == "inferred":
        defaults["length"] = spec.length_pointer
    unknown = [k for k in given if k not in defaults and k != "id"]
    if unknown:
        return (f"The pulse class {qclass} has no field named {unknown[0]}. "
                "Remove that value.")
    return _layer(defaults, given, copy_src, qclass)


def _layer(defaults: dict, given: dict, copy_src: dict | None,
           qclass: Any, base_label: str = DEFAULT) -> tuple[dict, dict]:
    pulse = {k: copy.deepcopy(v) for k, v in defaults.items()}
    prov = {k: base_label for k in defaults if k != "id"}
    if copy_src is not None:
        for k, v in copyable_fields(copy_src, defaults).items():
            if k == "length" and isinstance(pulse.get("length"), str):
                continue  # an inferred length stays inferred
            pulse[k] = v
            prov[k] = COPIED
    for k, v in given.items():
        if k == "id":
            continue
        pulse[k] = copy.deepcopy(v)
        prov[k] = GIVEN
    pulse.pop("id", None)
    pulse["__class__"] = qclass
    return pulse, prov


def _fields_for_model(model_pulse: dict, given: dict,
                      copy_src: dict | None) -> tuple[dict, dict] | str:
    """A pulse of the model's own class: the model's values, then copied,
    then given values (the class is known only through the model)."""
    defaults = {k: v for k, v in model_pulse.items() if k not in ("__class__", "id")}
    unknown = [k for k in given if k not in defaults]
    if unknown:
        return f"The model pulse has no field named {unknown[0]}. Remove that value."
    return _layer(defaults, given, copy_src, model_pulse.get("__class__"), FROM_MODEL)


def _refuse(reasons: list[str]) -> dict[str, Any]:
    return {"error": reasons[0], "refusals": list(reasons)}


def _gate_or_alias(macros: dict, name: str, merged: dict, pair_id: str
                   ) -> tuple[dict | None, str | None]:
    """The gate *name* names on the pair, following an alias one hop."""
    v = macros.get(name)
    if isinstance(v, dict):
        return v, None
    if isinstance(v, str):
        target = None
        if v.startswith("#./"):
            target = macros.get(v[3:])
        else:
            hop = _follow(merged, v)
            target = hop[1] if hop else None
        if isinstance(target, dict):
            return target, None
        return None, (f"{name} on pair {pair_id} is an alias ({v}) that SM could not "
                      "follow. Name the gate itself to copy values from.")
    return None, (f"Pair {pair_id} has no gate {name} to copy values from. "
                  "Pick a gate of this pair, or leave copying off.")


def _argument_problem(pair_id, gate_name, gate_class, pulses, model_gate,
                      copy_from) -> str | None:
    """A plain reason when an argument has the wrong shape."""
    if not isinstance(pair_id, str) or not pair_id:
        return "The pair must be given by its name. Pick a pair from the list."
    if not isinstance(gate_name, str) or not gate_name:
        return "The gate name must be text that starts with cz_. Type a name like cz_square."
    if not isinstance(gate_class, str) or not gate_class:
        return "The gate class must be given by its class name. Pick a gate class from the list."
    if pulses is not None and not isinstance(pulses, dict):
        return "The pulses must be given per pulse field. Pick the pulses again."
    for f, spec in (pulses or {}).items():
        if not isinstance(spec, dict):
            return f"The pulse for {f} must name a class and its fields. Pick it again."
        if spec.get("fields") is not None and not isinstance(spec["fields"], dict):
            return f"The field values for {f} must be given by field name. Type them again."
    if model_gate is not None and not (
            isinstance(model_gate, (list, tuple)) and len(model_gate) == 2
            and all(isinstance(x, str) for x in model_gate)):
        return "The model gate must be a pair and a gate name. Pick the model gate again."
    if copy_from is not None and not isinstance(copy_from, str):
        return "The gate to copy values from must be a gate name. Pick it again."
    return None


def plan(merged: dict, pair_id: str, *, gate_name: str, gate_class: str,
         pulses: dict[str, dict] | None = None,
         copy_from: str | None = None,
         model_gate: tuple[str, str] | None = None) -> dict[str, Any]:
    """Plan one new CZ gate. Never raises on chip shape; never changes *merged*.

    ``pulses`` maps a gate pulse field (``flux_pulse_qubit``,
    ``coupler_flux_pulse``, ...) to ``{"class": <pulse __class__>,
    "fields": {...}}``. For a table class an optional slot left out is not
    built. A copied layout always builds exactly the model's pulse slots
    and keeps the model's pulse classes; its ``pulses`` entries only give
    field values. ``copy_from`` is another gate of the same pair whose
    matching field values seed the pulses. ``model_gate`` is ``(pair,
    gate)`` for a class SM does not know. Malformed arguments are refusals.
    A copied layout (``"layout": "copied"``) is a proposal: SM copies a
    structure it cannot describe, so the lab's own code must confirm it
    before it is written. ``"reset"`` lists the model keys the copy does
    not inherit. A copied layout keeps a climbing relative reference
    (``#../``) only when it points inside its own copied object, and is
    refused when one points outside it.

    Returns ``{"creates": [(path, value)], "direction", "pulse_directions",
    "model_gate", "fields": {slot: {field: given|copied|default}, "macro":
    {key: copied from the model gate}}, "sources": {...}}`` or ``{"error":
    str, "refusals": [str]}``. Every created value is a fresh copy.
    """
    try:
        return _plan_body(merged, pair_id, gate_name, gate_class, pulses,
                          copy_from, model_gate)
    except Exception as exc:  # noqa: BLE001 -- the last net: plan never raises
        return _refuse([f"SM could not plan this gate ({type(exc).__name__}). "
                        "Nothing was changed. Report this with the gate class "
                        "and the pair."])


def _plan_body(merged, pair_id, gate_name, gate_class, pulses, copy_from,
               model_gate) -> dict[str, Any]:
    bad = _argument_problem(pair_id, gate_name, gate_class, pulses, model_gate,
                            copy_from)
    if bad:
        return _refuse([bad])
    if not isinstance(merged, dict) or not isinstance(merged.get("qubit_pairs"), dict):
        return _refuse(["This chip has no qubit_pairs section SM can read. "
                        "Open a chip that has qubit pairs."])
    pulses = dict(pulses or {})
    pairs = merged["qubit_pairs"]
    p = pairs.get(pair_id)
    if not isinstance(p, dict):
        return _refuse([f"Pair {pair_id} is not on this chip (or SM cannot read "
                        "it). Pick a pair from the list."])
    macros = p.get("macros")
    if not isinstance(macros, dict):
        return _refuse([f"Pair {pair_id} has no macros section to hold a gate. "
                        "Add an empty macros section to the pair first."])
    reasons: list[str] = []
    r = name_rule(macros, gate_name)
    if r:
        reasons.append(r)
    lines = _lines(merged, pair_id)
    control, target = lines["_ids"]
    if gate_class in GATE_CLASSES and not (control and target):
        return _refuse(reasons + [
            f"The control or target of pair {pair_id} does not point at a qubit, "
            "so SM cannot name the gate's pulses. Set the pair's qubit_control "
            "and qubit_target first."])

    copy_macro = None
    if copy_from is not None:
        copy_macro, why = _gate_or_alias(macros, copy_from, merged, pair_id)
        if why:
            reasons.append(why)

    def copy_source(field):
        if copy_macro is None:
            return None
        return _resolve_slot_pulse(copy_macro, field, lines)[0]

    if gate_class in GATE_CLASSES:
        out = _plan_known(merged, pair_id, p, gate_name, gate_class, pulses,
                          lines, copy_source, reasons)
    else:
        out = _plan_copied(merged, pair_id, gate_name, gate_class, pulses,
                           lines, copy_source, reasons, model_gate)
    if "error" in out:
        return out
    # The plan never overwrites a path, never writes one twice and never
    # touches the alias.
    paths = [path for path, _ in out["creates"]]
    for path in sorted({x for x in paths if paths.count(x) > 1}):
        reasons.append(f"Two pulses of the gate would both be written as "
                       f"{path.rsplit('.', 1)[-1]}. Pick another model gate.")
    for path, _ in out["creates"]:
        if path.endswith(".macros.cz"):
            reasons.append("The new gate would replace the pair's cz alias. "
                           "Pick another gate name.")
    if reasons:
        return _refuse(reasons)
    out["creates"] = [(path, copy.deepcopy(v)) for path, v in out["creates"]]
    return out


def _check_op(line: tuple, key: str, reasons: list[str]) -> None:
    if key in line[1]:
        reasons.append(f"The operation {key} already exists on {line[2]}. "
                       "Pick another gate name.")


def _name_problem(key: str, ids: tuple) -> str:
    """Why an operation name is not valid, in words the user can act on."""
    for q in ids:
        if q and not re.fullmatch(r"[A-Za-z0-9_]+", q):
            return (f"The qubit id {q} contains a character an operation name "
                    "cannot hold (only letters, digits and _ are allowed). "
                    "Rename the qubit in the chip first.")
    if len(key) > 64:
        return (f"The operation name {key} would be longer than 64 characters. "
                "Pick a shorter gate name.")
    return (f"The operation name {key} is not a valid name (a letter first, then "
            "letters, digits or _). Pick a plainer gate name.")


def _plan_known(merged, pair_id, p, gate_name, gate_class, pulses, lines,
                copy_source, reasons) -> dict[str, Any]:
    desc = GATE_CLASSES[gate_class]
    slots = {s.field: s for s in desc["slots"]}
    have = ", ".join(slots)
    for f in pulses:
        if f not in slots:
            reasons.append(f"This gate class has no pulse field {f} (its pulse "
                           f"fields are {have}). Remove that pulse.")
    for s in desc["slots"]:
        if s.required and s.field not in pulses:
            extra = (" A coupler pulse alone is not a gate of this class; for a "
                     "coupler-only gate pick the coupler-only gate class."
                     if gate_class == CZ_GATE else "")
            reasons.append(f"This gate needs a pulse for {s.field}. Pick a pulse "
                           f"class for it.{extra}")
    if reasons:
        return _refuse(reasons)
    direction = choose_direction(merged, pair_id)
    control, target = lines["_ids"]
    macro = desc["skeleton"]()
    macro_segs = ("qubit_pairs", pair_id, "macros", gate_name)
    op_creates: list[tuple[str, Any]] = []
    provenance: dict[str, dict] = {}
    operations: dict[str, dict] = {}
    for s in desc["slots"]:
        if s.field not in pulses:
            continue
        line = lines.get(s.line)
        if not isinstance(line, tuple):
            reasons.append(line)
            continue
        segs = line[0]
        spec = pulses[s.field] or {}
        qclass = spec.get("class")
        if not isinstance(qclass, str) or not qclass:
            reasons.append(f"Pick a pulse class for {s.field}.")
            continue
        built = _fields_for(qclass, dict(spec.get("fields") or {}),
                            copy_source(s.field))
        if isinstance(built, str):
            reasons.append(built)
            continue
        pulse, prov = built
        provenance[s.field] = prov
        r = length_rule(pulse)
        if r:
            reasons.append(r)
        try:
            key = operation_name(gate_name, s.role, control, target)
        except ValueError:
            reasons.append(_name_problem(f"{gate_name}_{s.role}_{control}_{target}",
                                         (control, target)))
            continue
        _check_op(line, key, reasons)
        operations[s.field] = {"path": _dot(segs, key), "key": key,
                               "channel": line[2], "created": True}
        if direction == MACRO_OWNS:
            owned = {"id": key, **pulse}
            macro[s.field] = owned
            op_creates.append((_dot(segs, key),
                               linked_operation(owned, _ref(*macro_segs, s.field))))
        else:
            macro[s.field] = key
            op_creates.append((_dot(segs, key), {"id": key, **pulse}))
    if reasons:
        return _refuse(reasons)
    return {"creates": [(_dot(macro_segs), macro)] + op_creates,
            "direction": direction,
            "pulse_directions": {f: direction for f in provenance},
            "model_gate": None, "layout": "known", "reset": [],
            "operations": operations, "renamed": {}, "unresolved_references": [],
            "fields": {**provenance, "macro": {
                k: (KEPT_REF if isinstance(v, str) and v.startswith("#") else DEFAULT)
                for k, v in macro.items()
                if k != "__class__" and k not in slots}},
            "sources": {"control": control, "target": target,
                        "moving_qubit": p.get("moving_qubit")}}


# ---------------------------------------------------------------------------
# Copied layout (a gate class SM does not know)
# ---------------------------------------------------------------------------

def model_candidates(merged: dict, gate_class: str) -> list[tuple[str, str]]:
    """Every ``(pair, gate)`` of *gate_class* on the chip, by pair name."""
    out = []
    pairs = merged.get("qubit_pairs")
    if not isinstance(pairs, dict):
        return []
    for pid in sorted(pairs):
        p = pairs[pid]
        macros = p.get("macros") if isinstance(p, dict) else None
        if not isinstance(macros, dict):
            continue
        for name, m in macros.items():
            if isinstance(m, dict) and m.get("__class__") == gate_class:
                out.append((pid, name))
    return out


def copied_name(model_name: str, model_gate: str, model_ids: tuple, gate: str,
                ids: tuple) -> str | None:
    """A copied pulse name, or None when the name is not the model gate's.

    The name must be the model gate name or start with it followed by
    ``_``. The gate-name part becomes the new gate name, used as given. In
    the rest, each whole ``_``-delimited run of tokens equal to the model
    pair's control or target id (longest id first, one pass) becomes the new
    pair's control or target id."""
    if model_name == model_gate:
        return gate
    if not model_name.startswith(model_gate + "_"):
        return None
    swaps = sorted(((tuple(m.split("_")), n) for m, n in
                    ((model_ids[0], ids[0]), (model_ids[1], ids[1]))
                    if isinstance(m, str) and isinstance(n, str) and m != n),
                   key=lambda x: -len(x[0]))
    toks = model_name[len(model_gate) + 1:].split("_")
    out: list[str] = []
    i = 0
    while i < len(toks):
        for mtoks, new in swaps:
            if tuple(toks[i:i + len(mtoks)]) == mtoks:
                out.append(new)
                i += len(mtoks)
                break
        else:
            out.append(toks[i])
            i += 1
    return gate + "_" + "_".join(out)


def _rename_values(value: Any, names: dict[str, str],
                   plain: dict[str, str] | None = None) -> Any:
    """Every string EQUAL to a model name, or a segment of an absolute
    reference equal to one, becomes the new name; a plain (non-reference)
    string equal to a key of *plain* becomes its value. Relative references
    are left alone (``_check_relative`` decides on them). Nothing matches
    inside a longer string."""
    plain = plain or {}
    if isinstance(value, dict):
        return {k: _rename_values(v, names, plain) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename_values(v, names, plain) for v in value]
    if isinstance(value, str):
        if value in names:
            return names[value]
        if value.startswith("#/"):
            return "/".join(names.get(seg, seg) for seg in value.split("/"))
        if value.startswith("#"):
            return value  # relative references are resolved, never renamed
        if value in plain:
            return plain[value]
    return value


def _check_relative(merged: dict, obj: Any, obj_path: tuple, mname: str,
                    reset: list[str], unresolved: list[str], reasons: list[str],
                    where: str) -> None:
    """Climbing relative references (``#../``, ``#./../``) in a copied object.

    Each is resolved in the model from its holder's model path with the
    resolver's own walk (``pointer_resolver._walk_relative``, which follows
    references and list indices on the way). A target inside the same
    object is kept, provided the copy still holds it; any other target is a
    refusal, since SM does not translate references that leave their own
    object; a reference that does not resolve in the model is kept and
    listed in *unresolved*."""
    def walk(v, rel, label):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, rel + (k,), f"{label}.{k}")
            return
        if isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, rel + (str(i),), f"{label}.{i}")
            return
        if not (isinstance(v, str) and (v.startswith("#../") or v.startswith("#./../"))):
            return
        ptr = "#" + v[3:] if v.startswith("#./../") else v
        t = _walk_relative(merged, ptr, obj_path + rel)
        if t is None or _get(merged, list(t)) is _SENTINEL:
            unresolved.append(f"{label}: {v}")
            return
        t = tuple(t)
        if t[:len(obj_path)] != obj_path:
            reasons.append(f"The model gate {mname} uses the relative reference {v} "
                           f"(in {label}), which points outside the gate. SM does not "
                           "copy such a layout. Pick another model gate, or a gate "
                           "class SM knows.")
            return
        inner = list(t[len(obj_path):])
        if _get(obj, inner) is _SENTINEL or (inner and inner[0] in reset):
            key = inner[0] if inner else ""
            reasons.append(f"The model gate {mname} uses the relative reference {v} "
                           f"(in {label}), whose target {key} is not copied to the new "
                           "gate. Pick another model gate, or a gate class SM knows.")
    walk(obj, (), where)


def _rewrite_refs(value: Any, old_prefix: str, new_prefix: str) -> Any:
    if isinstance(value, dict):
        return {k: _rewrite_refs(v, old_prefix, new_prefix) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_refs(v, old_prefix, new_prefix) for v in value]
    if isinstance(value, str) and (value == old_prefix or value.startswith(old_prefix + "/")):
        return new_prefix + value[len(old_prefix):]
    return value


def _structure_only(model: dict, pulse_keys: list[str],
                    op_keys: set | frozenset = frozenset()
                    ) -> tuple[dict, dict, list[str]]:
    """The model macro without its results: fidelity and extras emptied,
    phase shifts at 0.0, spectator containers emptied and a spectator value
    that is a reference or names a model operation set to None; every other
    key keeps the model's value. Returns (macro, the kept plain values: not
    references, not None, the keys that were reset)."""
    macro: dict[str, Any] = {}
    kept: dict[str, str] = {}
    reset: list[str] = []
    for k, v in model.items():
        if k in pulse_keys or k == "__class__":
            macro[k] = copy.deepcopy(v)
            continue
        if k in ("fidelity", "extras"):
            macro[k] = {}
            reset.append(k)
            continue
        if k.startswith("phase_shift"):
            macro[k] = 0.0
            reset.append(k)
            continue
        if k.startswith("spectator"):
            if isinstance(v, (list, dict)):
                macro[k] = [] if isinstance(v, list) else {}
                reset.append(k)
                continue
            if isinstance(v, str) and (v.startswith("#") or v in op_keys):
                macro[k] = None
                reset.append(k)
                continue
        macro[k] = copy.deepcopy(v)
        if isinstance(v, str) and v.startswith("#"):
            kept[k] = KEPT_REF
        elif v is not None:
            kept[k] = FROM_MODEL
    return macro, kept, reset


def all_operation_keys(merged: Any) -> set:
    """Every operation key on any channel of the chip: the keys of every
    ``operations`` dict anywhere in the state."""
    out: set = set()
    stack = [merged]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "operations" and isinstance(v, dict):
                    out.update(v)
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _strings(value: Any):
    if isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)
    elif isinstance(value, str):
        yield value


_ABS_REF = re.compile(r"#/[^\s\"'#]*")


def _canonical_dot(merged: dict, ref: str) -> str:
    """The dot path an absolute reference names, with a pair's coupler
    reference followed one hop (as ``_lines`` does)."""
    segs = tuple(s for s in ref[2:].split("/") if s)
    if len(segs) >= 3 and segs[0] == "qubit_pairs" and segs[2] == "coupler":
        pair = ((merged.get("qubit_pairs") or {}).get(segs[1]) or {})
        coupler = pair.get("coupler") if isinstance(pair, dict) else None
        if isinstance(coupler, str):
            hop = _follow(merged, coupler)
            if hop is not None:
                segs = hop[0] + segs[3:]
    return ".".join(segs)


def _walk_strings(value: Any, where: str):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_strings(v, f"{where}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_strings(v, f"{where}.{i}")
    elif isinstance(value, str):
        yield where, value


def _leftover_refs(merged, creates, mpair, mname, mids, pair_id, ids) -> list[str]:
    """Strings in the creates that still point at the model.

    Absolute references are scanned here; climbing relative references are
    checked by ``_check_relative``; same-object references (``#./``)
    keep their meaning because the copy keeps the object's shape."""
    created = {path for path, _ in creates}
    model_ref = _ref("qubit_pairs", mpair, "macros", mname)
    cross = mpair != pair_id
    # A model qubit whose role holds another qubit on the new pair.
    moved = [q for q, n in zip(mids, ids) if q and q != n]
    out = []
    for path, value in creates:
        for where, s in _walk_strings(value, path):
            refs = _ABS_REF.findall(s)
            for r in refs:
                bad = r == model_ref or r.startswith(model_ref + "/")
                pair_ref = _ref("qubit_pairs", mpair)
                if cross and (r == pair_ref or r.startswith(pair_ref + "/")):
                    bad = True
                if cross and any(r == _ref("qubits", q) or r.startswith(_ref("qubits", q) + "/")
                                 for q in moved):
                    bad = True
                if not bad and _OP_REF.match(r):
                    dot = _canonical_dot(merged, r)
                    if dot not in created and _follow(merged, _ref(*dot.split("."))) is None:
                        bad = True
                if bad:
                    out.append(f"The copy of {mname} would keep the reference {r} "
                               f"(in {where}), which still points at the model or "
                               "at nothing. Pick a model gate on this pair, or one "
                               "without that reference.")
    return out


def _has_run(name: str, qubit_id: str) -> bool:
    """Does *name* hold *qubit_id* as a whole ``_``-delimited run?"""
    toks, want = name.split("_"), qubit_id.split("_")
    return any(toks[i:i + len(want)] == want for i in range(len(toks) - len(want) + 1))


def _op_pointing_at(lines: dict, prefix: str) -> str | None:
    """The key of an operation on the pair's lines whose values reference
    *prefix* (a macro pulse), if any."""
    for name in ("control", "target", "coupler"):
        ln = lines.get(name)
        if not isinstance(ln, tuple):
            continue
        for key, op in ln[1].items():
            if any(s == prefix or s.startswith(prefix + "/") for s in _strings(op)):
                return key
    return None


def _model_lines_used(model: dict, fields: list[str], mlines: dict) -> set:
    return {_resolve_slot_pulse(model, f, mlines)[1] for f in fields} - {None}


def moving_rule(merged: dict, mpair: str, model: dict, pair_id: str) -> str | None:
    """A copy onto another pair whose model plays exactly one of the two z
    lines needs both pairs to record the same moving qubit; a model playing
    both z lines, or only the coupler, needs no check."""
    if mpair == pair_id:
        return None
    mlines = _lines(merged, mpair)
    used = _model_lines_used(model, pulse_fields(model, _op_keys(mlines)), mlines)
    if len(used & {"control", "target"}) != 1:
        return None
    m = merged["qubit_pairs"][mpair].get("moving_qubit")
    n = merged["qubit_pairs"][pair_id].get("moving_qubit")
    if m in ("control", "target") and m == n:
        return None
    return (f"The model gate plays one flux line of pair {mpair}, whose moving "
            f"qubit is {m or 'not set'}, while pair {pair_id} has moving qubit "
            f"{n or 'not set'}, so SM cannot tell which line to use. Pick a model "
            "gate on a pair with the same moving qubit, or set moving_qubit on "
            "both pairs.")


def default_model(merged: dict, pair_id: str, gate_class: str) -> tuple[str, str] | None:
    """The model gate: the same pair's gate of the class; else the first by
    pair name that the moving-qubit rule accepts; else the first by pair
    name (its plan then names the reason); None when the chip has none."""
    cands = model_candidates(merged, gate_class)
    for c in cands:
        if c[0] == pair_id:
            return c
    for c in cands:
        if moving_rule(merged, c[0], merged["qubit_pairs"][c[0]]["macros"][c[1]],
                       pair_id) is None:
            return c
    return cands[0] if cands else None


def _plan_copied(merged, pair_id, gate_name, gate_class, pulses, lines,
                 copy_source, reasons, model_gate) -> dict[str, Any]:
    if model_gate is None:
        model_gate = default_model(merged, pair_id, gate_class)
        if model_gate is None:
            return _refuse(reasons + [
                f"No gate of the class {gate_class} exists on this chip, so "
                "SM does not know its layout. Pick a gate class SM knows."])
    mpair, mname = model_gate
    mp = (merged.get("qubit_pairs") or {}).get(mpair)
    mmacros = mp.get("macros") if isinstance(mp, dict) else None
    model = mmacros.get(mname) if isinstance(mmacros, dict) else None
    if not isinstance(model, dict) or model.get("__class__") != gate_class:
        return _refuse(reasons + [
            f"{mname} on pair {mpair} is not a gate of the class {gate_class}. "
            "Pick a model gate of that class."])
    p = merged["qubit_pairs"][pair_id]
    r = moving_rule(merged, mpair, model, pair_id)
    if r:
        return _refuse(reasons + [r])
    mlines = _lines(merged, mpair)
    mids = mlines["_ids"]
    control, target = lines["_ids"]
    fields = pulse_fields(model, _op_keys(mlines))
    if not fields:
        return _refuse(reasons + [
            f"The model gate {mname} has no pulse SM can copy. Pick another "
            "model gate."])
    if mpair != pair_id:
        mkeys = [_resolve_slot_pulse(model, f, mlines)[2] or "" for f in fields]
        used = _model_lines_used(model, fields, mlines)
        chip_qubits = set(merged.get("qubits") or {}) if isinstance(
            merged.get("qubits"), dict) else set()
        known = {q for q in mids if q}
        # A missing model id is needed when the model plays that qubit's line,
        # or when a model name carries some other chip qubit id (likely the
        # missing one) that SM could not rename.
        tails = [k[len(mname) + 1:] if k.startswith(mname + "_") else "" for k in mkeys]
        for role, mq in (("control", mids[0]), ("target", mids[1])):
            raw = mp.get("qubit_" + role)
            suspects = chip_qubits - known
            if isinstance(raw, str) and raw and not raw.startswith("#"):
                suspects = suspects | {raw}
            stray = any(_has_run(t, q) for t in tails for q in suspects)
            if not mq and (role in used or stray):
                return _refuse(reasons + [
                    f"The {role} of the model pair {mpair} "
                    f"({mp.get('qubit_' + role)!r}) does not point at a qubit, so "
                    f"SM cannot rename its pulses for pair {pair_id}. Fix that "
                    "reference, or pick a model gate on this pair."])
        # The new pair's ids are needed only when a model name carries an id.
        for role, mq, nq in (("control", mids[0], control), ("target", mids[1], target)):
            needs = bool(mq) and any(_has_run(k, mq) for k in mkeys)
            if needs and not nq:
                return _refuse(reasons + [
                    f"The {role} of pair {pair_id} does not point at a qubit, so SM "
                    "cannot name the copied pulses. Set the pair's qubit_control "
                    "and qubit_target first."])
    old_ref = _ref("qubit_pairs", mpair, "macros", mname)
    macro_segs = ("qubit_pairs", pair_id, "macros", gate_name)
    new_ref = _ref(*macro_segs)
    # Aliases on the model pair that name the model gate are references to it.
    alias_refs = [_ref("qubit_pairs", mpair, "macros", a) for a, v in mmacros.items()
                  if isinstance(v, str) and v in ("#./" + mname, old_ref)]

    def norm(value):
        for a in alias_refs:
            value = _rewrite_refs(value, a, old_ref)
        return _rewrite_refs(value, old_ref, new_ref)

    macro, kept, reset = _structure_only(model, fields, all_operation_keys(merged))
    macro = norm(macro)
    op_creates: list[tuple[str, Any]] = []
    provenance: dict[str, dict] = {}
    directions: dict[str, str] = {}
    operations: dict[str, dict] = {}
    op_moves: list[tuple[tuple, tuple]] = []
    names: dict[str, str] = {}
    for f in pulses:
        if f not in fields:
            reasons.append(f"The model gate {mname} has no pulse field {f}; a copy "
                           f"builds exactly the model's pulses ({', '.join(fields)}). "
                           "Remove that pulse.")
    for f in fields:
        mpulse, line, mkey = _resolve_slot_pulse(model, f, mlines)
        if line is None or mkey is None:
            played = _op_pointing_at(mlines, old_ref + "/" + f)
            if isinstance(mpulse, dict) and mkey and played:
                reasons.append(f"The pulse {f} of {mname} is named {mkey} but its "
                               f"operation is {played}, so the gate would not play "
                               "it. Fix the model gate's pulse id, or pick another "
                               "model gate.")
            else:
                reasons.append(f"SM cannot find the operation that {f} of the model "
                               f"gate {mname} plays on pair {mpair}. Pick another "
                               "model gate.")
            continue
        base = mpulse if isinstance(mpulse, dict) else {}
        if not base.get("__class__"):
            reasons.append(f"The pulse {f} of the model gate {mname} names no pulse "
                           "class, so SM cannot copy it. Pick another model gate.")
            continue
        spec = pulses.get(f) or {}
        if spec.get("class") not in (None, base["__class__"]):
            reasons.append(f"A gate copied from {mname} keeps its pulse classes; to "
                           "use another pulse class pick a gate class SM knows.")
            continue
        key = copied_name(mkey, mname, mids, gate_name, (control, target))
        if key is None:
            reasons.append(f"The operation {mkey} of the model gate is not named "
                           f"after {mname}, so SM cannot name the copy. Pick "
                           "another model gate.")
            continue
        names[mkey] = key
        if not (OPERATION_NAME_RE.fullmatch(key) and ADDITION_NAME_RE.fullmatch(key)):
            reasons.append(_name_problem(key, (control, target)))
            continue
        ln = lines.get(line)
        if not isinstance(ln, tuple):
            reasons.append(ln)
            continue
        segs = ln[0]
        _check_op(ln, key, reasons)
        model_op = mlines[line][1].get(mkey)
        given = dict(spec.get("fields") or {})
        built = _fields_for_model(base, given, copy_source(f))
        if isinstance(built, str):
            reasons.append(built)
            continue
        pulse, prov = built
        provenance[f] = prov
        r = length_rule(pulse)
        if r:
            reasons.append(r)
        if isinstance(model.get(f), dict):
            directions[f] = MACRO_OWNS
            owned = norm(pulse)
            if "id" in model[f]:
                owned = {"id": key, **owned}
            macro[f] = owned
            if isinstance(model_op, dict):
                op = norm(copy.deepcopy(model_op))
                # Where the operation holds a plain copy, it holds what the
                # macro pulse holds, whatever the value's source.
                for k in op:
                    if k in ("id", "__class__") or k not in owned:
                        continue
                    if isinstance(op[k], str) and op[k].startswith("#"):
                        continue
                    v = owned[k]
                    if isinstance(v, str) and v.startswith("#.") :
                        continue  # a relative value would resolve on the operation
                    op[k] = copy.deepcopy(v)
                # An explicit macro length is referenced by the operation.
                ml = owned.get("length")
                if (isinstance(ml, (int, float)) and not isinstance(ml, bool)
                        and isinstance(op.get("length"), str)
                        and op["length"].startswith("#./")):
                    op["length"] = _ref(*macro_segs, f) + "/length"
            else:
                op = linked_operation(owned, _ref(*macro_segs, f))
        else:
            directions[f] = OPERATION_OWNS
            mval = model.get(f)
            if isinstance(mval, str) and mval.startswith("#/"):
                macro[f] = mval if mpair == pair_id else _ref(*segs, key)
            else:
                macro[f] = key
            op = norm(dict(pulse))
            if isinstance(model_op, dict) and "id" in model_op:
                mid = model_op["id"]
                # The id follows the model: its own key becomes the new key,
                # null stays null, anything else goes through the rename.
                op = {"id": key if mid == mkey else copy.deepcopy(mid), **op}
        op_creates.append((_dot(segs, key), op))
        operations[f] = {"path": _dot(segs, key), "key": key, "channel": ln[2],
                         "created": True}
        op_moves.append((tuple(mlines[line][0]) + (mkey,), tuple(segs) + (key,)))
    if len(set(names.values())) != len(names):
        reasons.append("Two pulses of the model gate would get the same name on "
                       f"pair {pair_id}. Pick another model gate.")
    if reasons:
        return _refuse(reasons)
    # Plain strings are mapped by exact equality only: the model gate's name
    # and, on another pair, a model qubit id (by role).
    plain = {mname: gate_name}
    if mpair != pair_id:
        for old, new in zip(mids, (control, target)):
            if old and new and old != new:
                plain[old] = new
    # Climbing relative references: kept only inside their own object.
    model_macro = ("qubit_pairs", mpair, "macros", mname)
    unresolved: list[str] = []
    _check_relative(merged, macro, model_macro, mname, reset, unresolved, reasons,
                    _dot(macro_segs))
    for (path, v), (old, _new) in zip(op_creates, op_moves):
        _check_relative(merged, v, old, mname, [], unresolved, reasons, path)
    if reasons:
        return _refuse(reasons)
    before = {k: macro[k] for k in kept}
    used_strings = set(_strings(macro)) | set(_strings([v for _, v in op_creates]))
    macro = _rename_values(macro, names, plain)
    op_creates = [(path, _rename_values(v, names, plain)) for path, v in op_creates]
    for k in kept:
        if kept[k] == FROM_MODEL and macro.get(k) != before[k]:
            kept[k] = FROM_MODEL_RENAMED
    creates = [(_dot(macro_segs), macro)] + op_creates
    leftovers = _leftover_refs(merged, creates, mpair, mname, mids, pair_id,
                               (control, target))
    if leftovers:
        return _refuse(leftovers)
    kinds = set(directions.values())
    provenance["macro"] = kept
    tails = [k[len(mname) + 1:] for k in names]
    renamed = {k: v for k, v in names.items() if k != v}
    if mname != gate_name:
        renamed[mname] = gate_name
    for old, new in plain.items():
        if old != mname and (old in used_strings or any(_has_run(t, old) for t in tails)):
            renamed[old] = new
    return {"creates": creates,
            "direction": kinds.pop() if len(kinds) == 1 else MIXED,
            "pulse_directions": directions,
            "model_gate": (mpair, mname),
            "layout": "copied", "reset": reset,
            "operations": operations, "unresolved_references": unresolved,
            "renamed": renamed,
            "fields": provenance,
            "sources": {"control": control, "target": target,
                        "moving_qubit": p.get("moving_qubit")}}
