"""The agent session file (docs/173 §3.3): one per chip, the reservation.

``<instance>/agent_sessions/<chip>.json`` says who is driving this chip, with
what, since when, until when, and whether a human pressed Stop. S3 defines
the record and the readers the pill needs; S4 (the chat backend) and S5
(run_node) write it. A terminal session that never talks to SM has no file
here -- the pill knows it only through hook events.

Nothing in this file talks to a process except ``alive()``, which asks the
OS whether the recorded pid still exists.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from quam_state_manager.core import safe_io
from quam_state_manager.core import journal as journal_mod

FIELDS = ("chip", "backend", "session_id", "mode", "owner", "started", "until", "pid", "worker_pid",
          "agent_stop", "stop_by", "plan_id", "limited_until", "claimed_by_tool", "window",
          "start_token", "armed_by", "armed_at", "run_key")


def _dir(instance_path) -> Path:
    return Path(instance_path) / "agent_sessions"


def path_for(instance_path, chip: str) -> Path:
    return _dir(instance_path) / (journal_mod._safe_key(chip) + ".json")


def load(instance_path, chip: str) -> dict | None:
    try:
        d = json.loads(path_for(instance_path, chip).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def save(instance_path, chip: str, **fields) -> dict:
    """Create or update the session record (merge). Returns the record."""
    p = path_for(instance_path, chip)
    p.parent.mkdir(parents=True, exist_ok=True)
    # One lock across the whole read -> change -> write: an atomic write stops
    # a CORRUPT file, not two cycles erasing one another (and on Windows two
    # `ReplaceFileW` calls on one target collide outright -- measured at 40
    # threads). Two windows, one process: see safe_io.path_lock.
    with safe_io.path_lock(p):
        cur = load(instance_path, chip) or {"chip": chip, "started": time.time()}
        for k, v in fields.items():
            if k in FIELDS:
                cur[k] = v
        cur["updated"] = time.time()
    # safe_io's temp file is THIS writer's alone. A fixed `<file>.tmp` is
    # shared by two concurrent writers: their bytes interleave and the
    # mixture is replaced into place, which the reader then swallows as an
    # empty store (agent_plans._save has the measurement).
        safe_io.atomic_write_json(p, json.loads(json.dumps(cur, default=str)),
                                  compact=True)
    return cur


def clear(instance_path, chip: str) -> bool:
    try:
        path_for(instance_path, chip).unlink()
        return True
    except OSError:
        return False


def pid_alive(pid) -> bool:
    try:
        from quam_state_manager.core.instances import pid_alive as _pa
        return bool(pid) and _pa(int(pid))
    except Exception:  # noqa: BLE001
        return False


def alive(rec: dict | None) -> bool:
    """A session is alive while its agent OR its worker process exists."""
    if not rec:
        return False
    return pid_alive(rec.get("pid")) or pid_alive(rec.get("worker_pid"))


def stopped(rec: dict | None) -> bool:
    return bool(rec and rec.get("agent_stop"))


def request_stop(instance_path, chip: str, *, who: str, mode: str = "after_run") -> dict | None:
    """Stop, recorded before anything is killed: run_node reads this first."""
    if load(instance_path, chip) is None:
        return None
    # rule 0 both ways: a Stop also takes the start token back -- the next run needs a new click
    return save(instance_path, chip, agent_stop={"who": who, "mode": mode, "at": time.time()}, start_token=None)


def summary(rec: dict | None) -> dict | None:
    """What the pill and the Calibration log header show."""
    if not rec:
        return None
    return {"owner": rec.get("owner"), "backend": rec.get("backend"), "mode": rec.get("mode"),
            "started": rec.get("started"), "until": rec.get("until"), "plan_id": rec.get("plan_id"),
            "alive": alive(rec), "stopped": stopped(rec), "stop": rec.get("agent_stop"),
            "limited_until": rec.get("limited_until"), "session_id": rec.get("session_id"),
            "claimed_by_tool": rec.get("claimed_by_tool"), "armed": bool(rec.get("start_token")),
            "armed_by": rec.get("armed_by"), "armed_at": rec.get("armed_at"), "run_key": rec.get("run_key")}


def safe_owner(name: str | None) -> str:
    return re.sub(r"[\r\n\t]+", " ", str(name or "")).strip()[:40]
