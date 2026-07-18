"""Flat "All values" completeness rows for the Live State Edit → All values tab.

Enumerates EVERY leaf of the merged state+wiring with a structural walk (tracking
whether each leaf sits inside a real JSON *list*, so the ``ports`` tree's
FEM/port-number dict keys are NOT mistaken for list indices), classifies each
through :mod:`core.leaf_classify`, and emits compact rows the tab virtual-scrolls.

Editable rows commit through the SAME ``/field/edit-batch`` path the curated
grids use — no new mutation code. v2 widens the editable surface: cross-ref
pointers display their RESOLVED value and edit-through (edit-batch resolves the
write path server-side); list/matrix elements edit via dot-form numeric paths.
Self-refs, identity/type keys, chip-membership arrays and DANGLING pointers stay
read-only. v2 also emits CONTAINER rows — one per non-empty array (``[N]`` /
``[R×C]``) and one per empty dict/list (previously invisible: the leaf walk
yields nothing for them, so the user couldn't see or fill them) — which the
client edits whole via a ✎ JSON modal.

The coverage summary's ``total`` equals ``len(loader.flatten(merged))`` and the
per-kind counts sum to it — that equality is the completeness proof the toolbar
shows. Container rows are counted SEPARATELY (``arrays`` / ``empties``) so the
leaf invariant is untouched: ``len(rows) == total + arrays + empties``.
"""

from __future__ import annotations

import json
from typing import Any, Iterable

from quam_state_manager.core import units
from quam_state_manager.core.leaf_classify import (
    ALL_KINDS,
    KIND_SCALAR,
    KIND_XREF,
    classify_leaf,
)
from quam_state_manager.core.pointer_resolver import is_pointer

# Container-row kinds — NOT part of leaf_classify's 6-kind leaf partition (they
# classify nodes the leaf walk skips), so ALL_KINDS / by_kind stay leaves-only.
KIND_ARRAY = "array"    # non-empty JSON list — display [N] / [R×C], ✎ whole-value edit
KIND_EMPTY = "empty"    # empty dict/list — display "{} empty" / "[] empty", ✎ to fill


def _walk_classified(
    obj: Any, prefix: str = "", in_list: bool = False,
    out: list[tuple[str, str, str, Any, bool]] | None = None,
) -> list[tuple[str, str, str, Any, bool]]:
    """Flatten ``obj`` to ``(node, dot_path, name, value, in_list)`` tuples.

    ``node`` is ``"leaf"`` (same leaf set + dot-path format as ``loader._walk``),
    ``"array"`` (every non-empty JSON list — including each inner row of a
    matrix, emitted BEFORE its elements so the header row sorts above them) or
    ``"empty"`` (empty dict/list — invisible to the plain leaf walk). The
    structural ``in_list`` flag threads exactly as before: once we descend into
    a JSON list it stays True for that subtree, so a list *element* is
    distinguishable from a numeric-keyed dict (port numbers).
    """
    if out is None:
        out = []
    items = obj.items() if isinstance(obj, dict) else enumerate(obj)
    inside = in_list or isinstance(obj, list)
    for k, v in items:
        key = str(k)
        cp = f"{prefix}.{key}" if prefix else key
        if isinstance(v, (dict, list)):
            if not v:
                out.append(("empty", cp, key, v, inside))
            else:
                if isinstance(v, list):
                    out.append(("array", cp, key, v, inside))
                _walk_classified(v, cp, inside, out)
        else:
            out.append(("leaf", cp, key, v, inside))
    return out


def _display(value: Any) -> str:
    """Editable/display string for a leaf. Numbers use the lossless comma-grouped
    form (round-trips through ``cli._parse_value``, identical to Bulk Edit cells);
    strings (incl. pointer targets) show verbatim; ``None`` shows blank.
    Containers (a pointer resolving to a dict/list) render as JSON so the
    edit-through input round-trips via the JSON-aware ``_parse_value``."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return units.group_digits(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _matrix_dims(value: list) -> str | None:
    """``"R×C"`` when *value* is a uniform matrix (every element a list of one
    shared length), else None. Ragged / mixed / scalar-element lists get the
    plain ``[N]`` display."""
    cols: int | None = None
    for el in value:
        if not isinstance(el, list):
            return None
        if cols is None:
            cols = len(el)
        elif len(el) != cols:
            return None
    if cols is None:
        return None
    return f"{len(value)}×{cols}"


def build_all_values_rows(
    store: Any, modified: Iterable[str] | None = None
) -> tuple[list[list[Any]], dict[str, Any]]:
    """Return ``(rows, summary)`` for the loaded chip.

    ``rows`` — compact ``[dot_path, display, kind, modified]`` arrays with an
    OPTIONAL 5th ``extra`` dict (v2): xref rows carry ``{"p": raw_pointer,
    "d": 0|1}`` (dangling flag; display is the RESOLVED value, or the raw
    pointer text when dangling), uniform-matrix array rows carry
    ``{"dims": "R×C"}``. Scalar type annotations (``extra["ty"]``) are attached
    by the ROUTE (it owns the store's type policy), not here.

    ``summary`` — ``{total, editable, readonly, by_kind, arrays, empties}``.
    ``total == len(loader.flatten(merged))`` (leaves only); container rows count
    into ``arrays``/``empties`` so ``len(rows) == total + arrays + empties``.
    """
    modified_set = set(modified or ())
    counts = {k: 0 for k in ALL_KINDS}
    n_arrays = n_empties = 0
    rows: list[list[Any]] = []
    # The whole build stays under the store lock: xref resolution walks `merged`,
    # so resolving outside the walk's lock could chase a concurrently-mutated
    # tree. The route already holds this RLock (re-entrant) around the call.
    with store._lock:
        for node, path, name, value, in_list in _walk_classified(store.merged):
            if node == "array":
                n_arrays += 1
                dims = _matrix_dims(value)
                row: list[Any] = [path, f"[{dims or len(value)}]", KIND_ARRAY, 0]
                if dims:
                    row.append({"dims": dims})
                rows.append(row)
                continue
            if node == "empty":
                n_empties += 1
                rows.append(
                    [path, "{} empty" if isinstance(value, dict) else "[] empty",
                     KIND_EMPTY, 0])
                continue
            top = path.split(".", 1)[0]
            kind = classify_leaf(top, name, value, in_list)
            counts[kind] += 1
            if kind == KIND_XREF:
                # Edit-through display: resolve like store.resolve_value. An
                # unresolvable chain returns a pointer string → dangling: keep
                # the RAW text and let the client render it read-only.
                resolved = store.resolve_pointer(value, tuple(path.split(".")))
                dangling = 1 if is_pointer(resolved) else 0
                rows.append([path, value if dangling else _display(resolved),
                             kind, 0, {"p": value, "d": dangling}])
            else:
                is_mod = 1 if (kind == KIND_SCALAR and path in modified_set) else 0
                rows.append([path, _display(value), kind, is_mod])

    total = sum(counts.values())
    editable = counts[KIND_SCALAR]
    summary = {
        "total": total,
        "editable": editable,
        "readonly": total - editable,
        "by_kind": counts,
        "arrays": n_arrays,
        "empties": n_empties,
    }
    return rows, summary
