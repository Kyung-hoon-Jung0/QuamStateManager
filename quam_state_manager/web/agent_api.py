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
Claude Code hook script) and ``GET /now`` (what the agent is doing, right
now, for the topbar).
"""

from __future__ import annotations

import collections
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from quam_state_manager.core import journal as journal_mod

logger = logging.getLogger(__name__)

agent_bp = Blueprint("agent", __name__, url_prefix="/api/agent")

_EVENT_RING = 500
_events_lock = threading.Lock()


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


# ---------------------------------------------------------------- the chip

@agent_bp.route("/chip")
def chip():
    """What is open, and how the agent should address it."""
    r = _r()
    store = r._store()
    ctx = r._active_ctx()
    if not store or not ctx:
        return jsonify(ok=True, loaded=False, sm_version=_version(), now=_now_state())
    try:
        r._refresh_live_diverged(ctx)
    except Exception:  # noqa: BLE001
        pass
    return jsonify(ok=True, loaded=True, sm_version=_version(),
                   path=r._active_path(), name=_chip_name(),
                   chip_token=r._active_chip_token() or "",
                   qubits=list(store.qubit_names), pairs=list(store.qubit_pair_names),
                   pending=r._change_count(),
                   live_diverged=bool(ctx.get("live_diverged")),
                   live_readonly=bool(ctx.get("live_readonly")),
                   now=_now_state())


def _version() -> str:
    try:
        from quam_state_manager import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------- the state

_MAX_SUBTREE_CHARS = 60_000


@agent_bp.route("/state")
def state_get():
    """One value (raw + pointer-resolved) or a bounded subtree."""
    r = _r()
    store = r._store()
    if not store:
        return _err("no chip loaded", 409)
    path = (request.args.get("path") or "").strip().strip(".")
    if not path:
        keys = sorted(k for k in store.merged.keys())
        return jsonify(ok=True, path="", kind="container", keys=keys)
    try:
        raw = store.get_value(path)
    except (KeyError, IndexError, TypeError, ValueError):
        return _err(f"no such path: {path}", 404)
    if isinstance(raw, (dict, list)):
        text = json.dumps(_jsonable(raw), default=str)
        keys = list(raw.keys()) if isinstance(raw, dict) else list(range(len(raw)))
        if len(text) > _MAX_SUBTREE_CHARS:
            return jsonify(ok=True, path=path, kind="container", keys=[str(k) for k in keys],
                           truncated=True, size_chars=len(text),
                           hint="ask for a deeper path; this subtree is too large to return whole")
        return jsonify(ok=True, path=path, kind="container", keys=[str(k) for k in keys],
                       value=_jsonable(raw))
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
                   is_pointer=isinstance(raw, str) and raw.startswith("#"))


@agent_bp.route("/tray")
def tray():
    """The staged, not-yet-applied edits -- what the human sees in Review."""
    r = _r()
    mod = r._modifier()
    if not mod:
        return _err("no chip loaded", 409)
    log = mod.get_change_log()
    rows = [{"index": i, "path": c.dot_path, "old": _jsonable(c.old_value), "new": _jsonable(c.new_value),
             "source": c.source_file, "created": c.created, "deleted": c.deleted,
             "group": c.group_id} for i, c in enumerate(log)]
    return jsonify(ok=True, count=len(rows), seen_changes=len(rows), entries=rows)


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
        folder = getattr(ds, "data_folder", None) or getattr(ds, "root", None) or getattr(ds, "folder", None)
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
    from quam_state_manager.core.autofit import families as fam_mod
    from quam_state_manager.core.autofit import knowledge
    out = []
    for key, fam in sorted(fam_mod.FAMILIES.items()):
        has_manual = knowledge.pack_path(key).exists()
        out.append({"family": key, "label": getattr(fam, "label", key),
                    "value_key": getattr(fam, "value_key", None),
                    "nodes": list(getattr(fam, "node_names", []) or []),
                    "manual": has_manual})
    return jsonify(ok=True, families=out)


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
                   file=str(journal_mod.day_file(current_app.instance_path, chip, day)),
                   text=text)


@agent_bp.route("/journal", methods=["POST"])
def journal_append():
    """The agent's own words. ``reason`` is REQUIRED for kind=agent: a log of
    what ran without why is what the customer already has."""
    data = request.get_json(silent=True) or request.form.to_dict()
    kind = str(data.get("kind") or "agent")
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
        return jsonify(ok=True, root=str(root))
    return jsonify(ok=True, root=str(journal_mod.root(current_app.instance_path)),
                   default=str(Path(current_app.instance_path) / "journal"))


# ------------------------------------------------------- the live strip

def _events() -> collections.deque:
    ev = current_app.config.get("agent_events")
    if ev is None:
        ev = collections.deque(maxlen=_EVENT_RING)
        current_app.config["agent_events"] = ev
        _replay_today(ev)
    return ev


def _events_dir() -> Path:
    return Path(current_app.instance_path) / "agent_events"


def _replay_today(ev: collections.deque) -> None:
    """After a restart, the morning-after view still knows last night: the
    hook wrote every event to disk before it ever talked to us."""
    f = _events_dir() / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    try:
        lines = f.read_text(encoding="utf-8").splitlines()[-_EVENT_RING:]
    except OSError:
        return
    for line in lines:
        try:
            ev.append(json.loads(line))
        except ValueError:
            continue


def _bump() -> None:
    current_app.config["agent_seq"] = int(current_app.config.get("agent_seq") or 0) + 1


@agent_bp.route("/event", methods=["POST"])
def event_post():
    """One hook event from the Claude Code hook script. The script has
    already appended it to disk; this is the live wake."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _err("json object required")
    data.setdefault("ts", time.time())
    with _events_lock:
        _events().append(data)
    _bump()
    return jsonify(ok=True)


def _now_state() -> dict:
    with _events_lock:
        ev = list(_events())
    if not ev:
        return {"state": "idle", "seq": int(current_app.config.get("agent_seq") or 0)}
    open_tools: dict[str, dict] = {}
    last_stop = None
    last = ev[-1]
    for e in ev:
        h = e.get("hook_event_name") or e.get("event")
        tid = e.get("tool_use_id")
        if h == "PreToolUse" and tid:
            open_tools[tid] = e
        elif h in ("PostToolUse", "PostToolUseFailure") and tid:
            open_tools.pop(tid, None)
        elif h == "Stop":
            last_stop = e
    running = None
    if open_tools:
        e = sorted(open_tools.values(), key=lambda x: x.get("ts") or 0)[-1]
        if time.time() - float(e.get("ts") or 0) < 3600:
            running = {"tool": e.get("tool_name"), "summary": e.get("summary"),
                       "since": e.get("ts"), "session": e.get("session_id")}
    state = "running" if running else ("idle" if time.time() - float(last.get("ts") or 0) > 900 else "between")
    return {"state": state, "running": running,
            "last": {"tool": last.get("tool_name"), "event": last.get("hook_event_name") or last.get("event"),
                     "summary": last.get("summary"), "ts": last.get("ts"), "session": last.get("session_id")},
            "last_message": (last_stop or {}).get("summary") if last_stop else None,
            "events_today": len(ev),
            "seq": int(current_app.config.get("agent_seq") or 0)}


@agent_bp.route("/now")
def now():
    return jsonify(ok=True, **_now_state())


@agent_bp.route("/events")
def events_list():
    n = max(1, min(int(request.args.get("n") or 50), _EVENT_RING))
    with _events_lock:
        ev = list(_events())[-n:]
    return jsonify(ok=True, count=len(ev), events=ev)
