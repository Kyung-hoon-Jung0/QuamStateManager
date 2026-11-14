"""What one CZ gate of a pair owns, computed from the state (pure).

:func:`gate_parts` is the one answer to "what goes when this gate goes" and
"what is this gate's content": the macro (with the pulses it holds), the
channel operations that belong to it and, when the gate is the pair's active
gate, the ``cz`` alias that names it. Both the one-step gate delete and the
check record's content hash read it, so the two can never disagree.

An operation belongs to the gate when

* it refers into the macro (a macro-owned pulse: the operation's values are
  references to the macro's pulse),
* the macro names it (a pulse field holding the operation key of one of the
  pair's lines, or a reference to an ``.../operations/<key>`` entry), or
* its key is one of the gate's own pulse names
  (``<gate>_<role>_<control>_<target>``, every role of
  :data:`cz_gate_build.ROLES`, spectator pulses included), on any channel.

An operation that another gate also uses (by reference or by name) is never
part of the set: the answer then carries a refusal naming that gate. A value
outside the set that refers into it would be left pointing at nothing: that
is a refusal too. Pure: no I/O, never changes *merged*.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from quam_state_manager.core import cz_gate_build as cgb
from quam_state_manager.core.pointer_resolver import target_path
from quam_state_manager.core.query import pair_operational_gate


def _walk_refs(value: Any, segs: tuple):
    """(holder segments, reference string) for every ``#`` string."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk_refs(v, segs + (str(k),))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk_refs(v, segs + (str(i),))
    elif isinstance(value, str) and value.startswith("#"):
        yield segs, value


def _land(merged: dict, segs: list[str]) -> str:
    """The dot path a reference lands on, a pair's coupler followed one hop."""
    return cgb._canonical_dot(merged, "#/" + "/".join(segs))


def ref_index(merged: dict) -> list[tuple[str, str]]:
    """``(holder dot path, landing dot path)`` for every reference in *merged*."""
    out = []
    for holder, ref in _walk_refs(merged, ()):
        try:
            segs = target_path(merged, ref, holder)
        except Exception:  # noqa: BLE001 -- an unreadable reference lands nowhere
            segs = None
        if segs:
            out.append((".".join(holder), _land(merged, segs)))
    return out


def _operations(merged: dict) -> dict[str, str]:
    """``{operation dot path: operation key}`` for every operation entry."""
    out: dict[str, str] = {}
    stack: list[tuple[Any, tuple]] = [(merged, ())]
    while stack:
        node, segs = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "operations" and isinstance(v, dict):
                    for key in v:
                        out[".".join(segs + ("operations", str(key)))] = str(key)
                stack.append((v, segs + (str(k),)))
    return out


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + ".")


def _owner_op(path: str, ops: dict) -> str | None:
    """The operation *path* lies in, or None."""
    segs = path.split(".")
    for n in range(len(segs), 1, -1):
        cand = ".".join(segs[:n])
        if cand in ops:
            return cand
    return None


def _d(x) -> dict:
    return x if isinstance(x, dict) else {}


def _line_paths(merged: dict, pair: str) -> dict[str, tuple]:
    """Each line of the pair that can hold operations -> (segments, ops)."""
    try:
        lines = cgb._lines(merged, pair)
    except Exception:  # noqa: BLE001 -- a pair SM cannot read has no lines
        return {}
    return {k: v for k, v in lines.items() if k != "_ids" and isinstance(v, tuple)}


def _slot_op(merged: dict, pair: str, macro: dict, field: str, key: str
             ) -> tuple[str | None, str | None]:
    """``(operation path, refusal)`` for a plain operation name in a macro
    field, resolved on the line that slot plays on: the slot's line for a
    class SM knows, else the one line of the pair that holds the key. More
    than one candidate is a refusal naming them."""
    lines = _line_paths(merged, pair)
    cls = str(macro.get("__class__") or "")
    desc = cgb.GATE_CLASSES.get(cls)
    if desc is not None:
        slot = next((sl for sl in desc["slots"] if sl.field == field), None)
        if slot is not None:
            ln = lines.get(slot.line)
            if ln is not None and key in ln[1]:
                return ".".join(ln[0] + (key,)), None
            return None, None
    cands = sorted({".".join(ln[0] + (key,)) for name, ln in lines.items()
                    if name in ("control", "target", "coupler") and key in ln[1]})
    if len(cands) > 1:
        return None, (f"The gate field {field} names {key}, which "
                      f"{' and '.join(cands)} both hold; SM cannot tell which one "
                      "it plays. Nothing was deleted.")
    return (cands[0] if cands else None), None


def _uses(merged: dict, pair: str, gate: str, index, ops) -> tuple[set, set, list]:
    """``(refers_in, named, refusals)``: the operations that refer into the
    macro, the operations the macro names (a reference, or a pulse field
    holding a plain operation key, resolved on its line), and why a plain
    name could not be resolved."""
    mpath = cgb._dot(("qubit_pairs", pair, "macros", gate))
    refers_in: set[str] = set()
    named: set[str] = set()
    for holder, land in index:
        if _under(land, mpath):
            op = _owner_op(holder, ops)
            if op:
                refers_in.add(op)
        if _under(holder, mpath):
            op = _owner_op(land, ops)
            if op:
                named.add(op)
    macro = merged["qubit_pairs"][pair]["macros"][gate]
    refusals = []
    for k, v in macro.items():
        if isinstance(v, str) and v and not v.startswith("#") and "pulse" in k:
            op, why = _slot_op(merged, pair, macro, k, v)
            if op:
                named.add(op)
            if why:
                refusals.append(why)
    return refers_in, named, refusals


def _all_macros(merged: dict):
    """(dot path, macro dict) of every macro of any class, anywhere."""
    stack: list[tuple[Any, tuple]] = [(merged, ())]
    while stack:
        node, segs = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "macros" and isinstance(v, dict):
                    for name, m in v.items():
                        if isinstance(m, dict):
                            yield ".".join(segs + ("macros", str(name))), m
                stack.append((v, segs + (str(k),)))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                stack.append((v, segs + (str(i),)))


def _strings_at(value: Any, segs: tuple):
    """(segments below the macro root, string) for every string, dicts and
    lists, any depth."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _strings_at(v, segs + (str(k),))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _strings_at(v, segs + (str(i),))
    elif isinstance(value, str):
        yield segs, value


def _channel_owner(merged: dict, op_path: str) -> tuple[str, str] | None:
    """Who plays an operation's channel: ``("qubit", q)`` for an operation
    under ``qubits.<q>``, ``("coupler", pair)`` for an operation on a pair's
    coupler (followed through the pair's coupler reference), else None."""
    segs = op_path.split(".")
    if len(segs) > 2 and segs[0] == "qubits":
        return "qubit", segs[1]
    for pair in _d(merged.get("qubit_pairs")):
        ln = _line_paths(merged, pair).get("coupler")
        if ln is not None and op_path.startswith(".".join(ln[0]) + "."):
            return "coupler", str(pair)
    return None


def _macro_can_play(merged: dict, macro_path: str, owner: tuple[str, str]) -> bool:
    """A macro can play a channel when it belongs to the same qubit, to a
    pair holding that qubit, or (a coupler) to that pair."""
    segs = macro_path.split(".")
    kind, who = owner
    if segs[0] == "qubits" and len(segs) > 1:
        return kind == "qubit" and segs[1] == who
    if segs[0] == "qubit_pairs" and len(segs) > 1:
        if kind == "coupler":
            return segs[1] == who
        return who in (cgb.pair_qubit(merged, segs[1], "qubit_control"),
                       cgb.pair_qubit(merged, segs[1], "qubit_target"))
    return False


#: keys whose plain string describes a macro and never plays an operation
DESCRIPTIVE_KEYS = frozenset({"__class__", "id", "name", "label", "note", "description",
                              "comment"})


def _other_users(merged: dict, mpath: str, mine: set, index, ops) -> list[str]:
    """Refusals for the operations of *mine* that another macro uses. The rule:

    * a reference (absolute or relative, at any depth, in dicts and lists)
      that resolves into one of the operations counts wherever the macro sits;
      so does an operation of the set that refers into another macro;
    * a plain string equal to an operation's key counts when the macro can
      play that operation's channel (the same qubit's macros, a pair's macros
      when the pair holds the qubit, a pair's macros for its coupler), under
      any key except the descriptive ones (:data:`DESCRIPTIVE_KEYS`: a
      string anywhere under ``note``, ``id`` and the like, at any depth
      below the macro's root, does not count). A dangling name is worse than a refusal naming the field.

    Each refusal names the holder."""
    out: list[str] = []
    by_key: dict[str, list[str]] = {}
    for p in mine:
        by_key.setdefault(ops[p], []).append(p)
    owners = {p: _channel_owner(merged, p) for p in mine}
    for path, macro in _all_macros(merged):
        if path == mpath:
            continue
        hits: set[tuple[str, str]] = set()
        for holder, land in index:
            if _under(holder, path):
                op = _owner_op(land, ops)
                if op in mine:
                    hits.add((op, holder))
            elif _under(land, path):
                op = _owner_op(holder, ops)
                if op in mine:
                    hits.add((op, holder))
        for segs, v in _strings_at(macro, ()):
            if v not in by_key or v.startswith("#"):
                continue
            if not segs or any(x in DESCRIPTIVE_KEYS for x in segs):
                continue
            for op in by_key[v]:
                own = owners.get(op)
                if own is not None and _macro_can_play(merged, path, own):
                    hits.add((op, ".".join((path,) + segs)))
        for op, holder in sorted(hits):
            out.append(f"The operation {op} is also used by {holder}. Nothing was deleted.")
    return out


def _names(merged: dict, pair: str, gate: str) -> set[str]:
    control = cgb.pair_qubit(merged, pair, "qubit_control")
    target = cgb.pair_qubit(merged, pair, "qubit_target")
    if not (control and target):
        return set()
    return {f"{gate}_{role}_{control}_{target}" for role in cgb.ROLES}


def _cz_gates(merged: dict):
    for p, pobj in _d(_d(merged).get("qubit_pairs")).items():
        macros = pobj.get("macros") if isinstance(pobj, dict) else None
        if isinstance(macros, dict):
            for g, m in macros.items():
                if cgb.is_cz_macro(str(g), m):
                    yield str(p), str(g)


def gate_parts(merged: dict, pair: str, gate: str, *, index=None) -> dict[str, Any]:
    """What gate *gate* of pair *pair* owns.

    Returns ``{"ok", "refusals": [str], "macro": dot path, "pulses": [macro
    pulse field paths], "operations": [operation dot paths], "active": bool,
    "alias": dot path of the cz alias when active else None, "paths": [macro,
    *operations], "kept": [operations the macro names that stay]}``. ``ok``
    is False with plain refusals when the gate is not a CZ gate of the pair,
    when an operation is also used by any other macro, when a plain
    operation name cannot be placed on one line, or when a value outside the
    set refers into it. *index* is :func:`ref_index` of the same *merged*."""
    out: dict[str, Any] = {"ok": False, "refusals": [], "macro": None, "pulses": [],
                           "operations": [], "kept": [], "active": False, "alias": None,
                           "paths": []}
    pobj = _d(_d(merged).get("qubit_pairs")).get(pair)
    macros = pobj.get("macros") if isinstance(pobj, dict) else None
    macro = macros.get(gate) if isinstance(macros, dict) else None
    if not isinstance(macro, dict) or not cgb.is_cz_macro(str(gate), macro):
        out["refusals"].append(f"Pair {pair} has no CZ gate named {gate}.")
        return out
    mpath = cgb._dot(("qubit_pairs", pair, "macros", gate))
    out["macro"] = mpath
    index = ref_index(merged) if index is None else index
    ops = _operations(merged)
    refers_in, named, unresolved = _uses(merged, pair, gate, index, ops)
    out["refusals"].extend(unresolved)
    names = _names(merged, pair, gate)
    # an operation the macro only names goes with it when it carries the
    # gate's name; a general operation (a shared const, an x180) stays
    mine = set(refers_in) | {p for p in named if ops[p].startswith(gate + "_")}
    mine.update(p for p, key in ops.items() if key in names)
    out["kept"] = sorted(named - mine)
    out["pulses"] = [f"{mpath}.{f}" for f in cgb.pulse_fields(macro)
                     if isinstance(macro.get(f), dict)]
    out["refusals"].extend(_other_users(merged, mpath, mine, index, ops))
    out["operations"] = sorted(mine)
    out["active"] = active_gate(pair, macros) == gate
    alias = cgb._dot(("qubit_pairs", pair, "macros", "cz"))
    out["alias"] = alias if out["active"] else None
    out["paths"] = [mpath] + out["operations"]
    if not out["refusals"]:
        dangling = sorted({holder for holder, land in index
                           if any(_under(land, s) for s in out["paths"])
                           and not any(_under(holder, s) for s in out["paths"])
                           and not (out["active"] and holder == alias)})
        for holder in dangling[:5]:
            out["refusals"].append(
                f"{holder} refers to a part of this gate and would point at "
                "nothing. Change it first. Nothing was deleted.")
    out["ok"] = not out["refusals"]
    return out


#: the version of the content-hash rule; a check record of another version
#: cannot be compared with the values now
HASH_RULE = 3


def _norm_numbers(v: Any) -> Any:
    """Numbers that are equal hash equal: an int stays an exact int, a float
    equals an int only when it is integral (then it is that int), -0.0 equals
    0.0; bools stay bools ("1" stays a string). Never raises."""
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return ["int", str(v)]
    if isinstance(v, float):
        if v == v and v not in (float("inf"), float("-inf")) and v.is_integer():
            return ["int", str(int(v))]
        return ["float", repr(v)]
    if isinstance(v, dict):
        return {k: _norm_numbers(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_norm_numbers(x) for x in v]
    return v


def _walk_to(merged: Any, segs: list) -> tuple[bool, Any]:
    node = merged
    for s in segs:
        if isinstance(node, dict) and s in node:
            node = node[s]
        elif isinstance(node, list) and s.isdigit() and int(s) < len(node):
            node = node[int(s)]
        else:
            return False, None
    return True, node


#: how many references deep :func:`_resolved` walks into referenced objects
RESOLVE_DEPTH = 8


def is_component(segs: tuple | list, node: Any) -> bool:
    """True when a reference target is a COMPONENT, which a gate names as a
    whole (its path is hashed, its settings are not): the machine root, a
    top-level container, an entry of ``qubits`` or ``qubit_pairs``, a channel
    (a dict holding an ``operations`` container that is a child or grandchild
    of a ``qubits`` / ``qubit_pairs`` entry and does not read as a pulse),
    or anything under ``ports``
    whatever its type (a port's offset included). Decided from the state's shape only, never from class names.
    Every other object or list (a pulse, an entry of an ``operations``
    container, a parameter dict, a waveform list) is not a component."""
    segs = tuple(segs)
    if len(segs) <= 1:
        return True
    if segs[0] in ("qubits", "qubit_pairs") and len(segs) == 2:
        return True
    if "ports" in segs:
        return True
    # a channel by position AND shape: a child (or grandchild, e.g. a pair's
    # coupler) of a qubits / qubit_pairs entry that holds operations
    return (segs[0] in ("qubits", "qubit_pairs") and len(segs) in (3, 4)
            and isinstance(node, dict) and isinstance(node.get("operations"), dict)
            and not _pulse_like(node))


def _pulse_like(node: dict) -> bool:
    """A dict that reads as a pulse (an amplitude, or a pulse class), so an
    ``operations`` key on it does not make it a channel."""
    from quam_state_manager.core import pulse_catalog
    return "amplitude" in node or bool(pulse_catalog.is_pulse_class(node.get("__class__")))


def _resolved(merged: dict, value: Any, holder: tuple, depth: int = 0,
              trail: frozenset = frozenset()) -> Any:
    """*value* with every reference replaced by what it plays.

    A reference is followed to its target (chains of references followed);
    a plain value there hashes as that value; a component there
    (:func:`is_component`) hashes as its path only; any other object or list
    there is walked and hashed by its own resolved content. A dangling reference
    hashes as a marker plus its text. A reference back into an object
    already being walked (a cycle) hashes as ``["cycle", path]``. The limit
    :data:`RESOLVE_DEPTH` counts REFERENCE HOPS (each followed reference,
    in a chain or inside a walked object), not dict nesting; the target
    reached past it hashes as ``["depth", path]`` (a change there is not
    seen)."""
    if isinstance(value, dict):
        return {k: _resolved(merged, v, holder + (str(k),), depth, trail)
                for k, v in value.items()}
    if isinstance(value, list):
        return [_resolved(merged, v, holder + (str(i),), depth, trail)
                for i, v in enumerate(value)]
    if not (isinstance(value, str) and value.startswith("#")):
        return value
    ref, at, seen = value, holder, set()
    while True:
        try:
            segs = target_path(merged, ref, at)
        except Exception:  # noqa: BLE001
            segs = None
        found, node = _walk_to(merged, segs) if segs else (False, None)
        if not found:
            return ["dangling", value]
        key = tuple(segs)
        if "ports" in key:
            return ["component", _land(merged, list(segs))]
        if depth + len(seen) >= RESOLVE_DEPTH:
            return ["depth", _land(merged, list(segs))]
        if isinstance(node, str) and node.startswith("#") and key not in seen:
            seen.add(key)
            ref, at = node, key
            continue
        if isinstance(node, (dict, list)):
            path = _land(merged, list(segs))
            if is_component(key, node):
                return ["component", path]
            if any(key[:len(t)] == t for t in trail):
                return ["cycle", path]
            return _resolved(merged, node, key, depth + 1, trail | {key})
        return node


def content_hash(merged: dict, pair: str, gate: str, *, index=None) -> str | None:
    """A hash of everything the gate PLAYS, as canonical JSON.

    Covered: the macro subtree; every operation the macro owns, names or
    refers to (owned or kept, a shared ``const`` included); and everything
    their references lead to, walked to the values (:func:`_resolved`).
    Numbers are normalised (:func:`_norm_numbers`). Rule version
    :data:`HASH_RULE`.

    NOT covered, on purpose: a port's output range, offset and delay, the
    channel's offset parameters, the qubits' other settings, and components
    the gate refers to as a whole (:func:`is_component`, anything under
    ``ports`` included): only which one, not their settings (they are
    retuned often and are not part of the gate). A plain number the gate
    refers to directly (``#/qubits/q1/f_01``, a pair-level value) IS
    covered: the gate uses that number. None when there is no such
    gate."""
    parts = gate_parts(merged, pair, gate, index=index)
    if not parts["macro"]:
        return None
    paths = [parts["macro"]] + sorted(set(parts["operations"]) | set(parts["kept"]))
    body = {}
    for p in paths:
        found, node = _walk_to(merged, p.split("."))
        body[p] = _norm_numbers(_resolved(merged, node, tuple(p.split(".")))) if found else None
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def active_gate(pair: str, macros: Any) -> str | None:
    """The pair's active gate: the macro its ``cz`` alias names
    (query.pair_operational_gate), or the macro whose plain name ``cz``
    holds."""
    name = pair_operational_gate(pair, macros)
    if name is None and isinstance(macros, dict):
        raw = macros.get("cz")
        if (isinstance(raw, str) and raw and not raw.startswith("#") and raw != "cz"
                and isinstance(macros.get(raw), dict)):
            name = raw
    return name


def gate_of_path(merged: dict, path: str) -> tuple[str, str] | None:
    """``(pair, gate)`` of the one CZ gate whose macro or operations hold
    *path*; None when no gate or more than one does."""
    if not isinstance(path, str) or not path or not isinstance(merged, dict):
        return None
    segs = path.split(".")
    if len(segs) >= 4 and segs[0] == "qubit_pairs" and segs[2] == "macros":
        pobj = _d(_d(merged).get("qubit_pairs")).get(segs[1])
        m = (pobj.get("macros") or {}).get(segs[3]) if isinstance(pobj, dict) else None
        return (segs[1], segs[3]) if cgb.is_cz_macro(segs[3], m) else None
    if ".operations." not in path:
        return None
    index = ref_index(merged)
    hits = set()
    for p, g in _cz_gates(merged):
        parts = gate_parts(merged, p, g, index=index)
        if any(_under(path, op) for op in parts["operations"]):
            hits.add((p, g))
    return hits.pop() if len(hits) == 1 else None
