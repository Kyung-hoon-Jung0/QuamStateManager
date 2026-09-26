"""Chip Status per-metric panel metadata (queue item 4, 2026-09-25).

"When was this value last measured, and which run wrote it?" -- asked of every
cell of every per-metric panel (T1, T2 Ramsey, readout fidelity, 2Q RB, ...).

The answer comes from the docs/83 change-point index: every numeric leaf of the
chip, one row per snapshot at which its value CHANGED, each row carrying the
snapshot's trigger / run id / experiment. The newest change point of the leaf a
panel shows is the snapshot that wrote the value on screen, which is exactly
"when it was last measured, and by which run". Three honesty rules:

* A value whose newest change point is the leaf's FIRST row has no measurement
  on record inside the history window: it was already there when history
  started. That is reported as ``first=True`` ("unchanged since history
  began"), never as a measurement at that time.
* A metric derived from a SUBTREE (a readout fidelity from its confusion
  matrix, a 2Q RB number from its nested fidelity block) is as new as the
  newest change among the subtree's leaves.
* A path the index declines (a pointer somewhere in its history) is followed
  to where it points in the CURRENT state when it is a pointer now; otherwise
  it is simply absent -- no entry, never an invented time.

This module is pure: it builds the path lists from a merged state dict and
folds index rows into entries. The route owns the history/IO side.
"""
from __future__ import annotations

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
                       skip_key=None) -> list[str]:
    """Every numeric leaf at or under *path* in the CURRENT state, as dot
    paths, with pointers followed (a pointer's history lives at its target)."""
    path, val = _follow(doc, path)
    out: list[str] = []

    def walk(p: tuple[str, ...], v: Any, depth: int) -> None:
        if depth > 6:
            return
        if is_pointer(v):
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


def qubit_paths(doc: dict, qubits: Iterable[str]) -> dict[str, dict[str, list[str]]]:
    """``{panel key: {qubit: [dot path, ...]}}`` for every qubit panel."""
    out: dict[str, dict[str, list[str]]] = {}
    for q in qubits:
        for key, rels in QUBIT_SOURCES.items():
            paths: list[str] = []
            for rel in rels:
                paths.extend(numeric_leaf_paths(doc, ("qubits", q) + rel))
            if paths:
                out.setdefault(key, {})[q] = paths
    return out


def _id_key(k: Any) -> bool:
    return fidelity_field_kind(k) == "load_id"


def pair_rb_paths(doc: dict, pairs: Iterable[str]) -> tuple[
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
                lp = numeric_leaf_paths(doc, base, skip_key=_id_key)
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


def newest_change(series: dict[str, list[tuple]], paths: list[str]) -> dict | None:
    """Fold the change-point rows of *paths* into one entry, or None.

    Rows are ``(ts, value, trigger, run_id, experiment, folder)`` oldest
    first (``leaf_index.series``). The entry is the newest change point over
    all of them; ``first`` is True only when EVERY path's newest row is also
    its first row (nothing changed inside the history window); ``value`` is
    set only for a single-leaf metric (a subtree has no one value)."""
    best = None
    all_first = True
    seen = 0
    for p in paths:
        rows = series.get(p)
        if not rows:
            continue
        seen += 1
        last = rows[-1]
        if len(rows) > 1:
            all_first = False
        if best is None or str(last[0]) > str(best[0]):
            best = last
    if best is None:
        return None
    out = {
        "ts": str(best[0]),
        "run": best[3] if _is_num(best[3]) else None,
        "trigger": best[2],
        "first": all_first,
        "leaves": seen,
    }
    if len(paths) == 1 and seen == 1 and _is_num(best[1]):
        out["value"] = best[1]
    if best[1] is None:
        out["gone"] = True
    return out
