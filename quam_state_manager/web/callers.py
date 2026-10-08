"""Who is calling: a person in SM's own window, an agent, SM's hook, or a
caller SM cannot vouch for (docs/252).

Before this module, identity was a header: ``X-SM-Agent`` named an agent,
anything else was "a human" -- so ``curl -X POST .../session/arm`` with no
header armed the agent, ``X-SM-Actor: user-c`` made the press user-c's, and any
POST to ``/api/agent/event`` became a ``by_claude`` journal line (A-09).

Two proofs, each as strong as this machine allows:

* **A person's window.** SM hands every HTML page it serves a cookie holding
  a random secret made when this SM process started: ``HttpOnly`` (no script
  reads it -- not SM's, not a page on another local port), ``SameSite=Strict``
  (no other site's page sends it), named per port (two SM windows on one
  machine share one cookie jar, and must not overwrite each other). The
  browser -- Chrome, or the desktop window's WebView2 -- attaches it to every
  same-origin request, so every button that already worked still works. The
  MCP bridge, the hook and a plain curl never fetched a page, so they never
  have it. The routes that are a PERSON's (``PERSON_ONLY``) refuse a request
  without it, and any request carrying ``X-SM-Agent``.
* **SM's hook.** ``/api/agent/event`` needs ``X-SM-Hook-Key``: a random key
  SM keeps in its instance dir (``agent_link.ensure_hook_key``), which the
  hook reads from the same dir it already writes its event log into.

The limit, stated rather than discovered later: every agent here runs as the
same OS user as SM. One with a shell can fetch a page and replay its cookie,
read the hook key, or edit SM's instance files outright. Nothing inside SM can
stop a same-user process that sets out to forge a person; that needs a second
OS account. What these proofs DO stop is every accidental or casual route:
the bridge's tools, a hook, an agent trying ``curl`` after a refusal. Forging
now takes a deliberate, multi-step impersonation, which the refusal text does
not explain.

The test client is exempt unless a test asks (``PROVE_CALLERS``), the same
switch the CSRF guard already has under ``TESTING``.
"""

from __future__ import annotations

import hmac
import logging
import secrets

from flask import current_app, jsonify, request

from quam_state_manager.core import agent_link

logger = logging.getLogger(__name__)

COOKIE_PREFIX = "sm_person"

#: endpoint -> what the press is, in the words the refusal uses. POST only:
#: the GETs of the same routes are reads.
PERSON_ONLY = {
    "agent.session_arm": "arming the agent",
    "agent.approvals_decide": "deciding an approval",
    "agent.plan_start": "starting a plan",
    "agent.plan_cancel": "cancelling a plan",
    "agent.plan_mode": "choosing a plan's mode",
    "agent.limits_route": "changing the limits",
    "agent.journal_root": "moving the journal folder",
    "journal.journal_claim": "saying who ran a run",
    "journal.journal_adopt": "filing unassigned journal lines under this chip",
    "agent_setup.journal_setup": "moving the journal folder",
    "agent_setup.connect": "connecting an agent CLI to SM",
    "agent_setup.disconnect": "disconnecting an agent CLI from SM",
    "agent_setup.context_post": "rewriting the lab context the agents read",
}

#: The person's guardrails: an attempt on one is itself worth a journal line.
GUARDRAILS = frozenset({"agent.limits_route", "agent.journal_root", "agent_setup.journal_setup",
                        "agent_setup.connect", "agent_setup.disconnect", "agent_setup.context_post"})

HOOK_ONLY = frozenset({"agent.event_post"})


def enforcing() -> bool:
    """The proofs are checked in a real SM. The Flask test client never served
    a page, so -- like the CSRF guard -- it is exempt unless a test sets
    ``PROVE_CALLERS`` (the pins of docs/252 do)."""
    cfg = current_app.config
    if "PROVE_CALLERS" in cfg:
        return bool(cfg["PROVE_CALLERS"])
    return not cfg.get("TESTING")


def cookie_name() -> str:
    """Per port: cookies ignore the port, and a second SM window on this
    machine must not overwrite this one's proof."""
    host = request.host or ""
    port = host.rsplit(":", 1)[1] if ":" in host and not host.endswith("]") else ""
    return f"{COOKIE_PREFIX}_{port}" if port.isdigit() else COOKIE_PREFIX


def _secret() -> str:
    return str(current_app.config.get("SM_PERSON_SECRET") or "")


def from_person() -> bool:
    """A person pressing in SM's own window: no agent header, and this
    process's cookie. (Not enforcing: no agent header is enough.)"""
    if (request.headers.get("X-SM-Agent") or "").strip():
        return False
    if not enforcing():
        return True
    got, want = request.cookies.get(cookie_name()) or "", _secret()
    return bool(got and want) and hmac.compare_digest(got, want)


def hook_key_ok() -> bool:
    if not enforcing():
        return True
    want = agent_link.read_hook_key(current_app.instance_path) or ""
    got = request.headers.get(agent_link.HOOK_KEY_HEADER) or ""
    return bool(got and want) and hmac.compare_digest(got, want)


def _attempt(ep: str) -> str:
    """What the refused request asked for, for the journal line."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        data = request.form.to_dict()
    try:
        if ep == "agent.limits_route":
            from quam_state_manager.core import limits
            said = limits.describe_patch(data)
            return f" ({said})" if said else ""
        if ep in ("agent.journal_root", "agent_setup.journal_setup"):
            return f" (to {data.get('root') or 'the default folder'})"
        if ep in ("agent_setup.connect", "agent_setup.disconnect"):
            return f" ({data.get('backend')})" if data.get("backend") else ""
    except Exception:  # noqa: BLE001 -- a description never fails the refusal
        logger.debug("refusal description failed", exc_info=True)
    return ""


def _journal_refusal(ep: str, what: str, agent: str) -> None:
    """An attempt on a guardrail goes into the open chip's journal: the person
    reads in the morning that an agent tried to loosen its own limits."""
    try:
        from quam_state_manager.core import journal as journal_mod
        from quam_state_manager.web import agent_api as aa
        r = aa._r()
        if not r._active_path():
            return
        who = r._request_actor() if agent else "a request with no SM window behind it"
        journal_mod.append(current_app.instance_path, aa._chip_name(),
                           f"{who} tried {what}{_attempt(ep)} -- refused: only a person can, "
                           "from the SM window", kind="sm")
        aa._bump()
    except Exception:  # noqa: BLE001
        logger.debug("refusal journal failed", exc_info=True)


def _gate():
    """before_request: the person-only routes and the hook's door."""
    if request.method != "POST":
        return None
    ep = request.endpoint or ""
    if ep in HOOK_ONLY:
        if hook_key_ok():
            return None
        logger.warning("an agent event without SM's hook key was refused (%s)", request.remote_addr)
        return jsonify(ok=False, refused="unproven_event",
                       error="not recorded: this event did not come from SM's own hook"), 403
    what = PERSON_ONLY.get(ep)
    if what is None:
        return None
    agent = (request.headers.get("X-SM-Agent") or "").strip()
    if agent:
        body = {"refused": "person_only",
                "error": f"only a person can do this ({what}), in the SM window -- never an agent",
                "how": "ask the person to do it in the SM window"}
    elif not from_person():
        body = {"refused": "no_window_proof",
                "error": f"{what} needs a press in the SM window. If SM was restarted, reload the page "
                         "and press again."}
    else:
        return None
    if ep in GUARDRAILS:
        _journal_refusal(ep, what, agent)
    return jsonify(ok=False, **body), 403


def _issue(resp):
    """after_request: every HTML page SM serves carries this process's proof."""
    try:
        if request.method != "GET" or resp.mimetype != "text/html":
            return resp
        if (request.headers.get("X-SM-Agent") or "").strip():
            return resp
        name, secret = cookie_name(), _secret()
        if secret and request.cookies.get(name) != secret:
            resp.set_cookie(name, secret, httponly=True, samesite="Strict",
                            path=(request.script_root or "/"))
    except Exception:  # noqa: BLE001 -- the proof never fails a page
        logger.debug("person cookie not set", exc_info=True)
    return resp


def init_app(app) -> None:
    """Register both proofs. After the CSRF guard: a cross-origin POST is
    refused as that first."""
    app.config.setdefault("SM_PERSON_SECRET", secrets.token_urlsafe(32))
    try:
        agent_link.ensure_hook_key(app.instance_path)
    except OSError:
        logger.warning("could not write the hook key into %s", app.instance_path, exc_info=True)
    app.before_request(_gate)
    app.after_request(_issue)
