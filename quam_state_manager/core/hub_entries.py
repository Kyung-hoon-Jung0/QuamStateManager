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
from typing import Any, Iterable

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


def _set(doc: Any, parts: list[str], value: Any) -> Any:
    """``doc`` with ``value`` at ``parts`` -- containers along the path are
    COPIED (the input is never mutated); a missing or scalar intermediate
    becomes a dict, as the modifier's create does."""
    if not parts:
        return value
    head, rest = parts[0], parts[1:]
    if isinstance(doc, list):
        out = list(doc)
        i = int(head)
        while len(out) <= i:
            out.append(None)
        out[i] = _set(out[i], rest, value)
        return out
    out = dict(doc) if isinstance(doc, dict) else {}
    out[head] = _set(out.get(head, {}) if rest else None, rest, value)
    return out


def _drop(doc: Any, parts: list[str]) -> Any:
    """``doc`` without the key at ``parts`` (copy along the path); a path that
    is not there leaves ``doc`` as it is."""
    if not parts:
        return doc
    head, rest = parts[0], parts[1:]
    if isinstance(doc, dict):
        if head not in doc:
            return doc
        out = dict(doc)
        if rest:
            out[head] = _drop(doc[head], rest)
        else:
            del out[head]
        return out
    if isinstance(doc, list):
        try:
            i = int(head)
        except ValueError:
            return doc
        if not 0 <= i < len(doc):
            return doc
        out = list(doc)
        if rest:
            out[i] = _drop(doc[i], rest)
        else:
            del out[i]
        return out
    return doc


def apply_entries(doc: Any, entries: Iterable[dict]) -> Any:
    """Replay *entries* forward onto *doc* (oldest first), as SM made them.
    The input document is never mutated."""
    for e in entries:
        parts = _parts(e["path"])
        if e.get("deleted"):
            doc = _drop(doc, parts)
        else:
            doc = _set(doc, parts, copy.deepcopy(e.get("new")))
    return doc


def revert_entries(doc: Any, entries: Iterable[dict]) -> Any:
    """Undo *entries* on *doc* (newest first): what the document held before
    them. The input document is never mutated."""
    for e in reversed(list(entries)):
        parts = _parts(e["path"])
        if e.get("created"):
            doc = _drop(doc, parts)
        else:
            doc = _set(doc, parts, copy.deepcopy(e.get("old")))
    return doc


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
    out: list[tuple[str, ...]] = []
    for r in sorted(roots, key=len):
        if not any(r[:len(k)] == k for k in out):
            out.append(r)
    return out


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
    if () in roots:
        out = copy.deepcopy(doc)
    else:
        out = {}
        for root in roots:
            node = doc
            for part in root:
                node = _child(node, part)
                if node is _ABSENT:
                    break
            if node is _ABSENT:
                out = _set(out, list(root[:-1]), _child_or_dict(out, root[:-1]))
            else:
                out = _set(out, list(root), copy.deepcopy(node))
    if pending:
        out = revert_entries(out, pending)
    return out


def rows_for(entries: list[dict], post_doc: dict) -> list[rules.Change]:
    """The S2 rows of an SM write: *post_doc* is the merged document SM wrote,
    *entries* what it changed to get there. Only the entries' roots are
    copied and compared, so the cost follows the edit, not the chip.

    Equal to ``rules.diff(flatten(revert_entries(post, entries)), flatten(post))``
    -- pinned by a randomized cross-check (tests/test_hub_record.py)."""
    if not entries:
        return []
    roots = _minimal({_root_of(_parts(e["path"]), post_doc) for e in entries})
    if () in roots:
        before = revert_entries(post_doc, entries)
        return rules.diff(rules.flatten(before), rules.flatten(post_doc))
    after_sparse: dict = {}
    for root in roots:
        node = post_doc
        for part in root:
            node = _child(node, part)
            if node is _ABSENT:
                break
        if node is _ABSENT:
            # absent in the written document: keep the dict ancestors so the
            # revert below can put the old subtree back under them
            after_sparse = _set(after_sparse, list(root[:-1]), _child_or_dict(after_sparse, root[:-1]))
            continue
        after_sparse = _set(after_sparse, list(root), copy.deepcopy(node))
    before_sparse = revert_entries(after_sparse, entries)
    return rules.diff(rules.flatten(before_sparse), rules.flatten(after_sparse))


def _child_or_dict(doc: Any, parts: tuple[str, ...]) -> Any:
    node = doc
    for part in parts:
        node = _child(node, part)
        if node is _ABSENT:
            return {}
    return node if isinstance(node, (dict, list)) else {}
