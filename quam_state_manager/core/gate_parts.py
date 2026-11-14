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


def _other_users(merged: dict, mpath: str, mine: set, index, ops) -> list[str]:
    """Refusals for every operation of *mine* another macro (any class, on
    any pair or qubit) refers to, is referred from, or names by plain key in
    a pulse-like field. Each names the holder."""
    out: list[str] = []
    keys = {ops[p]: p for p in mine}
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
        for k, v in macro.items():
            if isinstance(v, str) and "pulse" in k and v in keys:
                hits.add((keys[v], f"{path}.{k}"))
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
    out["active"] = pair_operational_gate(pair, macros) == gate
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


def content_hash(merged: dict, pair: str, gate: str, *, index=None) -> str | None:
    """A hash of the gate's content: the macro subtree and the operations it
    owns (:func:`gate_parts`), as canonical JSON. None when there is no such
    gate."""
    parts = gate_parts(merged, pair, gate, index=index)
    if not parts["macro"]:
        return None
    from quam_state_manager.core.working_copy import _at_dot_path
    body = {p: _at_dot_path(merged, p)[1] for p in parts["paths"]}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
