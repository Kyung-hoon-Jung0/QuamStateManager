"""Arming is a grant for ONE plan, to ONE driving agent (docs/253).

Rule 0: hardware starts only by a person's click. The click is [Start] on a
plan card -- a /run line is a one-step plan, so "one explicit run" is a plan
too. The click arms the chip's session FOR THAT PLAN: the grant names the
plan, the agent that drives it, the person, and the SM process that armed it.
``run_node`` passes only for a pending step of that plan, called by that
driver (``covers``). The grant ends -- with ONE journal line -- when:

* the plan finishes, fails, is skipped, stopped or cancelled;
* the person ends the in-app session that drives it, presses Stop or Disarm;
* the SM process that armed it is gone (a restart);
* the driving agent's process is gone.

Who drives a plan (``request_driver``): SM's own in-app session is told apart
by ``SM_SESSION``, a value SM writes only into that session's MCP config and
the bridge sends back as ``X-SM-Session``; every other agent is a terminal
agent, identified by the id its bridge chose for itself (+ its PID, read only
from a loopback caller). This is PROVENANCE -- which agent the record names --
not authentication: a process that can read SM's instance folder can read the
value too (the identity work, A-09, is separate).

The record lives in the chip's session file (``agent_session``): ``start_token``
(the truth ``check_gates`` reads) + ``grant`` (its scope) + ``last_grant`` (what
the strip says after it ended). Nothing here talks to Flask.
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import uuid
from datetime import datetime
from typing import Callable

from quam_state_manager.core import agent_session
from quam_state_manager.core import journal as journal_mod

logger = logging.getLogger(__name__)

# This SM process. A grant armed by an earlier process with the same PID (a
# restart that reused it) is stale too -- the PID alone cannot tell.
BOOT = uuid.uuid4().hex[:12]

ENDED = ("done", "failed", "stopped", "cancelled", "skipped")

# Called once per grant that ends, AFTER it ended: ``cb(instance_path, key, grant, why)``.
# The hook point for the overnight cluster (docs/253 "Later"): the plan_done /
# needs_human webhooks and the morning summary read "a plan's arming ended, and
# why" here; stop-loss enforcement ENDS a grant through ``end`` with its reason.
ON_END: list = []

STOP_BY_WHY = "the lab's stop time"
RESTART_WHY = "SM restarted"
_ENDED_WORD = {"done": "finished", "failed": "failed", "skipped": "ended with nothing run",
               "stopped": "was stopped", "cancelled": "was cancelled"}
_LOOPBACK = ("127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost")


# ------------------------------------------------------------------ drivers

def request_driver(headers, actor: str, remote_addr: str | None, rec: dict | None) -> dict:
    """Which agent sent this request. ``kind`` app = SM's own in-app session
    (its ``SM_SESSION`` matches the session file's); terminal = any other
    agent, by the id its bridge sent (None for a caller that sent none, which
    is then known by its CLI name alone)."""
    sid = str(headers.get("X-SM-Session") or "").strip()[:80] or None
    app = (rec or {}).get("app_session")
    if sid and app and sid == app:
        return {"kind": "app", "id": sid, "actor": actor, "backend": (rec or {}).get("backend")}
    pid = None
    if sid and (remote_addr or "") in _LOOPBACK:
        try:
            pid = int(str(headers.get("X-SM-Bridge-Pid") or "0")) or None
        except ValueError:
            pid = None
    return {"kind": "terminal", "id": sid, "actor": actor, "pid": pid}


def public(d: dict | None) -> dict | None:
    """What any reader may see of a driver: never the in-app session's value."""
    if not d:
        return None
    out = {"kind": d.get("kind"), "actor": d.get("actor")}
    if d.get("kind") == "app":
        out["backend"] = d.get("backend")
    else:
        out["id"] = d.get("id")
        out["pid"] = d.get("pid")
    return out


def same(a: dict | None, b: dict | None) -> bool:
    if not a or not b or a.get("kind") != b.get("kind"):
        return False
    if a.get("id") or b.get("id"):
        return a.get("id") == b.get("id")
    return a.get("actor") == b.get("actor")      # neither sent an id: known by the CLI name only


def describe(d: dict | None) -> str:
    if not d:
        return "no agent"
    if d.get("kind") == "app":
        return f"SM's in-app {d.get('backend') or 'agent'} session"
    who = d.get("actor") or "an agent"
    return f"{who} in a terminal" + (f" (bridge PID {d['pid']})" if d.get("pid") else "")


def terminal_alive(d: dict | None) -> bool:
    """A terminal driver is gone only when SM KNOWS its process exited (a
    loopback bridge that sent its PID). No PID = cannot tell = still there."""
    if not d or d.get("kind") != "terminal":
        return False
    return not d.get("pid") or agent_session.pid_alive(d.get("pid"))


# ------------------------------------------------------------------ the grant

def arm_plan(instance_path, key: str, *, plan: dict, actor: str, driver: dict, name: str,
             mode: str | None) -> dict:
    """The click: a token + its scope, written in one save."""
    grant = {"plan_id": plan.get("id"), "title": plan.get("title"), "steps": len(plan.get("steps") or []),
             "driver": dict(driver), "by": actor, "at": time.time(), "sm_pid": os.getpid(), "sm_boot": BOOT,
             "name": name, "key": key}
    agent_session.save(instance_path, key, start_token=uuid.uuid4().hex[:12], armed_by=actor,
                       armed_at=grant["at"], agent_stop=None, plan_id=plan.get("id"), mode=mode, grant=grant)
    return grant


def public_grant(g: dict | None) -> dict | None:
    if not g:
        return None
    out = {k: g.get(k) for k in ("plan_id", "title", "steps", "by", "at")}
    out["driver"] = public(g.get("driver"))
    for k in ("ended_at", "why"):
        if k in g:
            out[k] = g[k]
    return out


def end(instance_path, key: str, *, why: str, token: str | None = None, plan_id: str | None = None,
        driver_kind: str | None = None, journal: bool = True, name: str | None = None,
        code: str | None = None) -> dict | None:
    """End the chip's grant ONCE (compare-and-clear under the session lock).

    Returns the grant that ended (``{}`` for a token with no scope behind it),
    or None when there was nothing to end -- already ended, re-armed since
    (``token``), armed for another plan (``plan_id``) or driven by another
    kind of agent (``driver_kind``). The journal line is written by the one
    caller that cleared it."""
    def fn(rec):
        if not rec or not rec.get("start_token"):
            if rec and rec.get("grant"):
                return {"grant": None}, None             # a scope with no token left behind: tidied, nothing said
            return None, None
        if token and rec.get("start_token") != token:
            return None, None
        g = rec.get("grant") or {}
        if plan_id and g.get("plan_id") != plan_id:
            return None, None
        if driver_kind and (g.get("driver") or {}).get("kind") != driver_kind:
            return None, None
        last = dict(public_grant(g) or {}, ended_at=time.time(), why=why, code=code)
        return ({"start_token": None, "grant": None, "plan_id": None, "armed_by": None, "armed_at": None,
                 "last_grant": last}, g)
    ended = agent_session.change(instance_path, key, fn)
    if ended is not None:
        for cb in list(ON_END):
            try:
                cb(instance_path, key, ended, why)
            except Exception:  # noqa: BLE001
                logger.debug("grant end listener failed", exc_info=True)
    if ended is not None and journal:
        chip_name = ended.get("name") or name
        if chip_name:
            try:
                journal_mod.append(instance_path, chip_name, f"disarmed: {why} -- no hardware run starts on this chip "
                                                             "until a person presses Start on a plan", kind="sm")
            except Exception:  # noqa: BLE001
                logger.debug("disarm journal line failed", exc_info=True)
    return ended


def plan_ended(instance_path, key: str, plan: dict) -> dict | None:
    """The plan reached an end through its own steps (agent_plans calls this)."""
    st = plan.get("status")
    if st not in ENDED:
        return None
    why = f"plan `{plan.get('title')}` {_ENDED_WORD.get(st, st)}"
    if st in ("stopped", "cancelled") and plan.get("ended_by"):
        why += f" by {plan['ended_by']}"
    return end(instance_path, key, why=why, plan_id=plan.get("id"))


def _expire_run_requests(instance_path, key: str, grant: dict, why: str) -> None:
    """docs/254: the plan's run requests end with its arming (``approvals.expire_plan_runs``)."""
    if grant and grant.get("plan_id"):
        from quam_state_manager.core import approvals
        approvals.expire_plan_runs(instance_path, key, grant["plan_id"], why)


ON_END.append(_expire_run_requests)


# ------------------------------------------------------------------ is it still good?

def _armer_gone(g: dict) -> bool:
    """The SM process that armed this grant is not this one, alive."""
    sm_pid = g.get("sm_pid")
    if sm_pid == os.getpid():
        return g.get("sm_boot") != BOOT
    return not agent_session.pid_alive(sm_pid)


def invalid_why(instance_path, key: str, rec: dict | None, *, app_open: Callable[[str | None], bool]) -> str | None:
    """Why the armed grant no longer holds, or None while it does (or when
    nothing is armed). ``app_open(secret)`` answers for SM's own in-app
    session in THIS process."""
    return _invalid(instance_path, key, rec, app_open=app_open)[0]


def _invalid(instance_path, key: str, rec: dict | None, *,
             app_open: Callable[[str | None], bool]) -> tuple[str | None, str | None]:
    """``(why, code)`` -- docs/261: the code says which END it is (past_stop_by / restart /
    driver_gone / None), so the plan_done reason and the morning summary never parse the words."""
    if not rec or not rec.get("start_token"):
        return None, None
    g = rec.get("grant")
    if not g:
        return "an arming with no plan behind it (from before arming was per plan) was withdrawn", None
    mine = g.get("sm_pid") == os.getpid()
    if _armer_gone(g):
        return RESTART_WHY, "restart"
    from quam_state_manager.core import agent_plans
    plan = agent_plans.get(instance_path, key, g.get("plan_id") or "")
    if plan is None:
        return f"its plan {g.get('plan_id')} is gone", None
    if plan.get("status") in ENDED:
        why = f"plan `{plan.get('title')}` {_ENDED_WORD.get(plan['status'], plan['status'])}"
        return why + (f" by {plan['ended_by']}" if plan.get("ended_by") and plan["status"] in ("stopped", "cancelled")
                      else ""), None
    from quam_state_manager.core import limits as limits_mod
    try:
        lim = limits_mod.load(instance_path, key)
        since = rec.get("armed_at") or g.get("at")
        if since and limits_mod.past_stop_by(lim, datetime.now(), since=since):
            return f"{STOP_BY_WHY} ({lim.get('stop_by')}) was reached during plan `{g.get('title')}`", "past_stop_by"
    except Exception:  # noqa: BLE001
        logger.debug("stop_by check failed", exc_info=True)
    d = g.get("driver") or {}
    if d.get("kind") == "app" and mine and not app_open(d.get("id")):
        return (f"SM's in-app {d.get('backend') or 'agent'} session that drove plan `{g.get('title')}` is gone",
                "driver_gone")
    if d.get("kind") == "terminal" and not terminal_alive(d):
        return (f"{d.get('actor') or 'the agent'} that drove plan `{g.get('title')}` is gone "
                f"(its SM bridge, PID {d.get('pid')}, exited)"), "driver_gone"
    return None, None


def close_plan(instance_path, key: str, grant: dict | None, *, why: str, restarted: bool,
               code: str | None = None) -> None:
    """A grant that ended for a reason OTHER than its plan ending leaves no
    plan "running" with nobody allowed to run it. A run SM is still driving
    in this process finishes and reports (the plan closes at "stopping" ->
    "stopped"); after a restart nothing will report, so a step left running
    is failed as interrupted.

    docs/261: ``code`` is why SM closes it, said on the plan FIRST -- failing the interrupted step
    can end the plan by itself, and its plan_done must still read "restart", not "failed"."""
    if not grant or not grant.get("plan_id"):
        return
    from quam_state_manager.core import agent_plans
    pid = grant["plan_id"]
    rec = agent_plans.get(instance_path, key, pid)
    if rec is None or rec.get("status") not in ("running", "stopping"):
        return
    if code:
        agent_plans.mark_end(instance_path, key, pid, code=code, why=why)
    if restarted:
        for s in rec.get("steps") or []:
            if s.get("status") == "running":
                agent_plans.step_update(instance_path, key, pid, s["i"], status="failed",
                                        error="interrupted: SM restarted while this step ran", ended=time.time())
    agent_plans.stop(instance_path, key, pid, who="SM", how=why, after_run=not restarted, code=code)


def reconcile(instance_path, key: str, *, app_open: Callable[[str | None], bool], name: str | None = None) -> str | None:
    """End the grant if it no longer holds -- the lazy half of the lifecycle
    (the eager half is the plan's own end, Stop, End session, Disarm). Called
    on every read the strip / the pill / an agent makes and before every
    run_node and Start. Returns why it ended, or None."""
    rec = agent_session.load(instance_path, key)
    if rec and not rec.get("start_token") and rec.get("grant"):
        # the token was taken back elsewhere -- docs/249's restart path disarms the session an
        # interrupted run was armed for, with its own journal line. The scope goes quietly; a plan the
        # dead process left "running" is closed so it never blocks the next Start
        g = rec["grant"]
        end(instance_path, key, why="", journal=False)
        if _armer_gone(g):
            close_plan(instance_path, key, g, why="SM restarted", restarted=True, code="restart")
        return None
    why, code = _invalid(instance_path, key, rec, app_open=app_open)
    if not why:
        return None
    g = end(instance_path, key, why=why, token=rec.get("start_token"), name=name, code=code)
    if g is not None:
        close_plan(instance_path, key, g, why=why, restarted=code == "restart", code=code)
    return why


def sweep(instance_path) -> list[str]:
    """At SM start: every chip's grant armed by a process that is gone ends
    now -- not whenever someone next opens that chip. A grant another LIVE SM
    window armed is left to that window."""
    out: list[str] = []
    d = agent_session._dir(instance_path)
    try:
        files = sorted(d.glob("*.json"))
    except OSError:
        return out
    for f in files:
        try:
            import json
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or not (rec.get("start_token") or rec.get("grant")):
            continue
        key = str(rec.get("chip") or f.stem)
        g = rec.get("grant") or {}
        if g and not _armer_gone(g):
            continue                                     # a live SM (another window, or this one) armed it
        try:
            why = reconcile(instance_path, key, app_open=lambda _s: False, name=g.get("name"))
        except Exception:  # noqa: BLE001
            logger.debug("grant sweep failed for %s", f, exc_info=True)
            continue
        if why:
            out.append(key)
    return out


# ------------------------------------------------------------------ does it cover THIS run?

def _same_node(a: str | None, b: str | None, resolve: Callable[[str], str | None]) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        return (resolve(a) or a) == (resolve(b) or b)
    except Exception:  # noqa: BLE001
        return False


def same_terms(a_targets, a_params, b_targets, b_params) -> bool:
    """Is it the same run (beside its node)? ONE comparison with docs/254's approvals
    (CLAUDE.md: two modules implementing one model call one shared function):
    ``run_terms`` -- targets are a SET, params canonical (``True`` is not ``1``,
    ``"100"`` is not ``100``, ``100.0`` is ``100``)."""
    from quam_state_manager.core import run_terms
    return run_terms.key(None, a_targets, a_params) == run_terms.key(None, b_targets, b_params)


def _step_view(s: dict) -> dict:
    return {"i": s.get("i"), "node": s.get("node"), "targets": list(s.get("targets") or []),
            "params": dict(s.get("params") or {})}


def covers(rec: dict | None, plan: dict | None, *, plan_id: str | None, driver: dict, node: str,
           targets: list, params: dict, step: int | None, resolve: Callable[[str], str | None]) -> tuple[dict | None, int | None]:
    """``(None, step index)`` when the armed grant covers this run, else
    ``(refusal, None)``. A refusal is the data ``run_node`` answers with."""
    g = (rec or {}).get("grant") or {}
    armed = bool((rec or {}).get("start_token")) and bool(g)
    if not plan_id:
        if armed:
            return ({"refused": "no_start_token", "armed_plan": g.get("plan_id"),
                     "how": f"this chip is armed for plan `{g.get('title')}` ({g.get('plan_id')}) only: call run_node "
                            "with its plan_id and step. Anything else is a new plan (plan_propose) a person starts"}, None)
        return ({"refused": "no_start_token",
                 "how": "rule 0: hardware starts only by a person's click on a plan card. Propose this run with "
                        "plan_propose (one step: this node, these targets, these params); when a person presses Start, "
                        "call run_node with the plan_id and step"}, None)
    if plan is None:
        return {"refused": "no_start_token", "plan_id": plan_id, "how": f"there is no plan {plan_id} on this chip"}, None
    title = plan.get("title")
    st = plan.get("status")
    if st == "draft":
        return {"refused": "no_start_token", "plan_id": plan_id,
                "how": f"plan `{title}` waits for a person's Start; nothing of it runs before that"}, None
    if not armed or g.get("plan_id") != plan_id or st != "running":
        last = (rec or {}).get("last_grant") or {}
        why = last.get("why") if last.get("plan_id") == plan_id else None
        if why and last.get("code") == "past_stop_by":
            return {"refused": "past_stop_by", "plan_id": plan_id, "plan_status": st,
                    "how": f"{why}; its arming ended there. Summarize and stop"}, None
        if why and last.get("code") == "stop_loss":
            # docs/261: too many gate fails in the plan -- SM halted it; nothing more of it runs
            return {"refused": "stop_loss", "plan_id": plan_id, "plan_status": st,
                    "how": f"{why}; its arming ended there and nothing more of it runs. Summarize what ran, what "
                           "was applied and what failed, and stop -- a person looks at it"}, None
        return {"refused": "no_start_token", "plan_id": plan_id, "plan_status": st,
                "how": f"plan `{title}` is {st} and not armed" + (f" ({why})" if why else "")
                       + ": stop and tell the human. Anything more is a new plan a person starts"}, None
    if not same(g.get("driver"), driver):
        return {"refused": "not_the_driver", "plan_id": plan_id, "driver": public(g.get("driver")),
                "how": f"plan `{title}` is driven by {describe(g.get('driver'))}; exactly one agent drives a plan. "
                       "Do not run its steps -- tell the human"}, None
    halted = plan.get("halted") or {}
    hit = [t for t in (targets or []) if t in halted]
    if hit:
        # docs/261: a target the plan's stop-loss halted takes no further step under this plan
        first = (halted.get(hit[0]) or {}).get("why") or "stop-loss"
        return {"refused": "target_halted", "plan_id": plan_id, "targets": hit,
                "halted": {t: (halted.get(t) or {}).get("why") for t in hit},
                "how": f"{', '.join(hit)} was halted by plan `{title}`'s stop-loss ({first}); its remaining steps "
                       "are skipped and nothing more runs on it under this plan. Run the other targets' pending "
                       "steps; a person decides about it in the morning"}, None
    pending = [s for s in plan.get("steps") or [] if s.get("status") == "pending"]
    want_t = list(targets or [])
    want_p = dict(params or {})
    cands = pending
    if step is not None:
        cands = [s for s in pending if s.get("i") == step]
    for s in cands:
        if _same_node(s.get("node"), node, resolve) and same_terms(s.get("targets"), s.get("params"), want_t, want_p):
            return None, s.get("i")
    return {"refused": "not_in_plan", "plan_id": plan_id, "step": step,
            "asked": {"node": node, "targets": want_t, "params": want_p},
            "pending": [_step_view(s) for s in pending[:20]],
            "how": f"the person's Start armed plan `{title}` for its own pending steps only, exactly as the card "
                   "shows them (node, targets, params). Run one of `pending` as written, or propose a new plan"}, None
