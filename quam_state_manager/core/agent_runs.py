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

from quam_state_manager.core.loader import natural_key
from quam_state_manager.core import agent_session, approvals, limits as limits_mod, story

logger = logging.getLogger(__name__)

GATES = ("chip_mismatch", "no_env", "no_calibrations_folder", "node_not_found", "not_a_node",
         "stopped_by_human", "past_stop_by", "no_start_token", "run_active", "queue_not_empty", "awaiting_approval",
         "human_active", "orphan_running", "stale_live", "simulate_on_in_auto")
CLASSES = ("ok", "host_unreachable", "hardware_contention", "node_error", "timeout", "cancelled", "skipped",
           "unattributed", "interrupted")
DEFAULT_WAIT_S = 240.0
MAX_WRITES = 4000

# docs/249: what a failed run's text SAYS, read in a fixed order -- contention
# first (its own give-up is a TimeoutError), then reachability, then timeouts.
# Every literal below is quoted from the client source that prints it, so a
# class is a reading of the run's own words, never a guess about the hardware.
#
# hardware_contention -- another quantum machine holds the hardware:
#   qualang_tools/multi_user/multi_user_tools.py (qm_session, which opens with
#   close_other_machines=False and waits while the QOP answers "busy"):
#     msg = "cannot be used because it isn't shareable in other QM."   (QOP2)
#     msg_opx1000 = "Resources already locked"                         (QOP3)
#     raise TimeoutError(f"While waiting for QOP to free, reached timeout: {timeout}s")
#   qm/exceptions.py AnotherJobIsRunning: "Another job is running on the QM. Halt it first"
#   (the remaining alternatives are the pre-docs/249 contention wording, kept)
_CONTENTION_RE = re.compile(
    r"(isn't shareable in other QM|Resources already locked|While waiting for QOP to free"
    r"|another (?:job|program|qm) is (?:already )?(?:running|open)|qm is closed|quantum machine .* closed"
    r"|job queue is full|opx .* busy|controller .* in use|resources? .* in use)", re.I)
# qm_session logs this when it starts waiting, and "Opening QM" once the QOP
# frees; a wait that never opened is contention even when SM's own time limit
# is what ended the run
_BUSY_WAIT = "QOP is busy. Waiting for it to free up"
_BUSY_OPENED = "Opening QM"
# host_unreachable -- the node never reached a QM server at all:
#   qm/api/server_detector.py (qm-qua 1.2.6 .. 1.4.1, every env on this machine):
#     "Failed to detect to QuantumMachines server, failed to connect to {cluster_str}. "
#     "Tried connecting to {targets}."   -- logged with "Errors:\n{host}:{port}: {why}"
#   quam_builder .../qpu/base_quam.py connect():
#     raise ConnectionError(f"Failed to connect to Quantum Machines Manager: {e}")
#   qm/api/base_api.py (a gRPC error other than DEADLINE_EXCEEDED, i.e. the link dropped):
#     "Encountered connection error from QOP: details: {details}, status:  {status_code}"
#   plus the socket-level causes (refused / name not resolved / no route), and the
#   pre-docs/249 connect wording that used to be filed under contention
_UNREACHABLE_RE = re.compile(
    r"(Failed to detect to QuantumMachines server|Failed to connect to Quantum Machines Manager"
    r"|failed to connect to (?:qm|the qm|quantum machines)|could not connect to (?:qop|the qop|host)"
    r"|Encountered connection error from QOP|StatusCode\.UNAVAILABLE|grpc.*unavailable"
    r"|connection refused|actively refused|WinError 1006[015]|WinError 10051|Errno 111\b"
    r"|getaddrinfo failed|Name or service not known|nodename nor servname|Temporary failure in name resolution"
    r"|No route to host|Network is unreachable|Failed to establish a new connection"
    r"|httpx\.Connect(?:Error|Timeout)|httpcore\.Connect(?:Error|Timeout))", re.I)
# timeout -- something answered too slowly:
#   qm/api/base_api.py timeout_error_message(): "A timeout of {timeout} seconds was reached. ..."
#   qm/api/v2/job_api/job_api.py: "Job {id} did not reach any state of {state} within {timeout} seconds"
#   SM's own: scheduler "timed out after {n}s", run_node "timed out after {n}s (run_node timeout_s)"
_TIMEOUT_RE = re.compile(
    r"(timed out after \d+|A timeout of [\d.]+ seconds was reached|did not reach any state of .* within"
    r"|deadline exceeded|DEADLINE_EXCEEDED|timed out waiting for (?:the )?(?:opx|job|qm))", re.I)
# the run failed while CONNECTING (before any program reached the hardware), not mid-run
_CONNECT_PHASE_RE = re.compile(
    r"(Failed to detect to QuantumMachines server|Failed to connect to Quantum Machines Manager"
    r"|failed to connect to (?:qm|the qm|quantum machines)|could not connect to (?:qop|the qop|host)"
    r"|connection refused|actively refused|getaddrinfo failed|Name or service not known|nodename nor servname"
    r"|Temporary failure in name resolution|Failed to establish a new connection)", re.I)
_TRIED_RE = re.compile(r"Tried connecting to (\S+?)\.?(?:\s|$)")
_CLUSTER_RE = re.compile(r"failed to connect to cluster '([^']*)'")
_URL_HOST_RE = re.compile(r"host='([^']+)',\s*port=(\d+)")
# the per-target reason the qm detector logs, newest wording first
_CAUSES = (
    (re.compile(r"actively refused|connection refused|WinError 10061|Errno 111\b", re.I), "connection refused"),
    (re.compile(r"getaddrinfo failed|Name or service not known|nodename nor servname|name resolution"
                r"|Errno 11001|Errno -[23]\b", re.I), "the host name did not resolve"),
    (re.compile(r"No route to host|Network is unreachable|WinError 1005[01]|WinError 10065", re.I),
     "no network route to the host"),
    (re.compile(r"WinError 10060|timed out|ConnectTimeout", re.I), "no answer, timed out"),
    (re.compile(r"Server disconnected|RemoteProtocolError|status code [45]\d\d", re.I),
     "something answered there, but not a QM server"),
)


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
    chip_name: str | None = None                          # docs/249: the journal's name, kept in the run's meta


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
    # docs/249 (D-13/A-14): SM's OWN row from a run whose driver died with a previous SM process is
    # never a person's row. Still driving the OPX -> name it as the orphan it is; provably dead -> it
    # does not block (the run sweeps it out of the queue before it queues its own row)
    left = leftover_rows(queue_state, own_running=own_running)
    if left["orphan"]:
        o = left["orphan"][0]
        return {"refused": "orphan_running", "worker_pid": o.get("worker_pid"), "current": o.get("id"),
                "row": {"name": o.get("name"), "label": o.get("label"), "status": o.get("status"),
                        "run_key": o.get("run_key")},
                "how": f"`{o.get('name')}` from before SM restarted is still running (its node process, PID "
                       f"{o.get('worker_pid')}, is alive) and may be driving the OPX; SM will not collect what it "
                       "writes. Wait until it ends -- this clears itself then -- or ask the human to stop it"}
    dead_ids = {it.get("id") for it in left["dead"]}
    # review R1-C1: the chassis runs its queue FIFO -- a person's queued rows, or a leftover from a
    # previous life, would run FIRST under the click that authorized only the agent's node
    rows = [it for it in (queue_state.get("queue") or [])
            if it.get("enabled", True) and it.get("status") in ("queued", "running") and it.get("id") not in dead_ids]
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


def _own_row_run(it: dict) -> tuple[str, Path] | None:
    """``(run key, meta.json)`` when a queue row is one of run_node's own
    (its ``state_path`` is ``<instance>/agent_runs/<key>/quam_state``, which
    only :func:`make_scratch` creates), else None."""
    sp = it.get("state_path")
    if not sp:
        return None
    p = Path(str(sp))
    if p.name != "quam_state" or p.parent.parent.name != "agent_runs" or not p.parent.name:
        return None
    return p.parent.name, p.parent / "meta.json"


def _driver_gone(meta_path: Path) -> bool:
    """True when the run's own record says no driver owns it any more: the
    meta is not ``starting``/``running`` (a restart marks it ``interrupted``),
    or it is missing. A meta another live process is still driving reads
    ``running`` and is never treated as gone."""
    try:
        d = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return True
    return not (isinstance(d, dict) and d.get("status") in ("starting", "running"))


def leftover_rows(queue_state: dict, *, own_running: bool) -> dict:
    """SM's own queue rows left behind by a run whose driver is gone (docs/249,
    D-13): ``{"dead": [...], "orphan": [...]}``.

    *dead*: queued, or "running" with its node process provably gone -- it can
    never finish, and a later Start would RUN it first (``start()`` re-queues a
    stale running row). *orphan*: "running" while the persisted worker PID is
    still alive -- the node may still be driving the OPX. A person's rows, a
    row whose run is still driven, anything while this process's runner is
    live or another live SM window owns the run: never here."""
    out: dict = {"dead": [], "orphan": []}
    if own_running:
        return out
    from quam_state_manager.core import scheduler
    if scheduler.foreign_owner(queue_state) is not None:
        return out
    run = queue_state.get("run") or {}
    wp = run.get("worker_pid")
    for it in queue_state.get("queue") or []:
        if it.get("status") not in ("queued", "running"):
            continue
        own = _own_row_run(it)
        if own is None or not _driver_gone(own[1]):
            continue
        row = {**it, "run_key": own[0]}
        if it.get("status") == "running" and run.get("current_id") in (None, it.get("id")) \
                and agent_session.pid_alive(wp):
            out["orphan"].append({**row, "worker_pid": wp})
        else:
            out["dead"].append(row)
    return out


def sweep_leftover_rows(scope: str) -> list[dict]:
    """Remove SM's own dead rows (:func:`leftover_rows`) from the queue, under
    the queue lock, re-deciding there. Returns what was removed. An orphan --
    a live node process -- is never touched."""
    from quam_state_manager.core import scheduler
    with scheduler._QLOCK:
        st = scheduler.load_queue(scope)
        dead = leftover_rows(st, own_running=scheduler.is_running(scope))["dead"]
        if not dead:
            return []
        ids = {d.get("id") for d in dead}
        st["queue"] = [it for it in st.get("queue") or [] if it.get("id") not in ids]
        run = st.get("run") or {}
        if run.get("status") == "running" and run.get("current_id") in ids | {None}:
            # the run claim of a dead process over a row that is gone: what _reconcile_orphaned does
            run.update({"status": "idle", "current_id": None, "worker_pid": None, "owner_pid": None,
                        "owner_port": None, "message": "interrupted (worker stopped or app restarted)"})
        scheduler.save_queue(scope, st)
    return dead


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
    for path in sorted(set(a) | set(b), key=natural_key):
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


def _busy_wait_unresolved(text: str) -> bool:
    i = text.rfind(_BUSY_WAIT)
    return i >= 0 and text.find(_BUSY_OPENED, i) < 0


def classify(status: str, error: str | None, log_tail: str) -> str:
    """The run's failure class, read from its own words (docs/249). Order is
    the contract: contention (incl. its own TimeoutError) > host_unreachable >
    timeout > node_error."""
    text = f"{error or ''}\n{log_tail or ''}"
    if status == "done":
        return "ok"
    if status == "cancelled":
        return "cancelled"
    if status == "skipped":
        return "skipped"
    if _CONTENTION_RE.search(text) or _busy_wait_unresolved(text):
        return "hardware_contention"
    if _UNREACHABLE_RE.search(text):
        return "host_unreachable"
    if _TIMEOUT_RE.search(text) or (error and "timed out" in error):
        return "timeout"
    return "node_error"


def unreachable_detail(error: str | None, log_tail: str) -> dict:
    """Where the node tried to connect and why it failed, as far as the run's
    own text says -- ``{"target", "cluster", "cause"}``, each None when the
    text does not say (never invented)."""
    text = f"{error or ''}\n{log_tail or ''}"
    target = cluster = cause = None
    m = None
    for m in _TRIED_RE.finditer(text):
        pass
    if m is not None:
        target = m.group(1).rstrip(".")
    else:
        u = _URL_HOST_RE.search(text)
        if u:
            target = f"{u.group(1)}:{u.group(2)}"
    c = _CLUSTER_RE.search(text)
    if c:
        cluster = c.group(1)
    # the detector's own per-target line ("<host>:<port>: <why>") is the most
    # precise reason; otherwise any socket-level cause anywhere in the text
    scope = text
    if target:
        first = target.split(",")[0]
        lines = [ln for ln in text.splitlines() if ln.strip().startswith(first + ":")]
        if lines:
            scope = "\n".join(lines)
    for rx, why in _CAUSES:
        if rx.search(scope):
            cause = why
            break
    if cause is None and scope is not text:
        for rx, why in _CAUSES:
            if rx.search(text):
                cause = why
                break
    return {"target": target, "cluster": cluster, "cause": cause}


def failure_info(cls: str | None, error: str | None, log_tail: str) -> dict | None:
    """What failed and what to do about it, in words a person and an agent can
    act on (docs/249) -- ``{"what", "how", "retry"}`` (+ ``target``/``cluster``/
    ``cause`` for an unreachable host) -- or None when the class needs no advice.
    ``retry``: "no" (it fails the same way until a person acts), "after_check"
    (only once someone knows why), "after_it_ends" (the hardware is someone
    else's for now)."""
    if cls == "host_unreachable":
        d = unreachable_detail(error, log_tail)
        where = d["target"] or "the configured host"
        why = f" ({d['cause']})" if d["cause"] else ""
        clu = f", cluster '{d['cluster']}'" if d["cluster"] else ""
        text = f"{error or ''}\n{log_tail or ''}"
        if _CONNECT_PHASE_RE.search(text):
            if d["target"] or d["cause"]:
                what = f"QM host unreachable at {where}{why}"
            else:
                # the text names neither where nor why (a credentials file, a cloud client's own error):
                # quote it rather than claim an address
                first = str(error or "").strip().splitlines()[0][:200] if str(error or "").strip() else ""
                what = f"could not connect to the QM server: {first}" if first else "could not connect to the QM server"
            how = (f"the node could not reach the QM server at {where}{clu}{why}, so it never reached the "
                   "hardware and nothing was applied. Check the network and the chip's network config (host, port, "
                   "cluster_name). Do NOT retry until that is fixed -- it fails the same way; tell the human")
        else:
            what = f"lost the connection to the QM server{' at ' + d['target'] if d['target'] else ''}{why}"
            how = (f"the connection to the QM server dropped while the node ran{why}; nothing was applied. Check "
                   "the network and that the QM server is up. Do NOT retry until it answers again; tell the human")
        return {"what": what, "how": how, "retry": "no", **d}
    if cls == "hardware_contention":
        text = f"{error or ''}\n{log_tail or ''}"
        waited = re.search(r"reached timeout: (\d+)", text)
        what = "the hardware is busy: another quantum machine or job holds what this node needs"
        if waited:
            what += f" (waited {waited.group(1)}s for it to free)"
        how = ("the OPX is held elsewhere (another quantum machine, another user or session) -- hardware "
               "contention: do NOT retry now; tell the human, who decides when the hardware is free")
        return {"what": what, "how": how, "retry": "after_it_ends"}
    if cls == "timeout":
        first = str(error or "").strip().splitlines()[0][:200] if str(error or "").strip() else ""
        what = first if first.lower().startswith("timed out") else (f"timed out: {first}" if first else "timed out")
        how = ("the run hit a time limit; nothing was applied. Read the log tail for where it stalled before "
               "running it again, and tell the human if it repeats")
        return {"what": what, "how": how, "retry": "after_check"}
    if cls == "interrupted":
        what = str(error or "SM restarted while this run was in flight")
        how = ("SM restarted while this run was in flight; SM did not collect what it wrote, so nothing was "
               "applied. Tell the human; run it again only after they confirm the hardware is idle")
        return {"what": what, "how": how, "retry": "after_check"}
    return None


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

def interrupted_error(m: dict) -> str:
    """The honest sentence for a run a restart cut off (docs/249, A-14): its
    node process may have outlived SM and still be driving the OPX."""
    err = "SM restarted while this run was in flight"
    wp = m.get("worker_pid")
    if wp and agent_session.pid_alive(wp):
        err += (f"; its node process (PID {wp}) was still running at the restart and may still be driving the "
                "OPX -- SM will not collect what it writes")
    return err


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
                err = interrupted_error(m)
                m["status"] = "interrupted"
                m["ended"] = m.get("ended") or time.time()
                m["result"] = {"classification": "interrupted", "status": "failed", "applied": False,
                               "error": err, "writes": [], "failure": failure_info("interrupted", err, "")}
                self._write_meta(m)                     # written FIRST: the next restart never repeats what follows
                self._announce_interrupted(m, err)
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

    def _announce_interrupted(self, m: dict, err: str) -> None:
        """docs/249 (A-14): a run a restart cut off is said ONCE -- the session
        that was armed for it is disarmed (its run_key still names this run and
        nobody re-armed since it started), one journal line, one
        ``agent_failure`` webhook. Called only right after the meta was
        rewritten as ``interrupted``, so a second restart never repeats it."""
        inst, chip, key = self.instance_path, m.get("chip"), m.get("key")
        disarmed = False
        try:
            rec = agent_session.load(inst, chip) if chip else None
            if rec is not None and rec.get("run_key") == key:
                fields = {"run_key": None, "claimed_by_tool": None, "worker_pid": None}
                if rec.get("start_token") and float(rec.get("armed_at") or 0) <= float(m.get("since") or 0):
                    fields["start_token"] = None
                    disarmed = True
                agent_session.save(inst, chip, **fields)
        except Exception:  # noqa: BLE001
            logger.debug("interrupted-run session release failed", exc_info=True)
        targets = " ".join(m.get("targets") or []) or "(node defaults)"
        line = f"✗ ran `{m.get('node')}` on {targets} interrupted: {err}; nothing was applied"
        if disarmed:
            line += "; the session was disarmed -- a person arms it again"
        name = m.get("chip_name")
        if name:
            try:
                from quam_state_manager.core import journal as journal_mod
                journal_mod.append(inst, name, line, kind="sm")
            except Exception:  # noqa: BLE001
                logger.debug("interrupted-run journal line failed", exc_info=True)
        else:
            logger.info("interrupted run %s: no chip name in its record, journal line skipped", key)
        if chip:
            payload = {"node": m.get("node"), "targets": m.get("targets"), "error": err, "run_key": key,
                       "classification": "interrupted", "what": err, "disarmed": disarmed}

            def _post():
                try:
                    limits_mod.notify(inst, chip, "agent_failure", payload)
                except Exception:  # noqa: BLE001
                    logger.debug("interrupted-run webhook failed", exc_info=True)
            threading.Thread(target=_post, name=f"sm-run-interrupted-{key}", daemon=True).start()

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
                err = interrupted_error(d)
                d["status"] = "interrupted"
                d["result"] = {"classification": "interrupted", "error": err,
                               "failure": failure_info("interrupted", err, "")}
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
                "status": "starting", "since": time.time(), "result": None, "item_id": None,
                "chip_name": adapter.chip_name, "scope": adapter.scope}
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
            try:
                swept = sweep_leftover_rows(scope)       # docs/249 (D-13): SM's own dead rows never run first
            except Exception:  # noqa: BLE001
                logger.debug("leftover row sweep failed", exc_info=True)
                swept = []
            if swept:
                names = ", ".join(f"`{d.get('name')}`" for d in swept[:5])
                adapter.journal(f"removed {len(swept)} leftover row(s) from the run queue ({names}): SM's own, from "
                                "run(s) whose driver ended with a previous SM process", kind="sm")
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
                if wp and meta.get("worker_pid") != wp:
                    self._set(meta, worker_pid=wp)      # docs/249: a restart can tell a live orphan from a dead one
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
            if cancelled_why and "timed out" in cancelled_why and cls != "hardware_contention":
                # docs/249: SM's own limit ended it -- unless the node was still waiting for a busy QOP,
                # which is the honest cause of the wait
                cls = "timeout"
            result.update(status=status, error=error, classification=cls, log_tail=log_tail[-2000:])
            fail = failure_info(cls, error, log_tail) if status != "done" else None
            if fail:
                result["failure"] = fail
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
                elif result["classification"] == "host_unreachable":
                    line += f" — {(result.get('failure') or {}).get('what') or 'QM host unreachable'}; " \
                            "check the network / host config; not retried"
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
                                                 "classification": result["classification"],
                                                 "what": (result.get("failure") or {}).get("what")})
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
