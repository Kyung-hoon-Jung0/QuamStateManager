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


#: packages whose gate classes SM does not treat as the lab's own code
_KNOWN_PACKAGES = ("quam", "quam_builder", "qualang_tools", "qm")


def is_lab_macro(qclass: Any) -> bool:
    """A gate (macro) class the LAB wrote -- its ``apply()`` is the lab's own
    check on the pulses it plays (``CZGateTwoFlux.apply`` ->
    ``assert_lines_compatible``). quam/quam_builder gates are not asked."""
    if not isinstance(qclass, str) or "." not in qclass:
        return False
    if qclass.split(".", 1)[0] in _KNOWN_PACKAGES:
        return False
    from quam_state_manager.core.pulse_catalog import is_pulse_class
    return not is_pulse_class(qclass)


class LabWatch:
    """``affected(path)`` -> ``[(op_path, field, rel_segments)]`` for every
    tracked pulse (``ops``): the LAB-class pulses (``lab_ops``, drawn by their
    own class) plus any pulse a LAB-class gate plays (the gate's ``apply()``
    checks it). ``macros_for(path)`` -> the lab gates a write can change."""

    __slots__ = ("ops", "lab_ops", "exact", "container", "macros",
                 "macros_of", "macro_exact")

    def __init__(self) -> None:
        self.ops: set[str] = set()
        self.lab_ops: set[str] = set()
        # chain path -> [(op, field)] -- a write AT this path changes the field
        self.exact: dict[str, list[tuple[str, str]]] = {}
        # chain end that is a container -> [(op, field)] -- a write UNDER it
        self.container: dict[str, list[tuple[str, str]]] = {}
        # lab gate path -> {"qclass", "ops": {pulse paths it plays}}
        self.macros: dict[str, dict] = {}
        # pulse path -> {lab gates that play it}
        self.macros_of: dict[str, set[str]] = {}
        # a path on the ROUTE from a gate field to its pulse -> gates (a write
        # there re-routes the gate to another pulse)
        self.macro_exact: dict[str, set[str]] = {}

    def __bool__(self) -> bool:
        return bool(self.ops) or bool(self.macros)

    def macros_for(self, path: str, aff=None) -> set[str]:
        """The lab gates a write at *path* can change: a gate whose pulse the
        write changes (*aff*, from :meth:`affected`), a gate the write lands
        inside, and a gate whose route to its pulse passes through *path*."""
        if not self.macros or not isinstance(path, str) or not path:
            return set()
        out: set[str] = set()
        for op, _f, _r in (self.affected(path) if aff is None else aff):
            out |= self.macros_of.get(op, set())
        out |= self.macro_exact.get(path, set())
        segs = path.split(".")
        for n in range(len(segs), 0, -1):
            pre = ".".join(segs[:n])
            if pre in self.macros:
                out.add(pre)
                break
        return out

    def touches(self, path: str) -> bool:
        """Would a write at *path* be asked of the lab's own code at all?"""
        aff = self.affected(path)
        return bool(aff) or bool(self.macros_for(path, aff))

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


def build(merged: dict, lab_test: Callable[[Any], bool] = is_lab_class,
          macro_test: Callable[[Any], bool] = is_lab_macro) -> LabWatch:
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

    macros_found: list[tuple[list[str], dict]] = []
    mverdict: dict[str, bool] = {}

    def visit_macros(node: Any, segs: list[str]) -> None:
        # a gate lives at <owner>.macros.<name>; owners are qubits and pairs
        if not isinstance(node, dict):
            return
        for k, child in node.items():
            if not isinstance(child, dict):
                continue
            if k == "macros":
                for name, mac in child.items():
                    c = mac.get("__class__") if isinstance(mac, dict) else None
                    if isinstance(c, str):
                        v = mverdict.get(c)
                        if v is None:
                            v = mverdict[c] = bool(macro_test(c))
                        if v:
                            macros_found.append((segs + [k, str(name)], mac))
            elif len(segs) < 3:
                visit_macros(child, segs + [str(k)])

    visit(merged, [])
    visit_macros(merged, [])
    tracked: dict[str, list[str]] = {}
    for op_segs, _body in found:
        op = ".".join(op_segs)
        watch.lab_ops.add(op)
        tracked[op] = op_segs
    for mac_segs, body in macros_found:
        mp = ".".join(mac_segs)
        plays = _gate_pulses(merged, watch, mp, mac_segs, body)
        watch.macros[mp] = {"qclass": body.get("__class__"), "ops": plays}
        for op in plays:
            watch.macros_of.setdefault(op, set()).add(mp)
            tracked.setdefault(op, op.split("."))
    for op, op_segs in tracked.items():
        watch.ops.add(op)
        ok, body = _walk(merged, op_segs)
        if not ok or not isinstance(body, dict):
            continue
        for field, raw in body.items():
            if field == "__class__" or not is_pointer(raw):
                continue
            _chain(merged, watch, op, field, op_segs + [field], raw)
    return watch


def _at(merged, path):
    """The node at a dot *path* (``resolve_field_target`` reports a value only
    for a leaf: a pointer to a DICT comes back with ``resolved_value`` None)."""
    if not isinstance(path, str) or not path:
        return None
    ok, val = _walk(merged, path.split("."))
    return val if ok else None


def _owner_qubits(merged, mac_segs: list[str]) -> list[str]:
    """Paths of the qubits a gate's plain pulse NAMES are looked up on: a
    pair's ``qubit_control`` / ``qubit_target`` (followed through pointers),
    or the qubit that owns the gate."""
    owner = mac_segs[:-2]
    ok, body = _walk(merged, owner)
    if not ok or not isinstance(body, dict):
        return []
    out = []
    for key in ("qubit_control", "qubit_target"):
        if key in body:
            rt = resolve_field_target(merged, ".".join(owner + [key]))
            if rt.get("resolvable") and isinstance(_at(merged, rt.get("resolved_path")), dict):
                out.append(rt["resolved_path"])
    return out or [".".join(owner)]


def _gate_pulses(merged, watch: LabWatch, mp: str, mac_segs: list[str],
                 body: dict) -> set[str]:
    """Every pulse dict a lab gate plays: held inline, reached by a pointer
    (any hops), or NAMED (looked up on the owner qubits' channels, as
    ``qubit.get_pulse(name)`` does). Every path on a route is recorded in
    ``macro_exact``: a write there re-routes the gate."""
    plays: set[str] = set()
    owners = None
    for field, raw in body.items():
        if field == "__class__":
            continue
        fp = f"{mp}.{field}"
        if isinstance(raw, dict):
            if isinstance(raw.get("__class__"), str):
                plays.add(fp)
            continue
        if not isinstance(raw, str) or not raw:
            continue
        if is_pointer(raw):
            rt = resolve_field_target(merged, fp)
            for hop in rt.get("chain") or ():
                watch.macro_exact.setdefault(hop["to_path"], set()).add(mp)
            val = _at(merged, rt.get("resolved_path"))
            if rt.get("resolvable") and isinstance(val, dict) \
                    and isinstance(val.get("__class__"), str):
                plays.add(rt["resolved_path"])
            continue
        if owners is None:
            owners = _owner_qubits(merged, mac_segs)
        for q in owners:
            ok, qb = _walk(merged, q.split("."))
            if not ok or not isinstance(qb, dict):
                continue
            for ch, cb in qb.items():
                ops = cb.get("operations") if isinstance(cb, dict) else None
                if not isinstance(ops, dict) or raw not in ops:
                    continue
                named = f"{q}.{ch}.operations.{raw}"
                watch.macro_exact.setdefault(named, set()).add(mp)
                rt = resolve_field_target(merged, named)
                for hop in rt.get("chain") or ():
                    watch.macro_exact.setdefault(hop["to_path"], set()).add(mp)
                val = _at(merged, rt.get("resolved_path"))
                if rt.get("resolvable") and isinstance(val, dict):
                    plays.add(rt["resolved_path"])
    return plays


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
