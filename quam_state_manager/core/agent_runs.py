"""run_node (docs/173 S5): SM runs the node, so the author is certain.

The agent asks; SM decides (gates, as DATA), runs the node on the Experiment
Runner chassis against a SCRATCH COPY of the working copy, diffs what the
node wrote, and puts the writes through the one door -- applied at once in
``auto``, parked as an approval in ``ask-writes`` or when a Limit says so.
The run is attributed to the agent in ``agent_runs/index.jsonl`` (S1's one
certain attribution), the journal gets the line with the agent's reason,
and the pill wakes once.

This module never imports Flask. The web layer hands it a :class:`RunAdapter`
whose callables run under an app context.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from quam_state_manager.core import agent_session, approvals, limits as limits_mod, story

logger = logging.getLogger(__name__)

GATES = ("chip_mismatch", "no_env", "no_calibrations_folder", "node_not_found", "not_a_node",
         "stopped_by_human", "past_stop_by", "no_start_token", "run_active", "queue_not_empty", "awaiting_approval",
         "human_active", "orphan_running", "stale_live", "simulate_on_in_auto")
CLASSES = ("ok", "hardware_contention", "node_error", "timeout", "cancelled", "skipped", "unattributed")
DEFAULT_WAIT_S = 240.0
MAX_WRITES = 4000
# the OPX/qm signatures a node prints when the instrument is held by someone
# else -- a first cut from the qm client's own messages; a hit is never retried
_HARDWARE_RE = re.compile(
    r"(failed to connect to (?:qm|the qm|quantum machines)|connection refused|could not connect to (?:qop|the qop|host)"
    r"|another (?:job|program|qm) is (?:already )?(?:running|open)|qm is closed|quantum machine .* closed"
    r"|job queue is full|opx .* busy|controller .* in use|resource .* in use|grpc.*(?:unavailable|deadline exceeded)"
    r"|timed out waiting for (?:the )?(?:opx|job|qm))", re.I)


# ------------------------------------------------------------------ model

@dataclass
class RunRequest:
    node: str
    targets: list[str]
    params: dict = field(default_factory=dict)
    reason: str = ""
    timeout_s: float | None = None
    plan_id: str | None = None
    actor: str = "by_agent"
    approval_id: str | None = None
    session_id: str | None = None
    step: int | None = None


@dataclass
class RunAdapter:
    """What the engine needs from the app, captured once per request."""
    instance_path: str
    chip: str
    scope: str
    live_folder: str
    working_folder: str
    settings: Callable[[], dict]
    human_recent: Callable[[float], dict | None]
    list_runs: Callable[[], list[dict]]
    stage: Callable[[list[dict], str, str, str | None, bool], dict]   # writes, gid, actor, plan_id, apply -> result
    journal: Callable[..., None]
    wake: Callable[[], None]
    set_lock: Callable[[dict | None], None]
    notify: Callable[[str, dict], None]
    queue_state: Callable[[], dict]
    own_runner_alive: Callable[[], bool]
    live_diverged: Callable[[], Any] | None = None       # review R1-M5: the chip moved outside SM?


# ------------------------------------------------------------------ pure

def _norm(name: str) -> str:
    return story._norm(name or "")


def resolve_node(folder: str | None, node: str, *, instance_path=None):
    """The node file for a name the agent typed: exact name, else the file
    stem, else a unique normalized-prefix match. None when nothing matches."""
    from quam_state_manager.core import node_scan
    if not folder or not Path(folder).is_dir():
        return None, []
    infos = [i for i in node_scan.scan_folder(folder, instance_path=instance_path) if i.error is None]
    want = _norm(node)
    want_stem = _norm(Path(node).stem)
    for i in infos:
        if i.name == node or Path(i.file).stem == node or Path(i.file).name == node:
            return i, infos
    for i in infos:
        if _norm(i.name) == want or _norm(Path(i.file).stem) == want_stem:
            return i, infos
    hits = [i for i in infos if _norm(i.name).startswith(want) or _norm(Path(i.file).stem).startswith(want)]
    if len(hits) == 1:
        return hits[0], infos
    return None, infos


def run_mode(plan: dict | None, session: dict | None, lim: dict) -> str:
    """The mode a run obeys: the RUNNING plan's (auto is per plan -- the user's
    decision, review R1-M4), else the session's, else the chip's default."""
    return (plan or {}).get("mode") or (session or {}).get("mode") or lim.get("mode") or "ask-writes"


def check_gates(req: RunRequest, *, session: dict | None, lim: dict, settings: dict, pending: list[dict],
                human: dict | None, queue_state: dict, own_running: bool, run_active: dict | None,
                node_info, available: list, now: float | None = None, plan: dict | None = None,
                live_diverged: bool | None = None) -> dict | None:
    """The refusal, as data, or None. Order matters: the cheapest, most
    permanent reasons first; a refusal names what would clear it."""
    now = now or time.time()
    mode = run_mode(plan, session, lim)
    if not settings.get("env_python"):
        return {"refused": "no_env", "how": "pick the Python environment in Experiment Runner settings (Agent setup, S7)"}
    if not settings.get("calibrations_folder"):
        return {"refused": "no_calibrations_folder", "how": "set the calibrations folder in Experiment Runner settings"}
    if node_info is None:
        names = sorted({i.name for i in available})[:40]
        return {"refused": "node_not_found", "node": req.node, "available": names,
                "how": "name a node from `available` (the calibrations folder's own files)"}
    if getattr(node_info, "kind", None) != "node" or not getattr(node_info, "has_hook", False):
        return {"refused": "not_a_node", "node": node_info.name, "kind": getattr(node_info, "kind", None),
                "how": "SM runs nodes with the custom_param hook (targets and simulate are set through it); "
                       "a graph or a hookless file is run by a human from the QUAlibrate GUI"}
    if agent_session.stopped(session):
        st = (session or {}).get("agent_stop") or {}
        return {"refused": "stopped_by_human", "by": st.get("who"), "at": st.get("at"), "stop_mode": st.get("mode"),
                "how": "stop now: tell the human what you did and why; the human clears it by sending a new message"}
    if limits_mod.past_stop_by(lim, datetime.fromtimestamp(now)):
        return {"refused": "past_stop_by", "stop_by": lim.get("stop_by"),
                "how": "the lab's stop time for tonight has passed; summarize and stop"}
    if not (session or {}).get("start_token"):
        return {"refused": "no_start_token",
                "how": "rule 0: hardware starts only by a human click. Ask the human to press Arm on this session "
                       "in the SM window (Agent home / the pill), then call run_node again"}
    if run_active:
        return {"refused": "run_active", "run": {k: run_active.get(k) for k in ("key", "node", "targets", "since")},
                "how": "one node at a time on one chip; call run_wait on that key"}
    # review R1-C1: the chassis runs its queue FIFO -- a person's queued rows, or a leftover from a
    # previous life, would run FIRST under the click that authorized only the agent's node
    rows = [it for it in (queue_state.get("queue") or [])
            if it.get("enabled", True) and it.get("status") in ("queued", "running")]
    if rows:
        return {"refused": "queue_not_empty",
                "rows": [{"name": it.get("name"), "label": it.get("label"), "status": it.get("status")} for it in rows[:10]],
                "how": "the Experiment Runner queue holds rows that would run before yours (a person's, or a leftover); "
                       "a human clears them first (POST /api/agent/queue/clear, or the Experiment Runner page)"}
    mine = set(req.targets)
    blocking = [approvals.summary(a) for a in pending
                if a.get("kind") != "run" and (set(a.get("targets") or []) & mine or not a.get("targets"))]
    if mode == "ask-all":
        ap = None
        if req.approval_id:
            ap = next((a for a in pending if a.get("id") == req.approval_id), None) or None
        # an approved run record is not pending any more: the caller passes it explicitly
        if not req.approval_id:
            return {"refused": "awaiting_approval", "needs": "run", "mode": mode,
                    "how": "mode ask-all: every run needs a human's approval first. SM has filed the request; "
                           "call run_node again with approval_id once the human approves", "file_request": True}
        if ap is not None:
            return {"refused": "awaiting_approval", "needs": "run", "approval": approvals.summary(ap),
                    "how": "still pending"}
    if blocking:
        return {"refused": "awaiting_approval", "needs": "writes", "approvals": blocking,
                "how": "writes from an earlier run on these targets wait for the human; the chain on those targets "
                       "stops here (mode ask-writes). Run other targets, or tell the human"}
    if human is not None:
        return {"refused": "human_active", "run": human, "window_min": lim.get("human_recent_min"),
                "how": "a person (or a session SM cannot see) ran a node on this chip minutes ago; "
                       "the OPX is theirs. Wait, or ask them"}
    run = queue_state.get("run") or {}
    from quam_state_manager.core import scheduler
    owner = scheduler.foreign_owner(queue_state)
    if owner is not None:
        return {"refused": "orphan_running", "owner": {"pid": owner[0], "port": owner[1]},
                "how": "another SM window owns the Experiment Runner on this chip"}
    if run.get("status") == "running" and (own_running or agent_session.pid_alive(run.get("worker_pid"))):
        return {"refused": "orphan_running", "worker_pid": run.get("worker_pid"), "current": run.get("current_id"),
                "how": "a node is already running on this chip (the Experiment Runner queue or a previous "
                       "session's worker); a human must confirm the OPX is free (Experiment Runner → Start clears it)"}
    if live_diverged:
        # review R1-M5: the node would measure against a state the chip no longer holds, and the card's
        # "old" would lie
        return {"refused": "stale_live",
                "how": "the live files moved outside SM since the last sync: call take_live (empty tray) or ask the "
                       "human to take live in the window, then run_node again"}
    if settings.get("global_simulate", True) and mode == "auto":
        return {"refused": "simulate_on_in_auto",
                "how": "Dry run is ON in Experiment Runner settings: in auto mode a plan would report calibrated "
                       "values that never touched hardware. A human turns Dry run off, or runs in ask-writes"}
    return None


def make_scratch(instance_path, key: str, working_folder: str) -> tuple[Path, Path]:
    """``instance/agent_runs/<key>/quam_state`` (what the node reads and
    writes) + ``before/`` (the same bytes, kept for the diff)."""
    root = Path(instance_path) / "agent_runs" / key
    live = root / "quam_state"
    before = root / "before"
    for d in (live, before):
        d.mkdir(parents=True, exist_ok=True)
        for name in ("state.json", "wiring.json"):
            src = Path(working_folder) / name
            if src.exists():
                shutil.copyfile(src, d / name)
    return live, before


def _merged(folder: Path) -> dict:
    out: dict = {}
    for name in ("state.json", "wiring.json"):
        p = folder / name
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict):
            out.update(d)
    return out


def diff_states(before_folder: Path, after_folder: Path, *, cap: int = MAX_WRITES) -> tuple[list[dict], bool]:
    """Leaf writes the node made: ``[{path, old, new, created?, deleted?}]``,
    dot paths in SM's own grammar. Pointers compare verbatim."""
    from quam_state_manager.core import json_diff
    a, _ = json_diff.flatten(_merged(before_folder), cap=250_000)
    b, _ = json_diff.flatten(_merged(after_folder), cap=250_000)
    out: list[dict] = []
    truncated = False
    for path in sorted(set(a) | set(b)):
        if path in a and path in b:
            if a[path] == b[path] and type(a[path]) is type(b[path]):
                continue
            if _both_nan(a[path], b[path]):
                continue
            out.append({"path": path, "old": a[path], "new": b[path]})
        elif path in b:
            out.append({"path": path, "old": None, "new": b[path], "created": True})
        else:
            out.append({"path": path, "old": a[path], "new": None, "deleted": True})
        if len(out) >= cap:
            truncated = True
            break
    return out, truncated


def _both_nan(x, y) -> bool:
    try:
        return isinstance(x, float) and isinstance(y, float) and x != x and y != y
    except Exception:  # noqa: BLE001
        return False


def classify(status: str, error: str | None, log_tail: str) -> str:
    text = f"{error or ''}\n{log_tail or ''}"
    if status == "done":
        return "ok"
    if status == "cancelled":
        return "cancelled"
    if status == "skipped":
        return "skipped"
    if _HARDWARE_RE.search(text):
        return "hardware_contention"
    if error and "timed out" in error:
        return "timeout"
    return "node_error"


def resized_lists(writes: list[dict]) -> set[str]:
    """The list paths whose SHAPE changed: a created or deleted leaf under a
    numeric segment (review R1-M6)."""
    out: set[str] = set()
    for w in writes:
        if not (w.get("created") or w.get("deleted")):
            continue
        parts = str(w.get("path") or "").split(".")
        for i, seg in enumerate(parts):
            if seg.isdigit() and i > 0:
                out.add(".".join(parts[:i]))
                break
    return out


def family_key(node_name: str) -> str | None:
    try:
        from quam_state_manager.core.autofit import families
        fam = families.family_for(node_name)
        return getattr(fam, "key", None) if fam else None
    except Exception:  # noqa: BLE001
        return None


def limits_hold(lim: dict, fam: str | None, writes: list[dict], plan_writes_so_far: int) -> str | None:
    """Why the writes must wait for a human even in auto mode, or None."""
    cap = lim.get("max_writes_per_plan")
    if isinstance(cap, int) and cap > 0 and plan_writes_so_far + len(writes) > cap:
        return f"max_writes_per_plan {cap} exceeded ({plan_writes_so_far + len(writes)})"
    for w in writes:
        if limits_mod.delta_exceeds(lim, fam, w.get("old"), w.get("new")):
            return f"max_delta for {fam}: `{w['path']}` {w.get('old')} -> {w.get('new')}"
    return None


# --------------------------------------------------------------- registry

class Registry:
    """Every run_node this process started, by key; ``instance/agent_runs/<key>/meta.json``
    mirrors it so a restart can still answer run_wait honestly."""

    def __init__(self, instance_path):
        self.instance_path = str(instance_path)
        self.runs: dict[str, dict] = {}
        self.plan_writes: dict[str, int] = {}
        self._cv = threading.Condition()
        self._scan_metas()

    def _scan_metas(self, keep: int = 50) -> None:
        """review R3-7: after a restart the runs on disk are the record -- a run
        that was in flight is INTERRUPTED (its plan step failed), the recent
        finished ones stay visible on the cards."""
        root = Path(self.instance_path) / "agent_runs"
        metas = []
        try:
            for p in root.glob("*/meta.json"):
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(d, dict) and d.get("key"):
                    metas.append(d)
        except OSError:
            return
        metas.sort(key=lambda m: float(m.get("since") or 0))
        for m in metas[-keep:]:
            if m.get("status") in ("starting", "running"):
                m["status"] = "interrupted"
                m["ended"] = m.get("ended") or time.time()
                m["result"] = {"classification": "node_error", "status": "failed", "applied": False,
                               "error": "SM restarted while this run was in flight", "writes": []}
                self._write_meta(m)
                if m.get("plan_id"):
                    try:
                        from quam_state_manager.core import agent_plans
                        rec = agent_plans.get(self.instance_path, m.get("chip"), m["plan_id"])
                        if rec is not None and rec.get("status") in ("running", "stopping"):
                            st = agent_plans.step_for(rec, step=None, node=m.get("node"), targets=m.get("targets"))
                            if st is not None:
                                agent_plans.step_update(self.instance_path, m.get("chip"), m["plan_id"], st["i"],
                                                        status="failed", error="SM restarted while this step ran",
                                                        ended=time.time())
                            agent_plans.stop(self.instance_path, m.get("chip"), m["plan_id"], who="sm",
                                             how="interrupted by an SM restart")
                    except Exception:  # noqa: BLE001
                        logger.debug("plan interrupt failed", exc_info=True)
            self.runs[m["key"]] = m

    def active_for(self, chip: str) -> dict | None:
        with self._cv:
            for m in self.runs.values():
                if m.get("chip") == chip and m.get("status") in ("starting", "running"):
                    return m
        return None

    def _write_meta(self, meta: dict) -> None:
        try:
            p = Path(self.instance_path) / "agent_runs" / meta["key"] / "meta.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(meta, default=str), encoding="utf-8")
            os.replace(tmp, p)
        except OSError:
            logger.debug("run meta write failed", exc_info=True)

    def _set(self, meta: dict, **fields) -> None:
        with self._cv:
            meta.update(fields)
            meta["updated"] = time.time()
            self._cv.notify_all()
        self._write_meta(meta)

    def get(self, key: str) -> dict | None:
        with self._cv:
            m = self.runs.get(key)
        if m is not None:
            return m
        try:
            p = Path(self.instance_path) / "agent_runs" / key / "meta.json"
            d = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(d, dict) and d.get("status") in ("starting", "running"):
                d["status"] = "interrupted"
                d["result"] = {"classification": "node_error", "error": "SM restarted while this run was in flight"}
            return d if isinstance(d, dict) else None
        except (OSError, ValueError):
            return None

    def wait(self, key: str, wait_s: float) -> dict | None:
        deadline = time.monotonic() + max(0.0, float(wait_s))
        with self._cv:
            m = self.runs.get(key)
            if m is None:
                return self.get(key)
            while m.get("status") in ("starting", "running"):
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                self._cv.wait(timeout=min(left, 5.0))
            return dict(m)

    def start(self, req: RunRequest, adapter: RunAdapter, *, node_info, session: dict | None, lim: dict) -> dict:
        key = datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        meta = {"key": key, "chip": adapter.chip, "node": node_info.name, "file": node_info.file,
                "targets": list(req.targets), "params": dict(req.params or {}), "reason": req.reason,
                "plan_id": req.plan_id, "actor": req.actor, "session_id": req.session_id,
                "status": "starting", "since": time.time(), "result": None, "item_id": None}
        with self._cv:
            # review R1-M3: the gate's answer and this registration are one step under the lock
            for m in self.runs.values():
                if m.get("chip") == adapter.chip and m.get("status") in ("starting", "running"):
                    raise RuntimeError("run_active")
            self.runs[key] = meta
        self._write_meta(meta)
        t = threading.Thread(target=self._drive, args=(meta, req, adapter, node_info, session, lim),
                             name=f"sm-run-node-{key}", daemon=True)
        t.start()
        return meta

    # ------------------------------------------------------------ driver
    def _drive(self, meta: dict, req: RunRequest, adapter: RunAdapter, node_info, session: dict | None, lim: dict) -> None:
        from quam_state_manager.core import scheduler
        key = meta["key"]
        inst, scope, chip = self.instance_path, adapter.scope, adapter.chip
        settings = adapter.settings()
        timeout_s = req.timeout_s or settings.get("default_timeout_s") or 3600
        item_id = None
        window_start = time.time()
        simulated = bool(settings.get("global_simulate", True))
        plan = None
        if req.plan_id:
            try:
                from quam_state_manager.core import agent_plans
                plan = agent_plans.get(inst, chip, req.plan_id)
            except Exception:  # noqa: BLE001
                plan = None
        result: dict = {"classification": None, "status": None, "error": None, "run_id": None, "writes": [],
                        "applied": False, "approval": None, "group_id": None, "unstaged": [], "log_tail": "",
                        "simulated": simulated, "mode": run_mode(plan, session, lim)}
        try:
            adapter.set_lock({"key": key, "node": node_info.name, "since": window_start, "actor": req.actor,
                              "targets": list(req.targets)})
            live, before = make_scratch(inst, key, adapter.working_folder)
            item = scheduler.add_item(scope, {
                "file": node_info.file, "name": node_info.name, "kind": node_info.kind,
                "has_hook": node_info.has_hook, "targets_name": node_info.targets_name,
                "param_overrides": dict(req.params or {}),
                "label": f"agent: {node_info.name} ({req.actor})", "state_path": str(live),
                "baseline_path": str(before),  # docs/174: normalize the diff baseline
            }, targets=list(req.targets))
            item_id = item["id"]
            meta["item_id"] = item_id
            try:
                scheduler.start(scope)
            except scheduler.ForeignRunnerError as exc:
                self._remove_item(scope, item_id)
                result.update(status="refused", classification="node_error",
                              error=f"orphan_running: {exc}")
                return
            self._set(meta, status="running")
            agent_session.save(inst, chip, claimed_by_tool="run_node", run_key=key)
            self._plan_step(req, chip, node_info, status="running", run_key=key, started=window_start)
            adapter.journal(f"running `{node_info.name}` on {' '.join(req.targets) or '(node defaults)'}",
                            kind="agent", reason=req.reason or None)
            adapter.wake()
            # ---- the wait: heartbeat every second, Stop now -> cancel, per-call timeout -> cancel
            status, error = "running", None
            cancelled_why = None
            while True:
                scheduler.touch_ui(scope)
                with scheduler._QLOCK:          # the worker rewrites the file under this lock; an
                    st = scheduler.load_queue(scope)   # unlocked read mid-replace comes back BLANK on Windows
                it = scheduler._find(st, item_id)
                wp = (st.get("run") or {}).get("worker_pid")
                if wp and session is not None and (session.get("worker_pid") != wp):
                    session["worker_pid"] = wp
                    agent_session.save(inst, chip, worker_pid=wp)
                if it is None:
                    status, error = "failed", "the queue item vanished"
                    break
                if it.get("status") in ("done", "failed", "cancelled", "skipped"):
                    status, error = it["status"], it.get("error")
                    break
                rec = agent_session.load(inst, chip)
                stop = (rec or {}).get("agent_stop") or {}
                if stop and stop.get("mode") == "now" and cancelled_why is None:
                    cancelled_why = f"stopped now by {stop.get('who')}"
                    scheduler.cancel(scope)
                if time.time() - window_start > float(timeout_s) and cancelled_why is None:
                    cancelled_why = f"timed out after {int(timeout_s)}s (run_node timeout_s)"
                    scheduler.cancel(scope)
                if cancelled_why and it.get("status") == "queued":
                    # review R1-C2: cancel never touches a QUEUED item -- the worker will not run it, so
                    # waiting for a terminal status would wait forever
                    status, error = "cancelled", cancelled_why
                    break
                if not scheduler.is_running(scope):
                    # review R1-C2: the worker stopped (a row ahead failed and the queue paused, or it died):
                    # a queued item of ours will never run -- never hold the chip for it
                    status = "failed"
                    error = ("the worker died mid-run" if it.get("status") == "running"
                             else "the runner stopped before this item ran (queue paused after another row?)")
                    break
                time.sleep(1.0)
            agent_session.save(inst, chip, worker_pid=None)
            log_tail = ""
            try:
                log_tail = scheduler.tail_log(scope, item_id, max_bytes=6000)
            except Exception:  # noqa: BLE001
                pass
            self._remove_item(scope, item_id)
            if cancelled_why and status != "done":
                status = "cancelled" if "stopped" in cancelled_why else "failed"
                error = cancelled_why
            cls = classify(status, error, log_tail)
            if cancelled_why and "timed out" in cancelled_why:
                cls = "timeout"
            result.update(status=status, error=error, classification=cls, log_tail=log_tail[-2000:])
            # ---- what the node wrote
            writes, truncated = diff_states(before, live)
            result["writes"] = writes
            result["writes_truncated"] = truncated
            # ---- which run it was
            run = _attribute(adapter, node_info.name, window_start)
            if run is not None:
                result["run_id"] = run.get("run_id")
                result["run"] = run
            elif status == "done":
                result["classification"] = "unattributed" if cls == "ok" else cls
            # ---- the writes, through the door
            if writes and status == "done":
                self._route_writes(meta, req, adapter, lim, writes, truncated, result, node_info, session, plan)
            elif writes:
                # a failed/cancelled node that still wrote state: never staged on its own
                result["unstaged"] = [{"path": w["path"], "why": f"run {status}"} for w in writes[:50]]
            # ---- the record
            try:
                story.record_agent_run(inst, {
                    "run_id": result.get("run_id"), "chip": chip, "node": node_info.name,
                    "targets": list(req.targets), "actor": req.actor, "plan_id": req.plan_id,
                    "ts": time.time(), "key": key, "group_id": result.get("group_id"),
                    "outcome": status, "classification": result["classification"],
                    "session_id": req.session_id, "reason": req.reason,
                    "n_writes": len(writes), "applied": result.get("applied"),
                    "approval": (result.get("approval") or {}).get("id"), "simulated": simulated,
                    "mode": result.get("mode")})
            except Exception:  # noqa: BLE001
                logger.debug("record_agent_run failed", exc_info=True)
            # ---- the journal line
            rid = result.get("run_id")
            head = f"ran `{node_info.name}` on {' '.join(req.targets) or '(node defaults)'}"
            if rid:
                head += f" → #{rid}"
            if status == "done":
                if result.get("applied"):
                    tail = f"{len(writes)} write(s) applied"
                elif result.get("approval"):
                    tail = f"{len(writes)} write(s) waiting for approval ({result.get('why_held')})"
                elif writes:
                    tail = f"{len(writes)} write(s) not staged"
                else:
                    tail = "no writes"
                line = f"{head} ({tail})"
            else:
                line = f"✗ {head} {status}" + (f": {str(error)[:200]}" if error else "")
                if result["classification"] == "hardware_contention":
                    line += " — hardware contention (the OPX is held elsewhere); not retried"
            adapter.journal(line, kind="agent", reason=None, run_id=rid,
                            paths=[w["path"] for w in writes[:20]])
            self._plan_step(req, chip, node_info, status="done" if status == "done" else
                            ("cancelled" if status == "cancelled" else ("skipped" if status == "skipped" else "failed")),
                            run_key=key, run_id=result.get("run_id"), outcome=status,
                            classification=result["classification"], n_writes=len(writes),
                            applied=result.get("applied"), approval=(result.get("approval") or {}).get("id"),
                            error=str(error)[:300] if error else None, ended=time.time())
            self._plan_end_restore(req, chip, lim)
            if status != "done":
                adapter.notify("agent_failure", {"node": node_info.name, "targets": req.targets, "error": error,
                                                 "classification": result["classification"]})
            elif result.get("approval"):
                adapter.notify("needs_human", {"node": node_info.name, "approval": result["approval"],
                                               "why": result.get("why_held")})
        except Exception as exc:  # noqa: BLE001
            logger.exception("run_node driver crashed")
            result.update(status="failed", classification="node_error", error=f"driver error: {exc}")
            if item_id:
                self._remove_item(scope, item_id)
        finally:
            try:
                agent_session.save(inst, chip, worker_pid=None)
            except Exception:  # noqa: BLE001
                pass
            adapter.set_lock(None)
            self._set(meta, status="done" if result.get("status") == "done" else "ended", result=result,
                      ended=time.time())
            try:
                adapter.wake()
            except Exception:  # noqa: BLE001
                pass

    def _route_writes(self, meta, req, adapter, lim, writes, truncated, result, node_info, session, plan=None) -> None:
        inst, chip = self.instance_path, adapter.chip
        mode = run_mode(plan, session, lim)
        fam = family_key(node_info.name)
        plan_key = req.plan_id or f"session:{req.session_id or 'none'}"
        why = None
        resized = resized_lists(writes)
        if truncated:
            why = f"more than {MAX_WRITES} leaves changed"
        elif resized:
            # review R1-M6: a list that changed SHAPE cannot be staged leaf by leaf -- the overlapping cells
            # would land as a matrix that never existed
            why = f"list resized at {', '.join(sorted(resized)[:3])} -- SM stages leaves, not a new shape; " \
                  "apply the run's state from Datasets → Apply to chip"
        elif mode != "auto":
            why = f"mode {mode}"
        else:
            why = limits_hold(lim, fam, writes, self.plan_writes.get(plan_key, 0))
        if result.get("simulated"):
            # review R1-M8: a value from a simulated run is never a calibration
            why = "DRY RUN values (simulate ON in the run environment)" + (f"; {why}" if why else "")
        gid = f"agent:{meta['key']}"
        if why:
            ap = approvals.add(inst, chip, kind="writes", node=node_info.name, targets=req.targets, writes=writes,
                               reason=req.reason, why_held=why, actor=req.actor, plan_id=req.plan_id,
                               run_key=meta["key"], run_id=result.get("run_id"), params=req.params)
            result["approval"] = approvals.summary(ap)
            result["why_held"] = why
            return
        out = adapter.stage(writes, gid, req.actor, req.plan_id, True)
        result["group_id"] = out.get("group_id") or gid
        result["applied"] = bool(out.get("applied"))
        result["unstaged"] = out.get("unstaged") or []
        result["apply_error"] = out.get("error")
        if result["applied"]:
            self.plan_writes[plan_key] = self.plan_writes.get(plan_key, 0) + len(writes)
        elif out.get("saved_in_working_copy"):
            result["why_held"] = f"apply refused after the save: {out.get('error')}"
        elif out.get("error"):
            # the door refused (a human edit, stale live): park the writes for the human instead
            ap = approvals.add(inst, chip, kind="writes", node=node_info.name, targets=req.targets, writes=writes,
                               reason=req.reason, why_held=f"apply refused: {out.get('error')}", actor=req.actor,
                               plan_id=req.plan_id, run_key=meta["key"], run_id=result.get("run_id"),
                               params=req.params)
            result["approval"] = approvals.summary(ap)
            result["why_held"] = f"apply refused: {out.get('error')}"

    def _plan_end_restore(self, req: RunRequest, chip: str, lim: dict) -> None:
        """review R1-M4: the plan's mode is the PLAN's. When it ends, the session
        falls back to the chip's default so the next Arm never inherits auto."""
        if not req.plan_id:
            return
        try:
            from quam_state_manager.core import agent_plans
            rec = agent_plans.get(self.instance_path, chip, req.plan_id)
            if rec is not None and rec.get("status") in ("done", "failed", "stopped", "cancelled", "skipped"):
                agent_session.save(self.instance_path, chip, mode=lim.get("mode") or "ask-writes")
        except Exception:  # noqa: BLE001
            logger.debug("plan end restore failed", exc_info=True)

    def _plan_step(self, req: RunRequest, chip: str, node_info, **fields) -> None:
        """Report to the plan card (SM's own record of progress, docs/173 S6)."""
        if not req.plan_id:
            return
        try:
            from quam_state_manager.core import agent_plans
            rec = agent_plans.get(self.instance_path, chip, req.plan_id)
            if rec is None or rec.get("status") not in ("running", "stopping"):
                return                                  # a draft or a closed plan never moves
            st = agent_plans.step_for(rec, step=req.step, node=node_info.name, targets=req.targets)
            if st is None:
                return
            agent_plans.step_update(self.instance_path, chip, req.plan_id, st["i"], **fields)
        except Exception:  # noqa: BLE001
            logger.debug("plan step update failed", exc_info=True)

    @staticmethod
    def _remove_item(scope: str, item_id: str) -> None:
        from quam_state_manager.core import scheduler
        try:
            with scheduler._QLOCK:
                st = scheduler.load_queue(scope)
                st["queue"] = [i for i in st["queue"] if i.get("id") != item_id or i.get("status") == "running"]
                scheduler.save_queue(scope, st)
        except Exception:  # noqa: BLE001
            logger.debug("agent queue-item cleanup failed", exc_info=True)


def _attribute(adapter: RunAdapter, node_name: str, window_start: float, *, poll_s: float = 6.0) -> dict | None:
    """The newest run whose name is the node's and whose time is inside the
    window -- exact provenance, like autofit's (docs/78 §7b-B2); a bounded
    re-poll while the writeback lands. ``list_runs() is None`` = no dataset
    store for this chip at all: nothing to poll for."""
    want = _norm(node_name)
    deadline = time.monotonic() + poll_s
    while True:
        try:
            rows = adapter.list_runs()
        except Exception:  # noqa: BLE001
            logger.debug("list_runs failed", exc_info=True)
            rows = []
        if rows is None:
            return None
        for row in rows:
            name = _norm(row.get("experiment_name") or "")
            if not name.startswith(want):
                continue
            try:
                when = datetime.strptime(f"{row.get('date')} {row.get('time')}", "%Y-%m-%d %H:%M:%S").timestamp()
            except (TypeError, ValueError):
                continue
            if when < window_start - 5:
                continue
            return {"run_id": row.get("run_id"), "experiment_name": row.get("experiment_name"),
                    "outcomes": row.get("outcomes"), "qubits": row.get("qubits"), "status": row.get("status"),
                    "date": row.get("date"), "time": row.get("time")}
        if time.monotonic() > deadline:
            return None
        time.sleep(1.0)
