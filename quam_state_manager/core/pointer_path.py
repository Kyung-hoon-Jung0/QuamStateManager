"""Mid-path QUAM-pointer follower for click-to-edit.

The global :mod:`pointer_resolver` only resolves a *leaf* pointer and
**deliberately leaves ``#./`` pointers unresolved** (they double as runtime
aliases like ``#./inferred_intermediate_frequency``). But click-to-edit targets
such as ``qubits.q.xy.operations.x180.amplitude`` pass *through* the pointer
``operations.x180 == "#./x180_DragCosine"``, whose real literal lives at
``operations.x180_DragCosine.amplitude``.

This module walks a dot-path over the merged dict and transparently follows
pointer strings encountered mid-path AND at the leaf (including ``#./`` siblings
and chains like ``#../x180_DragCosine/amplitude``), so the popup can read/write
the value actually in use. It is **read-only** (never mutates ``merged``) and
**never raises** — dead ends (runtime aliases, missing keys, cycles) degrade to
``resolvable=False`` with best-effort candidates.

It does NOT touch the global resolver (the inspector / history / query rely on
``#./`` staying raw); this is a separate, opt-in follower with different
semantics.
"""
from __future__ import annotations

from typing import Any

from quam_state_manager.core.pointer_resolver import is_pointer

_MAX_HOPS = 64  # cycle / runaway backstop


def _scalar(value: Any) -> Any:
    """Return a JSON-safe scalar (incl. pointer strings) or None for containers."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return None


def _walk(root: dict, segments: list[str]) -> tuple[bool, Any]:
    """Walk *root* by absolute *segments*; return (found, value)."""
    current: Any = root
    for seg in segments:
        if isinstance(current, dict):
            if seg not in current:
                return False, None
            current = current[seg]
        elif isinstance(current, list):
            try:
                current = current[int(seg)]
            except (ValueError, IndexError):
                return False, None
        else:
            return False, None
    return True, current


def pointer_to_abs(pointer: str, holder: list[str]) -> list[str] | None:
    """Public alias of :func:`_pointer_to_abs` (used by pulse_index)."""
    return _pointer_to_abs(pointer, holder)


def _pointer_to_abs(pointer: str, holder: list[str]) -> list[str] | None:
    """Absolute path a pointer resolves to, given the *holder* node's path.

    ``#/X`` → ``X``; ``#../X`` → ``holder[:-2] + X``; ``#./X`` → ``holder[:-1] + X``.
    Returns ``None`` for malformed / too-short relative pointers.
    """
    def _segs(body: str) -> list[str]:
        return [s for s in body.split("/") if s != ""]

    if pointer.startswith("#../"):
        if len(holder) < 2:
            return None
        return list(holder[:-2]) + _segs(pointer[4:])
    if pointer.startswith("#./"):
        if len(holder) < 1:
            return None
        return list(holder[:-1]) + _segs(pointer[3:])
    if pointer.startswith("#/"):
        return _segs(pointer[2:])
    return None


def _candidate(path_segs: list[str], value: Any) -> dict:
    label = ".".join(path_segs[-2:]) if len(path_segs) >= 2 else ".".join(path_segs)
    return {
        "path": ".".join(path_segs),
        "label": label,
        "value": _scalar(value),
        "is_pointer": is_pointer(value),
    }


def resolve_field_target(merged: dict, dot_path: str) -> dict:
    """Follow pointers along *dot_path*; return the resolution result.

    Result keys: ``input_path``, ``resolved_path``, ``resolved_value``,
    ``candidates`` (writable leaf targets, immediate→final; default = last),
    ``chain`` (ordered pointer hops), ``is_pointer``, ``resolvable``.
    """
    segs = [s for s in dot_path.split(".") if s != ""]
    abs_path: list[str] = []
    current: Any = merged
    chain: list[dict] = []
    candidates: list[dict] = []
    visited: set[str] = set()
    is_ptr = False
    resolvable = True

    def one_hop() -> str:
        """Follow exactly one pointer hop at *current*: 'ok' | 'deadend' | 'notptr'."""
        nonlocal current, abs_path, is_ptr, resolvable
        if not is_pointer(current):
            return "notptr"
        is_ptr = True
        key = ".".join(abs_path)
        if key in visited:
            resolvable = False
            return "deadend"
        visited.add(key)
        target = _pointer_to_abs(current, abs_path)
        if target is None:
            resolvable = False
            return "deadend"
        chain.append({"from_path": key, "pointer": current, "to_path": ".".join(target)})
        found, value = _walk(merged, target)
        if not found:
            resolvable = False
            return "deadend"
        abs_path = list(target)
        current = value
        return "ok"

    consumed_all = True
    for seg in segs:
        # Resolve any mid-path pointer(s) at the current node before descending.
        hops = 0
        while hops < _MAX_HOPS:
            r = one_hop()
            if r == "ok":
                hops += 1
                continue
            break
        if not resolvable:
            consumed_all = False
            break
        # Descend one segment.
        if isinstance(current, dict):
            if seg not in current:
                resolvable = False
                consumed_all = False
                break
            abs_path.append(seg)
            current = current[seg]
        elif isinstance(current, list):
            try:
                idx = int(seg)
                value = current[idx]
            except (ValueError, IndexError):
                resolvable = False
                consumed_all = False
                break
            abs_path.append(seg)
            current = value
        else:
            resolvable = False
            consumed_all = False
            break

    if consumed_all:
        # At the requested leaf. Record it, then follow leaf pointers one hop
        # at a time so each intermediate level is offered as a write candidate.
        candidates.append(_candidate(abs_path, current))
        hops = 0
        while is_pointer(current) and hops < _MAX_HOPS:
            hops += 1
            if one_hop() != "ok":
                break
            candidates.append(_candidate(abs_path, current))

    if not candidates:
        # Dead-ended mid-path: best-effort = the deepest real node reached.
        candidates.append(_candidate(abs_path, current))

    final = candidates[-1]
    return {
        "input_path": dot_path,
        "resolved_path": final["path"],
        "resolved_value": final["value"],
        "candidates": candidates,
        "chain": chain,
        "is_pointer": is_ptr,
        "resolvable": resolvable,
    }


def find_shared_by(
    merged: dict,
    resolved_path: str,
    *,
    scope_qubit: str | None = None,
    input_op: str | None = None,
) -> list[str]:
    """Operation *aliases* in the same qubit that resolve to *resolved_path*.

    Scans the qubit's ``xy``/``z``/``resonator`` operations for alias entries
    (operation keys whose value is a pointer string, e.g. ``"x180"``) whose
    same-leaf field resolves to *resolved_path* — i.e. they share the literal.
    Excludes the clicked alias (*input_op*). Returns sorted, de-duplicated names.
    """
    segs = resolved_path.split(".")
    if len(segs) < 5 or segs[0] != "qubits":
        return []
    qubit = scope_qubit or segs[1]
    leaf = segs[-1]
    qd = merged.get("qubits", {})
    qd = qd.get(qubit, {}) if isinstance(qd, dict) else {}
    out: set[str] = set()
    for chan_name in ("xy", "z", "resonator"):
        chan = qd.get(chan_name) if isinstance(qd, dict) else None
        ops = chan.get("operations") if isinstance(chan, dict) else None
        if not isinstance(ops, dict):
            continue
        for op_key, op_val in ops.items():
            if not is_pointer(op_val):  # only user-facing alias entries
                continue
            if op_key == input_op:
                continue
            cand = f"qubits.{qubit}.{chan_name}.operations.{op_key}.{leaf}"
            ft = resolve_field_target(merged, cand)
            if ft["resolvable"] and ft["resolved_path"] == resolved_path:
                out.add(op_key)
    return sorted(out)
