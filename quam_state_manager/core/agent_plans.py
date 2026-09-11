"""Plans (docs/173 S6): the card between a sentence and hardware.

A plan is DATA the agent proposes (``plan_propose``) or SM derives from a
deterministic ``/run`` line -- steps of (node, targets, params, why). Rule 0:
nothing starts until a person presses [Start] on the card; that press arms
the session, takes a snapshot the whole plan can be reverted to, and tells
the driving session to go. ``run_node`` reports back step by step
(``plan_id`` + ``step``) so the card's progress is SM's own record, never
the model's claim.

One file per chip: ``instance/agent_plans/<chip>.json`` (newest last).
"""

from __future__ import annotations

import math

import json
import os
import time
import uuid
from pathlib import Path

from quam_state_manager.core import journal as journal_mod

STATUSES = ("draft", "running", "stopping", "done", "failed", "stopped", "cancelled", "skipped")
STEP_STATUSES = ("pending", "running", "done", "failed", "skipped", "cancelled")
MAX_STEPS = 60


def path_for(instance_path, chip: str) -> Path:
    return Path(instance_path) / "agent_plans" / (journal_mod._safe_key(chip) + ".json")


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
    # allow_nan=False: a bare NaN / Infinity is not JSON, and one that reached
    # this file made every later card feed unparseable -- silently, and across
    # a restart, because the poison was on disk. Refusing here means a bad
    # value fails the write that produced it instead of the next twenty reads.
    tmp.write_text(json.dumps(rows[-200:], default=str, allow_nan=False),
                   encoding="utf-8")
    os.replace(tmp, p)


def normalize_steps(steps) -> list[dict]:
    """The agent's step list, checked: node + targets required, params an
    object, at most MAX_STEPS. Raises ValueError with the reason."""
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list of {node, targets, params?, why?}")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} steps in one plan")
    out = []
    for i, s in enumerate(steps):
        if not isinstance(s, dict):
            raise ValueError(f"step {i}: must be an object")
        node = str(s.get("node") or "").strip()
        if not node:
            raise ValueError(f"step {i}: node required")
        targets = s.get("targets") or []
        if isinstance(targets, str):
            targets = targets.replace(",", " ").split()
        targets = [str(t).strip() for t in targets if str(t).strip()]
        params = s.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError(f"step {i}: params must be an object")
        out.append({"i": i, "node": node, "targets": targets, "params": params,
                    "why": str(s.get("why") or "").strip() or None, "status": "pending",
                    "run_key": None, "run_id": None, "outcome": None, "classification": None,
                    "n_writes": 0, "applied": None, "approval": None, "error": None,
                    "started": None, "ended": None})
    return out


def add(instance_path, chip: str, *, title: str, steps: list, mode: str | None, created_by: str,
        source: str = "agent", reason: str | None = None, session_id: str | None = None) -> dict:
    rec = {"id": "pl-" + uuid.uuid4().hex[:8], "chip": chip, "title": (title or "").strip()[:120] or "plan",
           "steps": normalize_steps(steps), "mode": mode, "status": "draft", "created": time.time(),
           "created_by": created_by, "source": source, "reason": reason, "session_id": session_id,
           "started_by": None, "started_at": None, "pre_ts": None, "ended": None, "ended_by": None,
           "summary": None, "note": None}
    rows = load(instance_path, chip)
    rows.append(rec)
    _save(instance_path, chip, rows)
    return rec


def get(instance_path, chip: str, plan_id: str) -> dict | None:
    return next((r for r in load(instance_path, chip) if r.get("id") == plan_id), None)


def update(instance_path, chip: str, plan_id: str, **fields) -> dict | None:
    rows = load(instance_path, chip)
    rec = next((r for r in rows if r.get("id") == plan_id), None)
    if rec is None:
        return None
    rec.update(fields)
    _save(instance_path, chip, rows)
    return rec


def running(instance_path, chip: str) -> dict | None:
    return next((r for r in reversed(load(instance_path, chip)) if r.get("status") == "running"), None)


def latest(instance_path, chip: str) -> dict | None:
    rows = load(instance_path, chip)
    return rows[-1] if rows else None


def step_for(rec: dict, *, step: int | None, node: str | None, targets: list | None) -> dict | None:
    """The step a run_node call belongs to: by index when given, else the
    first pending/running step with the same node and targets."""
    steps = rec.get("steps") or []
    if step is not None:
        return next((s for s in steps if s.get("i") == step), None)
    want = list(targets or [])
    for s in steps:
        if s.get("status") in ("pending", "running") and s.get("node") == node and list(s.get("targets") or []) == want:
            return s
    return None


def step_update(instance_path, chip: str, plan_id: str, step_i: int, **fields) -> dict | None:
    """Update one step and derive the plan's own status from its steps."""
    rows = load(instance_path, chip)
    rec = next((r for r in rows if r.get("id") == plan_id), None)
    if rec is None:
        return None
    st = next((s for s in rec.get("steps") or [] if s.get("i") == step_i), None)
    if st is None:
        return None
    st.update(fields)
    _derive(rec)
    _save(instance_path, chip, rows)
    return rec


def _derive(rec: dict) -> None:
    steps = rec.get("steps") or []
    if rec.get("status") in ("stopped", "cancelled", "done", "failed", "skipped"):
        return
    if rec.get("status") == "draft":
        return
    if all(s.get("status") in ("done", "failed", "skipped", "cancelled") for s in steps):
        if rec.get("status") == "stopping":
            rec["status"] = "stopped"                      # review R2-15: the current step finished
        elif any(s.get("status") == "failed" for s in steps):
            rec["status"] = "failed"
        elif any(s.get("status") == "done" for s in steps):
            rec["status"] = "done"
        else:
            rec["status"] = "skipped"                      # review R3-9: nothing ran, so nothing is done
        rec["ended"] = rec.get("ended") or time.time()
        rec["summary"] = counts(rec)


def counts(rec: dict) -> dict:
    steps = rec.get("steps") or []
    out = {"total": len(steps), "done": 0, "failed": 0, "skipped": 0, "pending": 0, "running": 0, "cancelled": 0,
           "writes": 0, "applied": 0, "waiting": 0}
    for s in steps:
        out[s.get("status") or "pending"] = out.get(s.get("status") or "pending", 0) + 1
        out["writes"] += int(s.get("n_writes") or 0)
        if s.get("applied"):
            out["applied"] += int(s.get("n_writes") or 0)
        if s.get("approval"):
            out["waiting"] += 1
    return out


def stop(instance_path, chip: str, plan_id: str, *, who: str, how: str) -> dict | None:
    rows = load(instance_path, chip)
    rec = next((r for r in rows if r.get("id") == plan_id), None)
    if rec is None:
        return None
    if rec.get("status") in ("running", "stopping", "draft"):
        running_step = any(s.get("status") == "running" for s in rec.get("steps") or [])
        if how == "cancelled":
            rec["status"] = "cancelled"
        elif how == "stop after this run" and running_step:
            rec["status"] = "stopping"                     # review R2-15: closes when that step ends
        else:
            rec["status"] = "stopped"
        rec["ended_by"] = who
        rec["note"] = how
        if rec["status"] != "stopping":
            rec["ended"] = time.time()
        for s in rec.get("steps") or []:
            if s.get("status") in ("pending",):
                s["status"] = "cancelled"
        rec["summary"] = counts(rec)
    _save(instance_path, chip, rows)
    return rec


def parse_run_line(text: str) -> dict | None:
    """``/run <node> <targets...> [k=v ...]`` -> {node, targets, params}; the
    deterministic input that never passes through the model (docs/173 §3.3)."""
    t = (text or "").strip()
    words = t.split()
    if not words or not words[0].startswith("/"):
        return None
    if words[0] != "/run":
        # `startswith("/run")` accepted `/runn <node> <targets>` and quietly
        # ran it AS /run (measured: it made a plan card). A command is the
        # whole word or it is not that command — and a `/`-line is never text
        # for the model, so it is named here rather than falling through.
        return {"error": "there is no %s command — the only one is "
                         "/run <node> <targets...> [param=value ...]" % words[0]}
    parts = words[1:]
    if not parts:
        return {"error": "usage: /run <node> <targets...> [param=value ...]"}
    node = parts[0]
    targets, params = [], {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            params[k] = _coerce(v)
        else:
            targets.extend(x for x in p.split(",") if x)
    return {"node": node, "targets": targets, "params": params}


def _coerce(v: str):
    """One `key=value` token from a /run line, as the value it names.

    A token that is not a number stays the string it was typed as -- and
    ``NaN`` / ``inf`` are in that group deliberately. ``float()`` accepts them,
    but the result is not a value any node takes, it is not representable in
    JSON, and ``jsonify`` emits it as a bare ``NaN`` that no browser can parse:
    one such token in one plan made every later card feed unreadable, silently,
    and it stayed that way across a restart because it was written to disk.
    """
    low = v.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none"):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        f = float(v)
    except ValueError:
        return v
    return f if math.isfinite(f) else v
