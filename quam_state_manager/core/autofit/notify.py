"""Autofit notifications — webhook + browser (docs/78 D-9).

**Four events only.** A notifier that fires on everything is one the user mutes,
and a muted notifier is worse than none: it reads as coverage while delivering
nothing.

    plan_done       the night finished — with the counts that decide whether
                    anyone needs to get up
    target_halted   one target gave up; the others continue (D-8's
                    revert-and-continue), so this is FYI, not an alarm
    plan_stopped    a stop-loss fired — budget, no-progress or harm
    needs_human     the valuable one: the loop narrowed a problem to something
                    it cannot resolve and is ASKING, e.g. "two hypotheses, I
                    cannot separate them" (docs/78 D-8: design the question as
                    a normal terminal state, not an exhaustion)

Delivery is best-effort and never raises into the engine: a failed webhook must
not fail a calibration. Browser delivery is a stored queue the UI drains, so a
closed laptop does not lose the night's result.
"""
from __future__ import annotations

import json
import logging
import threading
import urllib.error
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

EVENTS = ("plan_done", "target_halted", "plan_stopped", "needs_human")
_SETTINGS_FILE = "autofit_notify.json"
_QUEUE_FILE = "autofit_notifications.json"
_QUEUE_CAP = 200
_lock = threading.Lock()

_DEFAULTS = {
    "webhook_url": "",
    "browser": True,
    "events": list(EVENTS),
    "timeout_s": 10,
}


def load_settings(instance_path) -> dict:
    out = dict(_DEFAULTS)
    try:
        out.update(json.loads(
            (Path(instance_path) / _SETTINGS_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    ev = out.get("events")
    out["events"] = [e for e in ev if e in EVENTS] if isinstance(ev, list) \
        else list(EVENTS)
    return out


def save_settings(instance_path, patch: dict) -> dict:
    from quam_state_manager.core import safe_io

    cur = load_settings(instance_path)
    cur.update({k: v for k, v in (patch or {}).items() if k in _DEFAULTS})
    Path(instance_path).mkdir(parents=True, exist_ok=True)
    safe_io.atomic_write_json(Path(instance_path) / _SETTINGS_FILE, cur)
    return cur


def notify(instance_path, event: str, payload: dict | None = None) -> dict:
    """Fire one event. Returns ``{"sent": [...], "skipped": reason|None}``.

    Never raises. Never blocks a calibration on a network.
    """
    if event not in EVENTS:
        return {"sent": [], "skipped": f"unknown event {event!r}"}
    st = load_settings(instance_path)
    if event not in st["events"]:
        return {"sent": [], "skipped": "event disabled in settings"}
    body = {"event": event, **(payload or {})}
    sent = []
    if st.get("browser"):
        _enqueue(instance_path, body)
        sent.append("browser")
    url = (st.get("webhook_url") or "").strip()
    if url:
        if _post(url, body, float(st.get("timeout_s", 10))):
            sent.append("webhook")
    return {"sent": sent, "skipped": None}


def _post(url: str, body: dict, timeout: float) -> bool:
    try:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout):  # noqa: S310
            return True
    except (urllib.error.URLError, OSError, ValueError) as exc:
        # a failed webhook must never fail a calibration
        logger.warning("autofit webhook failed: %s", exc)
        return False


def _enqueue(instance_path, body: dict) -> None:
    """Persist for the UI to drain — a closed laptop must not lose the night."""
    from quam_state_manager.core import safe_io

    p = Path(instance_path) / _QUEUE_FILE
    with _lock:
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(cur, list):
                cur = []
        except (OSError, ValueError):
            cur = []
        cur.append(body)
        try:
            Path(instance_path).mkdir(parents=True, exist_ok=True)
            safe_io.atomic_write_json(p, cur[-_QUEUE_CAP:])
        except (OSError, ValueError):
            logger.warning("could not persist autofit notification")


def drain(instance_path) -> list[dict]:
    """Return and CLEAR the browser queue."""
    from quam_state_manager.core import safe_io

    p = Path(instance_path) / _QUEUE_FILE
    with _lock:
        try:
            cur = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        try:
            safe_io.atomic_write_json(p, [])
        except (OSError, ValueError):
            pass
    return cur if isinstance(cur, list) else []


def peek(instance_path) -> list[dict]:
    try:
        cur = json.loads(
            (Path(instance_path) / _QUEUE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return cur if isinstance(cur, list) else []
