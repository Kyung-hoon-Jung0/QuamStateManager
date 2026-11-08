"""Remove a qubit or a pair, in place -- the mirror of :mod:`diagram_add`.

A plan names only DELETIONS (the entity, its wiring, the operations on other
qubits that exist only to play its gates, keyed entries that name it) and the
name lists it leaves; the route stages them through the modifier under ONE
change group, so the pending tray, Review / Apply and one Ctrl+Z hold it like
any other edit.

What a removal may not do is leave a pointer that resolved before and does not
after: every such pointer is a ``blocker`` and the plan refuses, naming it.
Removing a qubit removes the pairs it is a member of together with it (they
cannot exist without it), and says so before anything is staged.

Free-form text (anything under an ``extras`` key) is never rewritten: a note
that mentions the qubit is the user's own record.

Ports are not deleted: a port configuration (its LO, band, full-scale power)
is hardware set-up the next qubit on that output reuses. The plan lists the
ports the removal leaves unused (``freed``).

Pure: functions read ``merged`` and return data; they never write.
"""

from __future__ import annotations

import copy
from typing import Any

from quam_state_manager.core.diagram_add import (
    _PORT_RE, _abs_target, _pointers, _walk_strings)
from quam_state_manager.core.regen_merge import _pair_membership, is_pointer

_FREE_FORM = "extras"


def _get(doc: Any, path: tuple):
    node = doc
    for p in path:
        if isinstance(node, dict) and p in node:
            node = node[p]
        elif isinstance(node, list) and p.isdigit() and int(p) < len(node):
            node = node[int(p)]
        else:
            return _NOPE
    return node


_NOPE = object()


def _delete(doc: dict, path: tuple) -> None:
    node = doc
    for p in path[:-1]:
        node = node[p]
    del node[path[-1]]


def _set(doc: dict, path: tuple, value: Any) -> None:
    node = doc
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = value


def _under(path: tuple, roots) -> bool:
    return any(path[:len(r)] == r for r in roots)


def _ports_of(node: Any) -> set:
    refs: list = []
    _walk_strings(node, refs)
    out = set()
    for r in refs:
        if r[0] == "list":
            continue
        out.add(r)
    return out


def plan_remove_qubit(merged: dict, qid: str, also=()) -> dict:
    qs = merged.get("qubits") if isinstance(merged.get("qubits"), dict) else {}
    if qid not in qs:
        return {"error": f"{qid} is not a qubit of this chip."}
    if len(qs) <= 1:
        return {"error": "This is the chip's only qubit: a chip with no qubit cannot be "
                         "loaded. Nothing to remove here."}
    pairs = merged.get("qubit_pairs") if isinstance(merged.get("qubit_pairs"), dict) else {}
    with_pairs = sorted(p for p, v in pairs.items()
                        if qid in (_pair_membership(v, merged) or ()))
    return _plan(merged, {qid}, set(with_pairs), kind="qubit", name=qid, also=also)


def plan_remove_pair(merged: dict, pid: str, also=()) -> dict:
    pairs = merged.get("qubit_pairs") if isinstance(merged.get("qubit_pairs"), dict) else {}
    if pid not in pairs:
        return {"error": f"{pid} is not a pair of this chip."}
    return _plan(merged, set(), {pid}, kind="pair", name=pid, also=also)


def _plan(merged: dict, rq: set, rp: set, *, kind: str, name: str, also=()) -> dict:
    roots: list[tuple] = []
    for q in sorted(rq):
        roots.append(("qubits", q))
        if _get(merged, ("wiring", "qubits", q)) is not _NOPE:
            roots.append(("wiring", "qubits", q))
    for p in sorted(rp):
        roots.append(("qubit_pairs", p))
        if _get(merged, ("wiring", "qubit_pairs", p)) is not _NOPE:
            roots.append(("wiring", "qubit_pairs", p))

    def removed_target(t: tuple) -> bool:
        return _under(t, roots)

    # operations / macros on SURVIVING qubits that play a removed entity's gate
    member_ops: list[tuple] = []
    for q, body in (merged.get("qubits") or {}).items():
        if q in rq or not isinstance(body, dict):
            continue
        holders = [((q,), body)] + [((q, line), ch) for line, ch in body.items()
                                    if isinstance(ch, dict)]
        for base, holder in holders:
            for coll in ("operations", "macros"):
                d = holder.get(coll)
                if not isinstance(d, dict):
                    continue
                for op, val in d.items():
                    path = ("qubits",) + base + (coll, op)
                    refs: list = []
                    _pointers(val, path, refs)
                    if any(removed_target(_abs_target(ptr, pp)) for pp, ptr in refs):
                        member_ops.append(path)
    # what the lab's own check asked to delete TOGETHER with it (an accepted
    # "Delete together" offer): existing paths not already inside the plan
    extra: list[tuple] = []
    for a in also or ():
        t = tuple(str(a).split("."))
        if a and _get(merged, t) is not _NOPE and not _under(t, roots + member_ops + extra):
            extra.append(t)
    roots_all = roots + member_ops + extra

    # keyed entries naming a removed id (spectator tables), and list items
    keyed: list[tuple] = []
    list_sets: dict[tuple, list] = {}
    ids = rq | rp

    def visit(node: Any, path: tuple):
        if _under(path, roots_all) or (path and path[-1] == _FREE_FORM):
            return
        if isinstance(node, dict):
            for k, v in node.items():
                sub = path + (str(k),)
                if str(k) in ids and path and path[0] != "ports" and not _under(sub, roots_all):
                    keyed.append(sub)
                    continue
                visit(v, sub)
        elif isinstance(node, list):
            if node and all(isinstance(x, str) for x in node) and any(x in ids for x in node):
                list_sets[path] = [x for x in node if x not in ids]
                return
            for i, v in enumerate(node):
                visit(v, path + (str(i),))

    visit(merged, ())
    deletes = roots_all + keyed

    # simulate, then judge every pointer that resolved before and not after
    sim = copy.deepcopy(merged)
    for path in sorted(deletes, key=len, reverse=True):
        if _get(sim, path) is not _NOPE:
            _delete(sim, path)
    for path, val in list_sets.items():
        _set(sim, path, val)
    refs: list = []
    _pointers(sim, (), refs)
    blockers = []
    dangle = []      # pointers in free-form extras (never rewritten) that will dangle
    for path, ptr in refs:
        tgt = _abs_target(ptr, path)
        if not (_get(sim, tgt) is _NOPE and _get(merged, tgt) is not _NOPE):
            continue
        if path and _FREE_FORM in path:
            dangle.append({"path": ".".join(path), "pointer": ptr})
        else:
            blockers.append({"path": ".".join(path), "pointer": ptr})
    silent = _silent_changes(merged, list_sets, keyed, ids)

    # ports the removal leaves unused (the port configuration itself is kept)
    before = _ports_of({k: v for k, v in merged.items() if k != "ports"})
    after = _ports_of({k: v for k, v in sim.items() if k != "ports"})
    freed = []
    for kind_, con, fem, pid in sorted(before - after, key=lambda r: (r[1], r[2], r[0], r[3])):
        freed.append({"kind": kind_, "con": con, "fem": fem, "port": pid,
                      "io": "in" if kind_.endswith("inputs") else "out",
                      "label": f"{con} slot {fem} {'in' if kind_.endswith('inputs') else 'out'} {pid}"})

    dotted = [".".join(p) for p in deletes]
    plan = {
        "deletes": dotted,
        "creates": [],
        "sets": [(".".join(p), v) for p, v in list_sets.items()],
        "summary": {
            "kind": kind, "name": name, "op": "remove",
            "qubits": sorted(rq), "pairs": sorted(rp),
            "pairs_with": sorted(rp) if kind == "qubit" else [],
            "member_ops": [".".join(p) for p in member_ops],
            "also": [".".join(p) for p in extra],
            "keyed": [".".join(p) for p in keyed],
            "lists": [".".join(p) for p in list_sets],
            "wiring": [".".join(p) for p in roots if p[0] == "wiring"],
            "freed": freed,
            "silent": silent,
            "extras_dangle": dangle,
        },
        "blockers": blockers,
    }
    if blockers:
        b = blockers[0]
        more = f" (and {len(blockers) - 1} more)" if len(blockers) > 1 else ""
        plan["error"] = (f"Removing {name} would leave {b['path']} pointing at "
                         f"{b['pointer']}, which would no longer exist{more}. Change or "
                         "delete that value first.")
    return plan


def _entity_ids(merged: dict) -> set:
    out = set()
    for coll in ("qubits", "qubit_pairs"):
        if isinstance(merged.get(coll), dict):
            out |= set(merged[coll])
    return out


def _silent_changes(merged: dict, list_sets: dict, keyed: list, ids: set) -> list[dict]:
    """Every list the removal shortens and every key it deletes BECAUSE an
    element / key equals a removed id -- changes made by name, not by
    structure. ``check`` marks the ones that do not look like a list (or a
    table) of entity names: a 2-element list of qubit ids is a pair's
    membership, not a name list, and a key among non-entity siblings may be
    a coincidence [derived heuristic]."""
    known = _entity_ids(merged)
    out = []
    for path, new in list_sets.items():
        old = _get(merged, path)
        why = None
        if isinstance(old, list):
            if len(old) == 2 and all(x in known for x in old):
                why = "a 2-element list of qubit names: this may be a pair membership, not a name list"
            elif not all(x in known for x in old):
                why = "not every element is a qubit or pair name"
        out.append({"path": ".".join(path), "what": "shortened",
                    "before": old if isinstance(old, list) else None, "after": new,
                    "check": why is not None, "why": why})
    for path in keyed:
        parent = _get(merged, path[:-1])
        sibs = [k for k in (parent if isinstance(parent, dict) else {}) if k != path[-1]]
        why = None
        if sibs and not all(k in known for k in sibs):
            why = "its sibling keys are not qubit or pair names"
        out.append({"path": ".".join(path), "what": "deleted key", "check": why is not None,
                    "why": why})
    return out


def apply_to_docs(state: dict, wiring: dict, plan: dict) -> tuple[dict, dict]:
    """The chip AFTER a plan (add or remove), as a (state, wiring) pair for an
    out-of-process check. Top-level keys present in ``wiring`` go there
    (the loader's own rule, ``QuamStore.source_file_for``)."""
    docs = {"state": copy.deepcopy(state), "wiring": copy.deepcopy(wiring)}

    def doc_for(path: str) -> dict:
        return docs["wiring"] if path.split(".")[0] in docs["wiring"] else docs["state"]

    for path in sorted(plan.get("deletes", []), key=lambda p: p.count("."), reverse=True):
        t = tuple(path.split("."))
        d = doc_for(path)
        if _get(d, t) is not _NOPE:
            _delete(d, t)
    for path, val in plan.get("creates", []):
        _set(doc_for(path), tuple(path.split(".")), copy.deepcopy(val))
    for path, val in plan.get("sets", []):
        _set(doc_for(path), tuple(path.split(".")), copy.deepcopy(val))
    return docs["state"], docs["wiring"]


def removal_rows(plan: dict, merged: dict, limit: int = 400) -> list[dict]:
    """What the removal deletes, one row per subtree (with its leaf count), and
    each list it shortens."""
    rows = []
    for path in plan.get("deletes", []):
        val = _get(merged, tuple(path.split(".")))
        n = 0
        stack = [val]
        while stack:
            v = stack.pop()
            if isinstance(v, dict) and v:
                stack.extend(v.values())
            else:
                n += 1
        rows.append({"path": path, "op": "delete", "leaves": n})
    for path, val in plan.get("sets", []):
        rows.append({"path": path, "op": "shorten", "value": val})
    return rows[:limit]


__all__ = ["plan_remove_qubit", "plan_remove_pair", "apply_to_docs", "removal_rows",
           "is_pointer", "_PORT_RE"]
