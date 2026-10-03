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
import math
import re
import time
from datetime import datetime
from pathlib import Path

from quam_state_manager.core import safe_io
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
    "notify_events": ["agent_failure", "plan_done", "needs_human"],   # docs/202 §16
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
            # Deliberately IGNORED here, and pinned that way: an older SM must
            # survive a newer patch. The DOOR is what tells the caller which
            # keys it did not take (docs/191 C02) -- the store stays lenient,
            # the answer stops implying it saved something it did not.
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
                    f = abs(float(lim))
                except (TypeError, ValueError):
                    raise LimitError(f"max_delta[{fam}] must be a number") from None
                if not math.isfinite(f):
                    # docs/191 C01: `Infinity` parsed, was stored, and turned
                    # the gate off without saying so -- and a bare `Infinity`
                    # token in the saved JSON is one no standard parser reads
                    # back (the docs/190 F02 rule).
                    raise LimitError(
                        f"max_delta[{fam}] must be a finite number "
                        "(leave it out to set no bound)")
                md[str(fam)] = f
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


def _shown(key: str, v) -> str:
    """A limit's value as a journal line may carry it. A webhook URL often
    embeds its own secret (a Slack/Teams token in the path), and the journal
    is a markdown file in a person's vault: only its scheme and host go in."""
    if key == "webhook_url":
        if not v:
            return "(none)"
        from urllib.parse import urlsplit
        try:
            u = urlsplit(str(v))
            host = u.hostname or ""
            port = f":{u.port}" if u.port else ""
        except ValueError:
            return "(set)"
        return f"{u.scheme}://{host}{port}/..." if u.scheme and host else "(set)"   # never the user:token@ part
    if v in ("", None, [], {}):
        return "(none)"
    if isinstance(v, (dict, list)):
        return json.dumps(v, separators=(",", ":"), sort_keys=True)
    return str(v)


def describe_patch(patch: dict) -> str:
    """``mode -> auto, human_recent_min -> 0`` -- what a request ASKED for, the
    keys SM knows only, redacted like the journal (docs/252: the line an
    agent's refused attempt leaves)."""
    if not isinstance(patch, dict):
        return ""
    return ", ".join(f"{k} -> {_shown(k, patch[k])}" for k in DEFAULTS if k in patch)


def save(instance_path, chip: str, patch: dict, *, who: str = "human", journal_chip: str | None = None) -> dict:
    """Merge a validated patch, journal every change with who/old/new, return the whole.

    ``chip`` is the machine KEY the gates read (agent_api._chip_key); the
    journal is for people, so a change is written under ``journal_chip``
    (the display name) when given. On-site 2026-09-07: the route saved under
    the display name while run_node loaded under the key, so a lowered
    human_recent_min never reached the gate.

    docs/252 (D-07): only a MODE change used to be journaled, so lowering
    ``human_recent_min`` to 0 or pointing the webhook elsewhere left no trace.
    The mode keeps its own line (its wording is what people search for); every
    other changed limit goes on one line beside it."""
    clean = validate(patch)
    p = path_for(instance_path, chip)
    p.parent.mkdir(parents=True, exist_ok=True)
    # One lock across the whole read -> change -> write: an atomic write stops
    # a CORRUPT file, not two cycles erasing one another (and on Windows two
    # `ReplaceFileW` calls on one target collide outright -- measured at 40
    # threads). Two windows, one process: see safe_io.path_lock.
    with safe_io.path_lock(p):
        cur = load(instance_path, chip)
        before = json.loads(json.dumps(cur))
        cur.update(clean)
    # safe_io's temp file is THIS writer's alone. A fixed `<file>.tmp` is
    # shared by two concurrent writers: their bytes interleave and the
    # mixture is replaced into place, which the reader then swallows as an
    # empty store (agent_plans._save has the measurement).
        safe_io.atomic_write_json(p, cur)
    jchip = journal_chip or chip
    if "mode" in clean and clean["mode"] != before["mode"]:
        journal_mod.append(instance_path, jchip,
                           f"mode {before['mode']} -> {clean['mode']} (set by {who})", kind="sm")
    changed = [k for k in DEFAULTS if k != "mode" and k in clean and clean[k] != before.get(k)]
    if changed:
        journal_mod.append(instance_path, jchip, "limits " + "; ".join(
            f"{k} {_shown(k, before.get(k))} -> {_shown(k, clean[k])}" for k in changed)
            + f" (set by {who})", kind="sm")
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
