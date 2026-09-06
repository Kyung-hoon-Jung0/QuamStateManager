"""Approvals (docs/173 S5): a write the agent proposed that a HUMAN must
accept before it reaches the chip.

Not a flag on tray groups -- the working copy is applied WHOLE, so a held
entry inside the tray could never be skipped by the one apply door. Instead
a held write is parked here, outside the tray, as one record per run: the
card (S6) shows it, the pill counts it (``waiting``), approve stages it
through the normal doors as the human's own press, reject drops it.

One file per chip: ``instance/agent_approvals/<chip>.json``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from quam_state_manager.core import journal as journal_mod

KINDS = ("writes", "run")
STATUSES = ("pending", "approved", "rejected", "expired")


def path_for(instance_path, chip: str) -> Path:
    return Path(instance_path) / "agent_approvals" / (journal_mod._safe_key(chip) + ".json")


def load(instance_path, chip: str) -> list[dict]:
    try:
        d = json.loads(path_for(instance_path, chip).read_text(encoding="utf-8"))
        return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def _save(instance_path, chip: str, rows: list[dict]) -> None:
    p = path_for(instance_path, chip)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows[-400:], default=str), encoding="utf-8")
    os.replace(tmp, p)


def pending(instance_path, chip: str) -> list[dict]:
    return [r for r in load(instance_path, chip) if r.get("status") == "pending"]


def add(instance_path, chip: str, *, kind: str, node: str | None, targets: list | None, writes: list[dict] | None,
        reason: str | None, why_held: str, actor: str, plan_id: str | None = None, run_key: str | None = None,
        run_id: int | None = None, params: dict | None = None) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    rec = {"id": "ap-" + uuid.uuid4().hex[:10], "kind": kind, "status": "pending", "chip": chip,
           "node": node, "targets": list(targets or []), "params": dict(params or {}),
           "writes": [dict(w) for w in (writes or [])], "reason": reason, "why_held": why_held,
           "actor": actor, "plan_id": plan_id, "run_key": run_key, "run_id": run_id,
           "created": time.time(), "decided_by": None, "decided_at": None, "note": None}
    rows = load(instance_path, chip)
    rows.append(rec)
    _save(instance_path, chip, rows)
    return rec


def get(instance_path, chip: str, approval_id: str) -> dict | None:
    return next((r for r in load(instance_path, chip) if r.get("id") == approval_id), None)


def decide(instance_path, chip: str, approval_id: str, *, status: str, who: str, note: str | None = None,
           writes: list[dict] | None = None) -> dict | None:
    """approve / reject. ``writes`` lets the human EDIT the rows before
    approving (docs/173 §3.3 "행 편집 가능")."""
    if status not in ("approved", "rejected"):
        raise ValueError("status must be approved or rejected")
    rows = load(instance_path, chip)
    rec = next((r for r in rows if r.get("id") == approval_id), None)
    if rec is None or rec.get("status") != "pending":
        return None
    rec["status"] = status
    rec["decided_by"] = who
    rec["decided_at"] = time.time()
    rec["note"] = (note or "").strip() or None
    if writes is not None:
        rec["writes_original"] = rec.get("writes")
        rec["writes"] = [dict(w) for w in writes]
    _save(instance_path, chip, rows)
    return rec


def mark_used(instance_path, chip: str, approval_id: str, run_key: str) -> dict | None:
    """review R1-M2: an approved RUN request is consumed by the run it allowed."""
    rows = load(instance_path, chip)
    rec = next((r for r in rows if r.get("id") == approval_id), None)
    if rec is None:
        return None
    rec["used_by_run"] = run_key
    rec["used_at"] = time.time()
    _save(instance_path, chip, rows)
    return rec


def find_pending_run(instance_path, chip: str, *, node: str, targets: list, params: dict | None) -> dict | None:
    """review R1-M2: the same ask twice is one request."""
    want = (node, list(targets or []), dict(params or {}))
    for r in pending(instance_path, chip):
        if r.get("kind") == "run" and (r.get("node"), list(r.get("targets") or []), dict(r.get("params") or {})) == want:
            return r
    return None


def summary(rec: dict) -> dict:
    return {"id": rec.get("id"), "kind": rec.get("kind"), "node": rec.get("node"), "targets": rec.get("targets"),
            "n_writes": len(rec.get("writes") or []), "why_held": rec.get("why_held"), "reason": rec.get("reason"),
            "actor": rec.get("actor"), "created": rec.get("created"), "run_id": rec.get("run_id"),
            "plan_id": rec.get("plan_id"), "status": rec.get("status"), "used_by_run": rec.get("used_by_run")}
