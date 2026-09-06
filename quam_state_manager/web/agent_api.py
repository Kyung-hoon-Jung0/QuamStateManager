"""The JSON door a terminal agent uses (docs/172).

SM is the agent's eyes and hands: every read here comes from the same store
the GUI renders, every write goes through the same staged edit -> Review
tray -> apply-to-live door the human uses, so the person watching the
window sees exactly what the agent did and can Ctrl+Z it.

Mounted at ``/api/agent``. Behind the app-wide CSRF guard like everything
else (an out-of-process caller sends ``Origin: http://<host:port>``). Only
JSON in, only JSON out -- the htmx fragments the GUI routes answer with are
not for a machine to parse.

Also here: the two ends of the LIVE strip -- ``POST /event`` (fed by the
Claude Code hook script, which RECORDS and never interprets) and ``GET /now``
(what the agent is doing, right now, for the topbar). SM is the ONE writer
of the journal: a journal line is derived from an event here, on the POST or
at the next start from the hook's jsonl, deduplicated by event identity.
"""

from __future__ import annotations

import collections
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from quam_state_manager.core import journal as journal_mod

logger = logging.getLogger(__name__)

agent_bp = Blueprint("agent", __name__, url_prefix="/api/agent")

_EVENT_RING = 800
_events_lock = threading.Lock()
_LIVE_WINDOW_S = 15 * 60          # a session with no sign of life for this long is 'stalled'
_NODE_RE = re.compile(r"python[^\s]*\s+(?:-m\s+\S+\s+)?(?:\"[^\"]*[\\/])?([^\s;&|\"']+)\.py\b")
_UNASSIGNED = "unassigned"


# ------------------------------------------------------------ small helpers

def _r():
    """The routes module, imported late (it is large and imports us first)."""
    from quam_state_manager.web import routes
    return routes


def _chip_name() -> str:
    r = _r()
    try:
        ident = r._active_chip_identity()
        if ident and ident.get("name"):
            return str(ident["name"])
    except Exception:  # noqa: BLE001
        pass
    p = r._active_path()
    return Path(p).name if p else "chip"


def _live_flag() -> bool:
    """Have the live files moved outside SM? Answered FRESH for the agent:
    the page's refresher is throttled (30 s) and skips a dirty working copy
    (docs/87 -- a human with staged edits gets the banner, not a pull), which
    is exactly when an agent that just ran a node would read stale values.
    One hash of two files per agent read is the price."""
    r = _r()
    ctx = r._active_ctx()
    if not ctx:
        return False
    wc = ctx.get("working_copy")
    if wc is not None:
        try:
            from quam_state_manager.core import working_copy as wc_mod
            fresh = wc_mod.live_diverged_now(wc)
            if fresh is not None:
                return bool(fresh)
        except Exception:  # noqa: BLE001
            logger.debug("live_diverged_now failed", exc_info=True)
    try:
        r._refresh_live_diverged(ctx)
    except Exception:  # noqa: BLE001
        pass
    return bool(ctx.get("live_diverged"))


def _jsonable(v: Any) -> Any:
    if isinstance(v, float) and v != v:
        return None
    if isinstance(v, Path):
        return str(v)
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return {k: _jsonable(getattr(v, k)) for k in v.__dataclass_fields__}
    return v


def _err(msg: str, code: int = 400, **extra):
    return jsonify(ok=False, error=msg, **extra), code


def _version() -> str:
    try:
        from quam_state_manager import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- the chip

@agent_bp.route("/ping")
def ping():
    """Cheap liveness for the hook: no chip work, no live-hash recheck."""
    return jsonify(ok=True, sm_version=_version())


@agent_bp.route("/chip")
def chip():
    """What is open, and how the agent should address it."""
    r = _r()
    store = r._store()
    ctx = r._active_ctx()
    if not store or not ctx:
        return jsonify(ok=True, loaded=False, sm_version=_version(), now=_now_state())
    diverged = _live_flag()
    return jsonify(ok=True, loaded=True, sm_version=_version(),
                   path=r._active_path(), name=_chip_name(),
                   chip_token=r._active_chip_token() or "",
                   qubits=list(store.qubit_names), pairs=list(store.qubit_pair_names),
                   pending=r._change_count(),
                   live_diverged=diverged,
                   stale_since=_stale_since() if diverged else None,
                   live_readonly=bool(ctx.get("live_readonly")),
                   waiting=_waiting_count(),
                   run_active=_run_active_view(),
                   plan=_plan_brief(),
                   now=_now_state())


# --------------------------------------------------------------- the state

_MAX_SUBTREE_CHARS = 60_000


@agent_bp.route("/state")
def state_get():
    """One value (raw + pointer-resolved) or a bounded subtree. Every answer
    says whether the live files have moved outside SM (a node's own write),
    because the working copy never adopts that by itself on this path."""
    r = _r()
    store = r._store()
    if not store:
        return _err("no chip loaded", 409)
    diverged = _live_flag()
    stale_since = _stale_since() if diverged else None
    path = (request.args.get("path") or "").strip().strip(".")
    if not path:
        keys = sorted(k for k in store.merged.keys())
        return jsonify(ok=True, path="", kind="container", keys=keys, live_diverged=diverged, stale_since=stale_since)
    try:
        raw = store.get_value(path)
    except (KeyError, IndexError, TypeError, ValueError):
        return _err(f"no such path: {path}", 404)
    if isinstance(raw, (dict, list)):
        text = json.dumps(_jsonable(raw), default=str)
        keys = list(raw.keys()) if isinstance(raw, dict) else list(range(len(raw)))
        if len(text) > _MAX_SUBTREE_CHARS:
            return jsonify(ok=True, path=path, kind="container", keys=[str(k) for k in keys],
                           truncated=True, size_chars=len(text), live_diverged=diverged, stale_since=stale_since,
                           hint="ask for a deeper path; this subtree is too large to return whole")
        return jsonify(ok=True, path=path, kind="container", keys=[str(k) for k in keys],
                       value=_jsonable(raw), live_diverged=diverged, stale_since=stale_since)
    resolved = raw
    if isinstance(raw, str) and raw.startswith("#"):
        try:
            resolved = store.resolve_value(path)
        except Exception:  # noqa: BLE001
            resolved = raw
    try:
        src = store.source_file_for(path)
    except Exception:  # noqa: BLE001
        src = None
    return jsonify(ok=True, path=path, kind="leaf", value=_jsonable(raw),
                   resolved=_jsonable(resolved), source_file=src,
                   is_pointer=isinstance(raw, str) and raw.startswith("#"),
                   live_diverged=diverged, stale_since=stale_since)


@agent_bp.route("/tray")
def tray():
    """The staged, not-yet-applied edits -- what the human sees in Review,
    each with who staged it."""
    r = _r()
    mod = r._modifier()
    if not mod:
        return _err("no chip loaded", 409)
    log = mod.get_change_log()
    rows = [{"index": i, "path": c.dot_path, "old": _jsonable(c.old_value), "new": _jsonable(c.new_value),
             "source": c.source_file, "created": c.created, "deleted": c.deleted,
             "group": c.group_id, "actor": getattr(c, "actor", "human")} for i, c in enumerate(log)]
    return jsonify(ok=True, count=len(rows), seen_changes=len(rows), entries=rows,
                   agent_count=sum(1 for x in rows if str(x["actor"]).startswith("by_")),
                   human_count=sum(1 for x in rows if str(x["actor"]).startswith("human")),
                   live_diverged=_live_flag())


# ------------------------------------------------------------- the history

@agent_bp.route("/versions")
def versions():
    r = _r()
    path = r._active_path()
    if not path:
        return _err("no chip loaded", 409)
    n = max(1, min(int(request.args.get("n") or 30), 500))
    try:
        snaps = r._history().list_snapshots(path)
    except Exception as exc:  # noqa: BLE001
        return _err(f"history unavailable: {exc}", 500)
    rows = []
    for s in list(snaps)[:n]:
        rows.append({"timestamp": s.timestamp, "trigger": s.trigger, "kind": getattr(s, "kind", None),
                     "label": getattr(s, "label", None), "pinned": getattr(s, "pinned", False),
                     "run_id": s.run_id, "experiment": s.experiment_name,
                     "diff": _jsonable(s.diff_summary)})
    return jsonify(ok=True, count=len(rows), versions=rows)


@agent_bp.route("/field-history")
def field_history():
    r = _r()
    path = r._active_path()
    if not path:
        return _err("no chip loaded", 409)
    dot = (request.args.get("path") or "").strip()
    if not dot:
        return _err("path required")
    try:
        data = r._history().field_history(path, dot)
    except Exception as exc:  # noqa: BLE001
        return _err(f"field history unavailable: {exc}", 500)
    return jsonify(ok=True, path=dot, history=_jsonable(data))


# ---------------------------------------------------------------- the runs

def _ds():
    r = _r()
    try:
        return r._dataset_store()
    except Exception:  # noqa: BLE001
        logger.debug("dataset store unavailable", exc_info=True)
        return None


def _uid(ds, run) -> str | None:
    r = _r()
    try:
        folder = getattr(ds, "folder_path", None)      # DatasetStore's own attribute (docs/173 S2 found the miss)
        if folder is None:
            return None
        return f"{r._folder_key(folder)}:{run['run_id'] if isinstance(run, dict) else run.run_id}"
    except Exception:  # noqa: BLE001
        return None


@agent_bp.route("/runs")
def runs():
    ds = _ds()
    if not ds:
        return jsonify(ok=True, count=0, runs=[], note="no dataset folder is open in SM")
    n = max(1, min(int(request.args.get("n") or 20), 500))
    rows = ds.list_runs(experiment=request.args.get("experiment") or None,
                        date=request.args.get("date") or None,
                        qubit=request.args.get("qubit") or None)[:n]
    out = []
    for row in rows:
        d = {k: _jsonable(row.get(k)) for k in ("run_id", "experiment_name", "date", "time", "qubits",
                                                  "qubit_pairs", "outcomes", "status", "duration_s",
                                                  "parent_id", "description")
             if k in row}
        d["uid"] = row.get("uid") or _uid(ds, row)
        out.append(d)
    return jsonify(ok=True, count=len(out), runs=out)


@agent_bp.route("/run/<int:run_id>")
def run_detail(run_id: int):
    ds = _ds()
    if not ds:
        return _err("no dataset folder is open in SM", 409)
    run = ds.get_run(run_id)
    if not run:
        return _err(f"no run #{run_id} in the open dataset folder", 404)
    folder = Path(run["folder_path"])
    figures = []
    for name in run.get("figure_names") or []:
        p = ds.get_figure_path(run_id, name)
        figures.append({"name": name, "path": str(p) if p else None})
    files = {}
    for fn in ("node.json", "data.json", "ds_raw.h5", "ds_fit.h5", "state.json", "wiring.json"):
        cand = folder / fn
        if not cand.exists():
            cand = folder / "quam_state" / fn
        if cand.exists():
            files[fn] = str(cand)
    d = _jsonable({k: run.get(k) for k in ("run_id", "experiment_name", "date", "time", "description",
                                            "qubits", "qubit_pairs", "outcomes", "parameters",
                                            "parent_id", "run_start", "run_end", "duration_s",
                                            "status", "fit_results")})
    d.update(uid=_uid(ds, run), folder=str(folder), figures=figures, files=files)
    return jsonify(ok=True, run=d)


# ---------------------------------------------------------- diagnostics

@agent_bp.route("/diagnostics")
def diagnostics_json():
    r = _r()
    store = r._store()
    if not store:
        return _err("no chip loaded", 409)
    from quam_state_manager.core import diagnostics as diag
    findings = r._active_chip_findings(store)
    rows = [{"severity": f.severity, "category": f.category, "location": f.location,
             "message": f.message, "detail": f.detail, "path": f.jump_path}
            for f in findings if not getattr(f, "acknowledged", False)]
    return jsonify(ok=True, summary=diag.summarize(findings), findings=rows[:400],
                   truncated=len(rows) > 400)


# ------------------------------------------------- the calibration knowledge

@agent_bp.route("/families")
def families():
    """The families SM knows. A node NAME is matched by ``family_for`` at call
    time; there is no per-family list of node names to show (a field that
    claimed one was empty for every family and read as 'no nodes')."""
    from quam_state_manager.core.autofit import families as fam_mod
    from quam_state_manager.core.autofit import knowledge
    out = []
    for key, fam in sorted(fam_mod.FAMILIES.items()):
        out.append({"family": key, "label": getattr(fam, "label", key),
                    "kind": getattr(fam, "kind", None),
                    "value_key": getattr(fam, "value_key", None),
                    "manual": knowledge.pack_path(key).exists()})
    return jsonify(ok=True, families=out,
                   note="pass a node name to check_fit / family_for; SM matches it to a family itself")


@agent_bp.route("/manual/<family>")
def manual(family: str):
    """A family's case manual: the lab knowledge a terminal agent does not
    have -- what each figure shape MEANS and what to do about it. Never a
    number: the pack lint drops absolute-scale rules at load."""
    from quam_state_manager.core.autofit import knowledge
    pack = knowledge.load_family(family)
    if not pack:
        return _err(f"no manual for family {family!r}", 404)
    cases = [{k: c.get(k) for k in ("id", "name", "kind", "geometry", "prescription", "seen_count")}
             for c in pack.get("cases") or []]
    return jsonify(ok=True, family=family, physics=pack.get("physics"),
                   cases=cases, rules=pack.get("rules"), closure_rules=pack.get("closure_rules"),
                   signal_map=pack.get("signal_map"))


@agent_bp.route("/check-fit/<int:run_id>")
def check_fit(run_id: int):
    """The deterministic gates over one saved run: outcome, physical bands,
    raw-data feature presence, metric consistency. No model, no number
    invented -- the same code the autofit engine trusted."""
    r = _r()
    store = r._store()
    ds = _ds()
    if not ds:
        return _err("no dataset folder is open in SM", 409)
    run = ds.get_run(run_id)
    if not run:
        return _err(f"no run #{run_id}", 404)
    from quam_state_manager.core.autofit import families as fam_mod
    from quam_state_manager.core.autofit import gates
    fam = fam_mod.family_for(run.get("experiment_name") or "")
    if fam is None:
        return jsonify(ok=True, run_id=run_id, family=None,
                       note="no autofit family registered for this node -- only the node's own outcome applies",
                       outcomes=_jsonable(run.get("outcomes")))
    run_obj = dict(run)
    run_obj["folder_path"] = Path(run["folder_path"])
    targets = list(run.get("qubits") or []) + list(run.get("qubit_pairs") or [])

    def current_value_of(dotted: str):
        if not store:
            raise KeyError(dotted)
        return store.get_value(dotted)

    verdicts = gates.evaluate_run(run_obj, fam, targets, current_value_of=current_value_of)
    return jsonify(ok=True, run_id=run_id, family=getattr(fam, "key", None) or str(fam),
                   verdicts={t: _jsonable(v) for t, v in verdicts.items()})


# ------------------------------------------------------------------ notes

@agent_bp.route("/note", methods=["POST"])
def note_set():
    r = _r()
    path = r._active_path()
    if not path:
        return _err("no chip loaded", 409)
    data = request.get_json(silent=True) or request.form.to_dict()
    subject = str(data.get("subject") or "").strip()
    text = str(data.get("text") or "")
    if not subject:
        return _err("subject required (a qubit, pair, or dot path)")
    from quam_state_manager.core import entity_notes
    try:
        rec = entity_notes.save(current_app.instance_path, path, subject, text,
                                author=str(data.get("author") or "claude-code"))
    except entity_notes.NoteConflict as exc:
        return _err("note changed underneath you", 409, stored=_jsonable(exc.stored))
    return jsonify(ok=True, note=_jsonable(rec))


# ---------------------------------------------------------------- journal

@agent_bp.route("/journal", methods=["GET"])
def journal_get():
    chip = request.args.get("chip") or _chip_name()
    day = request.args.get("date") or None
    text = journal_mod.read(current_app.instance_path, chip, day)
    return jsonify(ok=True, chip=chip, date=day or datetime.now().strftime("%Y-%m-%d"),
                   days=journal_mod.list_days(current_app.instance_path, chip),
                   chips=journal_mod.list_chips(current_app.instance_path),
                   file=str(journal_mod.day_file(current_app.instance_path, chip, day)),
                   text=text)


@agent_bp.route("/journal", methods=["POST"])
def journal_append():
    """The agent's own words. ``reason`` is REQUIRED for kind=agent: a log of
    what ran without why is what the customer already has. The actor is
    stamped from the caller, never from the payload: a request without the
    bridge header cannot claim to be the agent, and the agent cannot claim to
    be the human."""
    data = request.get_json(silent=True) or request.form.to_dict()
    kind = str(data.get("kind") or "agent")
    if request.headers.get("X-SM-Agent"):
        if kind == "human":
            kind = "agent"
    elif kind == "agent":
        kind = "human"
    text = str(data.get("text") or "").strip()
    reason = data.get("reason")
    if not text:
        return _err("text required")
    if kind == "agent" and not (reason and str(reason).strip()):
        return _err("reason required: say WHY (what you saw, what you changed, what you expect)")
    run_id = data.get("run_id")
    try:
        run_id = int(run_id) if run_id not in (None, "") else None
    except (TypeError, ValueError):
        return _err("run_id must be an integer")
    paths = data.get("paths") or []
    if isinstance(paths, str):
        paths = [p.strip() for p in paths.split(",") if p.strip()]
    chip = str(data.get("chip") or _chip_name())
    rec = journal_mod.append(current_app.instance_path, chip, text, kind=kind,
                             reason=str(reason) if reason else None, run_id=run_id, paths=list(paths))
    _bump()
    return jsonify(ok=True, entry=rec)


@agent_bp.route("/journal/root", methods=["GET", "POST"])
def journal_root():
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form.to_dict()
        try:
            root = journal_mod.set_root(current_app.instance_path, data.get("root"))
        except OSError as exc:
            return _err(f"cannot use that folder: {exc}")
        if "claude_says" in data:
            journal_mod.set_claude_says(current_app.instance_path, str(data.get("claude_says")).lower() in ("1", "true", "on"))
        return jsonify(ok=True, root=str(root),
                       claude_says=journal_mod.settings(current_app.instance_path)["claude_says"])
    return jsonify(ok=True, root=str(journal_mod.root(current_app.instance_path)),
                   default=str(Path(current_app.instance_path) / "journal"),
                   claude_says=journal_mod.settings(current_app.instance_path)["claude_says"])


# ------------------------------------------------------- the live strip

def _events() -> collections.deque:
    ev = current_app.config.get("agent_events")
    if ev is None:
        ev = collections.deque(maxlen=_EVENT_RING)
        current_app.config["agent_events"] = ev
        current_app.config["agent_journaled"] = _load_journaled()
        _replay(ev)
    return ev


def _events_dir() -> Path:
    return Path(current_app.instance_path) / "agent_events"


def _journaled_file() -> Path:
    return _events_dir() / "journaled.txt"


def _load_journaled() -> set:
    try:
        return set(l.strip() for l in _journaled_file().read_text(encoding="utf-8").splitlines() if l.strip())
    except OSError:
        return set()


def _mark_journaled(key: str) -> None:
    current_app.config.setdefault("agent_journaled", set()).add(key)
    try:
        _events_dir().mkdir(parents=True, exist_ok=True)
        with open(_journaled_file(), "a", encoding="utf-8") as f:
            f.write(key + "\n")
    except OSError:
        pass


def _event_key(rec: dict) -> str:
    return f"{rec.get('session_id')}|{rec.get('tool_use_id')}|{rec.get('hook_event_name')}|{rec.get('ts')}"


def _replay(ev: collections.deque) -> None:
    """After a restart, the morning-after view still knows last night: the
    hook wrote every event to disk before it ever talked to us. Yesterday
    AND today -- a session that started at 22:00 crosses midnight."""
    today = datetime.now()
    for day in (today - timedelta(days=1), today):
        f = _events_dir() / (day.strftime("%Y-%m-%d") + ".jsonl")
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines[-_EVENT_RING:]:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ev.append(rec)
    # journal lines the hook could not write (SM was closed) -- derived now
    for rec in list(ev):
        try:
            _absorb(rec, from_replay=True)
        except Exception:  # noqa: BLE001
            logger.debug("replay absorb failed", exc_info=True)


def _bump() -> None:
    current_app.config["agent_seq"] = int(current_app.config.get("agent_seq") or 0) + 1


def _chip_for_event(rec: dict) -> str:
    """Which chip's journal an event belongs to. Named by the session's own
    state path when it says one (matching the open chip -> the open chip's
    name), else the open chip, else 'unassigned' -- never an invented name."""
    if rec.get("origin") in ("chat", "ask") and rec.get("chip") and rec.get("chip") != _UNASSIGNED:
        return str(rec["chip"])                  # the chat knows which chip it was started on
    r = _r()
    active = r._active_path()
    sp = rec.get("quam_state_path")
    if sp:
        try:
            if active and Path(sp).resolve() == Path(active).resolve():
                return _chip_name()
        except OSError:
            pass
        p = Path(sp)
        return p.parent.name if p.name.lower() in ("quam_state", "state.json") else p.name
    return _chip_name() if active else _UNASSIGNED


def _node_of(summary: str) -> str | None:
    m = _NODE_RE.search(summary or "")
    return m.group(1).split("/")[-1].split("\\")[-1] if m else None


def _pre_ts_of(rec: dict) -> float | None:
    tid = rec.get("tool_use_id")
    if not tid:
        return None
    for e in reversed(list(current_app.config.get("agent_events") or [])):
        if e.get("tool_use_id") == tid and e.get("hook_event_name") == "PreToolUse":
            try:
                return float(e.get("ts") or 0)
            except (TypeError, ValueError):
                return None
    return None


def _newest_run_since(ts: float | None) -> int | None:
    """The run folder that appeared after a node command started -- the
    stamp that turns 'ran power_rabi' into 'ran power_rabi -> #2711'."""
    if ts is None:
        return None
    ds = _ds()
    if not ds:
        return None
    try:
        for row in ds.list_runs()[:5]:
            when = datetime.strptime(f"{row.get('date')} {row.get('time')}", "%Y-%m-%d %H:%M:%S").timestamp()
            if when >= ts - 5:
                return int(row["run_id"])
    except Exception:  # noqa: BLE001
        return None
    return None


def _journal_line(rec: dict) -> tuple[str | None, int | None]:
    """What of an event belongs in the human's notes. A node run (with its
    run id and failure), a .py edit, and -- opt-in -- what Claude said."""
    h = rec.get("hook_event_name")
    tool = rec.get("tool_name") or ""
    s = rec.get("summary") or ""
    if h == "Stop":
        if rec.get("stopped"):
            return None, None                     # the human's Stop is journaled by the door that pressed it
        if s and journal_mod.settings(current_app.instance_path)["claude_says"]:
            who = "Codex" if (rec.get("backend") or "") == "codex" else "Claude"
            return f"{who}: " + s.replace("\n", " ")[:600], None
        return None, None
    if h not in ("PostToolUse", "PostToolUseFailure"):
        return None, None
    failed = bool(rec.get("failed"))
    err = (rec.get("error") or "").replace("\n", " ")[-200:]
    if tool == "Bash":
        node = _node_of(s)
        if not node:
            return None, None
        run_id = None if failed else _newest_run_since(_pre_ts_of(rec) or (float(rec.get("ts") or 0) - 3600))
        line = f"ran `{node}`"
        if failed:
            line = f"✗ `{node}` failed" + (f": {err}" if err else "")
        return line, run_id
    if tool in ("Edit", "Write", "MultiEdit") and s.endswith(".py"):
        return (f"✗ edit of `{s}` failed" if failed else f"edited `{s}`"), None
    return None, None


def _absorb(rec: dict, *, from_replay: bool = False) -> None:
    """Derive the journal line + notification for one event, exactly once."""
    key = _event_key(rec)
    done: set = current_app.config.setdefault("agent_journaled", set())
    if key in done:
        return
    line, run_id = _journal_line(rec)
    if line:
        journal_mod.append(current_app.instance_path, _chip_for_event(rec), line, kind="hook", run_id=run_id)
    _mark_journaled(key)
    if rec.get("failed") and not from_replay:
        _notify("agent_failure", {"tool": rec.get("tool_name"), "summary": rec.get("summary"),
                                  "error": rec.get("error"), "session": rec.get("session_id")})


def _notify(event: str, payload: dict) -> None:
    try:
        from quam_state_manager.core.autofit import notify
        notify.notify(current_app.instance_path, event, payload)
    except Exception:  # noqa: BLE001
        logger.debug("notify failed", exc_info=True)


@agent_bp.route("/event", methods=["POST"])
def event_post():
    """One hook event. The script has already appended it to disk; here it
    becomes the live wake, a journal line (once), and a notification."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err("json object required")
    data.setdefault("ts", time.time())
    with _events_lock:
        _events().append(data)
    try:
        _absorb(data)
    except Exception:  # noqa: BLE001
        logger.debug("absorb failed", exc_info=True)
    _bump()
    _wake()
    return jsonify(ok=True)


def _wake() -> None:
    """One wake for the whole page: the run watcher's tick is what every
    open tab's live-wake long-poll waits on (docs/141 §4p)."""
    try:
        w = current_app.config.get("run_watcher")
        if w is not None:
            w.bump("agent")
    except Exception:  # noqa: BLE001
        logger.debug("wake failed", exc_info=True)


def _relevant(session_events: list[dict]) -> bool:
    """A Claude Code session that never touched SM or a calibration node is
    someone's paper-writing session: it must not light the strip."""
    for e in session_events:
        if (e.get("tool_name") or "").startswith("mcp__sm"):
            return True
        if e.get("tool_name") == "Bash" and _node_of(e.get("summary") or ""):
            return True
    return False


def _alive(session_events: list[dict], now: float) -> bool:
    """Signs of life: an event inside the window, or the session's own
    transcript file still growing (Claude Code appends to it while alive)."""
    last = max((float(e.get("ts") or 0) for e in session_events), default=0.0)
    if now - last < _LIVE_WINDOW_S:
        return True
    tp = next((e.get("transcript_path") for e in reversed(session_events) if e.get("transcript_path")), None)
    if tp:
        try:
            return now - Path(tp).stat().st_mtime < _LIVE_WINDOW_S
        except OSError:
            return False
    return False


_HUMAN_RECENT_S = 30 * 60


def _mode_and_limits() -> dict:
    from quam_state_manager.core import limits
    try:
        return limits.load(current_app.instance_path, _chip_name())
    except Exception:  # noqa: BLE001
        return dict(limits.DEFAULTS)


def _session() -> dict | None:
    from quam_state_manager.core import agent_session
    try:
        return agent_session.load(current_app.instance_path, _chip_name())
    except Exception:  # noqa: BLE001
        return None


def _human_ran_recently(now: float, agent_runs: dict, ev: list[dict]) -> dict | None:
    """The newest run folder with no agent origin inside the window: a person
    (or a session SM cannot see) is on the OPX. A FACT, past tense."""
    ds = _ds()
    if ds is None:
        return None
    try:
        rows = ds.list_runs()[:3]
    except Exception:  # noqa: BLE001
        return None
    from quam_state_manager.core import story
    for row in rows:
        try:
            when = datetime.strptime(f"{row.get('date')} {row.get('time')}", "%Y-%m-%d %H:%M:%S").timestamp()
        except (TypeError, ValueError):
            continue
        if now - when > _HUMAN_RECENT_S:
            continue
        rid = int(row["run_id"])
        if rid in agent_runs:
            continue
        want = story._norm(row.get("experiment_name") or "")
        hooked = any(e.get("tool_name") == "Bash" and e.get("hook_event_name") in ("PostToolUse", "PreToolUse")
                     and abs(float(e.get("ts") or 0) - when) < 600
                     and (story._node_of(e.get("summary") or "") or "") and want
                     and (story._norm(story._node_of(e.get("summary") or "")) in want) for e in ev)
        if hooked:
            continue
        return {"run_id": rid, "node": row.get("experiment_name"), "ts": when,
                "targets": list(row.get("qubits") or [])}
    return None


def _now_state() -> dict:
    """The pill's one state, in the precedence order of docs/173 §3.1:
    waiting > limited > stalled > failed > running > between > human-ran > idle."""
    from quam_state_manager.core import agent_session, story
    now = time.time()
    with _events_lock:
        ev = list(_events())
    seq = int(current_app.config.get("agent_seq") or 0)
    lim = _mode_and_limits()
    sess = _session()
    base = {"seq": seq, "mode": lim.get("mode"), "session": agent_session.summary(sess),
            "events_today": 0, "failures_today": 0, "waiting": _waiting_count()}
    agent_runs = story.load_agent_runs(current_app.instance_path)
    by_session: dict[str, list[dict]] = collections.defaultdict(list)
    for e in ev:
        by_session[str(e.get("session_id"))].append(e)
    sessions = {sid: es for sid, es in by_session.items() if _relevant(es)}
    day_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    failures = sum(1 for es in sessions.values() for e in es
                   if e.get("failed") and float(e.get("ts") or 0) >= day_start)
    base["failures_today"] = failures
    limited_until = (sess or {}).get("limited_until")
    for es in sessions.values():
        for e in es:
            if e.get("limited") and now - float(e.get("ts") or 0) < 6 * 3600:
                limited_until = e.get("limited_until") or limited_until or True
    if base["waiting"]:
        return {**base, "state": "waiting"}
    lim_ts, lim_text = _limit_until(limited_until)
    if lim_ts is True or (lim_ts and lim_ts > now):
        resets = lim_text if lim_ts is True else datetime.fromtimestamp(lim_ts).strftime("%H:%M")
        return {**base, "state": "limited", "limited_resets": resets}
    human = _human_ran_recently(now, agent_runs, ev)
    if not sessions:
        state = "human-ran" if human else "idle"
        return {**base, "state": state, "human_ran": human,
                "note": "events seen, none from a calibration session" if ev and not human else None}
    sid, es = max(sessions.items(), key=lambda kv: max(float(e.get("ts") or 0) for e in kv[1]))
    open_tools: dict[str, dict] = {}
    last_stop = None
    for e in es:
        h = e.get("hook_event_name")
        tid = e.get("tool_use_id")
        if h == "PreToolUse" and tid:
            open_tools[tid] = e
        elif h in ("PostToolUse", "PostToolUseFailure") and tid:
            open_tools.pop(tid, None)
        elif h == "Stop":
            open_tools.clear()
            last_stop = e
    alive = _alive(es, now) or agent_session.alive(sess)
    running = None
    if open_tools and alive:
        e = sorted(open_tools.values(), key=lambda x: float(x.get("ts") or 0))[-1]
        node = story._node_of(e.get("summary") or "")
        running = {"tool": e.get("tool_name"), "summary": e.get("summary"), "node": node,
                   "since": e.get("ts"), "session": e.get("session_id"), "backend": e.get("backend"),
                   "typical_s": _typical_duration(node)}
    last = es[-1]
    if running:
        state = "running"
    elif open_tools and not alive:
        state = "stalled"
    elif failures and now - float(last.get("ts") or 0) < 3600:
        state = "failed"
    elif alive:
        state = "between"
    elif human:
        state = "human-ran"
    else:
        state = "idle"
    if state == "running" and failures:
        pass                                    # a running agent outranks the day's count; the count rides along
    return {**base, "state": state, "running": running, "session_id": sid, "alive": alive,
            "last": {"tool": last.get("tool_name"), "event": last.get("hook_event_name"),
                     "summary": last.get("summary"), "ts": last.get("ts"), "failed": bool(last.get("failed")),
                     "backend": last.get("backend")},
            "last_message": (last_stop or {}).get("summary") if last_stop else None,
            "events_today": sum(1 for e in es if float(e.get("ts") or 0) >= day_start),
            "human_ran": human, "chip": _chip_for_event(last)}


def _limit_until(v):
    """(timestamp | True | None, text). A float is a reset time; True means
    limited with no known reset; a clock string the CLI printed is parsed,
    and kept as text when it cannot be."""
    if not v:
        return None, None
    if v is True:
        return True, None
    try:
        return float(v), None
    except (TypeError, ValueError):
        pass
    from quam_state_manager.core import agent_backend
    ts = agent_backend.reset_timestamp(str(v))
    return (ts, None) if ts else (True, str(v))


def _waiting_count() -> int:
    """Approval cards waiting for a human (docs/173 S5: a held write lives
    OUTSIDE the tray, in core/approvals.py -- the working copy applies whole)."""
    from quam_state_manager.core import approvals
    if not _r()._active_path():
        return 0
    try:
        return len(approvals.pending(current_app.instance_path, _chip_name()))
    except Exception:  # noqa: BLE001
        return 0


def _stale_since() -> float | None:
    """When the live files last moved, for an answer that says live_diverged
    (docs/173 S5 ``stale_since``): the newest mtime of the two live files."""
    r = _r()
    ctx = r._active_ctx()
    if not ctx or not ctx.get("path"):
        return None
    best = None
    for name in ("state.json", "wiring.json"):
        try:
            m = (Path(ctx["path"]) / name).stat().st_mtime
            best = m if best is None else max(best, m)
        except OSError:
            continue
    return best


def _typical_duration(node: str | None) -> float | None:
    """The family's typical run length from the archive -- for 'usually ~N min'."""
    if not node:
        return None
    ds = _ds()
    if ds is None:
        return None
    try:
        rows = [r for r in ds.list_runs(experiment=node)[:12] if isinstance(r.get("duration_s"), (int, float))]
        if len(rows) < 2:
            return None
        vals = sorted(float(r["duration_s"]) for r in rows)
        return vals[len(vals) // 2]
    except Exception:  # noqa: BLE001
        return None


@agent_bp.route("/now")
def now():
    return jsonify(ok=True, **_now_state())


@agent_bp.route("/limits", methods=["GET", "POST"])
def limits_route():
    """Per-chip Limits + the default mode (docs/173 S3b). A mode change is
    journaled with who pressed it."""
    from quam_state_manager.core import limits
    from quam_state_manager.web import routes as r
    chip = _chip_name()
    if request.method == "POST":
        data = request.get_json(silent=True) or request.form.to_dict()
        if "max_delta" in data and isinstance(data["max_delta"], str):
            try:
                data["max_delta"] = json.loads(data["max_delta"] or "{}")
            except ValueError:
                return _err("max_delta must be JSON")
        try:
            cur = limits.save(current_app.instance_path, chip, data, who=r._request_actor())
        except limits.LimitError as exc:
            return _err(str(exc))
        _bump()
        _wake()
        return jsonify(ok=True, chip=chip, limits=cur)
    return jsonify(ok=True, chip=chip, limits=limits.load(current_app.instance_path, chip),
                   modes=list(limits.MODES))


@agent_bp.route("/session", methods=["GET"])
def session_get():
    from quam_state_manager.core import agent_session
    rec = agent_session.load(current_app.instance_path, _chip_name())
    return jsonify(ok=True, chip=_chip_name(), session=agent_session.summary(rec))


@agent_bp.route("/session/stop", methods=["POST"])
def session_stop():
    """Stop, recorded first (docs/173 §3.3): run_node reads the flag before
    anything else; S4 adds the process kill for 'now'."""
    from quam_state_manager.core import agent_session
    from quam_state_manager.web import routes as r
    data = request.get_json(silent=True) or request.form.to_dict()
    mode = "now" if str(data.get("mode") or "") == "now" else "after_run"
    rec = agent_session.request_stop(current_app.instance_path, _chip_name(), who=r._request_actor(), mode=mode)
    if rec is None:
        return _err("no agent session on this chip", 409)
    mgr = current_app.config.get("agent_chat")
    if mgr is not None:
        try:
            mgr.stop(_chip_name(), now=(mode == "now"))   # recorded first (above), killed second
        except Exception:  # noqa: BLE001
            logger.debug("chat stop failed", exc_info=True)
    try:
        # docs/173 S6: the plan card closes too -- "Stopped by <who>", pending steps cancelled
        from quam_state_manager.core import agent_plans
        running_plan = agent_plans.running(current_app.instance_path, _chip_name())
        if running_plan is not None:
            agent_plans.stop(current_app.instance_path, _chip_name(), running_plan["id"], who=r._request_actor(),
                             how="stop now" if mode == "now" else "stop after this run")
    except Exception:  # noqa: BLE001
        logger.debug("plan stop failed", exc_info=True)
    journal_mod.append(current_app.instance_path, _chip_name(),
                       f"Stop ({'now' if mode == 'now' else 'after this run'}) pressed by {r._request_actor()}", kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, session=agent_session.summary(rec))


@agent_bp.route("/events")
def events_list():
    n = max(1, min(int(request.args.get("n") or 50), _EVENT_RING))
    with _events_lock:
        ev = list(_events())[-n:]
    return jsonify(ok=True, count=len(ev), events=ev)


# ================================================================ S5: run_node
# SM runs the node (core/agent_runs.py); these routes are the agent's door,
# the human's Arm / approve / reject clicks, and the adapter that hands the
# engine everything it needs from the app.

def _registry():
    from quam_state_manager.core import agent_runs
    app = current_app._get_current_object()
    reg = app.config.get("agent_run_registry")
    if reg is None:
        reg = agent_runs.Registry(app.instance_path)
        app.config["agent_run_registry"] = reg
    return reg


def _run_active_view() -> dict | None:
    try:
        if not _r()._active_path():
            return None
        m = _registry().active_for(_chip_name())
        return {k: m.get(k) for k in ("key", "node", "targets", "since", "actor")} if m else None
    except Exception:  # noqa: BLE001
        return None


def _stage_writes(app, live: str, writes: list[dict], gid: str, actor: str, plan_id: str | None,
                  apply: bool, *, presser: str | None = None) -> dict:
    """Stage ``writes`` onto the chip's working copy as ONE group with the
    agent's actor and, when ``apply``, push them through the ONE door
    (``/state/apply-to-live``) as the presser's own press -- the agent's
    (X-SM-Agent) or, for an approval, the human's (X-SM-Actor). Refused by
    the door => the group is un-staged again so the caller can park it."""
    r = _r()
    ctx = r._find_quam_ctx_by_path(live)
    if ctx is None or ctx.get("type") != "quam":
        return {"group_id": gid, "staged": 0, "applied": False, "error": "the chip is no longer loaded",
                "unstaged": [{"path": w["path"], "why": "chip not loaded"} for w in writes[:50]]}
    mod, store = ctx["modifier"], ctx["store"]
    staged, unstaged = [], []
    with store._lock:
        for w in writes:
            if w.get("created") or w.get("deleted"):
                unstaged.append({"path": w["path"], "why": "new/removed key -- SM stages existing leaves only; "
                                                            "apply the run's state from Datasets → Apply to chip"})
                continue
            try:
                e = mod.set_value(w["path"], w["new"], _defer_hooks=True, group_id=gid)
                e.actor = actor
                staged.append(e)
            except Exception as exc:  # noqa: BLE001
                unstaged.append({"path": w["path"], "why": f"{type(exc).__name__}: {str(exc)[:160]}"})
        store._clear_pointer_cache()
        if store.search_index is not None:
            for e in staged:
                store.search_index.update_entry(e.dot_path, e.new_value)
    out = {"group_id": gid, "staged": len(staged), "unstaged": unstaged, "applied": False, "error": None}
    if not staged or not apply:
        return out
    if r._active_ctx() is not ctx:
        out["error"] = "another chip is active in the window; staged only"
        return out

    def _take_back():
        with store._lock:                      # the group comes back out of the tray
            while store.change_log and store.change_log[-1].group_id == gid:
                mod.undo_group()
    # The door SAVES the log into the working copy before it writes the chip and
    # only then notices a moved chip (docs/65's re-apply stash) -- a refusal there
    # would leave the agent's values half-way, in SM but not on the chip. Ask first.
    try:
        from quam_state_manager.core import working_copy as wc_mod
        if wc_mod.live_diverged_now(ctx["working_copy"]):
            _take_back()
            out["error"] = "stale_live: the live files moved outside SM since the last sync (take live first)"
            return out
    except Exception:  # noqa: BLE001
        logger.debug("live_diverged_now failed", exc_info=True)
    n = len(store.change_log)
    headers = {"Accept": "application/json"}
    who = presser or actor
    if str(who).startswith("by_"):
        headers["X-SM-Agent"] = str(who)[3:]
    elif ":" in str(who):
        headers["X-SM-Actor"] = str(who).split(":", 1)[1]
    if plan_id:
        headers["X-SM-Plan"] = str(plan_id)
    try:
        with app.test_request_context("/state/apply-to-live", method="POST",
                                      data={"seen_changes": str(n)}, headers=headers):
            resp = r.state_apply_to_live()
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent apply failed")
        resp = (jsonify(ok=False, error=f"{type(exc).__name__}: {exc}"), 500)
    status = resp[1] if isinstance(resp, tuple) else getattr(resp, "status_code", 200)
    body = resp[0] if isinstance(resp, tuple) else resp
    if status != 200:
        js = body.get_json(silent=True) if hasattr(body, "get_json") else None
        out["error"] = ((js or {}).get("message") or (js or {}).get("conflict") or (js or {}).get("error")
                        or f"HTTP {status}")
        if store.change_log:
            _take_back()                       # refused BEFORE the save: the group comes back out
        else:
            # refused AFTER the save: the values sit in SM's working copy as unapplied edits
            out["saved_in_working_copy"] = True
            out["error"] += " -- the values are saved in SM's working copy (unapplied); a human decides in the window"
        return out
    if r._change_count() == 0 and not ctx.get("live_diverged"):
        out["applied"] = True
    else:
        out["error"] = "SM did not clear the tray"
    return out


def _run_adapter():
    """The engine's view of this app for the chip open NOW, captured on the
    request thread; every callable re-enters an app context on the driver."""
    from quam_state_manager.core import agent_runs, limits, scheduler, story
    app = current_app._get_current_object()
    r = _r()
    ctx = r._active_ctx()
    chip = _chip_name()
    live = str(ctx["path"])
    wc = ctx["working_copy"]
    scope = r._sched_inst()
    inst = app.instance_path

    def settings():
        return scheduler.load_settings(scope)

    def human_recent(window_min: float):
        with app.app_context():
            try:
                with _events_lock:
                    ev = list(_events())
                h = _human_ran_recently(time.time(), story.load_agent_runs(inst), ev)
                if h and time.time() - float(h.get("ts") or 0) <= float(window_min) * 60:
                    return h
            except Exception:  # noqa: BLE001
                logger.debug("human_recent failed", exc_info=True)
            return None

    def list_runs():
        with app.app_context():
            ds = _ds()
            if ds is None:
                return None                     # no dataset store: nothing to attribute against
            try:
                ds.rescan_if_stale()
            except Exception:  # noqa: BLE001
                pass
            try:
                return ds.list_runs()[:60]
            except Exception:  # noqa: BLE001
                return []

    def stage(writes, gid, actor, plan_id, apply):
        with app.app_context():
            return _stage_writes(app, live, writes, gid, actor, plan_id, apply)

    def journal(text, kind="agent", reason=None, run_id=None, paths=None):
        with app.app_context():
            journal_mod.append(inst, chip, text, kind=kind, reason=reason, run_id=run_id, paths=paths or [])

    def wake():
        with app.app_context():
            _bump()
            _wake()

    def set_lock(info):
        app.config["agent_edit_lock"] = ({**info, "chip": chip, "path": live} if info else None)

    def notify(event, payload):
        with app.app_context():
            try:
                limits.notify(inst, chip, event, payload)
            except Exception:  # noqa: BLE001
                logger.debug("notify failed", exc_info=True)

    def queue_state():
        with scheduler._QLOCK:
            return scheduler.load_queue(scope)

    return agent_runs.RunAdapter(
        instance_path=inst, chip=chip, scope=scope, live_folder=live, working_folder=str(wc.working_folder),
        settings=settings, human_recent=human_recent, list_runs=list_runs, stage=stage, journal=journal,
        wake=wake, set_lock=set_lock, notify=notify,
        queue_state=queue_state, own_runner_alive=lambda: scheduler.is_running(scope))


def _run_view(m: dict) -> dict:
    res = m.get("result") or {}
    out = {"key": m.get("key"), "status": m.get("status"), "node": m.get("node"), "targets": m.get("targets"),
           "since": m.get("since"), "ended": m.get("ended"), "actor": m.get("actor"), "plan_id": m.get("plan_id"),
           "result": res}
    if m.get("status") in ("starting", "running"):
        out["how"] = f"still running; call run_wait with key {m.get('key')}"
    elif res.get("classification") == "hardware_contention":
        out["how"] = "the OPX is held elsewhere (hardware contention): do NOT retry; tell the human"
    elif res.get("approval"):
        out["how"] = f"{len(res.get('writes') or [])} write(s) wait for the human's approval ({res.get('why_held')}); " \
                     "do not re-run this node on these targets until it is decided"
    elif res.get("classification") == "unattributed":
        out["how"] = "the node finished but no run folder appeared under its name; check the log tail"
    elif res.get("apply_error"):
        out["how"] = f"applied: no ({res.get('apply_error')})"
    return out


@agent_bp.route("/run-node", methods=["POST"])
def run_node():
    """The agent's ONE way to run a calibration node. Gates answer as data
    (docs/173 §3.4); the node runs on a scratch copy; the writes go through
    the door or into an approval; the run is attributed to the agent."""
    from quam_state_manager.core import agent_runs, agent_session, approvals, limits
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    data = request.get_json(silent=True) or {}
    node = str(data.get("node") or "").strip()
    if not node:
        return _err("node required")
    targets = data.get("targets") or []
    if isinstance(targets, str):
        targets = targets.replace(",", " ").split()
    targets = [str(t).strip() for t in targets if str(t).strip()]
    reason = str(data.get("reason") or "").strip()
    if not reason:
        return _err("reason required: say WHY this node now (it goes into the human's journal)")
    params = data.get("params") or {}
    if not isinstance(params, dict):
        return _err("params must be an object")
    actor = r._request_actor()
    if not actor.startswith("by_"):
        return _err("run_node is the agent's door; a person runs nodes from the QUAlibrate GUI", 403)
    store = r._store()
    known = set(store.qubit_names) | set(store.qubit_pair_names)
    bad = [t for t in targets if t not in known]
    if bad:
        return _err(f"unknown targets {bad}", 400, known=sorted(known)[:80])
    inst, chip = current_app.instance_path, _chip_name()
    adapter = _run_adapter()
    reg = _registry()
    settings = adapter.settings()
    node_info, available = agent_runs.resolve_node(settings.get("calibrations_folder"), node, instance_path=inst)
    session = agent_session.load(inst, chip)
    lim = limits.load(inst, chip)
    try:
        timeout_s = float(data.get("timeout_s")) if data.get("timeout_s") else None
    except (TypeError, ValueError):
        timeout_s = None
    req = agent_runs.RunRequest(node=node, targets=targets, params=params, reason=reason, timeout_s=timeout_s,
                                plan_id=request.headers.get("X-SM-Plan") or data.get("plan_id") or None,
                                actor=actor, approval_id=data.get("approval_id") or None,
                                session_id=(session or {}).get("session_id"),
                                step=int(data["step"]) if str(data.get("step") or "").lstrip("-").isdigit() else None)
    pend = approvals.pending(inst, chip)
    human = adapter.human_recent(float(lim.get("human_recent_min") or 30))
    refusal = agent_runs.check_gates(req, session=session, lim=lim, settings=settings, pending=pend, human=human,
                                     queue_state=adapter.queue_state(), own_running=adapter.own_runner_alive(),
                                     run_active=reg.active_for(chip), node_info=node_info, available=available)
    if refusal is not None:
        if refusal.pop("file_request", False) and node_info is not None:
            ap = approvals.add(inst, chip, kind="run", node=node_info.name, targets=targets, writes=None,
                               reason=reason, why_held="mode ask-all", actor=actor, plan_id=req.plan_id, params=params)
            refusal["approval"] = approvals.summary(ap)
            journal_mod.append(inst, chip, f"asked to run `{node_info.name}` on {' '.join(targets)} -- waiting for "
                                           f"approval (mode ask-all)", kind="agent", reason=reason)
            _bump()
            _wake()
        return jsonify(ok=False, **refusal), 409
    mode = (session or {}).get("mode") or lim.get("mode")
    if mode == "ask-all":
        ap = approvals.get(inst, chip, req.approval_id or "")
        if (not ap or ap.get("status") != "approved" or ap.get("kind") != "run"
                or ap.get("node") != node_info.name or list(ap.get("targets") or []) != targets):
            return jsonify(ok=False, refused="awaiting_approval", needs="run",
                           how="approval_id must name an APPROVED run request for this node and these targets"), 409
    meta = reg.start(req, adapter, node_info=node_info, session=session, lim=lim)
    try:
        wait_s = float(data.get("wait_s") or agent_runs.DEFAULT_WAIT_S)
    except (TypeError, ValueError):
        wait_s = agent_runs.DEFAULT_WAIT_S
    m = reg.wait(meta["key"], max(0.0, min(wait_s, 3600.0)))
    return jsonify(ok=True, **_run_view(m or meta))


@agent_bp.route("/run/<key>")
def run_wait(key: str):
    try:
        wait_s = float(request.args.get("wait_s") or 0)
    except ValueError:
        wait_s = 0.0
    m = _registry().wait(key, max(0.0, min(wait_s, 3600.0)))
    if m is None:
        return _err("unknown run key", 404)
    return jsonify(ok=True, **_run_view(m))


@agent_bp.route("/runs/agent")
def runs_agent():
    """This process's run_node runs, newest first (the cards read these)."""
    reg = _registry()
    chip = _chip_name() if _r()._active_path() else None
    rows = [_run_view(m) for m in reg.runs.values() if chip is None or m.get("chip") == chip]
    rows.sort(key=lambda x: float(x.get("since") or 0), reverse=True)
    return jsonify(ok=True, runs=rows[:50])


@agent_bp.route("/session/arm", methods=["POST"])
def session_arm():
    """Rule 0: hardware starts only by a human click. This IS the click."""
    from quam_state_manager.core import agent_session
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    actor = r._request_actor()
    if actor.startswith("by_"):
        return _err("only a person's click arms a session (rule 0)", 403)
    data = request.get_json(silent=True) or request.form.to_dict()
    inst, chip = current_app.instance_path, _chip_name()
    token = uuid.uuid4().hex[:12]
    rec = agent_session.save(inst, chip, start_token=token, armed_by=actor, armed_at=time.time(),
                             agent_stop=None, plan_id=data.get("plan_id") or (agent_session.load(inst, chip) or {}).get("plan_id"))
    journal_mod.append(inst, chip, f"armed by {actor}: the agent may start hardware runs on this chip", kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, session=agent_session.summary(rec))


@agent_bp.route("/session/disarm", methods=["POST"])
def session_disarm():
    from quam_state_manager.core import agent_session
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    inst, chip = current_app.instance_path, _chip_name()
    if agent_session.load(inst, chip) is None:
        return _err("no agent session on this chip", 409)
    rec = agent_session.save(inst, chip, start_token=None)
    journal_mod.append(inst, chip, f"disarmed by {r._request_actor()}", kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, session=agent_session.summary(rec))


@agent_bp.route("/approvals")
def approvals_list():
    from quam_state_manager.core import approvals
    r = _r()
    if not r._active_path():
        return jsonify(ok=True, pending=[], recent=[])
    rows = approvals.load(current_app.instance_path, _chip_name())
    pend = [x for x in rows if x.get("status") == "pending"]
    recent = [approvals.summary(x) for x in rows if x.get("status") != "pending"][-20:]
    return jsonify(ok=True, pending=pend, recent=recent, waiting=len(pend))


@agent_bp.route("/approvals/<aid>/<verb>", methods=["POST"])
def approvals_decide(aid: str, verb: str):
    """approve / reject, by a person. Approving ``writes`` stages them as the
    agent's rows and pushes them through the door as THIS person's press."""
    from quam_state_manager.core import approvals
    r = _r()
    if verb not in ("approve", "reject"):
        return _err("verb must be approve or reject", 404)
    if not r._active_path():
        return _err("open a chip first", 409)
    actor = r._request_actor()
    if actor.startswith("by_"):
        return _err("only a person decides an approval", 403)
    data = request.get_json(silent=True) or {}
    inst, chip = current_app.instance_path, _chip_name()
    writes = data.get("writes") if isinstance(data.get("writes"), list) else None
    cur = approvals.get(inst, chip, aid)
    if cur is None or cur.get("status") != "pending":
        return _err("no pending approval with that id", 404)
    out = {"ok": True}
    if verb == "approve" and cur.get("kind") == "writes":
        # the writes go through the door FIRST; a refusal keeps the approval pending
        # (the human sees why, takes live, presses again) instead of recording an
        # approval that never reached the chip
        res = _stage_writes(current_app._get_current_object(), str(r._active_path()),
                            writes if writes is not None else (cur.get("writes") or []),
                            f"approved:{aid}", cur.get("actor") or "by_agent", cur.get("plan_id"), True, presser=actor)
        out["stage"] = res
        if not res.get("applied") and res.get("staged", 0) > 0 and not res.get("saved_in_working_copy"):
            return jsonify(ok=False, error=res.get("error") or "not applied", stage=res,
                           approval=approvals.summary(cur), how="the approval stays pending; take live, then approve again"), 409
    rec = approvals.decide(inst, chip, aid, status="approved" if verb == "approve" else "rejected", who=actor,
                           note=data.get("note"), writes=writes)
    out["approval"] = approvals.summary(rec)
    if verb == "approve" and rec.get("kind") == "writes":
        res = out["stage"]
        rid = rec.get("run_id")
        journal_mod.append(inst, chip, f"approved {res.get('staged', 0)} write(s) from `{rec.get('node')}`"
                                       + (f" #{rid}" if rid else "") + f" -- {'applied' if res.get('applied') else 'NOT applied: ' + str(res.get('error'))}",
                           kind="sm", run_id=rid, paths=[w.get("path") for w in (rec.get("writes") or [])[:20]])
    elif verb == "approve":
        journal_mod.append(inst, chip, f"approved the run of `{rec.get('node')}` on {' '.join(rec.get('targets') or [])}",
                           kind="sm")
    else:
        journal_mod.append(inst, chip, f"rejected {rec.get('kind')} from `{rec.get('node')}`"
                                       + (f": {data.get('note')}" if data.get("note") else ""), kind="sm")
    _bump()
    _wake()
    return jsonify(**out)


@agent_bp.route("/undo-mine", methods=["POST"])
def undo_mine():
    """Undo the agent's OWN staged groups from the top of the tray, stopping
    at the first human entry (a person's edit is never undone by an agent)."""
    r = _r()
    mod = r._modifier()
    if not mod:
        return _err("no chip loaded", 409)
    store = mod.store
    reverted: list[str] = []
    stopped_at = None
    with store._lock:
        while store.change_log:
            top = store.change_log[-1]
            if not str(getattr(top, "actor", "human")).startswith("by_"):
                stopped_at = {"path": top.dot_path, "actor": getattr(top, "actor", "human")}
                break
            for e in mod.undo_group():
                reverted.append(e.dot_path)
    if reverted:
        journal_mod.append(current_app.instance_path, _chip_name(),
                           f"undid {len(reverted)} of its own staged edit(s)", kind="agent" if r._request_actor().startswith("by_") else "sm",
                           reason="undo_mine", paths=reverted[:20])
    _bump()
    _wake()
    return jsonify(ok=True, reverted=reverted, stopped_at=stopped_at, pending=r._change_count())


@agent_bp.route("/live-diff")
def live_diff():
    """What take_live would change: live leaves that differ from the working
    copy, and which of those the tray also touches (overlap)."""
    from quam_state_manager.core import json_diff, working_copy as wc_mod
    r = _r()
    ctx = r._active_ctx()
    if not ctx or ctx.get("type") != "quam":
        return _err("no chip loaded", 409)
    wc = ctx["working_copy"]
    store = ctx["store"]
    try:
        live_state, live_wiring = wc_mod.read_live(wc)
    except Exception as exc:  # noqa: BLE001
        return _err(f"live files unreadable: {exc}", 502)
    with store._lock:
        work, _ = json_diff.flatten({**store.state, **store.wiring}, cap=250_000)
        tray = {c.dot_path for c in store.change_log}
    live, _ = json_diff.flatten({**live_state, **live_wiring}, cap=250_000)
    changed = []
    for p in sorted(set(work) | set(live)):
        a, b = work.get(p, "<absent>"), live.get(p, "<absent>")
        if a == b and type(a) is type(b):
            continue
        changed.append({"path": p, "working": _jsonable(a), "live": _jsonable(b)})
        if len(changed) >= 300:
            break
    overlap = [c["path"] for c in changed if c["path"] in tray]
    return jsonify(ok=True, count=len(changed), changed=changed, overlap=overlap,
                   live_diverged=_live_flag(), stale_since=_stale_since())


# ================================================================ S6: the card feed + plans
# The Agent home and the floating panel read ONE feed: SM's own record (chat
# events on disk, plans, runs, approvals) -- never the model's claim.

def _plan_brief() -> dict | None:
    from quam_state_manager.core import agent_plans
    try:
        chip = _chip_name()
        rec = agent_plans.running(current_app.instance_path, chip) or agent_plans.latest(current_app.instance_path, chip)
        if not rec:
            return None
        return {"id": rec.get("id"), "title": rec.get("title"), "status": rec.get("status"),
                "counts": agent_plans.counts(rec), "mode": rec.get("mode")}
    except Exception:  # noqa: BLE001
        return None


def _chat_card(e: dict) -> dict | None:
    h = e.get("hook_event_name")
    base = {"n": e.get("n"), "ts": e.get("ts"), "backend": e.get("backend")}
    if h == "User":
        return {**base, "kind": "user", "text": e.get("text") or "", "who": e.get("who")}
    if h == "Text":
        txt = e.get("text") or ""
        try:
            html = journal_mod.render(txt)
        except Exception:  # noqa: BLE001
            html = None
        return {**base, "kind": "answer", "text": txt, "html": html}
    if h in ("PreToolUse",):
        tool = e.get("tool_name") or ""
        if not (tool.startswith("mcp__sm") or tool in ("Bash", "Edit", "Write", "MultiEdit")):
            return None
        return {**base, "kind": "tool", "tool": tool, "summary": e.get("summary") or "", "failed": False}
    if h in ("PostToolUseFailure",):
        return {**base, "kind": "tool", "tool": e.get("tool_name") or "", "summary": e.get("summary") or "",
                "failed": True, "error": e.get("error")}
    if h == "Error":
        return {**base, "kind": "error", "error": e.get("error"), "limited": bool(e.get("limited"))}
    if h == "Result" and e.get("limited"):
        return {**base, "kind": "limited", "text": (e.get("error") or e.get("summary") or "")[:200]}
    if h == "Stop" and e.get("stopped"):
        return {**base, "kind": "stop", "text": e.get("summary") or ""}
    return None


def _plan_view(rec: dict, *, with_may_change: bool = False) -> dict:
    from quam_state_manager.core import agent_plans
    out = dict(rec)
    out["counts"] = agent_plans.counts(rec)
    if with_may_change:
        try:
            out["may_change"] = _may_change(rec.get("steps") or [])
        except Exception:  # noqa: BLE001
            logger.debug("may_change failed", exc_info=True)
            out["may_change"] = []
    return out


def _may_change(steps: list[dict], cap: int = 60) -> list[dict]:
    """The values a plan may write, from the families' own update targets
    (run-derived, docs/78 D-14) filled in per target, with the value the chip
    holds NOW. Unknown family => nothing claimed."""
    from quam_state_manager.core.autofit import families
    r = _r()
    store = r._store()
    seen: set = set()
    out: list[dict] = []
    for s in steps:
        fam = families.family_for(s.get("node") or "")
        if not fam:
            continue
        params = s.get("params") or {}
        for u in getattr(fam, "updates", None) or []:
            tpl = getattr(u, "path", None) or getattr(u, "state_path", None)
            if not tpl:
                continue
            for t in s.get("targets") or []:
                path = str(tpl).replace("{q}", t).replace("{pair}", t).replace("{p}", t)
                assumed = None
                if "{operation}" in path:
                    # the node names its operation through a run parameter (docs/78 D-14);
                    # the step's own param first, else the node's usual default, SAID so
                    op = params.get("operation")
                    if not op:
                        op, assumed = "x180", "operation x180 assumed (the node's default)"
                    path = path.replace("{operation}", str(op))
                if "{" in path:
                    path = path.split("{")[0].rstrip(".") + " …"
                key = (t, path)
                if key in seen:
                    continue
                seen.add(key)
                now = None
                if store is not None and "…" not in path:
                    try:
                        now = _jsonable(store.get_value(path))
                    except Exception:  # noqa: BLE001
                        now = None
                out.append({"target": t, "path": path, "now": now, "family": getattr(fam, "label", None),
                            "label": getattr(u, "label", None) or None, "note": assumed})
                if len(out) >= cap:
                    return out
    return out


@agent_bp.route("/chat/cards")
def chat_cards():
    """The panel's feed: chat cards after ``after`` plus the live objects
    (plans, runs, approvals), the session, the pill's state."""
    from quam_state_manager.core import agent_plans, agent_session, approvals
    r = _r()
    chip = _chip_name() if r._active_path() else None
    after = int(request.args.get("after") or 0)
    cards: list[dict] = []
    last = after
    if chip:
        with _events_lock:
            ev = [e for e in _events() if e.get("origin") == "chat" and int(e.get("n") or 0) > after
                  and e.get("chip") == chip]
        for e in ev[-300:]:
            c = _chat_card(e)
            if c:
                cards.append(c)
            last = max(last, int(e.get("n") or 0))
    inst = current_app.instance_path
    live = {"plans": [], "runs": [], "approvals": []}
    file = None
    session = None
    if chip:
        plans = agent_plans.load(inst, chip)[-6:]
        live["plans"] = [_plan_view(p, with_may_change=(p.get("status") in ("draft", "running"))) for p in plans]
        reg = _registry()
        runs = [_run_view(m) for m in reg.runs.values() if m.get("chip") == chip]
        runs.sort(key=lambda x: float(x.get("since") or 0))
        live["runs"] = runs[-20:]
        live["approvals"] = approvals.pending(inst, chip)
        file = agent_session.summary(agent_session.load(inst, chip))
        mgr = current_app.config.get("agent_chat")
        session = mgr.status(chip) if mgr else None
    store = r._store()
    return jsonify(ok=True, chip=chip, cards=cards, last=last, live=live, session=session, file=file,
                   now=_now_state(), waiting=_waiting_count(), agent_seq=int(current_app.config.get("agent_seq") or 0),
                   qubits=len(store.qubit_names) if store else None)


@agent_bp.route("/plans", methods=["GET"])
def plans_list():
    from quam_state_manager.core import agent_plans
    r = _r()
    if not r._active_path():
        return jsonify(ok=True, plans=[])
    rows = agent_plans.load(current_app.instance_path, _chip_name())
    return jsonify(ok=True, plans=[_plan_view(p) for p in rows[-20:]])


@agent_bp.route("/plans/<pid>", methods=["GET"])
def plan_get(pid: str):
    from quam_state_manager.core import agent_plans
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    rec = agent_plans.get(current_app.instance_path, _chip_name(), pid)
    if rec is None:
        return _err("unknown plan", 404)
    return jsonify(ok=True, plan=_plan_view(rec, with_may_change=True))


@agent_bp.route("/plans", methods=["POST"])
def plans_add():
    """A plan CARD: from the agent (plan_propose: title + steps + why) or
    from a person's deterministic ``/run`` line. Nothing starts here."""
    from quam_state_manager.core import agent_plans, agent_session, limits
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    data = request.get_json(silent=True) or {}
    inst, chip = current_app.instance_path, _chip_name()
    actor = r._request_actor()
    store = r._store()
    known = set(store.qubit_names) | set(store.qubit_pair_names)
    if data.get("run_line"):
        parsed = agent_plans.parse_run_line(str(data["run_line"]))
        if not parsed or parsed.get("error"):
            return _err((parsed or {}).get("error") or "usage: /run <node> <targets...> [param=value ...]")
        bad = [t for t in parsed["targets"] if t not in known]
        if bad:
            return _err(f"unknown targets {bad}", 400, known=sorted(known)[:80])
        steps = [{"node": parsed["node"], "targets": parsed["targets"], "params": parsed["params"],
                  "why": "typed as /run (no model involved)"}]
        title = f"/run {parsed['node']} {' '.join(parsed['targets'])}"
        source = "run_cmd"
    else:
        steps = data.get("steps")
        title = str(data.get("title") or "").strip() or "plan"
        source = "agent" if actor.startswith("by_") else "human"
        try:
            agent_plans.normalize_steps(steps)
        except ValueError as exc:
            return _err(str(exc))
        bad = sorted({t for s in steps for t in (s.get("targets") or []) if t not in known})
        if bad:
            return _err(f"unknown targets {bad}", 400, known=sorted(known)[:80])
    session = agent_session.load(inst, chip)
    mode = (session or {}).get("mode") or limits.load(inst, chip).get("mode")
    rec = agent_plans.add(inst, chip, title=title, steps=steps, mode=mode, created_by=actor, source=source,
                          reason=data.get("why") or data.get("reason"), session_id=(session or {}).get("session_id"))
    journal_mod.append(inst, chip, f"plan `{rec['title']}` proposed ({len(rec['steps'])} step(s)) -- waiting for Start",
                       kind="agent" if actor.startswith("by_") else "sm",
                       reason=(data.get("why") or None) if actor.startswith("by_") else None)
    _bump()
    _wake()
    return jsonify(ok=True, plan=_plan_view(rec, with_may_change=True),
                   how="the card is on the human's screen; nothing runs until a person presses Start. "
                       "When told to go, call run_node step by step with plan_id and step.")


@agent_bp.route("/plans/<pid>/mode", methods=["POST"])
def plan_mode(pid: str):
    from quam_state_manager.core import agent_plans, limits
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    data = request.get_json(silent=True) or {}
    inst, chip = current_app.instance_path, _chip_name()
    rec = agent_plans.get(inst, chip, pid)
    if rec is None:
        return _err("unknown plan", 404)
    mode = str(data.get("mode") or "")
    if mode not in limits.MODES:
        return _err(f"mode must be one of {list(limits.MODES)}")
    rec = agent_plans.update(inst, chip, pid, mode=mode)
    return jsonify(ok=True, plan=_plan_view(rec))


@agent_bp.route("/plans/<pid>/start", methods=["POST"])
def plan_start(pid: str):
    """THE click of rule 0. A person presses Start: the session is armed, the
    chip is snapshotted (the whole plan can be reverted to it), the plan's
    mode is applied to the session, and the driving session is told to go --
    started with the plan as its first message when there is none."""
    from quam_state_manager.core import agent_plans, agent_session, limits
    from quam_state_manager.web import chat_api
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    actor = r._request_actor()
    if actor.startswith("by_"):
        return _err("only a person's click starts a plan (rule 0)", 403)
    inst, chip = current_app.instance_path, _chip_name()
    rec = agent_plans.get(inst, chip, pid)
    if rec is None:
        return _err("unknown plan", 404)
    if rec.get("status") != "draft":
        return _err(f"plan is {rec.get('status')}", 409)
    if agent_plans.running(inst, chip):
        return _err("another plan is running on this chip", 409)
    data = request.get_json(silent=True) or {}
    mode = rec.get("mode") or limits.load(inst, chip).get("mode")
    # the mode the card shows is the mode the session runs in
    try:
        limits.save(inst, chip, {"mode": mode}, who=actor)
    except limits.LimitError as exc:
        return _err(str(exc))
    # the snapshot the plan can be reverted to
    pre_ts = None
    try:
        hm = r._history()
        ctx = r._active_ctx()
        meta = hm.check_and_snapshot(ctx["path"], "manual", force=True, kind="backup", actor=actor,
                                     defer_index=not current_app.config.get("TESTING"),
                                     project=r._scope_for(ctx["path"], ctx))
        if meta is not None:
            pre_ts = meta.timestamp
            try:
                hm.annotate_snapshot(ctx["path"], pre_ts, label=f"before plan {rec['title'][:40]}")
            except Exception:  # noqa: BLE001
                logger.debug("plan snapshot label failed", exc_info=True)
    except Exception:  # noqa: BLE001
        logger.warning("plan pre-snapshot failed", exc_info=True)
    # arm
    token = uuid.uuid4().hex[:12]
    agent_session.save(inst, chip, start_token=token, armed_by=actor, armed_at=time.time(), agent_stop=None,
                       plan_id=pid, mode=mode)
    rec = agent_plans.update(inst, chip, pid, status="running", started_by=actor, started_at=time.time(),
                             pre_ts=pre_ts, mode=mode)
    # tell the agent
    lines = [f"The human ({actor}) pressed Start on plan {pid} (\"{rec['title']}\"), mode {mode}. "
             "Run it now, step by step, with run_node(plan_id=..., step=i, ...). After every step read the "
             "result; on a refusal or a failure tell the human and stop unless the refusal names a wait. "
             "When every step is done, summarize what changed."]
    for s in rec["steps"]:
        lines.append(f"  step {s['i']}: run_node node={s['node']} targets={s['targets']} params={s['params']}"
                     + (f"  # {s['why']}" if s.get("why") else ""))
    msg = "\n".join(lines)
    mgr = chat_api._manager()
    cur = mgr.get(chip)
    started = False
    try:
        if cur is not None and cur.alive() and not cur.ended:
            res = mgr.send(chip, msg)
            if res.get("error"):
                return _err(res["error"], 409)
        else:
            backend = str(data.get("backend") or chat_api._setup().get("default_backend") or "claude").lower()
            b = chat_api._build_backend(backend, readonly=False, chip=chip, mode=mode, cwd=chat_api._cwd(),
                                        model=data.get("model"))
            mgr.start(chip, b, owner=actor, mode=mode, until=None, prompt=msg, resume=None)
            started = True
    except (RuntimeError, ValueError, OSError) as exc:
        agent_plans.update(inst, chip, pid, status="draft", started_by=None, started_at=None)
        return _err(f"could not tell the agent: {exc}", 502)
    chat_api._record_user(chip, f"[Start] plan {rec['title']}", actor, (cur.backend.name if cur else data.get("backend") or "claude"))
    journal_mod.append(inst, chip, f"plan `{rec['title']}` STARTED by {actor} (mode {mode}"
                                   + (f", snapshot {pre_ts}" if pre_ts else "") + ")", kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, plan=_plan_view(rec, with_may_change=True), session_started=started, pre_ts=pre_ts)


@agent_bp.route("/plans/<pid>/cancel", methods=["POST"])
def plan_cancel(pid: str):
    from quam_state_manager.core import agent_plans
    r = _r()
    if not r._active_path():
        return _err("open a chip first", 409)
    inst, chip = current_app.instance_path, _chip_name()
    rec = agent_plans.stop(inst, chip, pid, who=r._request_actor(), how="cancelled")
    if rec is None:
        return _err("unknown plan", 404)
    journal_mod.append(inst, chip, f"plan `{rec.get('title')}` cancelled by {r._request_actor()}", kind="sm")
    _bump()
    _wake()
    return jsonify(ok=True, plan=_plan_view(rec))
