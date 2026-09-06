"""Chat inside SM (docs/173 S4): the routes over core/agent_chat.py.

``/api/agent/chat/*`` starts, feeds, ends and observes the chip's DRIVING
session (Claude Code or Codex, headless, with SM's MCP server as their
only door to the chip) and runs READ-ONLY questions on the side. Every
event a backend produces is recorded exactly once through ``_record``:
disk first (the same ``agent_events/<day>.jsonl`` the terminal hook writes,
so a restart replays it), then the live ring, the journal line, one wake.

What this module deliberately does NOT do: run nodes (S5's ``run_node`` is
an MCP tool the agent calls; SM runs the node), render cards (S6), or set
the agent up (S7 -- ``instance/agent_setup.json`` is read here, written
there).
"""

from __future__ import annotations

import collections
import json
import logging
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

import quam_state_manager
from quam_state_manager.core import agent_backend as ab
from quam_state_manager.core import agent_chat, agent_session, limits
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.web import agent_api as aa

logger = logging.getLogger(__name__)

chat_bp = Blueprint("agent_chat", __name__, url_prefix="/api/agent/chat")

# Tests swap these for fake-CLI subclasses; S7's setup never needs to.
BACKEND_CLASSES: dict[str, type] = {"claude": ab.ClaudeBackend, "codex": ab.CodexBackend}
_ASK_KEEP = 40
_DETECT_TTL_S = 60.0
_detect_lock = threading.Lock()


# --------------------------------------------------------------- helpers

def _err(msg: str, code: int = 400, **extra):
    return jsonify(ok=False, error=msg, **extra), code


def _r():
    from quam_state_manager.web import routes
    return routes


def _setup() -> dict:
    """``instance/agent_setup.json`` (S7 writes it): exe + model per backend,
    the default backend. Absent = the CLIs on PATH."""
    p = Path(current_app.instance_path) / "agent_setup.json"
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _repo_root() -> str:
    return str(Path(quam_state_manager.__file__).resolve().parent.parent)


def _sm_url() -> str:
    """The exact origin the CSRF guard accepts -- what the MCP client sends."""
    return request.host_url.rstrip("/")


def _home() -> str:
    """An EMPTY folder of SM's own: the cwd when no calibrations folder is
    set. Never the server's cwd -- measured 2026-09-06: started in SM's repo,
    Claude read the repo's CLAUDE.md as the lab's and tried to run `claude -p`
    itself for three minutes."""
    p = Path(current_app.instance_path) / "agent_home"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def _cwd() -> str:
    """The agent works in the calibrations folder the Experiment Runner was
    told about (machine-wide, docs/80), else in the empty home."""
    try:
        from quam_state_manager.core import scheduler
        folder = scheduler.load_settings(_r()._sched_inst()).get("calibrations_folder") or ""
    except Exception:  # noqa: BLE001
        return _home()
    folder = str(folder).strip()
    return folder if folder and Path(folder).is_dir() else _home()


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", str(name or "chip"))[:60]


def _manager() -> agent_chat.ChatManager:
    app = current_app._get_current_object()
    m = app.config.get("agent_chat")
    if m is None:
        m = agent_chat.ChatManager(_recorder(app), app.instance_path)
        app.config["agent_chat"] = m
    return m


def _recorder(app):
    def record(rec: dict) -> None:
        with app.app_context():
            try:
                _record(rec)
            except Exception:  # noqa: BLE001
                logger.debug("chat record failed", exc_info=True)
    return record


def _record(rec: dict) -> None:
    """One chat event -> (asks: the ask buffer) | (driving: disk, ring,
    journal, wake). Runs on the process reader thread under an app context."""
    if rec.get("origin") == "ask":
        asks: collections.OrderedDict = current_app.config.setdefault("agent_asks", collections.OrderedDict())
        asks.setdefault(rec["ask_id"], []).append(rec)
        while len(asks) > _ASK_KEEP:
            asks.popitem(last=False)
        aa._bump()
        aa._wake()
        return
    ev = aa._events()                               # initialise (and replay) BEFORE writing today's file
    rec["n"] = current_app.config["agent_chat_n"] = _next_n(ev)
    try:
        d = aa._events_dir()
        d.mkdir(parents=True, exist_ok=True)
        with open(d / (datetime.now().strftime("%Y-%m-%d") + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except OSError:
        logger.debug("chat event disk write failed", exc_info=True)
    with aa._events_lock:
        ev.append(rec)
    try:
        aa._absorb(rec)
    except Exception:  # noqa: BLE001
        logger.debug("absorb failed", exc_info=True)
    aa._bump()
    aa._wake()


def _next_n(ev) -> int:
    """The page's cursor, monotonic ACROSS restarts: the ring replays
    yesterday's and today's chat events with their old ``n``, so a fresh
    counter starting at 1 would hide every new event behind ``after=``
    (measured 2026-09-06: three turns invisible to a client after a restart)."""
    cur = current_app.config.get("agent_chat_n")
    if cur is None:
        with aa._events_lock:
            cur = max((int(e.get("n") or 0) for e in ev if e.get("origin") == "chat"), default=0)
    return int(cur) + 1


def _facts(chip: str, mode: str, cwd: str | None) -> str:
    modes = {"auto": "writes need no approval", "ask-writes": "every write is held for the human's approval",
             "ask-all": "every node run and every write is held for the human's approval"}
    return (f"\nChip: {chip}. Mode: {mode} ({modes.get(mode, mode)}). "
            f"Working folder: {cwd}. Today: {datetime.now().strftime('%Y-%m-%d')}.")


def _build_backend(name: str, *, readonly: bool, chip: str, mode: str, cwd: str | None,
                   model: str | None = None) -> ab.Backend:
    cls = BACKEND_CLASSES.get(name)
    if cls is None:
        raise ValueError(f"unknown backend {name!r} (claude | codex)")
    setup = _setup().get(name) or {}
    exe = str(setup.get("exe") or name)
    model = model or setup.get("model") or None
    mj = Path(current_app.instance_path) / "agent_mcp" / f"{_safe(chip)}-{name}{'-ro' if readonly else ''}.json"
    ab.write_mcp_config(mj, ab.mcp_config(sys.executable, _repo_root(), _sm_url(), readonly=readonly, chip=chip))
    rules = agent_chat.ASK_RULES if readonly else agent_chat.DEFAULT_RULES
    return cls(exe, mj, cwd=cwd, model=model, system_prompt=rules + _facts(chip, mode, cwd), readonly=readonly,
               sm_url=_sm_url(), repo=_repo_root(), python=sys.executable, chip=chip)


def _detect_all(refresh: bool = False) -> dict:
    with _detect_lock:
        cache = current_app.config.get("agent_detect") or {}
        if not refresh and cache and time.time() - cache.get("_at", 0) < _DETECT_TTL_S:
            return cache
        setup = _setup()
        out: dict = {"_at": time.time()}
        for name in ab.BACKENDS:
            exe = str((setup.get(name) or {}).get("exe") or name)
            out[name] = dict(ab.detect(exe), exe=exe)
        current_app.config["agent_detect"] = out
        return out


def _away_block(rec: dict | None, cap: int = 12) -> str:
    """What happened on the chip since the session's last event (docs/173
    §3.3): the runs the archive holds after that moment, newest first. A
    resumed agent must not reason from a chip that moved under it."""
    since = float((rec or {}).get("updated") or 0)
    ds = aa._ds()
    if not since or ds is None:
        return ""
    try:
        rows = ds.list_runs()[:200]
    except Exception:  # noqa: BLE001
        return ""
    fresh = []
    for row in rows:
        try:
            when = datetime.strptime(f"{row.get('date')} {row.get('time')}", "%Y-%m-%d %H:%M:%S").timestamp()
        except (TypeError, ValueError):
            continue
        if when > since:
            fresh.append(f"#{row.get('run_id')} {row.get('experiment_name')} {' '.join(row.get('qubits') or [])} "
                         f"({row.get('outcome') or row.get('status') or '?'})")
    if not fresh:
        return ""
    head = f"[While you were away since {datetime.fromtimestamp(since).strftime('%H:%M')}: {len(fresh)} run(s)]"
    more = f"\n... and {len(fresh) - cap} more" if len(fresh) > cap else ""
    return head + "\n" + "\n".join(fresh[:cap]) + more + "\n\n"


def _chip_or_409():
    if not _r()._active_path():
        return None, _err("open a chip first", 409)
    return aa._chip_name(), None


def _until(v) -> float | None:
    if v in (None, "", 0, "0"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(v)).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------- routes

@chat_bp.route("/backends")
def backends():
    det = _detect_all(refresh=request.args.get("refresh") == "1")
    setup = _setup()
    chip = aa._chip_name() if _r()._active_path() else None
    return jsonify(ok=True, chip=chip, default=str(setup.get("default_backend") or "claude"),
                   backends={k: v for k, v in det.items() if not k.startswith("_")},
                   cwd=_cwd(), python=sys.executable, sm_url=_sm_url())


@chat_bp.route("/status")
def status():
    chip = aa._chip_name() if _r()._active_path() else None
    mgr = _manager()
    rec = agent_session.load(current_app.instance_path, chip) if chip else None
    try:
        lim = limits.load(current_app.instance_path, chip) if chip else dict(limits.DEFAULTS)
    except Exception:  # noqa: BLE001
        lim = dict(limits.DEFAULTS)
    live = mgr.status(chip) if chip else None
    foreign = bool(rec and agent_session.alive(rec) and not (live and live["alive"]))
    return jsonify(ok=True, chip=chip, session=live, file=agent_session.summary(rec), mode=lim.get("mode"),
                   foreign_alive=foreign, resumable=bool(rec and rec.get("session_id") and not foreign),
                   agent_seq=int(current_app.config.get("agent_seq") or 0), cwd=_cwd())


@chat_bp.route("/start", methods=["POST"])
def start():
    """Start (or resume) the chip's driving session. Refused while another
    agent session is alive on the chip -- two drivers on one OPX is never
    safe (docs/80's one hard refusal, applied to the chat)."""
    chip, bad = _chip_or_409()
    if bad:
        return bad
    data = request.get_json(silent=True) or request.form.to_dict()
    inst = current_app.instance_path
    actor = _r()._request_actor()
    name = str(data.get("backend") or _setup().get("default_backend") or "claude").lower()
    if name not in BACKEND_CLASSES:
        return _err(f"unknown backend {name!r} (claude | codex)")
    try:
        lim = limits.load(inst, chip)
    except Exception:  # noqa: BLE001
        lim = dict(limits.DEFAULTS)
    mode = lim.get("mode") or limits.DEFAULTS["mode"]
    want = data.get("mode")
    if want and want != mode:
        try:
            limits.save(inst, chip, {"mode": want}, who=actor)
            mode = want
        except limits.LimitError as exc:
            return _err(str(exc))
    until = _until(data.get("until"))
    mgr = _manager()
    cur = mgr.get(chip)
    rec = agent_session.load(inst, chip)
    if rec and agent_session.alive(rec) and not (cur and cur.alive()):
        return _err(f"another {rec.get('backend') or 'agent'} session (pid {rec.get('pid')}, "
                    f"by {rec.get('owner') or '?'}) is alive on {chip}; stop it first", 409, session=agent_session.summary(rec))
    resume = data.get("resume")
    if resume in ("last", True, "1", "true"):
        resume = (rec or {}).get("session_id") if rec and rec.get("backend") == name else None
        if not resume:
            return _err("nothing to resume for this backend on this chip", 409)
    elif resume:
        resume = str(resume)
    else:
        resume = None
    cwd = _cwd()
    try:
        backend = _build_backend(name, readonly=False, chip=chip, mode=mode, cwd=cwd, model=data.get("model"))
    except ValueError as exc:
        return _err(str(exc))
    prompt = str(data.get("prompt") or "").strip() or None
    away = _away_block(rec) if resume else ""
    if away:
        prompt = away + (prompt or "Continue.")
    try:
        st = mgr.start(chip, backend, owner=actor, mode=mode, until=until, prompt=prompt, resume=resume)
    except RuntimeError as exc:
        return _err(str(exc), 409)
    except ValueError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"could not start {name}: {exc}", 502)
    tail = f", until {datetime.fromtimestamp(until).strftime('%H:%M')}" if until else ""
    tail += ", resumed" if resume else ""
    journal_mod.append(inst, chip, f"{name} session started in SM by {actor} (mode {mode}{tail})", kind="sm")
    aa._bump()
    aa._wake()
    return jsonify(ok=True, session=st, cwd=cwd, resumed=bool(resume), away=bool(away))


@chat_bp.route("/send", methods=["POST"])
def send():
    chip, bad = _chip_or_409()
    if bad:
        return bad
    data = request.get_json(silent=True) or request.form.to_dict()
    text = str(data.get("text") or "").strip()
    if not text:
        return _err("text required")
    mgr = _manager()
    cur = mgr.get(chip)
    if cur is None or (not cur.alive() and not cur.backend.one_turn_per_process) or cur.ended:
        return _err("no running session on this chip; start one", 409)
    inst = current_app.instance_path
    rec = agent_session.load(inst, chip)
    if agent_session.stopped(rec):
        # a human typing again IS the resumption; the flag run_node reads is cleared first
        agent_session.save(inst, chip, agent_stop=None)
        journal_mod.append(inst, chip, f"resumed by {_r()._request_actor()} (Stop cleared)", kind="sm")
    res = mgr.send(chip, text)
    if res.get("error"):
        return _err(res["error"], 409)
    aa._bump()
    aa._wake()
    return jsonify(ok=True, **res, session=mgr.status(chip))


@chat_bp.route("/end", methods=["POST"])
def end():
    chip, bad = _chip_or_409()
    if bad:
        return bad
    mgr = _manager()
    cur = mgr.get(chip)
    if cur is None:
        return _err("no session on this chip", 409)
    mgr.end(chip)
    inst = current_app.instance_path
    agent_session.save(inst, chip, pid=None)
    journal_mod.append(inst, chip, f"{cur.backend.name} session ended by {_r()._request_actor()}", kind="sm")
    aa._bump()
    aa._wake()
    return jsonify(ok=True, session=mgr.status(chip))


@chat_bp.route("/events")
def events():
    """This chip's chat events after ``after`` (the per-app counter ``n``);
    the page polls it on every wake. Text events carry their text; tool
    events their summary/failed/error -- the card renderer (S6) reads these."""
    chip = aa._chip_name() if _r()._active_path() else None
    after = int(request.args.get("after") or 0)
    limit = max(1, min(int(request.args.get("limit") or 200), aa._EVENT_RING))
    with aa._events_lock:
        ev = [e for e in aa._events() if e.get("origin") == "chat" and int(e.get("n") or 0) > after
              and (chip is None or e.get("chip") == chip)]
    ev = ev[-limit:]
    return jsonify(ok=True, chip=chip, events=ev, last=max((int(e.get("n") or 0) for e in ev), default=after),
                   agent_seq=int(current_app.config.get("agent_seq") or 0),
                   session=_manager().status(chip) if chip else None)


@chat_bp.route("/ask", methods=["POST"])
def ask():
    """A question about the chip: a read-only one-shot, never the driving
    session (docs/173 §2.7). Answer via GET /ask/<id>."""
    chip, bad = _chip_or_409()
    if bad:
        return bad
    data = request.get_json(silent=True) or request.form.to_dict()
    text = str(data.get("text") or "").strip()
    if not text:
        return _err("text required")
    name = str(data.get("backend") or _setup().get("default_backend") or "claude").lower()
    try:
        lim_mode = limits.load(current_app.instance_path, chip).get("mode")
    except Exception:  # noqa: BLE001
        lim_mode = limits.DEFAULTS["mode"]
    try:
        backend = _build_backend(name, readonly=True, chip=chip, mode=lim_mode or "ask-writes", cwd=_cwd(),
                                 model=data.get("model"))
        res = _manager().ask(chip, backend, text)
    except ValueError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"could not start {name}: {exc}", 502)
    current_app.config.setdefault("agent_asks", collections.OrderedDict()).setdefault(res["ask_id"], [])
    return jsonify(ok=True, **res, backend=name)


@chat_bp.route("/ask/<ask_id>")
def ask_get(ask_id: str):
    asks = current_app.config.get("agent_asks") or {}
    if ask_id not in asks:
        return _err("unknown ask", 404)
    evs = list(asks[ask_id])
    alive = _manager().ask_alive(ask_id)
    done = any(e.get("hook_event_name") == "Stop" for e in evs) or alive is False
    texts = [e.get("text") for e in evs if e.get("hook_event_name") == "Text" and e.get("text")]
    result = next((e for e in reversed(evs) if e.get("hook_event_name") in ("Result", "Error")), None)
    failed = bool(result and result.get("failed"))
    answer = (texts[-1] if texts else (result or {}).get("summary")) if done else None
    return jsonify(ok=True, ask_id=ask_id, done=done, failed=failed, answer=answer,
                   error=(result or {}).get("error") if failed else None,
                   limited=bool(result and result.get("limited")),
                   tools=[{"tool": e.get("tool_name"), "summary": e.get("summary"), "failed": bool(e.get("failed"))}
                          for e in evs if e.get("hook_event_name") in ("PostToolUse", "PostToolUseFailure")],
                   events=evs)
