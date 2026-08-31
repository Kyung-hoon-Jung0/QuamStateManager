"""Two JSON documents, and only what differs (docs/84).

The Compare hub works, and users do not use it: three different "Compare
selected" buttons lead to three different surfaces, and the one they reach asks
them to declare a comparison context, pick a tolerance preset and possibly map
entities before it will show anything. What they ask for is what an IDE gives
them — *show me the differences, and how big they are*.

This module is the engine for that. It is deliberately small: flatten both
documents to leaves, compare, and hand back three things.

**Rows** — one per differing leaf, each carrying both values, the change class
and the docs/76 delta, so the list view can rank by "what moved the most" and
every number reads the same as it does in the Review tray.

**Pruned trees** — the two documents reduced to the differing leaves plus their
ancestor chains. This is what makes "differences only" affordable: measured on
real snapshot pairs, neighbouring snapshots of a chip differ in **4 nodes out
of 15,285** (0.0%), and even a first-vs-last comparison prunes to 26-53%. The
client renders these with the existing JSON tree, so the diff view inherits
search, depth controls, lazy expansion and the type colouring for free.

**Counts** — including how much did NOT change, because "1 of 15,285" is the
sentence that makes a diff readable.

A measured caveat drives one design decision: a first-vs-last comparison on a
real chip reports 2,758 differences, of which only 117 are numeric — the rest
are keys added or removed by a regenerate, and a class rename churning
``__class__`` strings. Dumping 2,758 rows is how the old surface became
unusable, so rows carry their class and the caller is expected to lead with the
value changes and fold the structural ones.
"""
from __future__ import annotations

from typing import Any, Iterable

from quam_state_manager.core import value_delta

# Change classes.
CHANGED = "changed"       # both sides have the leaf, values differ
ADDED = "added"           # only the B side has it
REMOVED = "removed"       # only the A side has it

# A diff bigger than this stops producing rows. Real first-vs-last comparisons
# reach ~2,800; the cap exists for a pathological pair (two unrelated chips),
# not for anything a lab does on purpose.
ROW_CAP = 5_000
# Leaves per document. Same purpose as leaf_index.WALK_CAP.
WALK_CAP = 200_000

_MISSING = object()


def flatten(doc: Any, *, cap: int = WALK_CAP) -> tuple[dict[str, Any], bool]:
    """``({dot_path: leaf_value}, truncated)``.

    Containers are not leaves; an EMPTY container is, because "this list became
    empty" is a difference worth showing. The path grammar is the one used
    everywhere else (``a.b.3`` for list elements).
    """
    out: dict[str, Any] = {}
    stack: list[tuple[str, Any]] = [("", doc)]
    truncated = False
    while stack:
        prefix, node = stack.pop()
        if isinstance(node, dict) and node:
            for k, v in node.items():
                stack.append((f"{prefix}.{k}" if prefix else str(k), v))
            continue
        if isinstance(node, list) and node:
            for i, v in enumerate(node):
                stack.append((f"{prefix}.{i}", v))
            continue
        if not prefix and isinstance(node, (dict, list)):
            continue        # an empty DOCUMENT has no leaves, it is not one
        if len(out) >= cap:
            truncated = True
            break
        out[prefix] = node
    return out, truncated


def _class_of(a: Any, b: Any) -> str:
    if a is _MISSING:
        return ADDED
    if b is _MISSING:
        return REMOVED
    return CHANGED


def _sort_key(row: dict) -> tuple:
    """Biggest relative move first, then absolute, then path.

    A user scanning a diff wants "what moved the most", and a 5 % shift in a
    frequency matters more than a 1 ns change in a delay. Rows without a
    numeric delta (added, removed, text) sort after every numeric one — they
    are structure, not measurement.
    """
    d = row.get("delta")
    if not d:
        return (1, 0.0, 0.0, row["path"])
    pct = abs(d.get("pct") or 0.0)
    mag = abs(d.get("delta") or 0.0)
    return (0, -pct, -mag, row["path"])


def diff_rows(a_doc: Any, b_doc: Any, *, cap: int = ROW_CAP) -> dict:
    """Every differing leaf between two documents, ranked."""
    a_flat, a_trunc = flatten(a_doc)
    b_flat, b_trunc = flatten(b_doc)
    keys = set(a_flat) | set(b_flat)

    rows: list[dict] = []
    same = 0
    for path in keys:
        av = a_flat.get(path, _MISSING)
        bv = b_flat.get(path, _MISSING)
        if av is not _MISSING and bv is not _MISSING and av == bv:
            same += 1
            continue
        kind = _class_of(av, bv)
        rows.append({
            "path": path,
            "a": None if av is _MISSING else av,
            "b": None if bv is _MISSING else bv,
            "kind": kind,
            # The SAME arithmetic the Review tray uses (docs/76), so one change
            # reads identically on every surface. Only meaningful between two
            # numbers — value_delta returns None otherwise, and nothing is
            # rendered rather than a fabricated 0.
            "delta": (value_delta.compute(av, bv)
                      if kind == CHANGED else None),
        })
    rows.sort(key=_sort_key)
    truncated = a_trunc or b_trunc or len(rows) > cap
    counts = {
        CHANGED: sum(1 for r in rows if r["kind"] == CHANGED),
        ADDED: sum(1 for r in rows if r["kind"] == ADDED),
        REMOVED: sum(1 for r in rows if r["kind"] == REMOVED),
        "same": same,
        "total": len(rows),
    }
    counts["numeric"] = sum(1 for r in rows if r.get("delta"))
    return {"rows": rows[:cap], "counts": counts, "truncated": truncated}


def diff_rows_n(docs: list[Any], *, cap: int = ROW_CAP) -> dict:
    """Rows for an N-column list diff (the workbench's third source, customer
    2026-08-27): one row per leaf path where ANY two sides differ (absence
    counts as a difference), every side's value beside each other. Same
    flatten, same path grammar, same cap as :func:`diff_rows`; ranking is by
    path so a three-way read stays stable while sources are swapped.

    docs/141 4ac: the cap bounds what is RENDERED, never what is counted.
    ``counts["changed"]`` is every differing leaf and ``counts["same"]`` every
    leaf that truly agrees; ``counts["shown"]`` is how many rows came back.
    Subtracting the rendered rows from the path total (the old formula) told
    the user that 5,354 differing leaves "agree" the moment a real chip pair
    crossed the cap -- on a surface whose whole promise is "differences only".
    ``one_sided`` counts the leaves that exist on some sources and not others,
    which is what ``added``/``removed`` mean for two sides and cannot mean for
    N (kept at 0 rather than invented -- see the workbench's counts strip).
    """
    # flatten keeps its OWN (walk) cap: `cap` bounds the ROWS returned, never
    # the leaves compared -- a real chip has ~8,800 leaves, and capping the
    # walk at the row cap silently dropped every leaf past it (caught by a
    # real-chip screenshot: "identical (5,000 values compared) · capped").
    flats, truncated = [], False
    for d in docs:
        f, t = flatten(d)
        flats.append(f)
        truncated = truncated or t
    paths: set[str] = set()
    for f in flats:
        paths.update(f)
    rows: list[dict] = []
    same = 0
    differing = 0
    one_sided = 0
    for p in sorted(paths):
        present = [p in f for f in flats]
        vals = [f.get(p) for f in flats]
        # docs/141 4ac: compare each present value against EVERY representative
        # seen so far, not only the first one. `_eq` carries a relative
        # tolerance and is therefore not transitive, so "all equal to the
        # first" is not "all equal" -- a triple A~B, A~C, B!~C was reported as
        # agreeing and the row never rendered.
        reps: list[Any] = []
        differs = False
        for pr, v in zip(present, vals):
            if not pr:
                continue
            if not any(_eq(rv, v) for rv in reps):
                if reps:
                    differs = True
                reps.append(v)
        if any(present) and not all(present):
            differs = True
            one_sided += 1
        if not differs:
            same += 1
            continue
        differing += 1
        if len(rows) >= cap:
            truncated = True          # keep COUNTING past the cap, stop RENDERING
            continue
        rows.append({"path": p, "vals": vals, "present": present, "kind": "changed"})
    return {"ok": True, "rows": rows, "truncated": truncated,
            "counts": {"changed": differing, "added": 0, "removed": 0,
                       "same": same, "total": differing,
                       "shown": len(rows), "one_sided": one_sided}}


def _eq(a: Any, b: Any) -> bool:
    try:
        from .differ import compare_equal
        return compare_equal(a, b)
    except Exception:  # noqa: BLE001 — fall back to plain equality
        return a == b


def prune(doc: Any, paths: Iterable[str]) -> Any:
    """``doc`` reduced to ``paths`` and the containers on the way to them.

    Lists become dicts keyed by their index as a string. That is deliberate:
    a pruned list would have to either keep its holes (misleading indices) or
    renumber (wrong paths), and the tree renderer shows an index-keyed object
    with exactly the same dot paths the rest of the app uses.
    """
    root: dict = {}
    for path in paths:
        segs = path.split(".") if path else []
        src: Any = doc
        dst = root
        ok = True
        for i, seg in enumerate(segs):
            if isinstance(src, dict) and seg in src:
                src = src[seg]
            elif isinstance(src, list) and seg.isdigit() and int(seg) < len(src):
                src = src[int(seg)]
            else:
                ok = False
                break
            if i == len(segs) - 1:
                dst[seg] = src
            else:
                nxt = dst.get(seg)
                if not isinstance(nxt, dict):
                    nxt = {}
                    dst[seg] = nxt
                dst = nxt
        if not ok:
            continue
    return root


def build(a_doc: Any, b_doc: Any, *, cap: int = ROW_CAP,
          with_rows: bool = True) -> dict:
    """The whole payload: counts, both sides pruned to the diff, and — unless
    ``with_rows`` is off — the ranked rows.

    The tree view does not need the rows (the pruned documents carry the same
    values), and on a big cross-chip diff the rows are most of the payload:
    1.2 MB drops to ~0.4 MB by leaving them out. The list view fetches them
    separately, paged.
    """
    res = diff_rows(a_doc, b_doc, cap=cap)
    rows = res["rows"]
    res["tree_a"] = prune(a_doc, [r["path"] for r in rows if r["kind"] != ADDED])
    res["tree_b"] = prune(b_doc, [r["path"] for r in rows if r["kind"] != REMOVED])
    if not with_rows:
        res["rows"] = []
    return res
