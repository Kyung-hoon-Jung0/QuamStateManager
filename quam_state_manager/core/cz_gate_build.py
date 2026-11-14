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
from dataclasses import dataclass
from typing import Any

from quam_state_manager.core import pulse_catalog
from quam_state_manager.core.add_names import pulse_name
from quam_state_manager.core.pointer_path import resolve_field_target

CZ_GATE = ("quam_builder.architecture.superconducting.custom_gates."
           "flux_tunable_transmon_pair.two_qubit_gates.CZGate")
COUPLER_CZ_GATE = "qsm_quam_ext.CouplerCZGate"

MACRO_OWNS = "macro"
OPERATION_OWNS = "operation"

# Roles a pulse can have in a gate; the naming function accepts every one.
ROLES = ("pulse", "coupler_pulse", "control_pulse", "target_pulse",
         "spectator_pulse")

GIVEN, COPIED, DEFAULT = "given", "copied", "default"


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


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

def name_rule(macros: dict, gate_name: Any) -> str | None:
    """A gate name starts with ``cz_`` and does not exist on the pair."""
    if not isinstance(gate_name, str) or not gate_name.startswith("cz_"):
        return f"The gate name {gate_name!r} must start with cz_. Pick a name like cz_square."
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


def _pulse_fields(macro: dict) -> list[str]:
    return [k for k, v in macro.items()
            if "pulse" in k and not k.startswith("spectator")
            and (_is_pulse_dict(v) or (isinstance(v, str) and v))]


def gate_direction(macro: dict) -> str | None:
    """A gate is macro-owns-pulse when one of its pulse fields holds a pulse,
    operation-owns-pulse when its pulse fields only name operations, None
    when it has no pulse field."""
    fields = _pulse_fields(macro)
    if not fields:
        return None
    if any(isinstance(macro[f], dict) for f in fields):
        return MACRO_OWNS
    return OPERATION_OWNS


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
            for name, m in macros.items():
                if is_cz_macro(name, m):
                    d = gate_direction(m)
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
    """An explicit pulse length is a multiple of 4 ns and at least 16 ns."""
    length = pulse.get("length")
    if isinstance(length, bool) or not isinstance(length, (int, float)):
        return None
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


def copyable_fields(source: dict, defaults: dict) -> dict[str, Any]:
    """Initial values copied from another gate: only fields present in both
    pulses with the same name and the same type."""
    skip = {"__class__", "id"}
    out = {}
    for k, v in source.items():
        if k in skip or k not in defaults:
            continue
        d = defaults[k]
        ref = d if d is not None else None
        if ref is None:
            continue
        if same_kind(v, ref):
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
            op[k] = v
        else:
            op[k] = f"{macro_ref}/{k}"
    return op


# ---------------------------------------------------------------------------
# Chip lookups
# ---------------------------------------------------------------------------

def pair_qubit(merged: dict, pair_id: str, role_key: str) -> str | None:
    """The qubit name a pair's ``qubit_control`` / ``qubit_target`` points at."""
    try:
        ft = resolve_field_target(merged, f"qubit_pairs.{pair_id}.{role_key}")
    except Exception:  # noqa: BLE001 -- a broken chain is "no qubit"
        return None
    parts = (ft.get("resolved_path") or "").split(".")
    if len(parts) == 2 and parts[0] == "qubits":
        return parts[1]
    return None


def _lines(merged: dict, pair_id: str) -> dict[str, Any]:
    """Each line role of the pair -> (dot path of its operations, dict) or
    a plain reason why it has none."""
    p = merged["qubit_pairs"][pair_id]
    control = pair_qubit(merged, pair_id, "qubit_control")
    target = pair_qubit(merged, pair_id, "qubit_target")
    qubits = merged.get("qubits") or {}
    out: dict[str, Any] = {}

    def z_of(q, what):
        if not q:
            return f"The pair {pair_id} has no {what} qubit that resolves to a qubit."
        z = (qubits.get(q) or {}).get("z")
        if not isinstance(z, dict) or not isinstance(z.get("operations"), dict):
            return f"The {what} qubit {q} has no z operations to hold the pulse."
        return (f"qubits.{q}.z.operations", z["operations"])

    out["control"] = z_of(control, "control")
    out["target"] = z_of(target, "target")
    role = p.get("moving_qubit")
    if role in ("control", "target"):
        out["moving"] = z_of(control if role == "control" else target, "moving")
    else:
        out["moving"] = (f"The pair {pair_id} records no moving qubit. "
                         "Set moving_qubit on the pair first.")
    coupler = p.get("coupler")
    if isinstance(coupler, dict) and isinstance(coupler.get("operations"), dict):
        out["coupler"] = (f"qubit_pairs.{pair_id}.coupler.operations",
                          coupler["operations"])
    else:
        out["coupler"] = f"The pair {pair_id} has no coupler with operations."
    out["_ids"] = (control, target)
    return out


def _resolve_slot_pulse(merged: dict, pair_id: str, macro: dict, field: str,
                        lines: dict) -> tuple[dict | None, str | None, str | None]:
    """(pulse dict, line role, operation key) a gate's pulse field plays."""
    v = macro.get(field)
    key = None
    if isinstance(v, dict):
        key = v.get("id") if isinstance(v.get("id"), str) and not v["id"].startswith("#") else None
        pulse = v
    elif isinstance(v, str):
        if v.startswith("#/"):
            key = v.rstrip("/").rsplit("/", 1)[-1]
        else:
            key = v
        pulse = None
    else:
        return None, None, None
    for line in ("moving", "control", "target", "coupler"):
        ln = lines.get(line)
        if isinstance(ln, tuple) and key and key in ln[1]:
            if line == "moving":
                continue  # report the concrete line, not the alias role
            op = ln[1][key]
            return (pulse if pulse is not None else (op if isinstance(op, dict) else None)), line, key
    return pulse, None, key


# ---------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------

def _fields_for(qclass: str, given: dict, copy_src: dict | None,
                model_pulse: dict | None) -> tuple[dict, dict] | str:
    """The pulse dict (without id) and per-field provenance."""
    spec = pulse_catalog.by_qclass(qclass)
    if spec is not None:
        defaults: dict[str, Any] = {}
        for p in spec.params:
            covered = [n for n in (p.name, *p.aliases) if n in given]
            name = covered[0] if covered else p.name
            if copy_src is not None and not covered:
                alias_in_src = [n for n in (p.name, *p.aliases) if n in copy_src]
                if alias_in_src:
                    name = alias_in_src[0]
            d = p.default
            defaults[name] = list(d) if isinstance(d, tuple) else d
        if spec.length_mode == "inferred":
            defaults["length"] = spec.length_pointer
    elif model_pulse is not None:
        defaults = {k: copy.deepcopy(v) for k, v in model_pulse.items()
                    if k not in ("__class__",)}
    else:
        return (f"SM does not know the fields of the pulse class {qclass}. "
                "Pick a pulse class from the list.")
    unknown = [k for k in given if k not in defaults and k != "id"]
    if unknown:
        return (f"The pulse class {qclass} has no field named {unknown[0]}. "
                "Remove that value.")
    pulse = dict(defaults)
    prov = {k: DEFAULT for k in defaults if k != "id"}
    if copy_src is not None:
        for k, v in copyable_fields(copy_src, defaults).items():
            if k == "length" and isinstance(pulse.get("length"), str):
                continue  # an inferred length stays inferred
            pulse[k] = v
            prov[k] = COPIED
    for k, v in given.items():
        if k == "id":
            continue
        pulse[k] = v
        prov[k] = GIVEN
    pulse.pop("id", None)
    pulse["__class__"] = qclass
    return pulse, prov


def _refuse(reasons: list[str]) -> dict[str, Any]:
    return {"error": reasons[0], "refusals": list(reasons)}


def plan(merged: dict, pair_id: str, *, gate_name: str, gate_class: str,
         pulses: dict[str, dict] | None = None,
         copy_from: str | None = None,
         model_gate: tuple[str, str] | None = None) -> dict[str, Any]:
    """Plan one new CZ gate. Never raises on chip shape.

    ``pulses`` maps a gate pulse field (``flux_pulse_qubit``,
    ``coupler_flux_pulse``, ...) to ``{"class": <pulse __class__>,
    "fields": {...}}``; an optional slot left out is not built. For a copied
    layout the class defaults to the model's pulse class. ``copy_from`` is
    another gate of the same pair whose matching field values seed the
    pulses. ``model_gate`` is ``(pair, gate)`` for a class SM does not know.

    Returns ``{"creates": [(path, value)], "direction", "model_gate",
    "fields": {slot: {field: given|copied|default}}, "sources": {...}}`` or
    ``{"error": str, "refusals": [str]}``.
    """
    pulses = dict(pulses or {})
    pairs = merged.get("qubit_pairs") or {}
    p = pairs.get(pair_id)
    if not isinstance(p, dict):
        return _refuse([f"The pair {pair_id} is not on this chip."])
    macros = p.get("macros")
    if not isinstance(macros, dict):
        return _refuse([f"The pair {pair_id} has no macros to hold a gate."])
    reasons: list[str] = []
    r = name_rule(macros, gate_name)
    if r:
        reasons.append(r)
    lines = _lines(merged, pair_id)
    control, target = lines["_ids"]
    if not (control and target):
        return _refuse(reasons + [f"The pair {pair_id}: its control or target "
                                  "does not resolve to a qubit."])

    copy_macro = None
    if copy_from is not None:
        copy_macro = macros.get(copy_from)
        if not isinstance(copy_macro, dict):
            reasons.append(f"The pair {pair_id} has no gate {copy_from} to copy values from.")
            copy_macro = None

    def copy_source(field):
        if copy_macro is None:
            return None
        src, _, _ = _resolve_slot_pulse(merged, pair_id, copy_macro, field, lines)
        return src

    if gate_class in GATE_CLASSES:
        out = _plan_known(merged, pair_id, p, gate_name, gate_class, pulses,
                          lines, copy_source, reasons)
    else:
        out = _plan_copied(merged, pair_id, gate_name, gate_class, pulses,
                           lines, copy_source, reasons, model_gate)
    if "error" in out:
        return out
    # The plan never overwrites a path and never touches the alias.
    for path, _ in out["creates"]:
        if path.endswith(".macros.cz"):
            reasons.append("The new gate must not replace the pair's cz alias.")
    if reasons:
        return _refuse(reasons)
    return out


def _check_op(ops_path: str, ops: dict, key: str, reasons: list[str]) -> None:
    if key in ops:
        reasons.append(f"The operation {key} already exists on "
                       f"{ops_path.split('.')[1]}. Pick another gate name.")


def _plan_known(merged, pair_id, p, gate_name, gate_class, pulses, lines,
                copy_source, reasons) -> dict[str, Any]:
    desc = GATE_CLASSES[gate_class]
    slots = {s.field: s for s in desc["slots"]}
    for f in pulses:
        if f not in slots:
            reasons.append(f"The gate class has no pulse field {f}.")
    for s in desc["slots"]:
        if s.required and s.field not in pulses:
            reasons.append(f"The gate needs a pulse for {s.field}.")
    if reasons:
        return _refuse(reasons)
    direction = choose_direction(merged, pair_id)
    control, target = lines["_ids"]
    macro = desc["skeleton"]()
    macro_path = f"qubit_pairs.{pair_id}.macros.{gate_name}"
    macro_ref = f"#/qubit_pairs/{pair_id}/macros/{gate_name}"
    op_creates: list[tuple[str, Any]] = []
    provenance: dict[str, dict] = {}
    for s in desc["slots"]:
        if s.field not in pulses:
            continue
        line = lines.get(s.line)
        if not isinstance(line, tuple):
            reasons.append(line)
            continue
        ops_path, ops = line
        spec = pulses[s.field] or {}
        qclass = spec.get("class")
        if not isinstance(qclass, str) or not qclass:
            reasons.append(f"Pick a pulse class for {s.field}.")
            continue
        built = _fields_for(qclass, dict(spec.get("fields") or {}),
                            copy_source(s.field), None)
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
        except ValueError as exc:
            reasons.append(f"{exc}. Pick a shorter or plainer gate name.")
            continue
        _check_op(ops_path, ops, key, reasons)
        if direction == MACRO_OWNS:
            owned = {"id": key, **pulse}
            macro[s.field] = owned
            op_creates.append((f"{ops_path}.{key}",
                               linked_operation(owned, f"{macro_ref}/{s.field}")))
        else:
            macro[s.field] = key
            op_creates.append((f"{ops_path}.{key}", {"id": key, **pulse}))
    if reasons:
        return _refuse(reasons)
    return {"creates": [(macro_path, macro)] + op_creates,
            "direction": direction, "model_gate": None, "fields": provenance,
            "sources": {"control": control, "target": target,
                        "moving_qubit": p.get("moving_qubit")}}


# ---------------------------------------------------------------------------
# Copied layout (a gate class SM does not know)
# ---------------------------------------------------------------------------

def model_candidates(merged: dict, gate_class: str) -> list[tuple[str, str]]:
    """Every ``(pair, gate)`` of *gate_class* on the chip, by pair name."""
    out = []
    for pid in sorted((merged.get("qubit_pairs") or {})):
        macros = (merged["qubit_pairs"][pid] or {}).get("macros")
        if not isinstance(macros, dict):
            continue
        for name, m in macros.items():
            if isinstance(m, dict) and m.get("__class__") == gate_class:
                out.append((pid, name))
    return out


def default_model(merged: dict, pair_id: str, gate_class: str) -> tuple[str, str] | None:
    """The model gate: the same pair's gate of the class, else the first by
    pair name; None when the chip has none."""
    cands = model_candidates(merged, gate_class)
    for c in cands:
        if c[0] == pair_id:
            return c
    return cands[0] if cands else None


def copied_name(model_name: str, model_gate: str, model_ids: tuple, gate: str,
                ids: tuple) -> str | None:
    """A copied pulse name: the gate-name part becomes the new gate name, and
    in the rest each whole ``_``-separated token equal to the model pair's
    control or target id becomes the new pair's control or target id."""
    if not model_name.startswith(model_gate):
        return None
    swap = {model_ids[0]: ids[0], model_ids[1]: ids[1]}
    rest = model_name[len(model_gate):]
    return gate + "_".join(swap.get(tok, tok) for tok in rest.split("_"))


def _rename_values(value: Any, names: dict[str, str]) -> Any:
    """Every string equal to a model name, or a pointer ending in one,
    becomes the new name."""
    if isinstance(value, dict):
        return {k: _rename_values(v, names) for k, v in value.items()}
    if isinstance(value, list):
        return [_rename_values(v, names) for v in value]
    if isinstance(value, str):
        if value in names:
            return names[value]
        if value.startswith("#/"):
            return "/".join(names.get(seg, seg) for seg in value.split("/"))
    return value


def _rewrite_refs(value: Any, old_prefix: str, new_prefix: str) -> Any:
    if isinstance(value, dict):
        return {k: _rewrite_refs(v, old_prefix, new_prefix) for k, v in value.items()}
    if isinstance(value, list):
        return [_rewrite_refs(v, old_prefix, new_prefix) for v in value]
    if isinstance(value, str) and (value == old_prefix or value.startswith(old_prefix + "/")):
        return new_prefix + value[len(old_prefix):]
    return value


def _plan_copied(merged, pair_id, gate_name, gate_class, pulses, lines,
                 copy_source, reasons, model_gate) -> dict[str, Any]:
    if model_gate is None:
        model_gate = default_model(merged, pair_id, gate_class)
        if model_gate is None:
            return _refuse(reasons + [
                f"No gate of the class {gate_class} exists on this chip, so "
                "SM does not know its layout. Pick a class SM knows."])
    mpair, mname = model_gate
    mp = (merged.get("qubit_pairs") or {}).get(mpair) or {}
    model = (mp.get("macros") or {}).get(mname)
    if not isinstance(model, dict) or model.get("__class__") != gate_class:
        return _refuse(reasons + [
            f"The model gate {mname} on {mpair} is not a gate of the class {gate_class}."])
    mlines = _lines(merged, mpair)
    mids = mlines["_ids"]
    control, target = lines["_ids"]
    direction = gate_direction(model) or MACRO_OWNS
    old_ref = f"#/qubit_pairs/{mpair}/macros/{mname}"
    new_ref = f"#/qubit_pairs/{pair_id}/macros/{gate_name}"
    macro = _rewrite_refs(copy.deepcopy(model), old_ref, new_ref)
    op_creates: list[tuple[str, Any]] = []
    provenance: dict[str, dict] = {}
    fields = _pulse_fields(model)
    names: dict[str, str] = {}
    for f in pulses:
        if f not in fields:
            reasons.append(f"The model gate {mname} has no pulse field {f}.")
    for f in fields:
        mpulse, line, mkey = _resolve_slot_pulse(merged, mpair, model, f, mlines)
        if line is None or mkey is None:
            reasons.append(f"SM cannot tell which operation the field {f} of "
                           f"the model gate {mname} plays. Pick another model gate.")
            continue
        key = copied_name(mkey, mname, mids, gate_name, (control, target))
        if key is None:
            reasons.append(f"The operation name {mkey} of the model gate does not "
                           f"start with {mname}, so SM cannot name the copy. "
                           "Pick another model gate.")
            continue
        names[mkey] = key
        from quam_state_manager.core.add_names import OPERATION_NAME_RE
        if not OPERATION_NAME_RE.fullmatch(key):
            reasons.append(f"the name {key} is not a valid name. Pick a shorter or plainer gate name.")
            continue
        ln = lines.get(line)
        if not isinstance(ln, tuple):
            reasons.append(ln)
            continue
        ops_path, ops = ln
        _check_op(ops_path, ops, key, reasons)
        mops = mlines[line][1]
        model_op = mops.get(mkey)
        spec = pulses.get(f) or {}
        qclass = spec.get("class") or (mpulse or {}).get("__class__")
        base = mpulse if isinstance(mpulse, dict) else {}
        if qclass != base.get("__class__"):
            built = _fields_for(qclass, dict(spec.get("fields") or {}),
                                copy_source(f), None)
        else:
            built = _fields_for_model(base, dict(spec.get("fields") or {}),
                                      copy_source(f))
        if isinstance(built, str):
            reasons.append(built)
            continue
        pulse, prov = built
        provenance[f] = prov
        r = length_rule(pulse)
        if r:
            reasons.append(r)
        if direction == MACRO_OWNS and isinstance(model.get(f), dict):
            owned = {**_rewrite_refs(pulse, old_ref, new_ref)}
            if "id" in model[f]:
                owned = {"id": key, **owned}
            macro[f] = owned
            op = _rewrite_refs(copy.deepcopy(model_op), old_ref, new_ref) \
                if isinstance(model_op, dict) else linked_operation(owned, f"{new_ref}/{f}")
            op_creates.append((f"{ops_path}.{key}", op))
        else:
            macro[f] = key if not str(model.get(f, "")).startswith("#/") else \
                "#/" + f"{ops_path}.{key}".replace(".", "/")
            op = {**pulse}
            if isinstance(model_op, dict) and "id" in model_op:
                op = {"id": key, **op}
            op_creates.append((f"{ops_path}.{key}", op))
    if len(set(names.values())) != len(names):
        reasons.append("Two pulses of the model gate get the same name on this "
                       "pair. Pick another model gate.")
    if reasons:
        return _refuse(reasons)
    macro = _rename_values(macro, names)
    op_creates = [(p, _rename_values(v, names)) for p, v in op_creates]
    return {"creates": [(f"qubit_pairs.{pair_id}.macros.{gate_name}", macro)] + op_creates,
            "direction": direction, "model_gate": (mpair, mname),
            "fields": provenance,
            "sources": {"control": control, "target": target,
                        "moving_qubit": (merged["qubit_pairs"][pair_id]).get("moving_qubit")}}


def _fields_for_model(model_pulse: dict, given: dict,
                      copy_src: dict | None) -> tuple[dict, dict] | str:
    """A pulse of the model's own class: the model's values, then copied,
    then given values (the class is known only through the model)."""
    defaults = {k: copy.deepcopy(v) for k, v in model_pulse.items()
                if k not in ("__class__", "id")}
    unknown = [k for k in given if k not in defaults]
    if unknown:
        return (f"The model pulse has no field named {unknown[0]}. Remove that value.")
    pulse = dict(defaults)
    prov = {k: DEFAULT for k in defaults}
    if copy_src is not None:
        for k, v in copyable_fields(copy_src, defaults).items():
            pulse[k] = v
            prov[k] = COPIED
    for k, v in given.items():
        pulse[k] = v
        prov[k] = GIVEN
    pulse["__class__"] = model_pulse.get("__class__")
    return pulse, prov
