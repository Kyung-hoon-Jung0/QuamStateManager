"""Serialise a chip document without re-serialising what did not change.

w7/livewrite. A live write of a 19 MB chip used to spend most of its time in
``json`` -- an indented ``dumps`` (0.74 s: the stdlib has no C encoder for
``indent``), a canonical ``dumps(sort_keys=True)`` per content hash (0.23 s,
several per apply) -- re-producing text for the 29 qubits nobody touched.

This module produces the SAME strings, byte for byte, by composing them from
per-subtree pieces cached in RAM:

* :func:`dumps_indent` ``== json.dumps(doc, indent=i, ensure_ascii=False)``
* :func:`canonical` ``== json.dumps(doc, sort_keys=True, separators=(",", ":"))``

A piece is the text of one top-level value, or of one second-level value under
a top-level dict (``qubits.q7``). Its cache key is the piece's CONTENT: a SHA-1
of ``marshal.dumps(value, 2)``, which pins every key (and key order), every
leaf's exact type (``1`` vs ``1.0`` vs ``True``), every float's bits and every
string. So a hit can only ever return the text of an identical value -- the
cache is validated on read by construction, and nothing (no event, no
mutation counter) has to tell it that a value changed. Format 2 is used
because later formats emit back-references that depend on reference counts,
which would make two equal documents hash differently.

Why a piece at nesting level ``L`` can be reused: the stdlib encoder writes a
nested value exactly as it writes that value alone, with ``indent * L``
inserted after every newline (strings never contain a raw newline -- they are
escaped). The composition below mirrors ``json.encoder._make_iterencode``'s
dict layout; it is pinned against ``json.dumps`` on random and real chips.

Anything the composition does not model -- a non-``str`` key at a composed
level, a value ``marshal`` refuses (a subclass, a custom object) -- falls
back to the plain ``json.dumps`` of the whole document, so the output is
always the stdlib's.
"""
from __future__ import annotations

import hashlib
import json
import marshal
from typing import Any

from quam_state_manager.core import ramcache

__all__ = ["dumps_indent", "canonical", "content_hash_pair", "history_hash_pair", "PIECES"]

_MiB = 1024 * 1024
# Content-addressed: the slot IS the content digest (plus the rendering), the
# token is constant. Two documents that share 29 of 30 qubits share 29 entries.
PIECES = ramcache.KeyedMemo("json.pieces", max_bytes=160 * _MiB, max_entries=20000)
# Whole documents: the doc's own digest -> its full text (a hit skips even the
# join). Bounded small: a few documents (live, working, a snapshot) at a time.
DOCS = ramcache.KeyedMemo("json.docs", max_bytes=128 * _MiB, max_entries=12)

_MARSHAL_V = 2
# Dict levels composed above the cached pieces: a chip file is pieced per
# qubit CHILD (qubits -> q7 -> xy), the drift-baseline record one level
# deeper in (record -> state -> qubits -> q7).
_DEPTH = 2


class _Unmodelled(Exception):
    pass


def _digest(value: Any) -> bytes:
    try:
        return hashlib.sha1(marshal.dumps(value, _MARSHAL_V)).digest()
    except (ValueError, TypeError, RecursionError) as exc:   # not plain JSON data
        raise _Unmodelled(str(exc)) from exc


def mdigest(value: Any) -> bytes | None:
    """SHA-1 of ``marshal.dumps(value, 2)`` -- a strict content fingerprint
    (types, key order, float bits); None for a value marshal refuses."""
    try:
        return _digest(value)
    except _Unmodelled:
        return None


def _indent_str(indent) -> str:
    return " " * indent if isinstance(indent, int) else str(indent)


def _plain_indent(value: Any, indent) -> str:
    return json.dumps(value, indent=indent, ensure_ascii=False)


def _indented_piece(value: Any, indent, level: int) -> str:
    ind = _indent_str(indent)

    def compute():
        text = _plain_indent(value, indent)
        return text.replace("\n", "\n" + ind * level) if level else text
    return PIECES.get(("ind", indent, level, _digest(value)), None, compute)


def _enc_key_indent(k) -> str:
    if type(k) is not str:
        raise _Unmodelled("non-str key")
    return json.dumps(k, ensure_ascii=False)


def _compose_dict_indent(d: dict, indent, level: int, depth_left: int) -> str:
    """``d`` rendered at nesting ``level`` exactly as the stdlib does."""
    if not d:
        return "{}"
    ind = _indent_str(indent)
    inner = "\n" + ind * (level + 1)
    parts = []
    for k, v in d.items():
        ek = _enc_key_indent(k)
        if depth_left > 0 and type(v) is dict and v:
            vt = _compose_dict_indent(v, indent, level + 1, depth_left - 1)
        else:
            vt = _indented_piece(v, indent, level + 1)
        parts.append(ek + ": " + vt)
    return "{" + inner + ("," + inner).join(parts) + "\n" + ind * level + "}"


def dumps_indent(doc: Any, indent=4) -> str:
    """``json.dumps(doc, indent=indent, ensure_ascii=False)``, from cached pieces."""
    if type(doc) is not dict or indent is None or (isinstance(indent, int) and indent < 0):
        return _plain_indent(doc, indent)
    try:
        dig = _digest(doc)
        return DOCS.get(("ind", indent, dig), None,
                        lambda: _compose_dict_indent(doc, indent, 0, _DEPTH))
    except _Unmodelled:
        return _plain_indent(doc, indent)


# ---------------------------------------------------------------- canonical

def _plain_canon(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _canon_piece(value: Any) -> str:
    return PIECES.get(("can", _digest(value)), None, lambda: _plain_canon(value))


def _compose_dict_canon(d: dict, depth_left: int) -> str:
    if not d:
        return "{}"
    for k in d:
        if type(k) is not str:
            raise _Unmodelled("non-str key")
    parts = []
    for k in sorted(d):
        v = d[k]
        if depth_left > 0 and type(v) is dict and v:
            vt = _compose_dict_canon(v, depth_left - 1)
        else:
            vt = _canon_piece(v)
        parts.append(json.dumps(k) + ":" + vt)
    return "{" + ",".join(parts) + "}"


def canonical(doc: Any) -> str:
    """``json.dumps(doc, sort_keys=True, separators=(",", ":"))``, from pieces."""
    if type(doc) is not dict:
        return _plain_canon(doc)
    try:
        dig = _digest(doc)
        return DOCS.get(("can", dig), None, lambda: _compose_dict_canon(doc, _DEPTH))
    except _Unmodelled:
        return _plain_canon(doc)


def content_hash_pair(state: dict, wiring: dict) -> str:
    """``working_copy.content_hash``: sha256 of ``json.dumps([state, wiring], sort_keys=True,
    separators=(",", ":"))`` -- a list of two renders as ``[s,w]``."""
    h = hashlib.sha256()
    h.update(b"[")
    h.update(canonical(state).encode("utf-8"))
    h.update(b",")
    h.update(canonical(wiring).encode("utf-8"))
    h.update(b"]")
    return h.hexdigest()


def history_hash_pair(state: dict, wiring: dict) -> str:
    """``history._canonical_hash_of``'s formula, from the same pieces."""
    h = hashlib.sha256()
    h.update(b"STATE:")
    h.update(canonical(state).encode("utf-8"))
    h.update(b"\nWIRING:")
    h.update(canonical(wiring).encode("utf-8"))
    return h.hexdigest()


# ------------------------------------------------------- json_diff leaf count

def _plain_leafcount(node: Any) -> int:
    """``len(json_diff.flatten(node)[0])`` for a non-root node, uncapped:
    a non-empty dict/list is its children, anything else (an empty container
    included) is one leaf."""
    n = 0
    stack = [node]
    while stack:
        x = stack.pop()
        if type(x) is dict and x:
            stack.extend(x.values())
        elif type(x) is list and x:
            stack.extend(x)
        elif isinstance(x, dict) and x:
            stack.extend(x.values())
        elif isinstance(x, list) and x:
            stack.extend(x)
        else:
            n += 1
    return n


def _count_node(node: Any, depth_left: int) -> int:
    if isinstance(node, dict) and node:
        if depth_left > 0 and type(node) is dict:
            return sum(_count_node(v, depth_left - 1) for v in node.values())
        return PIECES.get(("jdn", _digest(node)), None, lambda: _plain_leafcount(node))
    if isinstance(node, list) and node:
        return PIECES.get(("jdn", _digest(node)), None, lambda: _plain_leafcount(node))
    return 1


def json_diff_leafcount(doc: Any) -> int:
    """How many leaves ``json_diff.flatten(doc)`` walks (uncapped) -- the
    number its ``cap`` is compared against -- from per-subtree counts cached
    by content. An empty document has none."""
    if not isinstance(doc, (dict, list)):
        return 1
    if not doc:
        return 0
    try:
        return _count_node(doc, _DEPTH + 1)
    except _Unmodelled:
        return _plain_leafcount(doc)
