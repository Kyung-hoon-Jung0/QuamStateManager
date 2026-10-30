"""Agent proposals for Generate-Config additions: a holding area, never the spec.

An agent proposes additions (or a knowledge-card overlay). Each proposal is
linted by :func:`additions.lint_proposal` exactly like a wizard addition;
a refused one is answered at once with its reasons and never stored, an
accepted one waits here as PENDING until a person presses Accept in the
wizard -- only then does it join ``spec.additions`` (in the browser). Nothing
here ever writes a chip.

The wizard's draft spec is recorded by the wizard itself (``set_context``)
so a proposal is judged against the chip the person is actually building.

File: ``<instance>/generate_proposals.json`` ``{"context": {...},
"items": [{id, kind, author, reason, addition | card, status, lint, ts}]}``.
"""
from __future__ import annotations

import copy
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any

FILE = "generate_proposals.json"
_LOCK = threading.Lock()
_MAX_ITEMS = 200
STATUSES = ("pending", "accepted", "dismissed")


def _path(instance_path) -> Path:
    return Path(instance_path) / FILE


def _read(instance_path) -> dict:
    try:
        d = json.loads(_path(instance_path).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(instance_path, d: dict) -> None:
    from quam_state_manager.core import safe_io
    p = _path(instance_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    d["items"] = (d.get("items") or [])[-_MAX_ITEMS:]
    safe_io.atomic_write_json(p, d)


def set_context(instance_path, spec: dict, python_path: str | None) -> None:
    with _LOCK:
        d = _read(instance_path)
        d["context"] = {"spec": copy.deepcopy(spec), "python": python_path, "ts": time.time()}
        _write(instance_path, d)


def context(instance_path) -> dict:
    return (_read(instance_path).get("context") or {})


def spec_summary(spec: dict) -> dict:
    """What an agent needs to place additions: qubits, pairs (builder ids),
    lines, TWPAs, QDAC qubits, the pair gate, and the additions already
    made (with their names, so a proposal does not clash)."""
    from quam_state_manager.core import additions
    spec = spec or {}
    lines = [{"line": ln.get("line"), "element": ln.get("element")}
             for ln in spec.get("lines") or [] if isinstance(ln, dict)]
    return {"qubits": list(spec.get("qubits") or []),
            "qubit_pairs": [{"spec": p, "id": additions.quam_pair_id(p)}
                            for p in spec.get("qubit_pairs") or []],
            "pair_gate": spec.get("pair_gate"), "lines": lines,
            "twpas": spec.get("twpas") or [],
            "qdac_qubits": sorted(((spec.get("qdac") or {}).get("qubits") or {}).keys()),
            "quam_class": spec.get("quam_class"),
            "pair_gates": additions.pair_gate_table(spec),
            "additions": [{k: a.get(k) for k in ("kind", "target", "name", "class")}
                          for a in spec.get("additions") or [] if isinstance(a, dict)]}


def propose(instance_path, items: list, author: str, manifest: dict | None,
            spec: dict | None = None) -> list[dict]:
    """Lint every ``{"addition", "reason"}`` item; store the ones that pass as
    pending. Returns one result per item, in order: ``{"status":
    "pending"|"rejected", "id"?, "errors", "warnings", "needs_person", "card"}``."""
    from quam_state_manager.core import additions
    spec = spec if spec is not None else (context(instance_path).get("spec") or {})
    out = []
    with _LOCK:
        d = _read(instance_path)
        held = [i["addition"] for i in d.get("items") or []
                if i.get("status") == "pending" and isinstance(i.get("addition"), dict)]
        accepted_here = []
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                out.append({"status": "rejected", "errors": ["each item is {addition, reason}"]})
                continue
            add, reason = it.get("addition"), it.get("reason")
            if not isinstance(reason, str) or not reason.strip():
                out.append({"status": "rejected", "errors": ["a reason is required for every item"]})
                continue
            others = [a for a in spec.get("additions") or [] if isinstance(a, dict)] + held + accepted_here
            lint = additions.lint_proposal(spec, add, manifest, author=author, others=others)
            res = {k: lint[k] for k in ("errors", "warnings", "needs_person", "card")}
            if not lint["ok"]:
                out.append({"status": "rejected", **res})
                continue
            pid = uuid.uuid4().hex[:10]
            d.setdefault("items", []).append({
                "id": pid, "kind": "addition", "author": author, "reason": reason.strip()[:500],
                "addition": copy.deepcopy(add), "status": "pending", "lint": res, "ts": time.time()})
            accepted_here.append(add)
            out.append({"status": "pending", "id": pid, **res})
        _write(instance_path, d)
    return out


def propose_card(instance_path, card: Any, reason: str, author: str) -> dict:
    """An overlay card draft: linted now, written only on a person's Accept."""
    from quam_state_manager.core import addition_cards
    if not isinstance(reason, str) or not reason.strip():
        return {"status": "rejected", "errors": ["a reason is required"]}
    why = addition_cards.overlay_card_reason(card, instance_path)
    if why:
        return {"status": "rejected", "errors": [why]}
    with _LOCK:
        d = _read(instance_path)
        pid = uuid.uuid4().hex[:10]
        d.setdefault("items", []).append({
            "id": pid, "kind": "card", "author": author, "reason": reason.strip()[:500],
            "card": copy.deepcopy(card), "status": "pending", "ts": time.time()})
        _write(instance_path, d)
    return {"status": "pending", "id": pid}


def pending(instance_path) -> list[dict]:
    return [i for i in _read(instance_path).get("items") or [] if i.get("status") == "pending"]


def get(instance_path, pid: str) -> dict | None:
    return next((i for i in _read(instance_path).get("items") or [] if i.get("id") == pid), None)


def decide(instance_path, pid: str, status: str, who: str) -> dict | None:
    if status not in STATUSES:
        raise ValueError(status)
    with _LOCK:
        d = _read(instance_path)
        it = next((i for i in d.get("items") or [] if i.get("id") == pid), None)
        if it is None or it.get("status") != "pending":
            return None
        it["status"], it["decided_by"], it["decided_ts"] = status, who, time.time()
        _write(instance_path, d)
        return it


def accept(instance_path, pid: str, spec: dict, typed: dict, manifest: dict | None,
           who: str) -> dict:
    """A person's Accept: the proposal plus the fields they typed, linted as
    THEIR addition. ``{"ok", "addition"?, "errors"?}``; on ok the item is
    marked accepted (the browser then adds it to ``spec.additions``)."""
    from quam_state_manager.core import addition_cards, additions
    it = get(instance_path, pid)
    if it is None or it.get("status") != "pending":
        return {"ok": False, "errors": ["this proposal is no longer pending"]}
    if it.get("kind") == "card":
        why = addition_cards.add_overlay_card(it["card"], instance_path)
        if why:
            return {"ok": False, "errors": [why]}
        decide(instance_path, pid, "accepted", who)
        return {"ok": True, "card": it["card"]["id"]}
    add = copy.deepcopy(it["addition"])
    typed = typed if isinstance(typed, dict) else {}
    if add.get("kind") == "field" and "value" in typed:
        add["value"] = typed["value"]
    elif typed:
        add["fields"] = dict(add.get("fields") or {}, **typed)
    lint = additions.lint_proposal(spec, add, manifest, author="person", typed=set(typed))
    if not lint["ok"]:
        return {"ok": False, "errors": lint["errors"]}
    add["proposed_by"] = it.get("author")
    decide(instance_path, pid, "accepted", who)
    return {"ok": True, "addition": add, "warnings": lint["warnings"]}
