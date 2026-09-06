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
    return jsonify(ok=True, loaded=True, sm_version=_version(),
                   path=r._active_path(), name=_chip_name(),
                   chip_token=r._active_chip_token() or "",
                   qubits=list(store.qubit_names), pairs=list(store.qubit_pair_names),
                   pending=r._change_count(),
                   live_diverged=_live_flag(),
                   live_readonly=bool(ctx.get("live_readonly")),
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
    path = (request.args.get("path") or "").strip().strip(".")
    if not path:
        keys = sorted(k for k in store.merged.keys())
        return jsonify(ok=True, path="", kind="container", keys=keys, live_diverged=diverged)
    try:
        raw = store.get_value(path)
    except (KeyError, IndexError, TypeError, ValueError):
        return _err(f"no such path: {path}", 404)
    if isinstance(raw, (dict, list)):
        text = json.dumps(_jsonable(raw), default=str)
        keys = list(raw.keys()) if isinstance(raw, dict) else list(range(len(raw)))
        if len(text) > _MAX_SUBTREE_CHARS:
            return jsonify(ok=True, path=path, kind="container", keys=[str(k) for k in keys],
                           truncated=True, size_chars=len(text), live_diverged=diverged,
                           hint="ask for a deeper path; this subtree is too large to return whole")
        return jsonify(ok=True, path=path, kind="container", keys=[str(k) for k in keys],
                       value=_jsonable(raw), live_diverged=diverged)
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
                   live_diverged=diverged)


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
    """Held groups + approval cards (S5 fills the hold flag; 0 until then)."""
    r = _r()
    mod = r._modifier()
    if not mod:
        return 0
    try:
        return sum(1 for c in mod.get_change_log() if getattr(c, "hold", False))
    except Exception:  # noqa: BLE001
        return 0


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
