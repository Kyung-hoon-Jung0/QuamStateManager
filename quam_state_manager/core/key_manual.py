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
    # wiring.json's own top-level keys (`wiring`, `network`) ARE the paths the
    # explorer shows -- walk it from its root, never under a synthetic prefix
    docs = [("", state)] + ([("", wiring)] if isinstance(wiring, dict) else [])
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


_CATEGORY_ORDER = ["Roots", "Qubits", "Qubit pairs", "Channels", "Ports", "Pulses",
                   "Resonators", "Flux & couplers", "Gates & macros", "Octave", "Hardware",
                   "Lab (quam_config)", "Other components", "Config keys (QM docs)"]


def _category_of(cls_path: str, entry: dict | None) -> str:
    cat = (entry or {}).get("category")
    if isinstance(cat, str) and cat:
        return cat
    # a chip class the catalogue did not cover: place it by its module
    mod = cls_path.rsplit(".", 1)[0].lower() if "." in cls_path else ""
    name = _leaf(cls_path).lower()
    if mod.startswith("quam_config") or not (mod.startswith("quam.") or mod.startswith("quam_builder")):
        return "Lab (quam_config)"
    if "quam" in name and ("root" in mod or name.endswith("quam")):
        return "Roots"
    if "pair" in name:
        return "Qubit pairs"
    if "qubit" in name or "transmon" in name:
        return "Qubits"
    if name.endswith("port"):
        return "Ports"
    if name.endswith("pulse"):
        return "Pulses"
    if name.endswith("channel") or "drive" in mod or name.startswith("xy"):
        return "Channels"
    if "resonator" in name or "readout" in mod:
        return "Resonators"
    if "flux" in name or "flux" in mod or "coupler" in name:
        return "Flux & couplers"
    if "octave" in name or "octave" in mod:
        return "Octave"
    if "twpa" in name or "mixer" in name or "converter" in name or "hardware" in mod:
        return "Hardware"
    if "gate" in name or "macro" in name:
        return "Gates & macros"
    return "Other components"


def manual_entries(state: Any, wiring: Any, manifest: dict | None,
                   catalog: dict | None = None) -> dict:
    """Everything the manual can say.

    Returns ``{"entries", "classes", "categories", "env", "catalog", "note"}``.
    ``entries`` is one row per (class, field) for every class the CATALOGUE
    offers (docs/141 4h -- everything the env has, not only what the chip
    uses) merged with the chip's own classes from the manifest, plus the
    QM-docs keys with no class behind them (so `band` is findable even before
    an env is selected). A row that the chip actually sets carries
    ``examples`` (clickable places) and ``present_in``."""
    occ = _class_occurrences(state, wiring)
    classes = dict((manifest or {}).get("classes") or {})
    catalog = catalog if isinstance(catalog, dict) else {}
    # the catalogue is the wider set; the manifest wins for a class both know
    # (it was probed for THIS chip's inventory, same env)
    merged: dict[str, dict] = {}
    for cp, e in catalog.items():
        if isinstance(e, dict):
            merged[cp] = e
    for cp, e in classes.items():
        if isinstance(e, dict):
            m = dict(e)
            if "category" not in m and cp in merged:
                m["category"] = merged[cp].get("category")
            merged[cp] = m
    # every class the chip uses is listed even when nobody described it
    for cp in occ:
        merged.setdefault(cp, {"importable": False, "fields": None})
    entries: list[dict] = []
    class_rows: list[dict] = []
    covered: set[tuple[str, str]] = set()
    for cls_path in sorted(merged, key=lambda c: (_category_of(c, merged[c]), _leaf(c).lower())):
        entry = merged[cls_path]
        paths = sorted(occ.get(cls_path, []))      # deterministic examples
        leaf = _leaf(cls_path)
        category = _category_of(cls_path, entry)
        if not isinstance(entry, dict) or not entry.get("importable") or not isinstance(entry.get("fields"), dict):
            class_rows.append({"cls": leaf, "cls_path": cls_path, "doc": None, "category": category,
                               "fields": 0, "count": len(paths), "known": False, "used": bool(paths)})
            continue
        fields = entry["fields"]
        class_rows.append({"cls": leaf, "cls_path": cls_path, "doc": entry.get("doc"), "category": category,
                           "fields": len(fields), "count": len(paths), "known": True, "used": bool(paths),
                           "abstract": bool(entry.get("abstract"))})
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
            fe = _field_entry(cls_path, leaf, name, rec, examples, present)
            fe["category"] = category
            fe["used"] = bool(paths)
            entries.append(fe)
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
            "category": "Config keys (QM docs)", "used": False,
        })
    note = None
    if not classes and not catalog:
        note = ("Class documentation loads once a Python environment is selected "
                "(Generate Config → environment); the QM docs entries are shown now.")
    cats_present = {e["category"] for e in entries}
    categories = [c for c in _CATEGORY_ORDER if c in cats_present] + sorted(
        c for c in cats_present if c not in _CATEGORY_ORDER)
    return {"entries": entries, "classes": class_rows, "categories": categories,
            "env": bool(classes) or bool(catalog), "catalog": bool(catalog), "note": note}


def _node_at(state: Any, wiring: Any, dot_path: str) -> Any:
    if not dot_path:
        return state
    segs = dot_path.split(".")
    # state first; a path whose first segment is a wiring.json top-level key
    # (`wiring.qubits.q1.xy`, `network.host`) walks the wiring document verbatim
    cur: Any = state
    if isinstance(state, dict) and segs[0] not in state and isinstance(wiring, dict) and segs[0] in wiring:
        cur = wiring
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


def node_keys(state: Any, wiring: Any, manifest: dict | None, dot_path: str,
              catalog: dict | None = None) -> dict:
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
    if cls_path and not isinstance(entry, dict) and isinstance(catalog, dict):
        entry = catalog.get(cls_path)                 # the catalogue covers what the chip probe did not
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
