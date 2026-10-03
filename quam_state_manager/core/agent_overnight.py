"""The overnight run (docs/261): one envelope a person approves before leaving.

The envelope is the plan (nodes, targets, params), mode ``auto``, ``stop_by``,
``max_writes_per_plan``, ``max_delta`` per family and the stop-loss
(``stoploss_target`` / ``stoploss_plan``). The person sees it on the plan card
and presses Start ONCE; inside it, writes that pass the gates apply on their
own (each through the one door: a pre-apply snapshot, a journal line, an undo
unit). Outside it, the write is held for the morning and that target stops;
the other targets go on. Too many gate fails halt the whole plan.

What a "gate fail" is (docs/261 §2) -- read from what the run path already
has, never a new judgement:

* the run FAILED, by its own classification (docs/249): host_unreachable,
  hardware_contention, timeout, node_error, interrupted. A person's Stop
  (``cancelled``) and ``skipped`` are not the run's failure;
* the run finished but the node's OWN outcome for that target says it failed
  (``node.json`` ``outcomes``, what ``_attribute`` reads -- the same reading
  ``story._outcome`` uses: the word "fail");
* the run's writes were HELD by the envelope in mode auto: ``max_delta``,
  ``max_writes_per_plan``, a write that touches a target whose fit failed,
  a list resized / too many leaves, or the door refusing the apply. A hold
  that is the MODE's own (ask-writes, ask-all, Dry run) is not a gate fail:
  that is what the mode was chosen for.

Every count is read back from the plan record (each step's ``gate``), so the
stop-loss and the morning summary survive an SM restart. Nothing here talks
to Flask.
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from pathlib import Path

from quam_state_manager.core import agent_plans, approvals
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.core import limits as limits_mod

logger = logging.getLogger(__name__)

# docs/249's failure classes: the run itself failed. ``cancelled`` (a person's Stop), ``skipped`` and
# ``ok`` / ``unattributed`` (the node finished) are not here.
FAIL_CLASSES = ("host_unreachable", "hardware_contention", "timeout", "node_error", "interrupted")
ENVELOPE_KEYS = ("mode", "stop_by", "max_writes_per_plan", "max_delta", "stoploss_target", "stoploss_plan")
REASONS = {"stop_loss": "stop-loss", "past_stop_by": "stop_by", "restart": "restart", "driver_gone": "driver_gone"}


def link_for(plan_id: str | None) -> str:
    """The morning summary's path (a webhook names it; SM's host is the reader's own)."""
    return "/agent/summary" + (f"?plan={plan_id}" if plan_id else "")


# ------------------------------------------------------------------ gates

def touches(path: str | None, target: str) -> bool:
    """A dot path belongs to ``target`` when one of its segments IS the target name
    (``qubits.qA1.f_01``, ``qubit_pairs.qA1-qA2.coupler...``) -- never a prefix match."""
    return bool(target) and target in str(path or "").split(".")


def _failed_outcome(v) -> bool:
    return "fail" in str(v).lower()


def fit_hold(run: dict | None, writes: list[dict]) -> str | None:
    """Why a run's writes must be held in auto: one of them would be written FROM a fit the node
    itself marked failed for that target. None when no write touches a failed target."""
    oc = (run or {}).get("outcomes")
    if not isinstance(oc, dict):
        return None
    for t, v in oc.items():
        if not _failed_outcome(v):
            continue
        hit = next((w for w in writes or [] if touches(w.get("path"), str(t))), None)
        if hit is not None:
            return f"fit failed for {t} (the node's own outcome: {v}); `{hit.get('path')}` would be written from it"
    return None


def gate_verdicts(result: dict, targets: list) -> dict | None:
    """``{target: {"v": "pass" | "fail" | "held", "why": ...}}`` for one finished run, or None
    when the run says nothing about its targets (a person's Stop, a skipped item)."""
    st = result.get("status")
    cls = result.get("classification")
    if st != "done":
        if cls not in FAIL_CLASSES:
            return None
        what = (result.get("failure") or {}).get("what")
        if not what:
            err = str(result.get("error") or "").strip()
            what = err.splitlines()[0][:160] if err else cls
        return {t: {"v": "fail", "why": f"{cls}: {what}"} for t in targets or []}
    why_held = str(result.get("why_held") or "")
    held = (result.get("mode") == "auto" and bool(result.get("approval"))
            and not why_held.startswith("DRY RUN"))
    oc = (result.get("run") or {}).get("outcomes")
    oc = oc if isinstance(oc, dict) else {}
    out = {}
    for t in targets or []:
        if held:
            out[t] = {"v": "held", "why": f"held: {why_held}"[:300]}
        elif t in oc and _failed_outcome(oc[t]):
            out[t] = {"v": "fail", "why": f"fit: the node's own outcome for {t} is {oc[t]}"}
        else:
            out[t] = {"v": "pass"}
    return out


def _int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def stoploss(plan: dict, lim: dict) -> dict:
    """The stop-loss read from the plan's own record. Per target, its steps in the order they
    ENDED; ``consecutive`` = the gate fails at the tail (a pass resets it). The plan's ``fails``
    = every (step, target) gate fail. Halts:

    * a target whose LAST step's writes were held (mode auto -- "outside the envelope, that
      target stops");
    * a target with ``stoploss_target`` (>0) gate fails in a row;
    * the plan, at ``stoploss_plan`` (>0) gate fails. 0 means off, like max_writes_per_plan."""
    rows: dict[str, list] = {}
    for s in plan.get("steps") or []:
        for t, v in (s.get("gate") or {}).items():
            rows.setdefault(t, []).append((float(s.get("ended") or 0), int(s.get("i") or 0),
                                           (v or {}).get("v"), (v or {}).get("why")))
    total = 0
    per: dict[str, dict] = {}
    for t, rs in rows.items():
        rs.sort(key=lambda r: (r[0], r[1]))
        fails = [r for r in rs if r[2] in ("fail", "held")]
        total += len(fails)
        consec = 0
        for r in reversed(rs):
            if r[2] not in ("fail", "held"):
                break
            consec += 1
        per[t] = {"consecutive": consec, "fails": len(fails), "last": rs[-1][2], "last_why": rs[-1][3]}
    nt, np_ = _int(lim.get("stoploss_target")), _int(lim.get("stoploss_plan"))
    halts: dict[str, str] = {}
    for t, d in per.items():
        if d["last"] == "held":
            halts[t] = f"its write is held for a person -- {d['last_why']}"
        elif nt > 0 and d["consecutive"] >= nt:
            halts[t] = f"{d['consecutive']} gate fails in a row (stoploss_target {nt}); last: {d['last_why']}"
    plan_halt = (f"{total} gate fails in this plan (stoploss_plan {np_})" if np_ > 0 and total >= np_ else None)
    return {"fails": total, "targets": per, "halt_targets": halts, "halt_plan": plan_halt}


def after_step(instance_path, key: str, plan_id: str | None, *, name: str | None = None,
               lim: dict | None = None) -> dict:
    """Apply the stop-loss after a step reported. Halts newly halted targets (their pending
    steps skipped, one journal line each) and, past ``stoploss_plan``, the plan: its arming
    ends through agent_grant's end path with code ``stop_loss`` and the plan closes.
    Returns ``{"halted": {target: why}, "plan_halt": why | None}``."""
    out: dict = {"halted": {}, "plan_halt": None}
    if not plan_id:
        return out
    plan = agent_plans.get(instance_path, key, plan_id)
    if plan is None or plan.get("status") != "running":
        return out                                 # ended by this very step, stopping, or closed
    lim = lim or limits_mod.load(instance_path, key)
    st = stoploss(plan, lim)
    already = plan.get("halted") or {}
    new = {t: why for t, why in st["halt_targets"].items() if t not in already}
    title = plan.get("title")
    if new:
        rec, _ended = agent_plans.halt_targets(instance_path, key, plan_id, new)
        out["halted"] = new
        for t, why in new.items():
            n = len(((rec or {}).get("halted") or {}).get(t, {}).get("skipped") or [])
            _journal(instance_path, key, name,
                     f"stop-loss: target {t} halted in plan `{title}` -- {why}; its {n} remaining step(s) are "
                     "skipped, the other targets continue")
        plan = rec or plan
    if st["halt_plan"] and (agent_plans.get(instance_path, key, plan_id) or {}).get("status") == "running":
        from quam_state_manager.core import agent_grant
        why = f"stop-loss: {st['halt_plan']} -- plan `{title}` halted"
        g = agent_grant.end(instance_path, key, why=why, plan_id=plan_id, code="stop_loss", name=name)
        agent_grant.close_plan(instance_path, key, g or {"plan_id": plan_id}, why=why, restarted=False,
                               code="stop_loss")
        out["plan_halt"] = why
    return out


def _journal(instance_path, key: str, name: str | None, line: str) -> None:
    if not name:
        try:
            from quam_state_manager.core import agent_session
            rec = agent_session.load(instance_path, key) or {}
            name = (rec.get("grant") or {}).get("name") or (rec.get("last_grant") or {}).get("name")
        except Exception:  # noqa: BLE001
            name = None
    if not name:
        logger.info("overnight journal line skipped (no chip name): %s", line)
        return
    try:
        journal_mod.append(instance_path, name, line, kind="sm")
    except Exception:  # noqa: BLE001
        logger.debug("overnight journal line failed", exc_info=True)


# ------------------------------------------------------------------ the plan's end

def plan_end_reason(plan: dict) -> tuple[str, str]:
    """``(reason, why)``: finished / failed / cancelled / stopped / stop-loss / stop_by /
    restart / driver_gone -- from the record SM wrote when it closed the plan, never parsed."""
    code = plan.get("end_code")
    if code in REASONS:
        return REASONS[code], str(plan.get("end_why") or code)
    st = plan.get("status")
    who = plan.get("ended_by")
    if st == "cancelled":
        return "cancelled", f"cancelled by {who or 'a person'}"
    if st == "stopped":
        note = plan.get("note")
        return "stopped", (f"stopped by {who}" if who else "stopped") + (
            f" ({note})" if note and who == "SM" else "")
    if st == "failed":
        n = sum(1 for s in plan.get("steps") or [] if s.get("status") == "failed")
        return "failed", f"{n} step(s) failed"
    if st == "skipped":
        return "finished", "nothing ran"
    return "finished", "every step ran"


def plan_counts(instance_path, key: str, plan: dict, lim: dict | None = None) -> dict:
    c = agent_plans.counts(plan)
    aps = [a for a in approvals.load(instance_path, key) if a.get("plan_id") == plan.get("id")]
    held_w = [a for a in aps if a.get("kind") == "writes" and a.get("status") == "pending"]
    held_r = [a for a in aps if a.get("kind") == "run" and a.get("status") == "pending"]
    applied = [s for s in plan.get("steps") or [] if s.get("applied")]
    st = stoploss(plan, lim or {})
    return {"applied": sum(int(s.get("n_writes") or 0) for s in applied), "applied_runs": len(applied),
            "held": len(held_w) + len(held_r), "held_writes": sum(len(a.get("writes") or []) for a in held_w),
            "failed": c.get("failed", 0), "steps": c.get("total", 0), "done": c.get("done", 0),
            "skipped": c.get("skipped", 0), "cancelled": c.get("cancelled", 0), "gate_fails": st["fails"]}


def plan_done_payload(instance_path, key: str, plan: dict) -> dict:
    reason, why = plan_end_reason(plan)
    halted = plan.get("halted") or {}
    return {"plan_id": plan.get("id"), "title": plan.get("title"), "status": plan.get("status"),
            "reason": reason, "why": why[:300], "mode": plan.get("mode"),
            "started_at": plan.get("started_at"), "started_by": plan.get("started_by"),
            "ended_at": plan.get("ended") or time.time(),
            "counts": plan_counts(instance_path, key, plan),
            "halted_targets": [{"target": t, "why": str((h or {}).get("why") or "")[:200]} for t, h in halted.items()],
            "link": link_for(plan.get("id"))}


def send(instance_path, key: str, event: str, payload: dict) -> None:
    """The chip's webhook (limits.notify, gated by its own Limits), from a thread: a dead URL
    never stalls a request or the run driver."""
    def _post():
        try:
            limits_mod.notify(instance_path, key, event, payload)
        except Exception:  # noqa: BLE001
            logger.debug("overnight webhook failed", exc_info=True)
    threading.Thread(target=_post, name=f"sm-notify-{event}", daemon=True).start()


def plan_ended(instance_path, key: str, plan: dict) -> None:
    """agent_plans.ON_PLAN_END: once per started plan that ended, whatever ended it.

    * its run requests expire (docs/254's rule, now also on a person's Stop, which ends the grant
      without agent_grant.ON_END) -- nothing can spend them any more;
    * ``plan_done`` goes out with the reason, the counts and the summary's path."""
    try:
        reason, why = plan_end_reason(plan)
        approvals.expire_plan_runs(instance_path, key, plan.get("id"), f"plan {reason}: {why}"[:200])
    except Exception:  # noqa: BLE001
        logger.debug("run request expiry at plan end failed", exc_info=True)
    try:
        send(instance_path, key, "plan_done", plan_done_payload(instance_path, key, plan))
    except Exception:  # noqa: BLE001
        logger.debug("plan_done failed", exc_info=True)


def needs_human_run_request(instance_path, key: str, ap: dict, plan: dict | None) -> None:
    """An ask-all run request is waiting for a person's Allow during an armed plan."""
    from quam_state_manager.core import run_terms
    send(instance_path, key, "needs_human", {
        "what": "run_request", "approval": ap.get("id"), "node": ap.get("node"), "targets": ap.get("targets"),
        "params": run_terms.params_text(ap.get("params")), "plan_id": ap.get("plan_id"),
        "plan": (plan or {}).get("title"), "step": ap.get("step"), "why": ap.get("why_held"),
        "link": link_for(ap.get("plan_id"))})


# ------------------------------------------------------------------ the envelope

def _num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isfinite(f) and f == int(f) and abs(f) < 1e15:
        return str(int(f))
    return repr(f)


def envelope_values(lim: dict, mode: str | None) -> dict:
    md = lim.get("max_delta") or {}
    return {"mode": mode or lim.get("mode") or "ask-writes", "stop_by": str(lim.get("stop_by") or ""),
            "max_writes_per_plan": _int(lim.get("max_writes_per_plan")),
            "max_delta": {str(k): float(v) for k, v in sorted(md.items()) if _finite(v)},
            "stoploss_target": _int(lim.get("stoploss_target")), "stoploss_plan": _int(lim.get("stoploss_plan"))}


def _finite(v) -> bool:
    try:
        return math.isfinite(float(v))
    except (TypeError, ValueError):
        return False


def _canon(v) -> str:
    from quam_state_manager.core import run_terms
    return json.dumps(run_terms.canon(v), sort_keys=True, default=str)


def envelope_differences(seen: dict, now: dict) -> list[dict]:
    """What separates the envelope the person saw from the one SM would arm now. Empty = the same."""
    out = []
    for k in ENVELOPE_KEYS:
        a, b = (seen or {}).get(k), (now or {}).get(k)
        if _canon(a) != _canon(b):
            out.append({"field": k, "seen": a, "now": b})
    return out


def envelope(lim: dict, *, mode: str | None, since: float | None = None, dry_run: bool | None = None) -> dict:
    """The envelope a Start would approve, in words a person reads before leaving. Instants are
    epoch seconds (UTC); the page shows them in the viewer's zone with its offset."""
    vals = envelope_values(lim, mode)
    since = float(since or time.time())
    deadline = limits_mod.stop_deadline(lim, since)
    url = str(lim.get("webhook_url") or "")
    events = [str(e) for e in (lim.get("notify_events") or [])]
    lines = []
    sb = vals["stop_by"]
    lines.append({"key": "stop_by", "label": "stop by",
                  "text": (f"{sb} -- the first {sb} after Start; the arming ends there and the run in flight finishes"
                           if sb else "none -- it runs until the plan ends or a person stops it"),
                  "at": deadline})
    mw = vals["max_writes_per_plan"]
    lines.append({"key": "max_writes_per_plan", "label": "max writes",
                  "text": f"{mw} per plan -- a run whose writes would pass it is held" if mw > 0 else "no cap (0)"})
    md = vals["max_delta"]
    lines.append({"key": "max_delta", "label": "max |Δ|",
                  "text": ("; ".join(f"{fam} {_num(v)}" for fam, v in md.items())
                           + " (in the value's own unit) -- a bigger jump is held and its target stops")
                  if md else "none set -- no write is held for its size"})
    nt, np_ = vals["stoploss_target"], vals["stoploss_plan"]
    lines.append({"key": "stoploss", "label": "stop-loss",
                  "text": "; ".join([
                      (f"a target halts after {nt} gate fails in a row" if nt > 0 else "no per-target halt (0)"),
                      (f"the plan halts after {np_} gate fails" if np_ > 0 else "no plan halt (0)")])
                  + "; a held write halts its target at once"})
    lines.append({"key": "gate_fail", "label": "gate fail",
                  "text": "a run that failed (host unreachable, hardware busy, timeout, node error, interrupted), "
                          "a fit the node itself marked failed, or a write the envelope held"})
    lines.append({"key": "alerts", "label": "alerts",
                  "text": (f"webhook on -> {limits_mod._shown('webhook_url', url)} for {', '.join(events) or 'no event'}"
                           if url else "no webhook -- nothing reaches a phone (Setup -> Limits)")})
    warnings = []
    if dry_run and vals["mode"] == "auto":
        warnings.append("Dry run is ON: SM refuses every run of an auto plan (simulate_on_in_auto). Turn it off in "
                        "Agent setup first.")
    return {"values": vals, "deadline": deadline, "since": since, "lines": lines, "warnings": warnings,
            "webhook": {"on": bool(url), "where": limits_mod._shown("webhook_url", url) if url else None,
                        "events": events}}


def envelope_record(env: dict) -> dict:
    """What the plan keeps of the envelope its Start approved (the summary shows it after a restart)."""
    return {**env.get("values", {}), "deadline": env.get("deadline"), "since": env.get("since"),
            "webhook": (env.get("webhook") or {}).get("on"), "lines": env.get("lines")}


# ------------------------------------------------------------------ the morning summary

def _meta(instance_path, run_key: str | None) -> dict:
    if not run_key:
        return {}
    try:
        d = json.loads((Path(instance_path) / "agent_runs" / str(run_key) / "meta.json").read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _units(instance_path, live_folder) -> tuple[dict, int]:
    """``{gid: (index, unit)}`` from the chip's undo journal (docs/107), and its cursor."""
    if not live_folder:
        return {}, 0
    try:
        from quam_state_manager.core import undo_journal
        units, cursor = undo_journal.load_state(undo_journal.sidecar_path(instance_path, live_folder))
    except Exception:  # noqa: BLE001
        return {}, 0
    out = {}
    for i, u in enumerate(units):
        if u.get("gid"):
            out[str(u["gid"])] = (i, u)
    return out, int(cursor or 0)


def _value_now(current, path):
    if current is None:
        return None, False
    try:
        return current(path), True
    except Exception:  # noqa: BLE001
        return None, False


def _rows(writes: list[dict], current, cap: int = 12) -> tuple[list[dict], int]:
    out = []
    for w in (writes or [])[:cap]:
        now, known = _value_now(current, w.get("path"))
        out.append({"path": w.get("path"), "old": w.get("old"), "new": w.get("new"), "now": now, "now_known": known})
    return out, max(0, len(writes or []) - cap)


def _same(a, b) -> bool:
    return _canon(a) == _canon(b)


def summary(instance_path, key: str, *, plan_id: str | None = None, live_folder=None, current=None,
            lim: dict | None = None) -> dict:
    """The morning summary of ONE started plan (the newest, or ``plan_id``), read from the
    records SM keeps on disk -- the plan file, the approvals file, each run's meta, the undo
    journal -- so it reads the same after a restart. ``current(path)`` reads the value SM holds
    now (an applied write a person changed since is not offered an undo it would refuse)."""
    plans = agent_plans.load(instance_path, key)
    started = sorted((p for p in plans if p.get("started_at")), key=lambda p: float(p.get("started_at") or 0))
    plan = next((p for p in plans if p.get("id") == plan_id), None) if plan_id else (started[-1] if started else None)
    others = [{"id": p.get("id"), "title": p.get("title"), "status": p.get("status"),
               "started_at": p.get("started_at")} for p in reversed(started[-8:])]
    if plan is None:
        return {"plan": None, "plans": others}
    lim = lim or limits_mod.load(instance_path, key)
    units, cursor = _units(instance_path, live_folder)
    steps = sorted(plan.get("steps") or [], key=lambda s: (float(s.get("ended") or s.get("started") or 0),
                                                          int(s.get("i") or 0)))
    applied, failures = [], []
    for s in steps:
        meta = _meta(instance_path, s.get("run_key"))
        res = meta.get("result") or {}
        if s.get("applied"):
            u = units.get(f"agent:{s.get('run_key')}")
            writes = res.get("writes") or []
            undo = {"state": "unknown"}
            if u is not None:
                idx, unit = u
                writes = [{"path": e.get("path"), "old": e.get("old"), "new": e.get("new")}
                          for e in unit.get("entries") or []]
                if (unit.get("meta") or {}).get("reverted_by"):
                    undo = {"state": "reverted", "unit_id": unit.get("id")}
                elif idx >= cursor:
                    undo = {"state": "undone", "unit_id": unit.get("id")}
                else:
                    undo = {"state": "available", "unit_id": unit.get("id")}
            rows, more = _rows(writes, current)
            if undo["state"] == "available" and any(r["now_known"] and not _same(r["now"], r["new"]) for r in rows):
                undo["state"] = "changed"            # a later write moved it: the undo would be refused
            applied.append({"step": s.get("i"), "node": s.get("node"), "targets": s.get("targets"),
                            "run_id": s.get("run_id"), "run_key": s.get("run_key"), "at": s.get("ended"),
                            "n_writes": s.get("n_writes"), "writes": rows, "more": more, "undo": undo,
                            "pre_apply_ts": res.get("pre_apply_ts")})
        gate = s.get("gate") or {}
        fails = [(t, v) for t, v in gate.items() if (v or {}).get("v") == "fail"]
        if not gate and s.get("status") == "failed":
            fails = [(t, {"why": s.get("classification") or "failed"}) for t in s.get("targets") or []]
        for t, v in fails:
            cls = s.get("classification") or res.get("classification")
            if str((v or {}).get("why") or "").startswith("fit:"):
                cls = "fit_failed"
            failures.append({"step": s.get("i"), "node": s.get("node"), "target": t, "class": cls,
                             "what": (res.get("failure") or {}).get("what") or (v or {}).get("why"),
                             "why": (v or {}).get("why"), "run_id": s.get("run_id"), "at": s.get("ended")})
    held = []
    for a in approvals.load(instance_path, key):
        if a.get("plan_id") != plan.get("id") or a.get("status") == "expired":
            continue
        if a.get("kind") == "run" and a.get("status") != "pending":
            continue
        rows, more = _rows(a.get("writes") or [], current)
        held.append({"id": a.get("id"), "kind": a.get("kind"), "status": a.get("status"), "node": a.get("node"),
                     "targets": a.get("targets"), "params": a.get("params") or {}, "run_id": a.get("run_id"),
                     "step": a.get("step"), "why": a.get("why_held"), "created": a.get("created"),
                     "decided_by": a.get("decided_by"), "decided_at": a.get("decided_at"),
                     "writes": rows, "more": more, "n_writes": len(a.get("writes") or [])})
    held.sort(key=lambda h: (h["status"] != "pending", float(h.get("created") or 0)))
    halted = [{"target": t, "why": (h or {}).get("why"), "at": (h or {}).get("at"),
               "skipped": len((h or {}).get("skipped") or [])} for t, h in (plan.get("halted") or {}).items()]
    open_ = plan.get("status") in ("running", "stopping")
    end = None
    if not open_:
        reason, why = plan_end_reason(plan)
        env = plan.get("envelope") or {}
        end = {"reason": reason, "why": why, "at": plan.get("ended"), "status": plan.get("status"),
               "noticed_at": plan.get("end_at"),
               "deadline": env.get("deadline") if plan.get("end_code") == "past_stop_by" else None}
    return {"plan": {"id": plan.get("id"), "title": plan.get("title"), "status": plan.get("status"),
                     "mode": plan.get("mode"), "started_at": plan.get("started_at"),
                     "started_by": plan.get("started_by"), "envelope": plan.get("envelope"),
                     "open": open_, "pre_ts": plan.get("pre_ts")},
            "end": end, "counts": plan_counts(instance_path, key, plan, lim), "applied": applied,
            "held": held, "failures": failures, "halted": halted, "plans": others}
