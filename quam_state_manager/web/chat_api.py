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
import uuid
from datetime import datetime, timedelta
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
_LIVE_LINGER_S = 4.0      # docs/289: a finished turn stays visible this long, so its last state is seen
_LIVE_KEEP_S = 120.0      # a finished ask's live view can still be fetched by id for this long
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
        if rec.get("hook_event_name") == "Stop":
            _post_feed_answer(rec["ask_id"], asks.get(rec["ask_id"]) or [])
        aa._bump()
        aa._wake()
        return
    ev = aa._events()                               # initialise (and replay) BEFORE writing today's file
    # docs/297: numbering and writing are one step -- a clear (which takes this lock to read
    # the counter) never sees a number whose event is not yet on disk and in the ring
    with _REC_LOCK:
        rec["n"] = _next_n(ev)                       # sets agent_chat_n under _N_LOCK (review R4-7)
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


def _post_feed_answer(ask_id: str, evs: list[dict]) -> None:
    """docs/247 (C-06): a question typed in the Agent panel is answered by the READ-ONLY one-shot,
    and its answer lands in the panel's feed as one card -- once, when the ask stops. Asks from
    elsewhere (Setup -> Test) are not in the feed set and stay off the feed."""
    feed: dict = current_app.config.setdefault("agent_feed_asks", {})
    meta = feed.pop(ask_id, None)
    if not meta:
        return
    texts = [e.get("text") for e in evs if e.get("hook_event_name") == "Text" and e.get("text")]
    result = next((e for e in reversed(evs) if e.get("hook_event_name") in ("Result", "Error")), None)
    if texts:
        text = texts[-1]
    elif result and result.get("failed"):
        text = "The read-only question did not get an answer: " + str(result.get("error") or "the CLI failed")[-300:]
    else:
        text = "(no answer)"
    _record({"ts": time.time(), "hook_event_name": "Text", "origin": "chat", "chip": meta["chip"], "text": text,
             "backend": meta["backend"], "session_id": None, "ask_id": ask_id, "readonly": True,
             "owner": meta.get("who"), "mode": None})


_N_LOCK = threading.Lock()
_REC_LOCK = threading.Lock()


def _next_n(ev) -> int:
    """The page's cursor, monotonic ACROSS restarts: the ring replays
    yesterday's and today's chat events with their old ``n``, so a fresh
    counter starting at 1 would hide every new event behind ``after=``
    (measured 2026-09-06: three turns invisible to a client after a restart).

    review R4-7: the whole read-modify-write is under ``_N_LOCK`` -- two
    threads recording at once (the request's User event and the process's Init)
    otherwise read the same cur and stamp the same n, and a page holding
    ``after=n`` never sees the loser."""
    with _N_LOCK:
        cur = current_app.config.get("agent_chat_n")
        if cur is None:
            with aa._events_lock:
                cur = max((int(e.get("n") or 0) for e in ev if e.get("origin") == "chat"), default=0)
            # review R3-5/R4: the ring forgets past ~800 lines / 2 days, so a burst of hook
            # events can evict the chat events whose n we must exceed -- read the persisted
            # high-water mark AND scan the day files directly, never just the ring.
            cur = max(cur, _persisted_n(), _disk_max_chat_n())
        # docs/297: another SM process on this instance numbers too, and a clear there moves
        # the shared since_n -- the persisted mark is re-read every time, never only at start
        cur = max(int(cur), _persisted_n())
        nxt = int(cur) + 1
        current_app.config["agent_chat_n"] = nxt
    try:
        d = aa._events_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "chat_n.txt").write_text(str(nxt), encoding="utf-8")
    except OSError:
        pass
    return nxt


def _persisted_n() -> int:
    try:
        return int((aa._events_dir() / "chat_n.txt").read_text(encoding="utf-8").strip() or 0)
    except (OSError, ValueError):
        return 0


def _disk_max_chat_n() -> int:
    """The highest ``n`` of a chat event on disk (today + yesterday), so an
    evicted-from-the-ring event still lifts the counter above it."""
    hi = 0
    try:
        d = aa._events_dir()
        for k in range(2):
            f = d / ((datetime.now() - timedelta(days=k)).strftime("%Y-%m-%d") + ".jsonl")
            if not f.exists():
                continue
            for line in f.read_text(encoding="utf-8").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("origin") == "chat":
                    hi = max(hi, int(e.get("n") or 0))
    except OSError:
        pass
    return hi


def _record_user(chip: str, text: str, who: str, backend: str, *, owner: str | None = None,
                 mode: str | None = None, **extra) -> None:
    """The person's own message, recorded like the agent's events so the card
    stream survives a reload and a restart (docs/173 S6). It carries the same
    owner/mode fields as the session's events (review R4-6)."""
    _record({"ts": time.time(), "hook_event_name": "User", "origin": "chat", "chip": chip, "text": text[:4000],
             "who": who, "backend": backend, "session_id": None, "owner": owner or who, "mode": mode, **extra})


def session_open(cur) -> bool:
    """Is this chip's driving conversation still open for a next message? Codex runs ONE process per
    turn, so between turns its process is gone while the conversation is not: treating that as "no
    session" started a fresh thread for every message (C-03, docs/247)."""
    if cur is None or cur.ended:
        return False
    return cur.alive() or bool(cur.backend.one_turn_per_process and cur.session_id)


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
    # docs/246 A-06: the bridge's SM_CHIP pin is the open chip's KEY (one per folder), not its display
    # name -- a second folder called the same would otherwise pass the in-app session's own pin. The key
    # also names the MCP config file, so two such folders never share one.
    pin = aa._chip_key()
    # docs/253: the driving session's bridge carries a value only SM and this config hold, so a plan
    # SM's in-app agent drives is told apart from one a terminal agent drives (a question needs none)
    secret = None if readonly else "app-" + uuid.uuid4().hex
    mj = Path(current_app.instance_path) / "agent_mcp" / f"{_safe(pin)}-{name}{'-ro' if readonly else ''}.json"
    ab.write_mcp_config(mj, ab.mcp_config(sys.executable, _repo_root(), _sm_url(), readonly=readonly, chip=pin,
                                          session=secret))
    rules = agent_chat.ASK_RULES if readonly else agent_chat.DEFAULT_RULES
    b = cls(exe, mj, cwd=cwd, model=model, system_prompt=rules + _facts(chip, mode, cwd), readonly=readonly,
            sm_url=_sm_url(), repo=_repo_root(), python=sys.executable, chip=pin)
    b.session_secret = secret
    why = b.preflight()
    if why:
        raise ValueError(why)
    return b


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
    from quam_state_manager.core import story
    fresh = []
    for row in rows:
        # docs/262: the run's instant vs the session's epoch, not the folder digits
        # read in this machine's zone; the "since HH:MM" below stays as printed
        when = story.run_epoch(row, ds)
        if when is None:
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
    key = aa._chip_key() if chip else None
    mgr = _manager()
    if chip:
        aa._reconcile_grant()                     # docs/253: never show a grant that ended
    rec = agent_session.load(current_app.instance_path, key) if chip else None
    try:
        lim = limits.load(current_app.instance_path, key) if chip else dict(limits.DEFAULTS)
    except Exception:  # noqa: BLE001
        lim = dict(limits.DEFAULTS)
    live = mgr.status(key) if chip else None
    foreign = bool(rec and agent_session.alive(rec) and not (live and live["alive"]))
    return jsonify(ok=True, chip=chip, chip_key=key, session=live, file=agent_session.summary(rec), mode=lim.get("mode"),
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
    key = aa._chip_key()
    data = request.get_json(silent=True) or request.form.to_dict()
    inst = current_app.instance_path
    actor = _r()._request_actor()
    name = str(data.get("backend") or _setup().get("default_backend") or "claude").lower()
    if name not in BACKEND_CLASSES:
        return _err(f"unknown backend {name!r} (claude | codex)")
    try:
        lim = limits.load(inst, key)
    except Exception:  # noqa: BLE001
        lim = dict(limits.DEFAULTS)
    mode = lim.get("mode") or limits.DEFAULTS["mode"]
    want = data.get("mode")
    if want and want != mode:
        try:
            limits.save(inst, key, {"mode": want}, who=actor, journal_chip=chip)
            mode = want
        except limits.LimitError as exc:
            return _err(str(exc))
    until = _until(data.get("until"))
    mgr = _manager()
    cur = mgr.get(key)
    rec = agent_session.load(inst, key)
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
    from quam_state_manager.core import agent_conversation
    conv = agent_conversation.load(inst, chip)
    if resume and agent_conversation.closed(conv, resume):
        return _err("that conversation was cleared; start a new one", 409)
    cwd = _cwd()
    try:
        backend = _build_backend(name, readonly=False, chip=chip, mode=mode, cwd=cwd, model=data.get("model"))
    except ValueError as exc:
        return _err(str(exc))
    prompt = str(data.get("prompt") or "").strip() or None
    away = _away_block(rec) if resume else ""
    if away:
        prompt = away + (prompt or "Continue.")
    from quam_state_manager.core import agent_conversation
    agent_conversation.reopen(inst, chip, _current_n())   # docs/297: a reused session id is the new conversation's from here
    try:
        st = mgr.start(key, backend, owner=actor, mode=mode, until=until, prompt=prompt, resume=resume, display=chip)
    except RuntimeError as exc:
        return _err(str(exc), 409)
    except ValueError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"could not start {name}: {exc}", 502)
    tail = f", until {datetime.fromtimestamp(until).strftime('%H:%M')}" if until else ""
    tail += ", resumed" if resume else ""
    journal_mod.append(inst, chip, f"{name} session started in SM by {actor} (mode {mode}{tail})", kind="sm")
    if prompt:
        _record_user(chip, prompt, actor, name, owner=actor, mode=mode)
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
    key = aa._chip_key()
    mgr = _manager()
    cur = mgr.get(key)
    if not session_open(cur):
        return _err("no running session on this chip; start one", 409)
    inst = current_app.instance_path
    from quam_state_manager.core import agent_conversation
    if _cleared_under(cur, agent_conversation.load(inst, chip)):
        mgr.end(key)
        aa._bump()
        aa._wake()
        return _err("this conversation was cleared (in another window); send again to start a new one", 409,
                    cleared=True)
    rec = agent_session.load(inst, key)
    if agent_session.stopped(rec):
        # a human typing again IS the resumption; the flag run_node reads is cleared first
        agent_session.save(inst, key, agent_stop=None)
        journal_mod.append(inst, chip, f"resumed by {_r()._request_actor()} (Stop cleared)", kind="sm")
    res = mgr.send(key, text)
    if res.get("error"):
        return _err(res["error"], 409)
    _record_user(chip, text, _r()._request_actor(), cur.backend.name, owner=cur.owner, mode=cur.mode)
    aa._bump()
    aa._wake()
    return jsonify(ok=True, **res, session=mgr.status(key))


@chat_bp.route("/end", methods=["POST"])
def end():
    chip, bad = _chip_or_409()
    if bad:
        return bad
    key = aa._chip_key()
    mgr = _manager()
    cur = mgr.get(key)
    if cur is None:
        return _err("no session on this chip", 409)
    who = _r()._request_actor()
    g = _end_session(cur, key, who)
    journal_mod.append(current_app.instance_path, chip, f"{cur.backend.name} session ended by {who}"
                       + (f" -- disarmed (plan `{g.get('title')}` stopped)" if g else ""), kind="sm")
    aa._bump()
    aa._wake()
    return jsonify(ok=True, session=mgr.status(key))


def _end_session(cur, key: str, who: str) -> dict | None:
    """End the chip's in-app session; returns the grant it ended, if any."""
    _manager().end(key)
    agent_session.save(current_app.instance_path, key, pid=None)
    # docs/253 (D-06): the arming SM's in-app session drove ends with it, and so does its plan (a step
    # still running finishes and reports); a plan a terminal agent drives is not this session's
    return aa._end_grant(f"the in-app {cur.backend.name} session was ended by {who}", driver_kind="app")


# ------------------------------------------------- clear / archive (docs/297)

def _current_n() -> int:
    """The chat counter's high-water mark, without taking a number."""
    ev = aa._events()
    with _N_LOCK:
        cur = current_app.config.get("agent_chat_n")
        if cur is None:
            with aa._events_lock:
                cur = max((int(e.get("n") or 0) for e in ev if e.get("origin") == "chat"), default=0)
            cur = max(cur, _persisted_n(), _disk_max_chat_n())
        return max(int(cur), _persisted_n())


def _raise_persisted_n(n: int) -> None:
    """Every SM process on this instance numbers above ``n`` from now on."""
    with _N_LOCK:
        try:
            if _persisted_n() < n:
                d = aa._events_dir()
                d.mkdir(parents=True, exist_ok=True)
                (d / "chat_n.txt").write_text(str(n), encoding="utf-8")
        except OSError:
            pass


_CHAT_MARK = b'"origin": "chat"'


def _conversation_events(chip: str, after_n: int, upto_n: int, since_ts: float | None) -> list[dict]:
    """The chip's chat events with ``after_n < n <= upto_n``: every day file
    from the previous clear on (the ring forgets past 800 lines / 7 days, and
    a conversation can be older than that), plus the ring for an event whose
    disk write failed. Lines are screened as bytes first: a day file is
    mostly hook events, and only chat lines are parsed."""
    found: dict[int, dict] = {}
    name = json.dumps(chip).encode("utf-8")

    def take(e) -> None:
        if not isinstance(e, dict) or e.get("origin") != "chat" or e.get("chip") != chip:
            return
        try:
            n = int(e.get("n") or 0)
        except (TypeError, ValueError):
            return
        if after_n < n <= upto_n:
            found.setdefault(n, e)

    first = datetime.fromtimestamp(since_ts).strftime("%Y-%m-%d") if since_ts else ""
    try:
        files = sorted(f for f in aa._events_dir().glob("*.jsonl") if re.match(r"^\d{4}-\d{2}-\d{2}$", f.stem))
    except OSError:
        files = []
    for f in files:
        if f.stem < first:
            continue
        try:
            data = f.read_bytes()
        except OSError:
            continue
        for line in data.splitlines():
            if _CHAT_MARK not in line or name not in line:
                continue
            try:
                take(json.loads(line))
            except ValueError:
                continue
    with aa._events_lock:
        for e in list(aa._events()):
            take(e)
    return [found[k] for k in sorted(found)]


def _busy_reason(chip: str, key: str, cur) -> str | None:
    """Why the conversation cannot be cleared right now, or None. A clear
    ends the agent's memory of the conversation, so it waits until nothing
    of it is still working."""
    from quam_state_manager.core import agent_plans
    if cur is not None and cur.busy():
        return "the agent is still answering; press Stop now or wait for it to finish, then clear"
    mgr = _manager()
    for aid, c in list((current_app.config.get("agent_live_asks") or {}).items()):
        if c == chip and mgr.ask_alive(aid):
            return "a question is still being answered; clear after its answer arrives"
    for p in agent_plans.load(current_app.instance_path, key):
        if p.get("status") in ("running", "stopping"):
            return f"plan `{p.get('title') or p.get('id')}` is {p.get('status')}; stop it or let it finish, then clear"
    for m in aa._registry().runs.values():
        if m.get("chip") == key and m.get("status") in ("starting", "running"):
            return f"`{m.get('node')}` is still running; clear after it ends"
    rec = agent_session.load(current_app.instance_path, key)
    if rec and agent_session.alive(rec) and not (cur and cur.alive()):
        return (f"another {rec.get('backend') or 'agent'} session (pid {rec.get('pid')}, by "
                f"{rec.get('owner') or '?'}) is alive on this chip; stop it first")
    return None


def _cleared_under(cur, conv: dict) -> bool:
    """Was this in-app session's conversation cleared -- in this process or in
    another SM process on the same instance (which cannot end our session)?"""
    from quam_state_manager.core import agent_conversation as conv_mod
    if cur is None:
        return False
    if conv_mod.closed(conv, cur.session_id):
        return True
    at = conv.get("cleared_at")
    return bool(at) and float(cur.started or 0) < float(at)


@chat_bp.route("/clear", methods=["POST"])
def clear():
    """docs/297: start the panel's conversation over. The in-app session ends
    and its id is forgotten, so the next message starts a fresh context.
    ``keep`` (default) puts the cleared conversation in the chip's archive;
    ``keep=0`` keeps no copy. ``since_n`` (optional) is the conversation the
    presser saw: if another window cleared it meanwhile, 409 and nothing
    happens. The index and the archive are written FIRST; only when they are
    on disk does the session end -- a failed clear changes nothing. Plans,
    runs, approvals, the Calibration log and the agent event log are never
    touched."""
    from quam_state_manager.core import agent_conversation as conv_mod
    chip, bad = _chip_or_409()
    if bad:
        return bad
    data = request.get_json(silent=True) or request.form.to_dict()
    keep = str(data.get("keep", "1")).lower() not in ("0", "false", "no", "")
    expect = data.get("since_n")
    try:
        expect = None if expect in (None, "") else int(expect)
    except (TypeError, ValueError):
        return _err("since_n must be an integer")
    key = aa._chip_key()
    inst = current_app.instance_path
    cur = _manager().get(key)
    why = _busy_reason(chip, key, cur)
    if why:
        return _err(why, 409)
    try:
        conv = conv_mod.read_strict(inst, chip)
    except conv_mod.Unreadable as exc:
        return _err(f"the conversation index cannot be read ({exc}); nothing was cleared", 500)
    if expect is not None and expect != int(conv["since_n"]):
        return _err("this conversation was already cleared (in another window); nothing more was cleared", 409,
                    since_n=conv["since_n"])
    who = _r()._request_actor()
    rec = agent_session.load(inst, key) or {}
    closed = (cur.session_id if cur is not None and not cur.ended else None) or rec.get("session_id")
    with _REC_LOCK:
        upto = _current_n()          # every event numbered up to here is on disk and in the ring
    events = _conversation_events(chip, int(conv["since_n"]), upto, conv.get("cleared_at"))
    try:
        meta = conv_mod.clear(inst, chip, since_n=upto, events=events, who=who, keep=keep, closed_session=closed,
                              expect_since=int(conv["since_n"]))
    except conv_mod.Moved:
        return _err("this conversation was already cleared (in another window); nothing more was cleared", 409)
    except conv_mod.Unreadable as exc:
        return _err(f"the conversation index cannot be read ({exc}); nothing was cleared", 500)
    except OSError as exc:
        return _err(f"the conversation could not be saved ({exc}); nothing was cleared", 500)
    _raise_persisted_n(upto)
    # only now, with the clear on disk: the session ends and its id is forgotten
    ended = None
    if cur is not None and not cur.ended:
        ended = cur.backend.name
        g = _end_session(cur, key, who)
        if g:
            ended += f" -- disarmed (plan `{g.get('title')}` stopped)"
    if rec.get("session_id"):
        agent_session.save(inst, key, session_id=None)       # the next Start cannot resume what was cleared
    if events or ended or rec.get("session_id"):
        say = f"agent conversation cleared by {who}"
        say += f" ({meta['messages']} messages archived)" if meta else (" (not kept)" if not keep else "")
        if ended:
            say += f"; {ended} session ended"
        journal_mod.append(inst, chip, say, kind="sm")
    aa._bump()
    aa._wake()
    return jsonify(ok=True, since_n=upto, archive=meta, kept=bool(meta), ended=bool(ended))


@chat_bp.route("/archives")
def archives_list():
    from quam_state_manager.core import agent_conversation as conv_mod
    chip, bad = _chip_or_409()
    if bad:
        return bad
    return jsonify(ok=True, chip=chip, archives=conv_mod.archives(current_app.instance_path, chip))


ARCHIVE_CARDS = 2000


@chat_bp.route("/archives/<aid>")
def archive_get(aid: str):
    """One kept conversation as read-only cards (the newest ``ARCHIVE_CARDS``;
    ``omitted`` says how many earlier ones are not shown)."""
    from quam_state_manager.core import agent_conversation as conv_mod
    chip, bad = _chip_or_409()
    if bad:
        return bad
    got = conv_mod.read_archive(current_app.instance_path, chip, aid)
    if got is None:
        return _err("no such archived conversation", 404)
    meta, events = got
    cards = [c for c in (aa._chat_card(e) for e in events) if c]
    omitted = max(0, len(cards) - ARCHIVE_CARDS)
    return jsonify(ok=True, chip=chip, archive=meta, cards=cards[omitted:], omitted=omitted)


@chat_bp.route("/archives/<aid>/delete", methods=["POST"])
def archive_delete(aid: str):
    from quam_state_manager.core import agent_conversation as conv_mod
    chip, bad = _chip_or_409()
    if bad:
        return bad
    try:
        gone = conv_mod.delete_archive(current_app.instance_path, chip, aid)
    except conv_mod.Unreadable as exc:
        return _err(f"the conversation index cannot be read ({exc}); nothing was deleted", 500)
    except OSError as exc:
        return _err(f"the archive could not be updated ({exc}); nothing was deleted", 500)
    if not gone:
        return _err("no such archived conversation", 404)
    aa._bump()
    aa._wake()
    return jsonify(ok=True)


def _live_item(proc, **extra) -> dict:
    snap = proc.live.snapshot()
    snap.update(extra)
    snap["alive"] = proc.alive()
    return snap


@chat_bp.route("/live")
def live():
    """What the CLI is doing right now (docs/289): the open chip's driving
    session while its turn is open, and the panel's questions in flight. The
    page polls this about once a second, and only while something is in
    flight; a finished turn lingers a few seconds so its last state is seen.
    ``?id=<ask_id>|session`` asks for one item even after it finished."""
    chip = aa._chip_name() if _r()._active_path() else None
    now = time.time()
    if not chip:
        return jsonify(ok=True, now=now, items=[])
    want = str(request.args.get("id") or "")
    mgr = _manager()
    items = []

    def fresh(proc) -> bool:
        ended = proc.live.ended
        return ended is None or now - ended < _LIVE_LINGER_S

    s = mgr.get(aa._chip_key())
    if s is not None and s.proc is not None and (s.busy() or want == "session" or fresh(s.proc)):
        items.append(_live_item(s.proc, id="session", kind="task", queued=len(s.queue)))
    reg: dict = current_app.config.setdefault("agent_live_asks", {})
    for aid, ask_chip in list(reg.items()):
        proc = mgr.asks.get(aid)
        if proc is None or (proc.live.ended and now - proc.live.ended > _LIVE_KEEP_S):
            reg.pop(aid, None)
            continue
        if ask_chip != chip:
            continue
        if proc.alive() or want == aid or fresh(proc):
            items.append(_live_item(proc, id=aid, kind="ask"))
    return jsonify(ok=True, now=now, items=items)


@chat_bp.route("/events")
def events():
    """This chip's chat events after ``after`` (the per-app counter ``n``);
    the page polls it on every wake. Text events carry their text; tool
    events their summary/failed/error -- the card renderer (S6) reads these."""
    chip = aa._chip_name() if _r()._active_path() else None
    after = int(request.args.get("after") or 0)
    limit = max(1, min(int(request.args.get("limit") or 200), aa._EVENT_RING))
    from quam_state_manager.core import agent_conversation as conv_mod
    conv = conv_mod.load(current_app.instance_path, chip) if chip else None
    with aa._events_lock:
        ev = [e for e in aa._events() if e.get("origin") == "chat" and int(e.get("n") or 0) > after
              and (chip is None or e.get("chip") == chip) and (conv is None or conv_mod.visible(e, conv))]
    ev = ev[-limit:]
    return jsonify(ok=True, chip=chip, events=ev, last=max((int(e.get("n") or 0) for e in ev), default=after),
                   agent_seq=int(current_app.config.get("agent_seq") or 0),
                   session=_manager().status(aa._chip_key()) if chip else None)


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
        lim_mode = limits.load(current_app.instance_path, aa._chip_key()).get("mode")
    except Exception:  # noqa: BLE001
        lim_mode = limits.DEFAULTS["mode"]
    feed = str(data.get("feed") or "").lower() in ("1", "true", "yes")
    try:
        backend = _build_backend(name, readonly=True, chip=chip, mode=lim_mode or "ask-writes", cwd=_cwd(),
                                 model=data.get("model"))
        res = _manager().ask(chip, backend, text)
    except ValueError as exc:
        return _err(str(exc))
    except OSError as exc:
        return _err(f"could not start {name}: {exc}", 502)
    current_app.config.setdefault("agent_asks", collections.OrderedDict()).setdefault(res["ask_id"], [])
    if feed:
        # docs/289: the panel shows this question's live progress until its answer card lands
        current_app.config.setdefault("agent_live_asks", {})[res["ask_id"]] = chip
        # docs/247 (C-06): the panel's question -- its card now, its answer card when the ask stops
        who = _r()._request_actor()
        current_app.config.setdefault("agent_feed_asks", {})[res["ask_id"]] = {"chip": chip, "backend": name, "who": who}
        _record_user(chip, text, who, name, owner=who, ask_id=res["ask_id"], readonly=True)
        aa._bump()
        aa._wake()
    return jsonify(ok=True, **res, backend=name, feed=feed)


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
