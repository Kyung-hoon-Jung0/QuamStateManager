"""The state.json KEY MANUAL (2026-08-27): what keys a node can carry, what
values they take, and what they mean — assembled from two sources and
labelled by source, never blended:

* the env's own classes (the docs/56 schema manifest, now carrying the
  per-field descriptions their docstrings wrote — ``probe_state_schema``);
* the official QM docs for the config-level keys the port/channel classes
  do not describe (``key_manual_docs``, each entry naming its page).

A key neither source describes is listed with its type and default only —
"no description" is stated, never filled in.
"""
from __future__ import annotations

import re
from typing import Any

from . import key_manual_docs

_SKIP_KEYS = {"__class__"}
_MAX_EXAMPLES = 3


def _leaf(cls_path: str | None) -> str:
    return (cls_path or "").rsplit(".", 1)[-1]


def _class_occurrences(state: Any, wiring: Any = None, *, cap: int = 200_000) -> dict[str, list[str]]:
    """``{class path: [dot paths of nodes declaring it]}`` — every node in
    the state that carries ``__class__`` (bounded walk)."""
    out: dict[str, list[str]] = {}
    docs = [("", state)] + ([("wiring", wiring)] if isinstance(wiring, dict) else [])
    stack = list(reversed(docs))
    seen = 0
    while stack:
        prefix, node = stack.pop()
        seen += 1
        if seen > cap:
            break
        if isinstance(node, dict):
            cls = node.get("__class__")
            if isinstance(cls, str) and prefix:
                out.setdefault(cls, []).append(prefix)
            for k, v in node.items():
                if k == "__class__" or not isinstance(v, (dict, list)):
                    continue
                stack.append((f"{prefix}.{k}" if prefix else str(k), v))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if isinstance(v, (dict, list)):
                    stack.append((f"{prefix}.{i}", v))
    return out


_CLASS_REPR_RE = re.compile(r"<class '([\w.]+)'>")
_MODULE_PATH_RE = re.compile(r"\b(?:[a-z_][\w]*\.)+([A-Za-z_]\w*)")


def _type_label(rec: dict) -> str:
    """A readable type: the annotation as written when it was a string,
    else the repr cleaned of ``<class '…'>`` and module paths — so
    ``typing.Optional[typing.Literal['auto', 'always_on']]`` reads
    ``Optional[Literal['auto', 'always_on']]`` and ``quam.….pulses.Pulse``
    reads ``Pulse``."""
    raw = rec.get("raw")
    if isinstance(raw, str) and raw:
        raw = _CLASS_REPR_RE.sub(lambda m: m.group(1).rsplit(".", 1)[-1], raw)
        raw = raw.replace("typing.", "")
        raw = _MODULE_PATH_RE.sub(r"\1", raw)
        return raw
    ts = rec.get("type") or {}
    base = ts.get("base") or "any"
    return f"Optional[{base}]" if ts.get("optional") else base


def _choices(rec: dict) -> list | None:
    ts = rec.get("type") or {}
    enum = ts.get("enum")
    return list(enum) if isinstance(enum, list) and enum else None


def _docs_block(class_leaf: str | None, key: str) -> dict | None:
    hits = key_manual_docs.entries_for(class_leaf, key)
    if not hits:
        return None
    e = hits[0]
    return {"summary": e["summary"], "allowed": e.get("allowed"),
            "default": e.get("default"), "unit": e.get("unit"),
            "docs": e["docs"], "quote": e.get("quote"), "since": e.get("since"),
            "applies": e["applies"], "ambiguous": len(hits) > 1 and not class_leaf}


def _field_entry(cls_path: str, leaf: str, name: str, rec: dict,
                 examples: list[str], present_in: int) -> dict:
    docs = _docs_block(leaf, name)
    return {
        "id": f"{leaf}.{name}",
        "key": name,
        "cls": leaf,
        "cls_path": cls_path,
        "type": _type_label(rec),
        "required": not rec.get("has_default", False),
        "default": rec.get("default"),
        "default_repr": rec.get("default_repr"),
        "choices": _choices(rec),
        "doc": rec.get("doc"),                       # the class's own words, or None
        "docs": docs,                                # the QM docs entry, or None
        "source": ("class docstring" if rec.get("doc") else
                   "QM docs" if docs else None),
        "examples": examples[:_MAX_EXAMPLES],
        "present_in": present_in,
    }


def manual_entries(state: Any, wiring: Any, manifest: dict | None) -> dict:
    """Everything the manual can say about this chip.

    Returns ``{"entries": [...], "classes": [...], "env": bool, "note": str|None}``.
    ``entries`` is one row per (class, field) for every class the manifest
    knows AND the chip uses, plus the QM-docs keys with no class behind them
    (so `band` is findable even before an env is selected)."""
    occ = _class_occurrences(state, wiring)
    classes = (manifest or {}).get("classes") or {}
    entries: list[dict] = []
    class_rows: list[dict] = []
    covered: set[tuple[str, str]] = set()
    for cls_path, paths in sorted(occ.items()):
        paths = sorted(paths)                      # deterministic examples
        entry = classes.get(cls_path)
        leaf = _leaf(cls_path)
        if not isinstance(entry, dict) or not entry.get("importable") or not isinstance(entry.get("fields"), dict):
            class_rows.append({"cls": leaf, "cls_path": cls_path, "doc": None,
                               "fields": 0, "count": len(paths), "known": False})
            continue
        fields = entry["fields"]
        class_rows.append({"cls": leaf, "cls_path": cls_path, "doc": entry.get("doc"),
                           "fields": len(fields), "count": len(paths), "known": True})
        for name, rec in fields.items():
            if name in _SKIP_KEYS or not isinstance(rec, dict):
                continue
            present = 0
            examples: list[str] = []
            for p in paths:
                node = _node_at(state, wiring, p)
                if isinstance(node, dict) and name in node:
                    present += 1
                    if len(examples) < _MAX_EXAMPLES:
                        examples.append(f"{p}.{name}")
            entries.append(_field_entry(cls_path, leaf, name, rec, examples, present))
            covered.add((leaf, name))
    # docs-only keys (no class field behind them on this chip)
    for e in key_manual_docs.DOC_ENTRIES:
        if any((a, e["key"]) in covered for a in e["applies"]):
            continue
        entries.append({
            "id": f"{e['applies'][0]}.{e['key']}", "key": e["key"], "cls": e["applies"][0],
            "cls_path": None, "type": None, "required": False, "default": e.get("default"),
            "default_repr": None, "choices": None, "doc": None,
            "docs": {"summary": e["summary"], "allowed": e.get("allowed"), "default": e.get("default"),
                     "unit": e.get("unit"), "docs": e["docs"], "quote": e.get("quote"),
                     "since": e.get("since"), "applies": e["applies"], "ambiguous": False},
            "source": "QM docs", "examples": [], "present_in": 0,
        })
    note = None
    if not classes:
        note = ("Class documentation loads once a Python environment is selected "
                "(Generate Config → environment); the QM docs entries are shown now.")
    return {"entries": entries, "classes": class_rows, "env": bool(classes), "note": note}


def _node_at(state: Any, wiring: Any, dot_path: str) -> Any:
    if not dot_path:
        return state
    segs = dot_path.split(".")
    cur: Any = wiring if segs[0] == "wiring" and isinstance(wiring, dict) else state
    if segs[0] == "wiring" and isinstance(wiring, dict):
        segs = segs[1:]
    for s in segs:
        if isinstance(cur, dict):
            if s not in cur:
                return None
            cur = cur[s]
        elif isinstance(cur, list) and s.isdigit() and int(s) < len(cur):
            cur = cur[int(s)]
        else:
            return None
    return cur


def node_keys(state: Any, wiring: Any, manifest: dict | None, dot_path: str) -> dict:
    """What THIS place can carry: for a node with a class, every field of
    that class with a `present` flag (the unset ones are the "keys you could
    add"); for a leaf, the same view of its parent with the leaf focused."""
    node = _node_at(state, wiring, dot_path)
    focus: str | None = None
    owner_path = dot_path
    if not (isinstance(node, dict) and isinstance(node.get("__class__"), str)):
        if "." in dot_path:
            owner_path, focus = dot_path.rsplit(".", 1)
            node = _node_at(state, wiring, owner_path)
        else:
            node = None
    if not isinstance(node, dict):
        return {"ok": False, "path": dot_path, "reason": "no node at this path"}
    cls_path = node.get("__class__") if isinstance(node.get("__class__"), str) else None
    leaf = _leaf(cls_path) if cls_path else None
    classes = (manifest or {}).get("classes") or {}
    entry = classes.get(cls_path) if cls_path else None
    fields: list[dict] = []
    known = isinstance(entry, dict) and entry.get("importable") and isinstance(entry.get("fields"), dict)
    if known:
        for name, rec in entry["fields"].items():
            if name in _SKIP_KEYS or not isinstance(rec, dict):
                continue
            fe = _field_entry(cls_path, leaf, name, rec, [f"{owner_path}.{name}"], int(name in node))
            fe["present"] = name in node
            fe["focus"] = name == focus
            fields.append(fe)
    # keys the node carries that the class does not declare (lab extras) —
    # shown so the picture is complete, marked as undeclared
    declared = {f["key"] for f in fields}
    for k in node.keys():
        if k in _SKIP_KEYS or k in declared:
            continue
        docs = _docs_block(leaf, k)
        fields.append({"id": f"{leaf or 'node'}.{k}", "key": k, "cls": leaf, "cls_path": cls_path,
                       "type": None, "required": False, "default": None, "default_repr": None,
                       "choices": None, "doc": None, "docs": docs,
                       "source": "QM docs" if docs else None, "examples": [f"{owner_path}.{k}"],
                       "present_in": 1, "present": True, "focus": k == focus, "undeclared": True})
    return {"ok": True, "path": dot_path, "owner": owner_path, "focus": focus,
            "cls": leaf, "cls_path": cls_path, "cls_doc": (entry or {}).get("doc") if known else None,
            "known": bool(known), "fields": fields,
            "unset": [f["key"] for f in fields if not f.get("present")],
            "note": None if known or not cls_path else
                    ("This class is not in the selected environment's schema — "
                     "select a Python environment (Generate Config) to load its fields.")}
