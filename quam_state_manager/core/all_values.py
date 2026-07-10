"""Flat "All values" completeness rows for the Live State Edit → All values tab.

Enumerates EVERY leaf of the merged state+wiring with a structural walk (tracking
whether each leaf sits inside a real JSON *list*, so the ``ports`` tree's
FEM/port-number dict keys are NOT mistaken for list indices), classifies each
through :mod:`core.leaf_classify`, and emits compact rows the tab virtual-scrolls.

Editable rows (plain scalars) commit through the SAME ``/field/edit-batch`` path
the curated grids use — no new mutation code. Cross-ref pointers, self-refs,
list/matrix elements, identity/type keys, and chip-membership arrays are emitted
read-only (per the user's safety decisions), so the user can SEE and account for
every leaf while only scalars accept input.

The coverage summary's ``total`` equals ``len(loader.flatten(merged))`` and the
per-kind counts sum to it — that equality is the completeness proof the toolbar
shows ("X of N leaves · E editable · R read-only").
"""

from __future__ import annotations

from typing import Any, Iterable

from quam_state_manager.core import units
from quam_state_manager.core.leaf_classify import (
    ALL_KINDS,
    KIND_SCALAR,
    classify_leaf,
)


def _walk_classified(
    obj: Any, prefix: str = "", in_list: bool = False,
    out: list[tuple[str, str, Any, bool]] | None = None,
) -> list[tuple[str, str, Any, bool]]:
    """Flatten ``obj`` to ``(dot_path, leaf_name, value, in_list)`` leaf tuples.

    Mirrors ``loader._walk`` (same leaf set, same dot-path format) but additionally
    threads an ``in_list`` flag: once we descend into a JSON list it stays True for
    that subtree, so a list *element* is distinguishable from a numeric-keyed dict
    (port numbers). Empty dicts/lists yield nothing — exactly like ``loader._walk``.
    """
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            cp = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                _walk_classified(v, cp, in_list, out)
            else:
                out.append((cp, k, v, in_list))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            si = str(i)
            cp = f"{prefix}.{si}" if prefix else si
            if isinstance(v, (dict, list)):
                _walk_classified(v, cp, True, out)
            else:
                out.append((cp, si, v, True))
    return out


def _display(value: Any) -> str:
    """Editable/display string for a leaf. Numbers use the lossless comma-grouped
    form (round-trips through ``cli._parse_value``, identical to Bulk Edit cells);
    strings (incl. pointer targets) show verbatim; ``None`` shows blank."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return units.group_digits(value)
    return str(value)


def build_all_values_rows(
    store: Any, modified: Iterable[str] | None = None
) -> tuple[list[list[Any]], dict[str, Any]]:
    """Return ``(rows, summary)`` for the loaded chip.

    ``rows`` — compact ``[dot_path, display, kind, modified]`` arrays (one per
    leaf), where ``kind`` is a :mod:`leaf_classify` policy kind and ``modified``
    is ``1`` for an edited-but-unsaved editable leaf else ``0``.

    ``summary`` — ``{total, editable, readonly, by_kind}`` for the coverage
    counter. ``total == len(rows) == len(loader.flatten(merged))``.
    """
    modified_set = set(modified or ())
    with store._lock:
        leaves = _walk_classified(store.merged)

    counts = {k: 0 for k in ALL_KINDS}
    rows: list[list[Any]] = []
    for path, leaf_name, value, in_list in leaves:
        top = path.split(".", 1)[0]
        kind = classify_leaf(top, leaf_name, value, in_list)
        counts[kind] += 1
        is_mod = 1 if (kind == KIND_SCALAR and path in modified_set) else 0
        rows.append([path, _display(value), kind, is_mod])

    total = len(leaves)
    editable = counts[KIND_SCALAR]
    summary = {
        "total": total,
        "editable": editable,
        "readonly": total - editable,
        "by_kind": counts,
    }
    return rows, summary
