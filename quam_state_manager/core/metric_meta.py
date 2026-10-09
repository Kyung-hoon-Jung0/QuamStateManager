"""Chip Status per-metric panel metadata (queue item 4, 2026-09-25).

"When was this value last measured, and which run wrote it?" -- asked of every
cell of every per-metric panel (T1, T2 Ramsey, readout fidelity, 2Q RB, ...).

This module names the leaves each panel's NUMBER is made of; the route answers
from the chip's change ledger (docs/283: the newest recorded change of those
leaves, a run named as the writer only on its own patch). Two rules:

* A metric derived from a SUBTREE is as new as the newest change among
  the leaves its NUMBER is made of -- a readout fidelity reads one diagonal
  cell of its confusion matrix, an assignment fidelity the diagonal, a 2Q RB
  panel the one value its row shows. (Until the S8 review, P0-1, every leaf
  of the subtree counted: an edit of the e-row then dated -- and, on the
  ledger, NAMED THE WRITER OF -- the |g> fidelity it never touched.)
* A path through a pointer is followed to where it points in the CURRENT
  state (or through the caller's resolver), so a panel reads the holder.

This module is pure: it builds the path lists from a merged state dict. The
route owns the history/IO side.

S10 C5: the snapshot-index fold (the newest-change fold, the truncated-index
check, its snapshot loader and number comparison) -> gone with the snapshot
metric meta; the ledger answer needs none of them.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from quam_state_manager.core.pointer_resolver import _compute_resolved_path, is_pointer
from quam_state_manager.core.query import fidelity_field_kind

# Panel key -> the path(s) inside ONE qubit dict its value is read from.
# Mirrors QueryEngine.get_topology's node fields (query.py) one for one; a
# subtree (the confusion matrices) stands for every numeric leaf under it.
QUBIT_SOURCES: dict[str, tuple[tuple[str, ...], ...]] = {
    "T1": (("T1",),),
    "T2ramsey": (("T2ramsey",),),
    "T2echo": (("T2echo",),),
    "f_01": (("f_01",),),
    "anharmonicity": (("anharmonicity",),),
    "readout_frequency": (("resonator", "f_01"),),
    "x180_amplitude": (("xy", "operations", "x180_DragCosine", "amplitude"),),
    "x90_amplitude": (("xy", "operations", "x90_DragCosine", "amplitude"),),
    "readout_amplitude": (("resonator", "operations", "readout", "amplitude"),),
    "gate_fidelity_avg": (("gate_fidelity", "averaged"),),
    "gate_fidelity_x180": (("gate_fidelity", "x180"),),
    "gate_fidelity_x90": (("gate_fidelity", "x90"),),
    "assignment_fidelity": (("resonator", "confusion_matrix"),),
    "ro_fidelity_g": (("resonator", "confusion_matrix"),),
    "ro_fidelity_e": (("resonator", "confusion_matrix"),),
    "assignment_fidelity_gef": (("resonator", "gef_confusion_matrix"),),
    "ro_fidelity_gef_g": (("resonator", "gef_confusion_matrix"),),
    "ro_fidelity_gef_e": (("resonator", "gef_confusion_matrix"),),
    "ro_fidelity_gef_f": (("resonator", "gef_confusion_matrix"),),
}

# The leaves a panel's NUMBER is made of, inside its source subtree (S8 review
# P0-1), mirroring query.py: ``_cm_diag(cm, i)`` reads cell i.i,
# ``_assignment_fidelity(_n)`` the mean of the diagonal. A panel not listed
# here shows the whole (scalar) source. A string is one cell, a pattern any
# cell whose last two path segments match it.
_DIAGONAL = re.compile(r"^(\d+)\.\1$")
VALUE_LEAVES: dict[str, Any] = {
    "assignment_fidelity": _DIAGONAL,
    "ro_fidelity_g": "0.0",
    "ro_fidelity_e": "1.1",
    "assignment_fidelity_gef": _DIAGONAL,
    "ro_fidelity_gef_g": "0.0",
    "ro_fidelity_gef_e": "1.1",
    "ro_fidelity_gef_f": "2.2",
}


def _value_leaf(key: str, dot_path: str) -> bool:
    spec = VALUE_LEAVES.get(key)
    if spec is None:
        return True
    cell = ".".join(dot_path.split(".")[-2:])
    return spec.match(cell) is not None if hasattr(spec, "match") else cell == spec


# The scalar a 2Q RB row shows when its metric is a block (query.py
# _extract_pair_gate_fidelities: ``value`` itself, else the first of these).
_RB_VALUE_KEYS = ("value", "average_gate_fidelity", "Fidelity", "fidelity")

# The 2Q RB panel grouping the page uses (chip-status.js build2QRBPanels):
# StandardRB -> StandardRB, InterleavedRB / IRB -> InterleavedRB.
_RB_TYPE = {"StandardRB": "StandardRB", "InterleavedRB": "InterleavedRB",
            "IRB": "InterleavedRB"}

_MAX_HOPS = 4


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _get(doc: Any, path: tuple[str, ...]) -> Any:
    cur = doc
    for seg in path:
        if isinstance(cur, dict):
            if seg not in cur:
                return None
            cur = cur[seg]
        elif isinstance(cur, list):
            if not seg.isdigit() or int(seg) >= len(cur):
                return None
            cur = cur[int(seg)]
        else:
            return None
    return cur


def _follow(doc: dict, path: tuple[str, ...]) -> tuple[tuple[str, ...], Any]:
    """(path, value) after following the value's pointer chain (<= 4 hops).
    A self-reference or an unresolvable hop stops where it is."""
    val = _get(doc, path)
    for _ in range(_MAX_HOPS):
        if not is_pointer(val) or str(val).startswith("#./"):
            break
        nxt = _compute_resolved_path(doc, str(val), path)
        if not nxt or nxt == path:
            break
        path, val = tuple(nxt), _get(doc, tuple(nxt))
    return path, val


def numeric_leaf_paths(doc: dict, path: tuple[str, ...],
                       skip_key=None, resolver=None) -> list[str]:
    """Every numeric leaf at or under *path* in the CURRENT state, as dot
    paths, with pointers followed (a pointer's history lives at its target)."""
    if resolver is None:
        path, val = _follow(doc, path)
    else:
        val = resolver(doc, ".".join(path)).get("current")
    out: list[str] = []

    def walk(p: tuple[str, ...], v: Any, depth: int) -> None:
        if depth > 6:
            return
        if is_pointer(v):
            if resolver is not None:
                resolved = resolver(doc, ".".join(p)).get("current")
                if not is_pointer(resolved):
                    walk(p, resolved, depth + 1)
                return
            p2, v2 = _follow(doc, p)
            if p2 != p and not is_pointer(v2):
                walk(p2, v2, depth + 1)
            return
        if _is_num(v):
            out.append(".".join(p))
        elif isinstance(v, dict):
            for k, sub in v.items():
                if skip_key and skip_key(k):
                    continue
                walk(p + (str(k),), sub, depth + 1)
        elif isinstance(v, list):
            for i, sub in enumerate(v):
                walk(p + (str(i),), sub, depth + 1)

    walk(path, val, 0)
    return out


def qubit_paths(doc: dict, qubits: Iterable[str], *, resolver=None) -> dict[str, dict[str, list[str]]]:
    """``{panel key: {qubit: [dot path, ...]}}`` for every qubit panel."""
    out: dict[str, dict[str, list[str]]] = {}
    for q in qubits:
        for key, rels in QUBIT_SOURCES.items():
            paths: list[str] = []
            for rel in rels:
                paths.extend(p for p in numeric_leaf_paths(doc, ("qubits", q) + rel, resolver=resolver)
                             if _value_leaf(key, p))
            if paths:
                out.setdefault(key, {})[q] = paths
    return out


def _id_key(k: Any) -> bool:
    return fidelity_field_kind(k) == "load_id"


def pair_rb_paths(doc: dict, pairs: Iterable[str], *, resolver=None) -> tuple[
        dict[str, dict[str, list[str]]], dict[str, dict[str, Any]]]:
    """``({"2q:<rbType>:<gate>": {pair: [dot path]}}, {same key: {pair: load_id}})``.

    The key is the page's own panel key (``dKey`` in build2QRBPanels). The
    ``*_load_id`` beside a fidelity is the run the lab's node says produced
    it -- STATE-recorded provenance, returned as-is and never mixed with the
    history's own run id.
    """
    paths: dict[str, dict[str, list[str]]] = {}
    loads: dict[str, dict[str, Any]] = {}
    for pid in pairs:
        macros = _get(doc, ("qubit_pairs", pid, "macros"))
        if not isinstance(macros, dict):
            continue
        for gate, g in macros.items():
            fid = g.get("fidelity") if isinstance(g, dict) else None
            if not isinstance(fid, dict):
                continue
            gate_load = fid.get("StandardRB_load_id") or fid.get("InterleavedRB_load_id")
            for metric, mv in fid.items():
                rb = _RB_TYPE.get(metric)
                if not rb:
                    continue
                key = f"2q:{rb}:{gate}"
                base = ("qubit_pairs", pid, "macros", str(gate), "fidelity", metric)
                # S8 review P0-1: the ONE leaf the row's number is read from (an alpha
                # or an error_per_gate beside it changes nothing the panel shows)
                block = mv if not is_pointer(mv) else _follow(doc, base)[1]
                if isinstance(block, dict):
                    vk = next((k for k in _RB_VALUE_KEYS if _is_num(block.get(k))
                               or is_pointer(block.get(k))), None)
                    lp = numeric_leaf_paths(doc, base + (vk,), resolver=resolver) if vk else []
                else:
                    lp = numeric_leaf_paths(doc, base, skip_key=_id_key, resolver=resolver)
                if lp:
                    paths.setdefault(key, {}).setdefault(pid, []).extend(lp)
                lid = fid.get(f"{metric}_load_id")
                if lid is None and isinstance(mv, dict):
                    lid = next((mv[k] for k in mv if _id_key(k)), None)
                if lid is None:
                    lid = gate_load
                if lid is not None and not isinstance(lid, (dict, list)):
                    loads.setdefault(key, {})[pid] = lid
    return paths, loads
