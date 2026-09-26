"""The leaf changes between two documents, at the cost of the DIFFERENCE.

w7/livewrite. A pull answers the page with ``{"changes", "structural"}``
(routes ``_sync_patch``): every leaf whose value differs, in the grammar of
:func:`json_diff.flatten`, and whether any leaf appeared or disappeared. That
comparison flattens both documents, so it is capped at ``json_diff.WALK_CAP``
leaves -- and a chip over the cap (313k leaves on the 30-qubit rig) always
answered "structural", which made the page re-render the whole Live-Edit grid
after every pull that moved one value.

:func:`leaf_changes` gives the answer the uncapped flatten comparison would
give -- the same set of changed paths with the same values, the same
structural verdict -- by walking both documents together and skipping every
subtree pair that is provably leaf-for-leaf equal (``differ``'s strict test:
``==`` and equal marshal bytes and no NaN, so no leaf's ``!=`` can be True).
It returns None whenever the dotted-path grammar could make two different
leaves share a path (a key that is not a string, is empty or holds a dot);
the caller then keeps its old answer.
"""
from __future__ import annotations

from typing import Any

from quam_state_manager.core.differ import _strictly_identical


class _Unsafe(Exception):
    pass


def _keys_safe(d: dict) -> None:
    for k in d:
        if type(k) is not str or not k or "." in k:
            raise _Unsafe(k)


def _leaves(node: Any, prefix: str, out: dict) -> None:
    """``json_diff.flatten`` restricted to one non-root node."""
    if isinstance(node, dict) and node:
        _keys_safe(node)
        for k, v in node.items():
            _leaves(v, f"{prefix}.{k}" if prefix else k, out)
    elif isinstance(node, list) and node:
        for i, v in enumerate(node):
            _leaves(v, f"{prefix}.{i}", out)
    else:
        out[prefix] = node


def leaf_changes(before: Any, after: Any, *, max_changes: int | None = None):
    """``(changes, structural)`` -- ``changes`` is ``[(path, after_value)]``
    for every leaf present on both sides whose values compare ``!=`` --
    or None when the path grammar is ambiguous for these documents.
    Stops early (returns ``(changes, True)``) once more than *max_changes*
    changes were found, which the caller treats as "structural" anyway."""
    changes: list = []
    state = {"structural": False}

    class _Stop(Exception):
        pass

    def add(p, v):
        changes.append((p, v))
        if max_changes is not None and len(changes) > max_changes:
            raise _Stop

    def mixed(a, b, p):
        fa: dict = {}
        fb: dict = {}
        _leaves(a, p, fa)
        _leaves(b, p, fb)
        if fa.keys() != fb.keys():
            state["structural"] = True
        for q, v in fb.items():
            if q in fa and fa[q] != v:
                add(q, v)

    def node(a, b, p):
        da, db = isinstance(a, dict), isinstance(b, dict)
        la, lb = isinstance(a, list), isinstance(b, list)
        if da and db and a and b:
            if _strictly_identical(a, b):
                return
            _keys_safe(a)
            _keys_safe(b)
            if a.keys() != b.keys():
                state["structural"] = True
            for k, vb in b.items():
                if k in a:
                    node(a[k], vb, f"{p}.{k}" if p else k)
            return
        if la and lb and a and b:
            if _strictly_identical(a, b):
                return
            if len(a) != len(b):
                state["structural"] = True
            for i in range(min(len(a), len(b))):
                node(a[i], b[i], f"{p}.{i}")
            return
        if (da and a) or (db and b) or (la and a) or (lb and b):
            mixed(a, b, p)          # container vs leaf / dict vs list / emptied
            return
        if a != b:                  # two leaves (an empty container is one)
            add(p, b)

    try:
        if not isinstance(before, dict) or not isinstance(after, dict):
            return None
        if not before or not after:
            return None
        node(before, after, "")
    except _Unsafe:
        return None
    except _Stop:
        return changes, True
    except RecursionError:
        return None
    return changes, state["structural"]
