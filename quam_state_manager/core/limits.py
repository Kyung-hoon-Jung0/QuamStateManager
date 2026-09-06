"""Per-chip Limits and the default mode (docs/173 §2.4-2, §5, S3b).

The manager's answer to "what do I need to see to allow auto": numbers SM's
own door enforces regardless of backend or mode. Stored per chip in
``<instance>/agent_limits/<chip>.json``; S5's ``run_node`` / ``apply_to_live``
are the enforcement points, this module is the store + the judgement
helpers.

A NEW chip's mode is ``ask-writes`` until a person flips it -- auto is a
choice a lab makes, not one it inherits. Every change is journaled with who.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

from quam_state_manager.core import journal as journal_mod

MODES = ("auto", "ask-writes", "ask-all")
DEFAULTS = {
    "mode": "ask-writes",
    "max_writes_per_plan": 200,          # hold everything past this
    "max_delta": {},                     # family -> max |Δ| (absolute, in the value's own unit) -> hold
    "stoploss_target": 3,                # consecutive gate fails on one target -> that target halts
    "stoploss_plan": 8,                  # gate fails in one plan -> the plan halts
    "stop_by": "",                       # "HH:MM" local; empty = none
    "human_recent_min": 30,              # a human/unknown run within this many minutes refuses run_node
    "webhook_url": "",
    "notify_events": ["agent_failure", "agent_apply_refused", "agent_stalled", "plan_done", "needs_human"],
}


def path_for(instance_path, chip: str) -> Path:
    return Path(instance_path) / "agent_limits" / (journal_mod._safe_key(chip) + ".json")


def load(instance_path, chip: str) -> dict:
    out = dict(DEFAULTS)
    out["max_delta"] = dict(DEFAULTS["max_delta"])
    out["notify_events"] = list(DEFAULTS["notify_events"])
    try:
        cfg = json.loads(path_for(instance_path, chip).read_text(encoding="utf-8"))
        if isinstance(cfg, dict):
            for k, v in cfg.items():
                if k in DEFAULTS:
                    out[k] = v
    except (OSError, ValueError):
        pass
    if out["mode"] not in MODES:
        out["mode"] = DEFAULTS["mode"]
    return out


class LimitError(ValueError):
    pass


def validate(patch: dict) -> dict:
    """Coerce + check a partial update. Raises LimitError with the field."""
    out: dict = {}
    for k, v in (patch or {}).items():
        if k not in DEFAULTS:
            continue
        if k == "mode":
            if v not in MODES:
                raise LimitError(f"mode must be one of {MODES}")
            out[k] = v
        elif k in ("max_writes_per_plan", "stoploss_target", "stoploss_plan", "human_recent_min"):
            try:
                n = int(v)
            except (TypeError, ValueError):
                raise LimitError(f"{k} must be a whole number") from None
            if n < 0:
                raise LimitError(f"{k} must be >= 0")
            out[k] = n
        elif k == "stop_by":
            s = str(v or "").strip()
            if s and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
                raise LimitError("stop_by must be HH:MM")
            out[k] = s
        elif k == "max_delta":
            if not isinstance(v, dict):
                raise LimitError("max_delta must be an object of family -> number")
            md = {}
            for fam, lim in v.items():
                try:
                    md[str(fam)] = abs(float(lim))
                except (TypeError, ValueError):
                    raise LimitError(f"max_delta[{fam}] must be a number") from None
            out[k] = md
        elif k == "webhook_url":
            s = str(v or "").strip()
            if s and not s.startswith(("http://", "https://")):
                raise LimitError("webhook_url must start with http:// or https://")
            out[k] = s
        elif k == "notify_events":
            if not isinstance(v, list):
                raise LimitError("notify_events must be a list")
            out[k] = [str(x) for x in v]
    return out


def save(instance_path, chip: str, patch: dict, *, who: str = "human") -> dict:
    """Merge a validated patch, journal a mode change with who, return the whole."""
    clean = validate(patch)
    cur = load(instance_path, chip)
    before_mode = cur["mode"]
    cur.update(clean)
    p = path_for(instance_path, chip)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, indent=1), encoding="utf-8")
    os.replace(tmp, p)
    if "mode" in clean and clean["mode"] != before_mode:
        journal_mod.append(instance_path, chip, f"mode {before_mode} -> {clean['mode']} (set by {who})", kind="sm")
    return cur


def past_stop_by(limits: dict, now: datetime | None = None) -> bool:
    """True once the wall clock passed today's stop_by (a night plan ends at
    the time the lab said, even if the agent is mid-chain)."""
    s = limits.get("stop_by") or ""
    if not s:
        return False
    now = now or datetime.now()
    hh, mm = (int(x) for x in s.split(":"))
    return (now.hour, now.minute) >= (hh, mm)


def delta_exceeds(limits: dict, family: str | None, old, new) -> bool:
    """A write outside the family's band is HELD in every mode."""
    lim = (limits.get("max_delta") or {}).get(family or "")
    if lim is None:
        return False
    try:
        return abs(float(new) - float(old)) > float(lim)
    except (TypeError, ValueError):
        return False


def notify(instance_path, chip: str, event: str, payload: dict | None = None) -> dict:
    """Webhook through the existing notify.py, gated by THIS chip's Limits."""
    lim = load(instance_path, chip)
    url = lim.get("webhook_url") or ""
    if not url or event not in (lim.get("notify_events") or []):
        return {"sent": [], "skipped": "no webhook or event off"}
    try:
        from quam_state_manager.core.autofit import notify as nmod
        body = {"event": event, "chip": chip, "at": time.time(), "payload": payload or {}}
        ok = nmod._post(url, body, 10.0)
        return {"sent": [url] if ok else [], "skipped": None if ok else "post failed"}
    except Exception as exc:  # noqa: BLE001
        return {"sent": [], "skipped": f"notify failed: {exc}"}
