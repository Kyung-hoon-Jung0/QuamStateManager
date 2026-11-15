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


def pointer_to_abs(pointer: str, holder: list[str], root: Any = None) -> list[str] | None:
    """Public alias of :func:`_pointer_to_abs` (used by pulse_index)."""
    return _pointer_to_abs(pointer, holder, root)


def _pointer_to_abs(pointer: str, holder: list[str], root: Any = None) -> list[str] | None:
    """Absolute path a pointer names, given the *holder* leaf's path.

    One rule with the value resolver: ``pointer_resolver.target_path``. Pass
    *root* (the merged document) whenever it is at hand: a ``..`` after a
    named step needs it, and without it the target is None (unknown).
    """
    from quam_state_manager.core.pointer_resolver import target_path
    return target_path(root, pointer, holder)


def _candidate(path_segs: list[str], value: Any) -> dict:
    label = ".".join(path_segs[-2:]) if len(path_segs) >= 2 else ".".join(path_segs)
    return {
        "path": ".".join(path_segs),
        "label": label,
        "value": _scalar(value),
        "is_pointer": is_pointer(value),
    }


def resolve_field_target(merged: dict, dot_path: str, *, cross: bool = False) -> dict:
    """Follow pointers along *dot_path*; return the resolution result.

    Result keys: ``input_path``, ``resolved_path``, ``resolved_value``,
    ``candidates`` (writable leaf targets, immediate→final; default = last),
    ``chain`` (ordered pointer hops), ``is_pointer``, ``resolvable``.

    With *cross* True, a pointer whose target path passes through another
    pointer (``#/a/ref/x`` with ``a.ref = "#/b"``) is followed through it, as
    quam reads it: the walk continues from the crossed pointer's real target,
    so a ``#./`` met afterwards is relative to the object really reached
    (``b``). Without it such a hop is a dead end (the click-to-edit default).

    A leaf chain follows at most ``_MAX_HOPS`` pointers: a chain of exactly
    ``_MAX_HOPS`` hops resolves, a longer one is cut, ``resolvable`` False and
    the last pointer reached is ``resolved_value``. Nothing is cached.
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
        target = _pointer_to_abs(current, abs_path, merged)
        if target is None:
            resolvable = False
            return "deadend"
        chain.append({"from_path": key, "pointer": current, "to_path": ".".join(target)})
        found, value = _walk(merged, target)
        if not found and cross:
            # the target path passes through a pointer: walk it from the root,
            # following each crossed pointer to its real target
            abs_path, current = [], merged
            return "ok" if descend(target) else "deadend"
        if not found:
            resolvable = False
            return "deadend"
        abs_path = list(target)
        current = value
        return "ok"

    def descend(path_segs: list[str]) -> bool:
        """Walk *path_segs* from the current node, following any pointer met
        before each step; False (``resolvable`` cleared) on a dead end."""
        nonlocal current, abs_path, resolvable
        for seg in path_segs:
            # Resolve any mid-path pointer(s) at the current node before descending.
            hops = 0
            while hops < _MAX_HOPS:
                if one_hop() != "ok":
                    break
                hops += 1
            if not resolvable:
                return False
            # Descend one segment.
            if isinstance(current, dict) and seg in current:
                abs_path.append(seg)
                current = current[seg]
                continue
            if isinstance(current, list):
                try:
                    value = current[int(seg)]
                except (ValueError, IndexError):
                    resolvable = False
                    return False
                abs_path.append(seg)
                current = value
                continue
            resolvable = False
            return False
        return True

    consumed_all = descend(segs)

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
        if resolvable and is_pointer(current) and hops >= _MAX_HOPS:
            resolvable = False   # cut by the hop limit, not a dead end

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
    """Other operation references in the same qubit sharing *resolved_path*.

    Scan operation aliases and pointer leaves through the same resolver.
    Exclude the clicked operation and the literal target itself.
    Return sorted, de-duplicated names.
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
            if not is_pointer(op_val) and not isinstance(op_val, dict):
                continue
            if op_key == input_op:
                continue
            cand = f"qubits.{qubit}.{chan_name}.operations.{op_key}.{leaf}"
            ft = resolve_field_target(merged, cand)
            if ft["is_pointer"] and ft["resolvable"] and ft["resolved_path"] == resolved_path:
                out.add(op_key)
    return sorted(out)
