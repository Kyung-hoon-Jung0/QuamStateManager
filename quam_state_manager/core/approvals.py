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

from quam_state_manager.core import run_terms, safe_io
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
    # safe_io's temp file is THIS writer's alone. A fixed `<file>.tmp` is
    # shared by two concurrent writers: their bytes interleave and the
    # mixture is replaced into place, which the reader then swallows as an
    # empty store (agent_plans._save has the measurement).
    safe_io.atomic_write_json(p, json.loads(json.dumps(rows[-400:], default=str)),
                              compact=True)


def pending(instance_path, chip: str) -> list[dict]:
    return [r for r in load(instance_path, chip) if r.get("status") == "pending"]


def _new(*, kind, chip, node, targets, writes, reason, why_held, actor, plan_id, run_key, run_id, params,
         step) -> dict:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    return {"id": "ap-" + uuid.uuid4().hex[:10], "kind": kind, "status": "pending", "chip": chip,
            "node": node, "targets": list(targets or []), "params": dict(params or {}),
            "writes": [dict(w) for w in (writes or [])], "reason": reason, "why_held": why_held,
            "actor": actor, "plan_id": plan_id, "step": step, "run_key": run_key, "run_id": run_id,
            "created": time.time(), "decided_by": None, "decided_at": None, "note": None}


def add(instance_path, chip: str, *, kind: str, node: str | None, targets: list | None, writes: list[dict] | None,
        reason: str | None, why_held: str, actor: str, plan_id: str | None = None, run_key: str | None = None,
        run_id: int | None = None, params: dict | None = None, step: int | None = None) -> dict:
    rec = _new(kind=kind, chip=chip, node=node, targets=targets, writes=writes, reason=reason, why_held=why_held,
               actor=actor, plan_id=plan_id, run_key=run_key, run_id=run_id, params=params, step=step)
    # The lock spans read -> change -> write: an atomic write stops a CORRUPT
    # file, not two cycles erasing one another. This store gates a write to
    # the chip, so a lost decision is not a cosmetic loss.
    with safe_io.path_lock(path_for(instance_path, chip)):
        rows = load(instance_path, chip)
        rows.append(rec)
        _save(instance_path, chip, rows)
    return rec


def get(instance_path, chip: str, approval_id: str) -> dict | None:
    return next((r for r in load(instance_path, chip) if r.get("id") == approval_id), None)


def decide(instance_path, chip: str, approval_id: str, *, status: str, who: str, note: str | None = None,
           writes: list[dict] | None = None) -> dict | None:
    """approve / reject. ``writes`` lets the human EDIT the rows before
    approving (docs/173 §3.3 "rows editable")."""
    if status not in ("approved", "rejected"):
        raise ValueError("status must be approved or rejected")
    # The lock spans read -> change -> write: an atomic write stops a CORRUPT
    # file, not two cycles erasing one another. This store gates a write to
    # the chip, so a lost decision is not a cosmetic loss.
    with safe_io.path_lock(path_for(instance_path, chip)):
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


def expire_plan_runs(instance_path, chip: str, plan_id: str | None, why: str) -> list[str]:
    """docs/254 x docs/253: a run request belongs to a step of an ARMED plan. When that
    plan's arming ends (it finished, failed, was cancelled or stopped, SM restarted ...),
    nothing can spend its requests any more -- a card still offering "Allow run" would
    promise a run that cannot happen. Pending and allowed-but-unspent run requests of
    the plan expire, with the reason on the record. Writes approvals stay: those values
    exist and remain the person's to apply."""
    if not plan_id:
        return []
    out: list[str] = []
    with safe_io.path_lock(path_for(instance_path, chip)):
        rows = load(instance_path, chip)
        for r in rows:
            if r.get("kind") == "run" and r.get("plan_id") == plan_id and (
                    r.get("status") == "pending" or (r.get("status") == "approved" and not r.get("used_by_run"))):
                r["status"] = "expired"
                r["expired_at"] = time.time()
                r["note"] = f"expired: its plan's arming ended ({why})"[:300]
                out.append(r.get("id"))
        if out:
            _save(instance_path, chip, rows)
    return out


def mark_used(instance_path, chip: str, approval_id: str, run_key: str) -> dict | None:
    """review R1-M2: an approved RUN request is consumed by the run it allowed."""
    # The lock spans read -> change -> write: an atomic write stops a CORRUPT
    # file, not two cycles erasing one another. This store gates a write to
    # the chip, so a lost decision is not a cosmetic loss.
    with safe_io.path_lock(path_for(instance_path, chip)):
        rows = load(instance_path, chip)
        rec = next((r for r in rows if r.get("id") == approval_id), None)
        if rec is None:
            return None
        rec["used_by_run"] = run_key
        rec["used_at"] = time.time()
        _save(instance_path, chip, rows)
    return rec


def find_pending_run(instance_path, chip: str, *, node: str, targets: list, params: dict | None,
                     plan_id: str | None = None, step: int | None = None) -> dict | None:
    """review R1-M2: the same ask twice is one request. "The same" is
    ``run_terms.key`` -- the one reading the approval check uses (docs/254):
    ``{"a": True}`` and ``{"a": 1}`` were one request under ``==`` -- plus the
    plan step it was asked for: two steps with the same terms are two runs, so
    two requests (docs/254, reconciled with docs/253)."""
    return _find_pending_run(pending(instance_path, chip), node=node, targets=targets, params=params,
                             plan_id=plan_id, step=step)


def _find_pending_run(rows, *, node, targets, params, plan_id, step=None) -> dict | None:
    want = run_terms.key(node, targets, params, plan_id)
    for r in rows:
        if r.get("status") == "pending" and r.get("kind") == "run" and r.get("step") == step and run_terms.key(
                r.get("node"), r.get("targets"), r.get("params"), r.get("plan_id")) == want:
            return r
    return None


def file_run_request(instance_path, chip: str, *, node: str, targets: list, params: dict | None, reason: str | None,
                     actor: str, plan_id: str | None = None, step: int | None = None,
                     why_held: str = "mode ask-all") -> tuple[dict, bool]:
    """An ask-all run request: the pending one for exactly this run, or a new
    one. ``(record, created)``. The lookup and the append are one step under
    the file lock, so two asks at once file one request."""
    with safe_io.path_lock(path_for(instance_path, chip)):
        rows = load(instance_path, chip)
        hit = _find_pending_run(rows, node=node, targets=targets, params=params, plan_id=plan_id, step=step)
        if hit is not None:
            return hit, False
        rec = _new(kind="run", chip=chip, node=node, targets=targets, writes=None, reason=reason,
                   why_held=why_held, actor=actor, plan_id=plan_id, run_key=None, run_id=None, params=params,
                   step=step)
        rows.append(rec)
        _save(instance_path, chip, rows)
    return rec, True


def run_differences(ap: dict, *, node: str, targets: list, params: dict | None,
                    plan_id: str | None = None, step: int | None = None) -> list[dict]:
    """D-05 (docs/254): what separates the run this approval allowed from the
    run asked for now. Empty = the approval covers it.

    A request filed for a plan STEP is spent by that step's run only
    (reconciled with docs/253: the card shows "plan P · step i", and a plan may
    hold two steps with the same terms)."""
    out = run_terms.differences(
        {"node": ap.get("node"), "targets": ap.get("targets"), "params": ap.get("params"),
         "plan_id": ap.get("plan_id")},
        {"node": node, "targets": targets, "params": params, "plan_id": plan_id})
    if ap.get("step") is not None and step != ap.get("step"):
        out.append({"field": "step", "allowed": ap.get("step"), "asked": step})
    return out


def summary(rec: dict) -> dict:
    return {"id": rec.get("id"), "kind": rec.get("kind"), "node": rec.get("node"), "targets": rec.get("targets"),
            "params": rec.get("params") or {},
            "n_writes": len(rec.get("writes") or []), "why_held": rec.get("why_held"), "reason": rec.get("reason"),
            "actor": rec.get("actor"), "created": rec.get("created"), "run_id": rec.get("run_id"),
            "plan_id": rec.get("plan_id"), "step": rec.get("step"), "status": rec.get("status"),
            "decided_by": rec.get("decided_by"), "used_by_run": rec.get("used_by_run")}
