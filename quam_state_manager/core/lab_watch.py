"""Which leaves feed a LAB-class pulse (2026-09-27, the /field/edit hole).

A lab's own pulse class (``quam_config.two_flux_gate.GaussianNZTwoFluxPulse``)
validates its fields in its own code. A value that code rejects, once applied,
makes ``generate_config()`` raise for the WHOLE chip. The Pulses page asks the
class before it writes; every other editing surface (Live Edit grids, Json Tree,
plot-click popup, paste/fill-down batches) writes through ``/field/edit`` and
``/field/edit-batch``, and must ask the same question -- but only when the write
actually reaches such a pulse, because the answer costs a subprocess and the
overwhelming majority of edits are to ordinary numbers.

:func:`build` answers "which written paths change which (pulse, field)":

* a write INSIDE a lab-class pulse dict changes that field directly;
* a write to any path on a pulse field's POINTER CHAIN changes it too, however
  many hops (``A.flat_length -> B.flat_length -> C.flat_length``: a write at C
  changes A and B), including a chain that ends on a container (a write under
  that container changes the field).

The map depends only on STRUCTURE -- where lab-class dicts are and what their
pointer fields point at -- never on numeric leaf values, so it is cached against
``QuamStore.structure_seq`` (bumped only by writes that can move structure)
and survives the ordinary numeric edit untouched. That is what keeps a non-pulse
``/field/edit`` exactly as fast as before: a stamp compare plus a few dict
lookups over the path's own prefixes.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from quam_state_manager.core.pointer_path import (
    _walk, pointer_to_abs, resolve_field_target)
from quam_state_manager.core.pointer_resolver import is_pointer

_MAX_HOPS = 64


def is_lab_class(qclass: Any) -> bool:
    """A pulse class SM's catalog does not draw in-process -- the same test
    ``synth_for_operation`` answers with ``reason == "unknown_class"`` for a
    dict that names a class."""
    from quam_state_manager.core.pulse_catalog import is_pulse_class, resolve_qclass
    if not isinstance(qclass, str) or not qclass:
        return False
    return resolve_qclass(qclass)[0] is None and is_pulse_class(qclass)


class LabWatch:
    """``affected(path)`` -> ``[(op_path, field, rel_segments)]``."""

    __slots__ = ("ops", "exact", "container")

    def __init__(self) -> None:
        self.ops: set[str] = set()
        # chain path -> [(op, field)] -- a write AT this path changes the field
        self.exact: dict[str, list[tuple[str, str]]] = {}
        # chain end that is a container -> [(op, field)] -- a write UNDER it
        self.container: dict[str, list[tuple[str, str]]] = {}

    def __bool__(self) -> bool:
        return bool(self.ops)

    def affected(self, path: str) -> list[tuple[str, str, tuple[str, ...]]]:
        if not self.ops or not isinstance(path, str) or not path:
            return []
        segs = path.split(".")
        out: list[tuple[str, str, tuple[str, ...]]] = []
        # 1. inside a lab pulse dict itself
        for n in range(len(segs) - 1, 0, -1):
            if ".".join(segs[:n]) in self.ops:
                out.append((".".join(segs[:n]), segs[n], tuple(segs[n + 1:])))
                break
        # 2. on a field's pointer chain
        for op, field in self.exact.get(path, ()):
            out.append((op, field, ()))
        # 3. under a container a chain ends on
        if self.container:
            for n in range(len(segs) - 1, 0, -1):
                for op, field in self.container.get(".".join(segs[:n]), ()):
                    out.append((op, field, tuple(segs[n:])))
        return out


def build(merged: dict, lab_test: Callable[[Any], bool] = is_lab_class) -> LabWatch:
    """One walk over *merged*: every lab-class pulse dict and its fields'
    pointer chains. The class test is memoized per class string (a chip has
    tens of classes and thousands of dicts)."""
    watch = LabWatch()
    verdict: dict[str, bool] = {}
    found: list[tuple[list[str], dict]] = []

    def visit(node: Any, segs: list[str]) -> None:
        if isinstance(node, dict):
            c = node.get("__class__")
            if isinstance(c, str):
                v = verdict.get(c)
                if v is None:
                    v = verdict[c] = bool(lab_test(c))
                if v:
                    found.append((segs, node))
            for k, child in node.items():
                if isinstance(child, (dict, list)):
                    visit(child, segs + [str(k)])
        elif isinstance(node, list):
            for i, child in enumerate(node):
                if isinstance(child, (dict, list)):
                    visit(child, segs + [str(i)])

    visit(merged, [])
    for op_segs, body in found:
        op = ".".join(op_segs)
        watch.ops.add(op)
        for field, raw in body.items():
            if field == "__class__" or not is_pointer(raw):
                continue
            _chain(merged, watch, op, field, op_segs + [field], raw)
    return watch


def _chain(merged, watch: LabWatch, op: str, field: str,
           holder: list[str], raw: Any) -> None:
    seen: set[str] = set()
    hops = 0
    while is_pointer(raw) and hops < _MAX_HOPS:
        hops += 1
        target = pointer_to_abs(raw, list(holder))
        if not target:
            return
        tp = ".".join(target)
        if tp in seen:
            return
        seen.add(tp)
        watch.exact.setdefault(tp, []).append((op, field))
        ok, val = _walk(merged, target)
        if not ok:
            # a pointer met MID-path (``operations.x180 = "#./x180_Drag"``):
            # the follower knows where the write really lands
            rt = resolve_field_target(merged, tp)
            if not rt.get("resolvable") or not rt.get("resolved_path"):
                return
            rp = rt["resolved_path"]
            if rp in seen:
                return
            seen.add(rp)
            watch.exact.setdefault(rp, []).append((op, field))
            target = rp.split(".")
            ok, val = _walk(merged, target)
            if not ok:
                return
            tp = rp
        if isinstance(val, (dict, list)):
            watch.container.setdefault(tp, []).append((op, field))
            return
        holder, raw = target, val


_LOCK = threading.Lock()


def _stamp(store) -> tuple:
    from quam_state_manager.core import pulse_catalog
    seq = getattr(store, "structure_seq", None)
    if seq is None:   # a store built without the counter: every edit counts
        seq = ("m", getattr(store, "mutation_seq", None))
    return (id(store.merged), seq, pulse_catalog.class_info_generation())


def watch_for(store) -> LabWatch:
    """The store's cached :class:`LabWatch`, rebuilt only when structure moved
    (``structure_seq``) or the installed class knowledge changed (a probe
    landing can turn an unknown class into a known one)."""
    stamp = _stamp(store)
    cached = getattr(store, "_lab_watch", None)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    with _LOCK:
        cached = getattr(store, "_lab_watch", None)
        if cached is not None and cached[0] == stamp:
            return cached[1]
        with store._lock:
            stamp = _stamp(store)
            watch = build(store.merged)
        store._lab_watch = (stamp, watch)
        return watch
