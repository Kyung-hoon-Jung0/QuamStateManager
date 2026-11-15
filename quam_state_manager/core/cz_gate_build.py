"""Plan the creation of one new CZ gate on one qubit pair (pure).

``plan`` turns "create gate G of class C on pair P" into the ordered list of
state paths and values to create: a route writes the creates under ONE
change group. It
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
import math
import re
from dataclasses import dataclass
from typing import Any

from quam_state_manager.core import pulse_catalog
from quam_state_manager.core.add_names import (
    ADDITION_NAME_RE, OPERATION_NAME_RE, pulse_name)
from quam_state_manager.core.pointer_path import resolve_field_target
from quam_state_manager.core.pointer_resolver import (
    _SENTINEL, _get, _walk_relative, target_path)

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


def _desc(gate_class: Any, gate_desc: dict | None = None) -> dict[str, Any] | None:
    """The descriptor a gate of *gate_class* is built from: its row in
    ``GATE_CLASSES``, else the descriptor the caller handed in (same keys as
    a row), else None. The table always wins, so a caller cannot redefine a
    class SM already knows."""
    row = GATE_CLASSES.get(gate_class)
    return row if row is not None else gate_desc


#: the lines a slot can play on (the line roles ``_lines`` answers)
SLOT_LINES = ("moving", "control", "target", "coupler")

#: the one answer to a handed-in descriptor SM cannot build a gate from
BAD_DESCRIPTION = (
    "SM's own description of this gate class is not one it can build a gate "
    "from, so it plans no gate of this class. Nothing was changed. Report this "
    "with the gate class.")


def desc_problem(gate_class: Any, gate_desc: Any) -> str | None:
    """Why the descriptor handed in for *gate_class* cannot be used, or None.

    None when no descriptor was given or the table holds the class (the
    descriptor is then never read). A usable descriptor is a dict with a
    callable ``skeleton`` and ``slots`` that is a list or tuple of ``Slot``
    rows: field names, roles and lines SM knows, none of them used by two
    rows (a role names the row's operation and a line is its channel, so a
    repeat could never be planned), no field named ``__class__``, and at
    least one row required, so a planned gate always holds a pulse. Reads
    the descriptor only; calls nothing (what the skeleton returns is checked
    by :func:`_skeleton_problem` when a gate is planned)."""
    if gate_desc is None:
        return None
    if gate_class in GATE_CLASSES:
        return None
    if not isinstance(gate_desc, dict):
        return BAD_DESCRIPTION
    if not callable(gate_desc.get("skeleton")):
        return BAD_DESCRIPTION
    slots = gate_desc.get("slots")
    if not isinstance(slots, (list, tuple)):
        return BAD_DESCRIPTION
    for s in slots:
        if not isinstance(s, Slot):
            return BAD_DESCRIPTION
        if not isinstance(s.field, str):
            return BAD_DESCRIPTION
        if not s.field:
            return BAD_DESCRIPTION
        if s.field == "__class__":
            return BAD_DESCRIPTION
        if s.role not in ROLES:
            return BAD_DESCRIPTION
        if s.line not in SLOT_LINES:
            return BAD_DESCRIPTION
        if not isinstance(s.required, bool):
            return BAD_DESCRIPTION
    if len({s.field for s in slots}) != len(slots):
        return BAD_DESCRIPTION
    if len({s.role for s in slots}) != len(slots):
        return BAD_DESCRIPTION
    if len({s.line for s in slots}) != len(slots):
        return BAD_DESCRIPTION
    if not any(s.required for s in slots):
        return BAD_DESCRIPTION
    return None


def _skeleton_problem(macro: Any, slots: Any) -> str | None:
    """Why the macro a handed-in skeleton returned cannot hold a gate, or
    None. A usable one is a dict whose ``__class__`` is a non-empty string
    and that holds every slot field of *slots*, each still empty (None)."""
    if not isinstance(macro, dict):
        return BAD_DESCRIPTION
    cls = macro.get("__class__")
    if not isinstance(cls, str):
        return BAD_DESCRIPTION
    if not cls:
        return BAD_DESCRIPTION
    for s in slots:
        if s.field not in macro:
            return BAD_DESCRIPTION
        if macro[s.field] is not None:
            return BAD_DESCRIPTION
    return None


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


def _deciding_gates(merged: dict, pair_id: str) -> list[tuple[dict, str, str]]:
    """The CZ gates that decide the direction, each with its direction: the
    pair's own gates when it has any with a pulse field, else every such
    gate on the chip, each as (macro, direction, pair). Alias pointers are
    not gates."""
    def gates(pairs):
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
                        out.append((m, d, pid))
        return out

    own = gates([pair_id])
    if own:
        return own
    return gates(list((merged.get("qubit_pairs") or {}).keys()))


def choose_direction(merged: dict, pair_id: str) -> str:
    """Operation-owns-pulse only when every CZ gate of the pair is, or the
    pair has none and every CZ gate on the chip is (at least one); otherwise
    macro-owns-pulse. Alias pointers are not gates."""
    found = _deciding_gates(merged, pair_id)
    if found and all(d == OPERATION_OWNS for _, d, _p in found):
        return OPERATION_OWNS
    return MACRO_OWNS


def _is_plain_name(v: Any) -> bool:
    return isinstance(v, str) and bool(v) and not v.startswith("#")


def name_form_fields(merged: dict, pair_id: str, slot_lines: dict) -> set:
    """Slot fields (field -> line role) that every gate deciding the direction
    fills with a plain operation name: the key of an operation that exists
    on that slot's channel of the gate's own pair. Only there does an
    operation-owned gate get a name; in every other field it gets an
    absolute reference."""
    found = _deciding_gates(merged, pair_id)
    if not found:
        return set()
    out = set()
    for field, role in slot_lines.items():
        ok = True
        for m, _d, pid in found:
            v = m.get(field)
            ln = _lines(merged, pid).get(role)
            if not (_is_plain_name(v) and isinstance(ln, tuple) and isinstance(ln[1].get(v), dict)):
                ok = False
                break
        if ok:
            out.add(field)
    return out


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
    no_z = all(isinstance(qubits.get(q), dict) and qubits[q].get("z") is None
               for q in (control, target) if q) and bool(control and target)
    if no_z:
        out["moving"] = (f"The qubits of pair {pair_id} have no flux line, so a "
                         "gate that plays on a qubit flux line cannot run here. "
                         "Pick the coupler-only gate class.")
    elif role in ("control", "target"):
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


#: the refusal when no gate class was given; it belongs to the class field
NO_CLASS = "The gate class must be given by its class name. Pick a gate class from the list."


def class_problem(merged: dict, pair_id: str, gate_class: str, *,
                  gate_desc: dict | None = None) -> str | None:
    """Why a gate of *gate_class* cannot run on *pair_id*, or None.

    Only the line facts the plan itself refuses on: a required pulse slot
    whose line has no operations gives the very reason ``plan`` gives for
    it. A class SM does not know (a copied layout) is judged by its plan.
    ``gate_desc`` describes a class that has no row in ``GATE_CLASSES``; one
    SM cannot build a gate from gives the sentence ``plan`` refuses with."""
    bad = desc_problem(gate_class, gate_desc)
    if bad:
        return bad
    desc = _desc(gate_class, gate_desc)
    if desc is None:
        return None
    try:
        lines = _lines(merged, pair_id)
    except (KeyError, TypeError, AttributeError):
        return None
    for s in desc["slots"]:
        line = lines.get(s.line)
        if s.required and isinstance(line, str):
            return line
    return None


def _dot(segs: tuple, key: str | None = None) -> str:
    return ".".join(segs + ((key,) if key is not None else ()))


_LIST_INDEX = re.compile(r"0|[1-9][0-9]*")


def _walk_path(merged: Any, segs) -> Any:
    """The node at *segs*; a list step must be a plain non-negative decimal
    index without a leading zero or sign. _SENTINEL when it does not exist."""
    node = merged
    for seg in segs:
        if isinstance(node, dict) and seg in node:
            node = node[seg]
        elif (isinstance(node, list) and isinstance(seg, str)
              and _LIST_INDEX.fullmatch(seg) and int(seg) < len(node)):
            node = node[int(seg)]
        else:
            return _SENTINEL
    return node


def _resolve_reference(merged: dict, ref: str, holder: tuple | None
                       ) -> tuple[tuple, Any] | None:
    """(path, object) a reference held at *holder* (the path of the holding
    field) names in *merged*, with the meaning the state gives it; None when
    it does not resolve."""
    try:
        if ref.startswith("#/"):
            t = tuple(x for x in ref[2:].split("/") if x)
        else:
            if holder is None:
                return None
            ptr = "#" + ref[3:] if ref.startswith("#./../") else ref
            t = (_walk_relative(merged, ptr, holder) if ptr.startswith("#../")
                 else target_path(merged, ptr, holder))
            if t is None:
                return None
            t = tuple(t)
        node = _walk_path(merged, t)
        return None if node is _SENTINEL else (t, node)
    except Exception:  # noqa: BLE001 -- a reference that cannot be walked does not resolve
        return None


def _is_pulse_place(path: tuple) -> bool:
    """A pulse sits in an ``operations`` container of a channel, or inline in
    a macro's pulse field (list steps below either place are allowed). A gate
    macro, a channel, a qubit or a pair is not a pulse, whatever its class."""
    p = list(path)
    # Which list steps are valid is the walker's decision; here any step a
    # list could take is skipped.
    while len(p) > 1 and str(p[-1]).strip().lstrip("-").isdigit() and p[-2] != "operations":
        p.pop()
    if len(p) >= 2 and p[-2] == "operations":
        return True
    return len(p) >= 3 and p[-3] == "macros" and "pulse" in str(p[-1])


def _resolve_slot_pulse(macro: dict, field: str, lines: dict, merged: dict | None = None,
                        holder: tuple | None = None
                        ) -> tuple[dict | None, str | None, str | None]:
    """(pulse dict, line role, operation key) a gate's pulse field plays.

    With *merged*, a reference is followed as the state reads it (a
    relative one from the field under *holder*, the macro's path) and only
    its target counts; without it, a reference is matched by its last
    segment on the pair's lines."""
    v = macro.get(field)
    key = None
    if isinstance(v, dict):
        key = v.get("id") if isinstance(v.get("id"), str) and not v["id"].startswith("#") else None
        pulse = v
    elif isinstance(v, str):
        key = v.rstrip("/").rsplit("/", 1)[-1] if v.startswith("#") else v
        pulse = None
        if merged is not None and v.startswith("#"):
            hit = _resolve_reference(merged, v, None if holder is None
                                     else tuple(holder) + (field,))
            if hit is not None and isinstance(hit[1], dict) and _is_pulse_place(hit[0]):
                pulse = hit[1]
            for line in ("control", "target", "coupler"):
                ln = lines.get(line)
                if isinstance(ln, tuple) and key and key in ln[1]:
                    return pulse, line, key
            return pulse, None, key
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
        pulse[k] = keep_float(defaults.get(k), copy.deepcopy(v))
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


def _gate_path(macros: dict, name: str, merged: dict, pair_id: str) -> tuple | None:
    """The path of the gate *name* names on the pair, following an alias one hop."""
    v = macros.get(name)
    base = ("qubit_pairs", pair_id, "macros")
    if isinstance(v, dict):
        return base + (name,)
    if isinstance(v, str):
        if v.startswith("#./"):
            return base + (v[3:],)
        hop = _follow(merged, v)
        return hop[0] if hop else None
    return None


def _argument_problem(pair_id, gate_name, gate_class, pulses, model_gate,
                      copy_from) -> str | None:
    """A plain reason when an argument has the wrong shape."""
    if not isinstance(pair_id, str) or not pair_id:
        return "The pair must be given by its name. Pick a pair from the list."
    if not isinstance(gate_name, str) or not gate_name:
        return "The gate name must be text that starts with cz_. Type a name like cz_square."
    if not isinstance(gate_class, str) or not gate_class:
        return NO_CLASS
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
         model_gate: tuple[str, str] | None = None,
         macro_fields: dict[str, Any] | None = None,
         spectators: list | None = None,
         gate_desc: dict | None = None) -> dict[str, Any]:
    """Plan one new CZ gate. Never raises on chip shape; never changes *merged*.

    ``pulses`` maps a gate pulse field (``flux_pulse_qubit``,
    ``coupler_flux_pulse``, ...) to ``{"class": <pulse __class__>,
    "fields": {...}}``. For a table class an optional slot left out is not
    built. A copied layout always builds exactly the model's pulse slots
    and keeps the model's pulse classes; its ``pulses`` entries only give
    field values. ``copy_from`` is another gate of the same pair whose
    matching field values seed the pulses. ``model_gate`` is ``(pair,
    gate)`` for a class SM does not know. ``macro_fields`` gives values for
    the gate's own settings in a copied layout (keys the model macro has,
    never a pulse field, a reset key or a reference); they are reported as
    ``given`` in ``fields["macro"]``. Malformed arguments are refusals.
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

    ``spectators`` (official class only, see :func:`spectator_refusal`):
    ``[{"qubit", "length" (ns), "level" (V)}]``; each becomes a square pulse
    operation on that qubit's flux line, filled into the macro's three
    spectator fields. With spectators the answer also carries
    ``"spectators": [...]`` and ``"spectator_ops": {qubit: count}``; without
    them the answer is exactly what it is for a gate with no spectators.

    ``gate_desc`` is the descriptor (``{"skeleton": callable, "slots":
    (Slot, ...)}``) of a class that has no row in ``GATE_CLASSES``; with it
    the class is built like a table class instead of copied from a model
    gate. It is ignored for a class the table already holds. A descriptor
    that fails :func:`desc_problem`, or whose skeleton returns a macro SM
    cannot fill, is refused with the same sentence; spectators
    for a described class are refused too (they are planned separately).
    """
    try:
        out = _plan_body(merged, pair_id, gate_name, gate_class, pulses,
                         copy_from, model_gate, macro_fields, spectators,
                         gate_desc=gate_desc)
        if "refusals" in out:
            names = _echoed_names(pair_id, gate_name, gate_class, pulses, copy_from,
                                  model_gate, macro_fields) + _spectator_names(spectators)
            out = _refuse([cap_names(r, names) for r in out["refusals"]])
        return out
    except Exception as exc:  # noqa: BLE001 -- the last net: plan never raises
        return _refuse([f"SM could not plan this gate ({type(exc).__name__}). "
                        "Nothing was changed. Report this with the gate class "
                        "and the pair."])


#: the longest name a refusal echoes in full
ECHO_CAP = 64


def cap_name(name: str) -> str:
    """*name* as a refusal shows it: at most :data:`ECHO_CAP` characters, a
    longer one cut with an ellipsis."""
    name = str(name)
    return name if len(name) <= ECHO_CAP else name[:ECHO_CAP - 1] + "…"


def cap_names(text: str, names) -> str:
    """*text* with every long name in *names* cut to :func:`cap_name`."""
    for n in sorted({str(n) for n in names if isinstance(n, str) and len(n) > ECHO_CAP},
                    key=len, reverse=True):
        text = text.replace(n, cap_name(n))
    return text


def _echoed_names(pair_id, gate_name, gate_class, pulses, copy_from, model_gate,
                  macro_fields) -> list:
    """Every name the person gave that a refusal may echo."""
    out = [pair_id, gate_name, gate_class, copy_from]
    if isinstance(model_gate, (list, tuple)):
        out.extend(model_gate)
    if isinstance(pulses, dict):
        for f, spec in pulses.items():
            out.append(f)
            if isinstance(spec, dict):
                out.append(spec.get("class"))
                if isinstance(spec.get("fields"), dict):
                    out.extend(spec["fields"])
    if isinstance(macro_fields, dict):
        out.extend(macro_fields)
    return [n for n in out if isinstance(n, str)]


def _plan_body(merged, pair_id, gate_name, gate_class, pulses, copy_from,
               model_gate, macro_fields=None, spectators=None, *,
               gate_desc=None) -> dict[str, Any]:
    bad = _argument_problem(pair_id, gate_name, gate_class, pulses, model_gate,
                            copy_from)
    if bad:
        return _refuse([bad])
    bad = desc_problem(gate_class, gate_desc)
    if bad:
        return _refuse([bad])
    if spectators is not None and not isinstance(spectators, list):
        return _refuse([f"The spectators of pair {pair_id} must be a list of qubits, "
                        "each with a length and a level. Pick the spectators again."])
    if spectators:
        why = spectator_refusal(gate_class, gate_desc=gate_desc)
        if why:
            return _refuse([why])
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
    desc = _desc(gate_class, gate_desc)
    if desc is not None and not (control and target):
        return _refuse(reasons + [
            f"The control or target of pair {pair_id} does not point at a qubit, "
            "so SM cannot name the gate's pulses. Set the pair's qubit_control "
            "and qubit_target first."])
    if desc is not None:
        # Both classes SM builds align the channels of BOTH qubits of the pair
        # when the gate runs, so a pair that names a qubit the chip does not
        # have cannot run such a gate, whichever line the pulse plays on.
        qubits = merged.get("qubits")
        for role, q in (("control", control), ("target", target)):
            if not (isinstance(qubits, dict) and isinstance(qubits.get(q), dict)):
                return _refuse(reasons + [
                    f"The {role} of pair {pair_id} points at the qubit {q}, which "
                    "this chip does not have. A gate of this class uses both "
                    f"qubits of the pair when it runs. Fix the pair's qubit_{role} "
                    "first, or pick another pair."])

    copy_macro = None
    copy_path = None
    if copy_from is not None:
        copy_macro, why = _gate_or_alias(macros, copy_from, merged, pair_id)
        if why:
            # Without the source gate, no slot can be judged: refuse on the
            # cause alone so no advice about slots is given that does not fit.
            return _refuse(reasons + [why])
        else:
            copy_path = _gate_path(macros, copy_from, merged, pair_id)

    def copy_source(field):
        if copy_macro is None:
            return None
        return _resolve_slot_pulse(copy_macro, field, lines, merged, copy_path)[0]

    copy_source.raw = lambda field: (
        None if copy_macro is None else (copy_from, copy_macro.get(field)))

    if macro_fields is not None and not isinstance(macro_fields, dict):
        return _refuse(reasons + ["The gate settings must be given by key. Type them again."])
    if desc is not None:
        if macro_fields:
            return _refuse(reasons + [
                f"This gate class has no gate settings to change ({', '.join(sorted(macro_fields))}). "
                "Remove them."])
        out = _plan_known(merged, pair_id, p, gate_name, gate_class, pulses,
                          lines, copy_source, reasons, spectators or [],
                          gate_desc=gate_desc)
    else:
        out = _plan_copied(merged, pair_id, gate_name, gate_class, pulses,
                           lines, copy_source, reasons, model_gate, macro_fields)
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
                copy_source, reasons, spectators=(), *,
                gate_desc=None) -> dict[str, Any]:
    desc = _desc(gate_class, gate_desc)
    macro = desc["skeleton"]()
    if gate_class not in GATE_CLASSES:
        bad = _skeleton_problem(macro, desc["slots"])
        if bad:
            return _refuse([bad])
    slots = {s.field: s for s in desc["slots"]}
    have = ", ".join(slots)
    copy_refused: set = set()
    for f in pulses:
        if f not in slots:
            reasons.append(f"This gate class has no pulse field {f} (its pulse "
                           f"fields are {have}). Remove that pulse.")
    for s in desc["slots"]:
        spec = pulses.get(s.field)
        if isinstance(spec, dict) and isinstance(spec.get("class"), str) and spec["class"]:
            continue
        src = copy_source(s.field)
        cls = src.get("__class__") if isinstance(src, dict) else None
        if isinstance(cls, str) and cls:
            pulses[s.field] = {"class": cls,
                               "fields": dict((spec or {}).get("fields") or {})}
            continue
        raw = getattr(copy_source, "raw", lambda f: None)(s.field)
        if raw is not None and s.required:
            src_name, held = raw
            if isinstance(held, str) and held:
                why = (f"The gate {src_name} holds the reference {held} in "
                       f"{s.field}, which does not lead to a pulse.")
            else:
                why = f"The gate {src_name} holds no pulse in {s.field} to copy."
            reasons.append(f"{why} Pick a pulse class for {s.field}, or start "
                           "from another gate.")
            copy_refused.add(s.field)
    for s in desc["slots"]:
        if s.required and s.field not in pulses and s.field not in copy_refused:
            extra = (" A coupler pulse alone is not a gate of this class; for a "
                     "coupler-only gate pick the coupler-only gate class."
                     if gate_class == CZ_GATE else "")
            reasons.append(f"This gate needs a pulse for {s.field}. Pick a pulse "
                           f"class for it.{extra}")
    if reasons:
        return _refuse(reasons)
    direction = choose_direction(merged, pair_id)
    named = (name_form_fields(merged, pair_id, {f: x.line for f, x in slots.items()})
             if direction == OPERATION_OWNS else set())
    control, target = lines["_ids"]
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
            if s.field in named:
                macro[s.field] = key
                operations[s.field]["macro_holds"] = "name"
            else:
                macro[s.field] = _ref(*segs, key)
                operations[s.field]["macro_holds"] = "reference"
            op_creates.append((_dot(segs, key), {"id": key, **pulse}))
    extra: dict[str, Any] = {}
    if spectators:
        extra = _plan_spectators(merged, pair_id, gate_name, spectators, macro,
                                 macro_segs, direction, op_creates, operations,
                                 provenance, reasons)
    if reasons:
        return _refuse(reasons)
    return {**extra, "creates": [(_dot(macro_segs), macro)] + op_creates,
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
# Spectators (official class only)
# ---------------------------------------------------------------------------

#: the macro fields the official class reads its spectators from
SPECTATOR_QUBITS = "spectator_qubits"
SPECTATOR_CONTROL = "spectator_qubits_control"
SPECTATOR_PHASE = "spectator_qubits_phase_shift"
SPECTATOR_ROLE = "spectator_pulse"
SPECTATOR_CLASS = "quam.components.pulses.SquarePulse"

#: the note every new spectator carries about its phase shift
NOT_CALIBRATED = ("The phase shift of this spectator starts at 0 and is not calibrated. "
                  "Calibrate it before relying on the gate.")

NO_SPECTATORS_COUPLER = (
    "The coupler-only gate class plays only the coupler, so SM adds no spectator "
    "pulses to it. Pick the CZ gate (quam-builder) class to add spectators.")
NO_SPECTATORS_COPIED = (
    "This gate copies the layout of a gate on this chip, and SM does not know where "
    "that class keeps spectator pulses, so it offers none. Pick the CZ gate "
    "(quam-builder) class to add spectators.")


NO_SPECTATORS_DESCRIBED = (
    "This step plans the gate's own pulses only. Leave the spectators out of "
    "this request.")


def spectator_refusal(gate_class: Any, *, gate_desc: Any = None) -> str | None:
    """None when the window offers spectators for *gate_class* (the official
    class), else the sentence saying why it does not. ``gate_desc`` is the
    descriptor handed to ``plan`` for a class the table does not hold: such a
    class copies no gate, and ``plan`` adds no spectator pulses to it, so it
    gets a sentence that says only that."""
    if gate_class == CZ_GATE:
        return None
    if gate_class == COUPLER_CZ_GATE:
        return NO_SPECTATORS_COUPLER
    if gate_desc is not None:
        return NO_SPECTATORS_DESCRIBED
    return NO_SPECTATORS_COPIED


def _spectator_names(spectators: Any) -> list[str]:
    if not isinstance(spectators, list):
        return []
    return [s["qubit"] for s in spectators
            if isinstance(s, dict) and isinstance(s.get("qubit"), str)]


def _port_node(merged: dict, ports_ref: str) -> Any:
    node: Any = merged
    for seg in ports_ref[2:].split("/"):
        node = node.get(seg) if isinstance(node, dict) else None
    return node


def spectator_line(merged: dict, qubit: str) -> dict[str, Any] | str:
    """The flux line *qubit* would play a spectator pulse on, or a plain
    reason it has none: ``{"qubit", "line", "segs", "ops", "port", "range",
    "offset"}``. ``port`` is the port reference (None when SM cannot follow
    it), ``range`` the port's output range in V (None when not known) and
    ``offset`` the port's DC offset in V (0 when not stated). ``dc_levels``
    lists every DC level the line may hold while the gate plays
    (:func:`flux_dc.line_dc_levels`: port offset and flux-point offset, in V
    at the port); a level is checked against the range on top of each."""
    from quam_state_manager.core.diagnostics import _follow_to_ports_ref, _sample_bound
    from quam_state_manager.core.flux_dc import landed_path, line_dc_levels
    qubits = merged.get("qubits") if isinstance(merged, dict) else None
    entry = qubits.get(qubit) if isinstance(qubits, dict) else None
    qpath: tuple = ("qubits", qubit)
    if isinstance(entry, str) and entry.startswith("#"):
        real = landed_path(merged, qpath)
        entry = _get(merged, list(real)) if real is not None else None
        qpath = real or qpath
        if not isinstance(entry, dict):
            return (f"The spectator {qubit} is a reference ({qubits[qubit]}) that SM "
                    "could not follow to a qubit. Pick another qubit, or fix the reference.")
    if not isinstance(entry, dict):
        return (f"The spectator {qubit} is not a qubit of this chip. Pick a qubit "
                "from the list.")
    z = entry.get("z")
    if not isinstance(z, dict) or not isinstance(z.get("operations"), dict):
        return (f"The spectator {qubit} has no pulsed flux line with operations, so it "
                "cannot play a spectator pulse. Pick another qubit.")
    port, rng, off = None, None, 0.0
    out_ref = z.get("opx_output")
    if isinstance(out_ref, str):
        try:
            port = _follow_to_ports_ref(merged, out_ref)
        except Exception:  # noqa: BLE001 -- an unreadable port is an unknown port
            port = None
    if port:
        rng = _sample_bound(merged, port)[0]
        node = _port_node(merged, port)
        o = node.get("offset") if isinstance(node, dict) else None
        if isinstance(o, (int, float)) and not isinstance(o, bool) and math.isfinite(o):
            off = float(o)
    levels, why = line_dc_levels(merged, tuple(qpath) + ("z",), off)
    if why:
        return (f"The spectator {qubit}: SM cannot tell the DC level of its flux line "
                f"while the gate plays ({why}). Fix that value, or pick another qubit.")
    return {"qubit": qubit, "line": f"{qubit}.z", "segs": tuple(qpath) + ("z", "operations"),
            "ops": z["operations"], "port": port, "range": rng, "offset": off,
            "dc_levels": levels}


def spectator_candidates(merged: dict, pair_id: str) -> list[dict[str, Any]]:
    """The qubits that may be chosen as spectators of a gate on *pair_id*:
    every qubit other than the pair's control and target with a pulsed flux
    line, each ``{"qubit", "line", "port", "range"}`` (port and range None
    when SM cannot tell)."""
    control = pair_qubit(merged, pair_id, "qubit_control")
    target = pair_qubit(merged, pair_id, "qubit_target")
    out = []
    for q in sorted((merged.get("qubits") or {}) if isinstance(merged, dict) else {}):
        if q in (control, target):
            continue
        ln = spectator_line(merged, str(q))
        if isinstance(ln, dict):
            out.append({"qubit": ln["qubit"], "line": ln["line"], "port": ln["port"],
                        "range": ln["range"]})
    return out


def _qubit_of_ref(ref: Any) -> str | None:
    if isinstance(ref, str) and ref.startswith("#/qubits/"):
        rest = ref[len("#/qubits/"):].split("/")
        return rest[0] if len(rest) == 1 and rest[0] else None
    return None


def spectator_ops_on_lines(merged: dict) -> dict[str, set]:
    """``{qubit: {operation key}}``: the operations on each qubit's flux line
    that existing gates play as spectators, read from the gates' own
    spectator lists (never from an operation's name). A reference names its
    line and key; a macro-owned pulse sits on its qubit's line under its id.
    Only operations the line actually holds are counted."""
    out: dict[str, set] = {}
    qubits = (merged or {}).get("qubits") or {}
    for pv in ((merged or {}).get("qubit_pairs") or {}).values():
        if not isinstance(pv, dict):
            continue
        for macro in (pv.get("macros") or {}).values():
            if not isinstance(macro, dict) or not isinstance(macro.get(SPECTATOR_CONTROL), dict):
                continue
            who = macro.get(SPECTATOR_QUBITS)
            for q, v in macro[SPECTATOR_CONTROL].items():
                owner = _qubit_of_ref(who.get(q) if isinstance(who, dict) else None) or str(q)
                if isinstance(v, str) and v.startswith("#/qubits/"):
                    segs = v[2:].split("/")
                    if len(segs) != 5 or segs[2:4] != ["z", "operations"]:
                        continue
                    owner, key = segs[1], segs[4]
                elif isinstance(v, dict) and isinstance(v.get("id"), str) and v["id"]:
                    key = v["id"]
                else:
                    continue
                entry = qubits.get(owner)
                z = entry.get("z") if isinstance(entry, dict) else None
                ops = z.get("operations") if isinstance(z, dict) else None
                if isinstance(ops, dict) and key in ops:
                    out.setdefault(owner, set()).add(key)
    return out


def count_request_spectators(merged: dict, plans: list) -> None:
    """Set every planned pair's ``spectator_ops`` to what each line holds
    after the WHOLE request: the existing gates' spectator operations plus
    the new ones of every pair that is not refused. *plans* is
    ``[(pair, plan)]`` in request order; changed in place."""
    held = spectator_ops_on_lines(merged)
    for _pid, p in plans:
        if "error" not in p:
            for row in p.get("spectators") or []:
                held.setdefault(row["qubit"], set()).add(row["operation"])
    for _pid, p in plans:
        if "error" not in p and "spectator_ops" in p:
            p["spectator_ops"] = {q: len(held.get(q, ())) for q in p["spectator_ops"]}


def range_not_compared(line: str, port: Any) -> str:
    """The sentence a spectator row carries when its level could not be
    compared with its port's output range (the lab check's words)."""
    from quam_state_manager.core.gate_check import range_unknown_words
    why = range_unknown_words(port) if port else f"SM cannot follow the port of {line}"
    return f"The level on {line} was not compared with its port's output range: {why}."


def _spectator_problem(merged, pair_id, spec, control, target, seen) -> tuple[Any, str | None]:
    """``(line info with "length" and "level", None)`` or ``(None, reason)``."""
    if not isinstance(spec, dict) or not isinstance(spec.get("qubit"), str) or not spec["qubit"]:
        return None, "Each spectator needs a qubit, a length and a level. Pick the spectators again."
    q = spec["qubit"]
    if q in seen:
        return None, f"The spectator {q} is listed twice. Keep one entry for it."
    seen.add(q)
    for role, who in (("control", control), ("target", target)):
        if q == who:
            return None, (f"The spectator {q} is the {role} of pair {pair_id}. A spectator "
                          "must be another qubit.")
    ln = spectator_line(merged, q)
    if isinstance(ln, str):
        return None, ln
    length = spec.get("length")
    if length is None:
        return None, (f"For the spectator {q}: the length is required. Type a whole "
                      "number of ns such as 48.")
    if isinstance(length, str):
        return None, (f"For the spectator {q}: the length {length!r} must be a number "
                      "of ns. Type a whole number such as 48.")
    probe = {"length": length}
    r = length_rule(probe)
    if r:
        return None, f"For the spectator {q}: {r[0].lower()}{r[1:]}"
    level = spec.get("level")
    if level is None:
        return None, (f"For the spectator {q}: the level is required. Type a level in V.")
    if isinstance(level, bool) or not isinstance(level, (int, float)) or not math.isfinite(level):
        return None, (f"For the spectator {q}: the level {level!r} must be a number of V. "
                      "Type a level such as 0.05.")
    if level == 0:
        return None, (f"For the spectator {q}: a level of 0 V plays nothing. Type a level, "
                      "or remove the spectator.")
    rng = ln["range"]
    # An absolute 1e-12 V margin: it only absorbs float rounding of the sum,
    # far below any DAC step, so it is the same for every range.
    for label, dc in ln["dc_levels"]:
        total = float(level) + dc
        if rng is not None and abs(total) > rng + 1e-12:
            off = f" on top of its {dc:g} V {label} ({total:g} V)" if dc else ""
            return None, (f"For the spectator {q}: {ln['line']} would play {float(level):g} V"
                          f"{off}, outside its port's range of ±{rng:g} V. Lower the level.")
    return {**ln, "length": probe["length"], "level": float(level)}, None


def _plan_spectators(merged, pair_id, gate_name, spectators, macro, macro_segs,
                     direction, op_creates, operations, provenance, reasons) -> dict[str, Any]:
    """Fill the macro's spectator fields and add one square pulse operation
    per spectator (the way the macro's own pulse is held: a macro-owned pulse
    with a linked operation, or an operation the macro refers to). Returns
    the answer's spectator keys; problems go to *reasons*."""
    control = pair_qubit(merged, pair_id, "qubit_control")
    target = pair_qubit(merged, pair_id, "qubit_target")
    seen: set = set()
    rows, counts = [], {}
    for spec in spectators:
        info, why = _spectator_problem(merged, pair_id, spec, control, target, seen)
        if why:
            reasons.append(why)
            continue
        q = info["qubit"]
        try:
            key = operation_name(gate_name, SPECTATOR_ROLE, control, target)
        except ValueError:
            reasons.append(_name_problem(f"{gate_name}_{SPECTATOR_ROLE}_{control}_{target}",
                                         (control, target)))
            continue
        line = (info["segs"], info["ops"], info["line"])
        _check_op(line, key, reasons)
        built = _fields_for(SPECTATOR_CLASS, {"length": info["length"],
                                              "amplitude": info["level"]}, None)
        if isinstance(built, str):
            reasons.append(built)
            continue
        pulse, prov = built
        field = f"{SPECTATOR_CONTROL}.{q}"
        provenance[field] = prov
        op_path = _dot(info["segs"], key)
        operations[field] = {"path": op_path, "key": key, "channel": info["line"],
                             "created": True, "group": "spectator", "qubit": q}
        macro[SPECTATOR_QUBITS][q] = _ref("qubits", q)
        macro[SPECTATOR_PHASE][q] = 0.0
        if direction == MACRO_OWNS:
            owned = {"id": key, **pulse}
            macro[SPECTATOR_CONTROL][q] = owned
            operations[field]["macro_holds"] = "pulse"
            op_creates.append((op_path, linked_operation(
                owned, _ref(*macro_segs, SPECTATOR_CONTROL, q))))
        else:
            macro[SPECTATOR_CONTROL][q] = _ref(*info["segs"], key)
            operations[field]["macro_holds"] = "reference"
            op_creates.append((op_path, {"id": key, **pulse}))
        counts[q] = len(spectator_ops_on_lines(merged).get(q, set()) | {key})
        row = {"qubit": q, "line": info["line"], "operation": key, "path": op_path,
               "length": info["length"], "level": info["level"],
               "port": info["port"], "range": info["range"],
               "phase_shift": 0.0, "not_calibrated": NOT_CALIBRATED}
        if info["range"] is None:
            row["range_not_compared"] = range_not_compared(info["line"], info["port"])
        rows.append(row)
    return {"spectators": rows, "spectator_ops": counts}


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


def _is_climbing(v: Any) -> bool:
    """A relative reference that climbs: ``#../...``, or ``#./...`` with a
    ``..`` segment."""
    if not isinstance(v, str):
        return False
    if v.startswith("#../"):
        return True
    return v.startswith("#./") and ".." in v[3:].split("/")


def _textual_target(ref: str, holder: tuple) -> tuple | None:
    """The path a relative reference names by plain path arithmetic on the
    holder path (each ``..`` one level up from the holding object, names
    appended), without following any reference string."""
    body = ref[1:]
    if body.startswith("./"):
        cur = list(holder[:-1])
        segs = body[2:].split("/")
    else:
        cur = list(holder[:-1])
        segs = body.split("/")
    for seg in segs:
        if seg in ("", "."):
            continue
        if seg == "..":
            if not cur:
                return None
            cur.pop()
        else:
            cur.append(seg)
    return tuple(cur)


def _is_index(seg: str) -> bool:
    """A path segment that names a list index: ASCII digits only."""
    return seg.isascii() and seg.isdigit()


def _walk_inside(ref: str, rel: tuple, obj: Any) -> bool:
    """Does the text of *ref*, held at *rel* inside *obj*, stay inside *obj*
    and pass only through real nodes of it? Every named step, including one
    a later ``..`` pops, must exist; every step but the last must be a
    container (not a reference string)."""
    body = ref[1:]
    segs = [x for x in (body[2:] if body.startswith("./") else body).split("/")
            if x not in ("", ".")]
    cur = list(rel[:-1])
    for i, seg in enumerate(segs):
        if seg == "..":
            if not cur:
                return False
            cur.pop()
            continue
        cur.append(seg)
        node: Any = obj
        for step in cur:
            if isinstance(node, dict) and step in node:
                node = node[step]
            elif isinstance(node, list) and _is_index(step) and int(step) < len(node):
                node = node[int(step)]
            else:
                return False
        if i < len(segs) - 1 and not isinstance(node, (dict, list)):
            return False
    return True


def _check_relative(merged: dict, obj: Any, obj_path: tuple, mname: str,
                    reset: list[str], unresolved: list[str], reasons: list[str],
                    where: str) -> None:
    """Climbing relative references in a copied object.

    A reference that does not resolve in the model (``pointer_resolver``'s
    own reading) is kept and listed in *unresolved*. Otherwise it is kept
    only when its textual target (plain path arithmetic, no reference
    followed) stays inside the same copied object and every node on the way
    is a real container of the planned copy; anything else is refused."""
    def walk(v, rel, label):
        if isinstance(v, dict):
            for k, x in v.items():
                walk(x, rel + (k,), f"{label}.{k}")
            return
        if isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, rel + (str(i),), f"{label}.{i}")
            return
        if not _is_climbing(v):
            return
        holder = obj_path + rel
        if any(seg.isdigit() and not _is_index(seg) for seg in v[1:].split("/")):
            reasons.append(f"The model gate {mname} uses the relative reference {v} "
                           f"(in {label}), which has a step that looks like a number "
                           "but is not one. Use an absolute reference there in the "
                           "model gate, or pick another model gate.")
            return
        ptr = "#" + v[3:] if v.startswith("#./../") else v
        t = (_walk_relative(merged, ptr, holder) if ptr.startswith("#../")
             else target_path(merged, ptr, holder))
        if t is None or _get(merged, list(t)) is _SENTINEL:
            unresolved.append(f"{label}: {v}")
            return
        text = _textual_target(v, holder)
        inner = (list(text[len(obj_path):])
                 if text is not None and text[:len(obj_path)] == obj_path else None)
        if inner and inner[0] in reset:
            reasons.append(f"The model gate {mname} uses the relative reference {v} "
                           f"(in {label}), whose target {inner[0]} is not copied to "
                           "the new gate. Pick another model gate, or a gate class "
                           "SM knows.")
            return
        ok = inner is not None and _walk_inside(v, rel, obj)
        if not ok:
            reasons.append(f"The model gate {mname} uses the relative reference {v} "
                           f"(in {label}), which points outside its own macro or "
                           "operation. SM copies only relative references that stay "
                           "inside their own object. Use an absolute reference there "
                           "in the model gate, pick another model gate, or pick a "
                           "gate class SM knows.")
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


def keep_float(old: Any, new: Any) -> Any:
    """A whole number given for a field that holds a float stays a float
    (JSON cannot tell 1 from 1.0, so the dialog's 1 arrives as an int)."""
    if isinstance(old, float) and isinstance(new, int) and not isinstance(new, bool):
        return float(new)
    return new


def _apply_macro_fields(macro: dict, kept: dict, model: dict, pulse_keys,
                        reset: list, macro_fields: dict | None, reasons: list) -> None:
    """The person's values for the gate's own settings (copied layout only).

    A key is accepted only when the model macro has it, it is not a pulse
    field, not a key the copy resets (fidelity, phase shifts, spectators,
    extras) and the model's value is not a reference; anything else is a
    refusal naming the key. The lab check decides whether the value is valid."""
    for k, v in (macro_fields or {}).items():
        why = None
        if k == "__class__" or k not in model:
            why = f"The model gate has no setting named {k}. Remove it."
        elif k in pulse_keys:
            why = f"{k} is a pulse of the gate, not a setting. Change it in its pulse block."
        elif k in reset:
            why = f"{k} is not copied from the model gate (it starts empty). Remove it."
        elif isinstance(model[k], str) and model[k].startswith("#"):
            why = f"{k} holds a reference and is kept as it is. Remove it."
        if why:
            reasons.append(why)
            continue
        macro[k] = keep_float(model[k], copy.deepcopy(v))
        kept[k] = GIVEN


def _plan_copied(merged, pair_id, gate_name, gate_class, pulses, lines,
                 copy_source, reasons, model_gate, macro_fields=None) -> dict[str, Any]:
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
        climbing = [(k, v) for k, v in model.items()
                    if "pulse" in k and not k.startswith("spectator")
                    and _is_climbing(v)]
        if climbing:
            k, v = climbing[0]
            return _refuse(reasons + [
                f"The model gate {mname} uses the relative reference {v} (in "
                f"{k}), which points outside its own macro. SM copies only "
                "relative references that stay inside their own object. Use an "
                "absolute reference there in the model gate, pick another model "
                "gate, or pick a gate class SM knows."])
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
    _apply_macro_fields(macro, kept, model, fields, reset, macro_fields, reasons)
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
