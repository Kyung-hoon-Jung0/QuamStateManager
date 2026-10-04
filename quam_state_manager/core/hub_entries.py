"""SM write entries <-> S2 ledger rows (docs/271). Pure: no I/O, no clock.

An *entry* is one SM edit exactly as SM made it -- the undo journal's unit
entry shape: ``{"path": <SM dot-path>, "old": v, "new": v}`` plus
``"created": True`` (the key did not exist before) or ``"deleted": True``
(the subtree was removed), and optionally ``"by"`` (who staged it) and
``"file"`` (state/wiring). Paths are SM dot-paths: segments split on ``.``,
a list element is its decimal index -- the modifier's own grammar.

The ledger's rows are S2 holder rows (``hub_rules.Change``). Converting needs
the document SM wrote, because an S2 holder is not always an SM path: a long
scalar list is ONE holder, so an edit of ``x.3`` inside it is a change of
``x``. :func:`rows_for` therefore lifts every entry to its S2 root in the
written document, rebuilds the BEFORE side by reverting the entries on a copy
of just those roots, and diffs the two with the S2 rules.
"""

from __future__ import annotations

import copy
import marshal
from typing import Any, Callable, Iterable

from quam_state_manager.core import hub_rules as rules

_ABSENT = object()


def entry_of(change, *, by: str | None = None) -> dict:
    """One ``loader.ChangeEntry`` (or an undo-journal entry dict) as a ledger
    entry. Values are deep-copied: the caller's documents keep mutating."""
    if isinstance(change, dict):
        e = {"path": change["path"], "old": copy.deepcopy(change.get("old")),
             "new": copy.deepcopy(change.get("new"))}
        created, deleted = change.get("created"), change.get("deleted")
        actor = change.get("actor") or change.get("by") or by
        src = change.get("source_file") or change.get("file")
    else:
        e = {"path": change.dot_path, "old": copy.deepcopy(change.old_value),
             "new": copy.deepcopy(change.new_value)}
        created, deleted = change.created, change.deleted
        actor = getattr(change, "actor", None) or by
        src = getattr(change, "source_file", None)
    if created:
        e["created"] = True
        e.pop("old", None)
    if deleted:
        e["deleted"] = True
        e.pop("new", None)
    if actor:
        e["by"] = str(actor)
    if src and src != "state":
        e["file"] = src
    return e


def _parts(path: str) -> list[str]:
    return path.split(".") if path else []


def _child(node: Any, part: str) -> Any:
    if isinstance(node, dict):
        return node.get(part, _ABSENT)
    if isinstance(node, list):
        try:
            i = int(part)
        except ValueError:
            return _ABSENT
        return node[i] if 0 <= i < len(node) else _ABSENT
    return _ABSENT


def apply_entries(doc: Any, entries: Iterable[dict]) -> Any:
    """Replay *entries* forward onto *doc* (oldest first), as SM made them.
    The input document is never mutated: every container on a written path
    is copied once (copy-on-write), so the cost follows the entries, not the
    chip, and stays linear in their number."""
    owned: dict[int, Any] = {}
    for e in entries:
        parts = _parts(e["path"])
        if e.get("deleted"):
            doc = _cow_drop(doc, parts, owned)
        else:
            doc = _cow_set(doc, parts, copy.deepcopy(e.get("new")), owned)
    return doc


def revert_entries(doc: Any, entries: Iterable[dict]) -> Any:
    """Undo *entries* on *doc* (newest first): what the document held before
    them. The input document is never mutated (copy-on-write, as above)."""
    owned: dict[int, Any] = {}
    for e in reversed(list(entries)):
        parts = _parts(e["path"])
        if e.get("created"):
            doc = _cow_drop(doc, parts, owned)
        else:
            doc = _cow_set(doc, parts, copy.deepcopy(e.get("old")), owned)
    return doc


def _own(node: Any, owned: dict) -> Any:
    """*node* if this call already copied it, else its shallow copy (now owned)."""
    if id(node) in owned:
        return node
    out = dict(node) if isinstance(node, dict) else list(node)
    owned[id(out)] = out
    return out


def _cow_set(doc: Any, parts: list[str], value: Any, owned: dict) -> Any:
    if not parts:
        return value
    root = _own(doc, owned) if isinstance(doc, (dict, list)) else _own({}, owned)
    node = root
    for part in parts[:-1]:
        if isinstance(node, list):
            i = int(part)
            while len(node) <= i:
                node.append(None)
            child = node[i]
            child = _own(child if isinstance(child, (dict, list)) else {}, owned)
            node[i] = child
        else:
            child = node.get(part)
            child = _own(child if isinstance(child, (dict, list)) else {}, owned)
            node[part] = child
        node = child
    last = parts[-1]
    if isinstance(node, list):
        i = int(last)
        while len(node) <= i:
            node.append(None)
        node[i] = value
    else:
        node[last] = value
    return root


def _cow_drop(doc: Any, parts: list[str], owned: dict) -> Any:
    if not parts or not isinstance(doc, (dict, list)):
        return doc
    # only copy the path when the key is there
    node = doc
    for part in parts[:-1]:
        node = _child(node, part)
        if node is _ABSENT or not isinstance(node, (dict, list)):
            return doc
    if _child(node, parts[-1]) is _ABSENT:
        return doc
    root = _own(doc, owned)
    node = root
    for part in parts[:-1]:
        if isinstance(node, list):
            i = int(part)
            node[i] = child = _own(node[i], owned)
        else:
            node[part] = child = _own(node[part], owned)
        node = child
    if isinstance(node, list):
        del node[int(parts[-1])]
    else:
        del node[parts[-1]]
    return root


def _root_of(parts: list[str], doc: Any) -> tuple[str, ...]:
    """The S2 root of one SM path in *doc*: walk dicts only. A list on the way
    is the root (S2 decides per list whether it is one holder or per-element
    holders, so the whole list is diffed); a missing key or a scalar on the
    way ends the walk there."""
    node = doc
    for i, part in enumerate(parts):
        if isinstance(node, list):
            return tuple(parts[:i])
        if not isinstance(node, dict):
            return tuple(parts[:i])
        if part not in node:
            return tuple(parts[:i + 1])
        node = node[part]
    return tuple(parts)


def _minimal(roots: set[tuple[str, ...]]) -> list[tuple[str, ...]]:
    """The roots no other root contains -- by prefix lookup in a set, so it is
    linear in the number of roots (times their depth), not quadratic."""
    kept: set[tuple[str, ...]] = set()
    out: list[tuple[str, ...]] = []
    for r in sorted(roots, key=len):
        if any(r[:k] in kept for k in range(len(r) + 1)):
            continue
        kept.add(r)
        out.append(r)
    return out


def _sparse(roots: list[tuple[str, ...]], doc: dict) -> dict:
    """A private sparse copy of *doc*: only *roots* (deep-copied) under their
    dict ancestors; an absent root keeps its ancestors so a revert can put a
    removed subtree back. Built in place, linear in the roots."""
    out: dict = {}
    for root in roots:
        src: Any = doc
        node = out
        for part in root[:-1]:
            src = _child(src, part) if src is not _ABSENT else _ABSENT
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = node[part] = {}
            node = nxt
        if not root:
            continue
        val = _child(src, root[-1]) if src is not _ABSENT else _ABSENT
        if val is not _ABSENT:
            node[root[-1]] = copy.deepcopy(val)
    return out


def _revert_inplace(doc: dict, entries: list[dict]) -> dict:
    """:func:`revert_entries` on a tree this module owns (no copies)."""
    owned = _AllOwned()
    for e in reversed(entries):
        parts = _parts(e["path"])
        if e.get("created"):
            doc = _cow_drop(doc, parts, owned)
        else:
            doc = _cow_set(doc, parts, copy.deepcopy(e.get("old")), owned)
    return doc


class _AllOwned(dict):
    """Every container counts as owned: mutate in place."""

    def __contains__(self, _key) -> bool:   # noqa: D401
        return True


def post_fragments(entries: list[dict], doc: dict, pending: list[dict] | None = None) -> dict:
    """The written document reduced to the S2 roots of *entries*: a sparse
    dict with only those roots (deep-copied) under their dict ancestors.
    ``rows_for(entries, post_fragments(entries, doc)) == rows_for(entries, doc)``
    -- the roots are found the same way in both -- so the projector never has
    to parse a whole chip to turn one edit into rows.

    *pending* are edits that are in *doc* (the store's live document) but not
    in the bytes written (they landed after the save): they are taken back
    out of the copy, newest first."""
    if not entries:
        return {}
    roots = _minimal({_root_of(_parts(e["path"]), doc) for e in entries})
    out = copy.deepcopy(doc) if () in roots else _sparse(roots, doc)
    if pending:
        out = _revert_inplace(out, list(pending))
    return out


def rows_for(entries: list[dict], post_doc: dict) -> list[rules.Change]:
    """The S2 rows of an SM write: *post_doc* is the merged document SM wrote,
    *entries* what it changed to get there. Only the entries' roots are
    copied and compared, so the cost follows the edit, not the chip.

    Equal to ``rules.diff(flatten(revert_entries(post, entries)), flatten(post))``
    -- pinned by a randomized cross-check (tests/test_hub_record.py)."""
    if not entries:
        return []
    entries = list(entries)
    roots = _minimal({_root_of(_parts(e["path"]), post_doc) for e in entries})
    if () in roots:
        before = revert_entries(post_doc, entries)
        return rules.diff(rules.flatten(before), rules.flatten(post_doc))
    after_sparse = _sparse(roots, post_doc)
    before_sparse = _revert_inplace(copy.deepcopy(after_sparse), entries)
    return rules.diff(rules.flatten(before_sparse), rules.flatten(after_sparse))


# ----------------------------------------------------------------------
# The whole-chip difference (a write whose content the change log does not
# name: a staged version, saved-but-unapplied edits, a forced push, ...)
# ----------------------------------------------------------------------

def _marshal_equal(a: Any, b: Any) -> bool:
    """Byte-equal marshal images (types, key order, float bits) -- a strict
    content test in C. Equal here implies equal under ``hub_rules.same``;
    unequal says nothing, so the caller walks."""
    try:
        return marshal.dumps(a, 2) == marshal.dumps(b, 2)
    except (ValueError, TypeError):
        return False


def tree_entries(before: Any, after: Any, *, file_of: Callable[[str], str] | None = None) -> list[dict]:
    """SM entries that turn *before* into *after*: a TREE difference with the
    one equality (``hub_rules.same``: 1 == 1.0, never 1 == True, NaN == NaN).
    A subtree one side lacks is ONE created/deleted entry at its highest
    path; a list is a whole value. Pieces at depth 1-2 (``qubits.q7`` ...)
    whose marshal images are byte-equal are skipped without a walk, so the
    Python walk covers only what differs."""
    out: list[dict] = []

    def fil(e: dict, p: str) -> dict:
        if file_of is not None:
            try:
                f = file_of(p)
            except Exception:  # noqa: BLE001 -- the file is a hint only
                f = None
            if f and f != "state":
                e["file"] = f
        return e

    def walk(b: dict, a: dict, prefix: str, depth: int) -> None:
        for k, av in a.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            if k not in b:
                out.append(fil({"path": p, "new": copy.deepcopy(av), "created": True}, p))
                continue
            bv = b[k]
            if bv is av:
                continue
            if isinstance(bv, dict) and isinstance(av, dict):
                if 1 <= depth <= 2 and _marshal_equal(bv, av):
                    continue
                walk(bv, av, p, depth + 1)
            elif not rules.same(bv, av):
                out.append(fil({"path": p, "old": copy.deepcopy(bv), "new": copy.deepcopy(av)}, p))
        for k, bv in b.items():
            if k not in a:
                p = f"{prefix}.{k}" if prefix else str(k)
                out.append(fil({"path": p, "old": copy.deepcopy(bv), "deleted": True}, p))

    walk(before if isinstance(before, dict) else {}, after if isinstance(after, dict) else {}, "", 0)
    return out
