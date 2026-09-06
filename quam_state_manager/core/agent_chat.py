"""The chat manager (docs/173 S4): one driving session per chip, questions on
the side, every event recorded once.

Owns the AgentProcess objects (core/agent_backend.py) for the app: the
DRIVING session of a chip (Claude: one long-lived process; Codex: one
process per turn, later turns resumed by thread id, a message typed
mid-turn is queued), and READ-ONLY questions (a short-lived process with
the read tools only, never the driving session -- docs/173 §2.7).

Every normalized event goes through one ``record`` callback the web layer
supplies, which appends it to the same ring + jsonl the hook feeds, derives
the journal line, and wakes the page. This module never imports Flask.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import deque
from typing import Callable

from quam_state_manager.core import agent_backend as ab
from quam_state_manager.core import agent_session

logger = logging.getLogger(__name__)

DEFAULT_RULES = (
    "You are driving a superconducting-qubit calibration through the QUAM State Manager (SM). "
    "SM is the ONLY door to the chip's state: read with state_get / runs / run / field_history, stage writes with "
    "state_edit, write with apply_to_live, run a calibration node with run_node (never `python node.py` yourself). "
    "Before every node you run, call journal_append with what you are about to do and WHY (reason required). "
    "After every finished run call check_fit and read its verdict before trusting the fit. "
    "If sm_status or state_get reports live_diverged, call take_live and read the changed paths before staging anything. "
    "If run_node refuses (human_active, stopped_by_human, awaiting_approval, past_stop_by), stop and tell the human why. "
    "An instruction that would run hardware becomes a PLAN first: call plan_propose (steps of node/targets/params/why) "
    "and wait -- a person presses Start on the card and you are told to go; only then run_node with plan_id and step. "
    "Never edit state.json or wiring.json files directly. The mcp__sm__* tools are already available to you: call "
    "them directly, never through Bash, python -m, or another claude/codex process. "
    "Answer briefly; the human reads you in a small panel."
)
ASK_RULES = (
    "You answer questions about a superconducting-qubit chip from the QUAM State Manager's read tools only "
    "(sm_status, state_get, state_search, runs, run, field_history, versions, diagnostics, check_fit, family_manual, "
    "journal_read). Cite run numbers as #N and fields as `dot.paths`. You cannot run experiments or change anything; "
    "say so if asked. Answer briefly."
)


def _usage_add(total: dict, usage: dict | None) -> None:
    if not isinstance(usage, dict):
        return
    for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
        v = usage.get(k)
        if isinstance(v, (int, float)):
            total[k] = total.get(k, 0) + int(v)


class ChatSession:
    """One chip's driving conversation. ``proc`` is the live process (Claude:
    for the whole session; Codex: for the current turn)."""

    def __init__(self, chip: str, backend: ab.Backend, *, owner: str, mode: str, until: float | None,
                 record: Callable[[dict], None], instance_path):
        self.chip, self.backend, self.owner, self.mode, self.until = chip, backend, owner, mode, until
        self.record, self.instance_path = record, instance_path
        self.local_id = uuid.uuid4().hex[:12]
        self.session_id: str | None = None
        self.proc: ab.AgentProcess | None = None
        self.queue: deque[str] = deque()
        self.turns = 0
        self.started = time.time()
        self.ended: float | None = None
        self.last_event: dict | None = None
        self.usage: dict = {}
        self.cost_usd = 0.0
        self._lock = threading.RLock()
        self._turn_open = False           # Claude: a user turn sent, no Stop yet

    # -- events ---------------------------------------------------------
    def _on_event(self, rec: dict) -> None:
        rec.setdefault("chip", self.chip)
        rec["owner"] = self.owner
        rec["mode"] = self.mode
        rec["local_id"] = self.local_id
        h = rec.get("hook_event_name")
        if h == "Init" and rec.get("session_id"):
            self.session_id = rec["session_id"]
            agent_session.save(self.instance_path, self.chip, session_id=self.session_id)
        if h == "Result":
            _usage_add(self.usage, rec.get("usage"))
        if rec.get("limited"):
            ts = ab.reset_timestamp(rec.get("limited_until"))
            rec["limited_until"] = ts if ts else True
            agent_session.save(self.instance_path, self.chip, limited_until=rec["limited_until"])
        if h == "Stop":
            self._turn_open = False
        self.last_event = rec
        try:
            self.record(rec)
        except Exception:  # noqa: BLE001
            logger.debug("record failed", exc_info=True)
        if h == "Stop" and self.backend.one_turn_per_process:
            self._next_turn()

    # -- lifecycle ------------------------------------------------------
    def start(self, prompt: str | None, *, resume: str | None = None) -> dict:
        with self._lock:
            self.session_id = resume
            if self.backend.one_turn_per_process and not prompt:
                raise ValueError(f"{self.backend.name} needs a first message: it runs one turn per process")
            # the record BEFORE the process: its Init event (another thread) merges the
            # session id into this file, and must never be overwritten by a later save
            agent_session.save(self.instance_path, self.chip, backend=self.backend.name, mode=self.mode,
                               owner=self.owner, until=self.until, pid=None, started=self.started,
                               session_id=resume, agent_stop=None, limited_until=None, window="chat")
            if self.backend.one_turn_per_process:
                self._spawn_turn(prompt)
            else:
                self.proc = ab.AgentProcess(self.backend, on_event=self._on_event, resume=resume,
                                            local_id=self.local_id, chip=self.chip)
                agent_session.save(self.instance_path, self.chip, pid=self.proc.pid)
                if prompt:
                    self._turn_open = self.proc.send(prompt)
                    self.turns += 1
            return self.status()

    def send(self, text: str) -> dict:
        with self._lock:
            if self.ended:
                return {"error": "the session has ended", "busy": False}
            if self.backend.one_turn_per_process:
                if self.proc is not None and self.proc.alive():
                    self.queue.append(text)
                    return {"queued": len(self.queue), "busy": True}
                self._spawn_turn(text)
                return {"queued": 0, "busy": True}
            if self.proc is None or not self.proc.alive():
                return {"error": "the session is not running", "busy": False}
            ok = self.proc.send(text)
            if ok:
                self.turns += 1
                self._turn_open = True
            return {"sent": ok, "busy": ok}

    def _spawn_turn(self, text: str) -> None:
        self.proc = ab.AgentProcess(self.backend, on_event=self._on_event, on_exit=self._on_exit,
                                    resume=self.session_id, prompt=text, local_id=self.local_id, chip=self.chip)
        self.turns += 1
        self._turn_open = True
        agent_session.save(self.instance_path, self.chip, pid=self.proc.pid)

    def _on_exit(self, proc) -> None:
        """Measured on the real CLI: Codex outlives its own turn.completed by
        a moment, so a message sent right after the Stop event was queued
        behind a process that was still exiting. The exit drains it."""
        self._next_turn()

    def _next_turn(self) -> None:
        with self._lock:
            if self.ended:
                self.queue.clear()
                return
            rec = agent_session.load(self.instance_path, self.chip)
            if agent_session.stopped(rec):
                self.queue.clear()
                return
            if self.queue and (self.proc is None or not self.proc.alive()):
                self._spawn_turn(self.queue.popleft())

    def busy(self) -> bool:
        p = self.proc
        if p is None or not p.alive():
            return False
        if self.backend.one_turn_per_process:
            return True
        return self._turn_open

    def alive(self) -> bool:
        return self.proc is not None and self.proc.alive()

    def stop(self, *, now: bool) -> None:
        """Stop. ``now`` kills the process tree (a Stop event is emitted by
        the process itself); otherwise only the queue is dropped -- the stop
        flag in the session file is what run_node reads."""
        with self._lock:
            self.queue.clear()
            if now and self.proc is not None and self.proc.alive():
                self.proc.stop()

    def end(self) -> None:
        """A graceful end: no more input; a Claude process exits on stdin EOF."""
        with self._lock:
            self.ended = time.time()
            self.queue.clear()
            if self.proc is not None and self.proc.alive():
                if self.backend.one_turn_per_process:
                    pass                                    # the turn finishes on its own
                else:
                    self.proc.close_stdin()

    def status(self) -> dict:
        p = self.proc
        return {"chip": self.chip, "backend": self.backend.name, "session_id": self.session_id,
                "local_id": self.local_id, "owner": self.owner, "mode": self.mode, "until": self.until,
                "alive": self.alive(), "busy": self.busy(), "queued": len(self.queue), "turns": self.turns,
                "pid": p.pid if p else None, "started": self.started, "ended": self.ended,
                "one_turn_per_process": self.backend.one_turn_per_process, "usage": dict(self.usage),
                "last": (self.last_event or {}).get("hook_event_name"),
                "last_ts": (self.last_event or {}).get("ts"),
                "readonly": bool(getattr(self.backend, "readonly", False)),
                "cwd": getattr(self.backend, "cwd", None), "model": getattr(self.backend, "model", None)}


class ChatManager:
    def __init__(self, record: Callable[[dict], None], instance_path):
        self.record = record
        self.instance_path = instance_path
        self.sessions: dict[str, ChatSession] = {}
        self.asks: dict[str, ab.AgentProcess] = {}
        self._lock = threading.RLock()

    def get(self, chip: str) -> ChatSession | None:
        return self.sessions.get(chip)

    def start(self, chip: str, backend: ab.Backend, *, owner: str, mode: str, until: float | None,
              prompt: str | None, resume: str | None = None) -> dict:
        with self._lock:
            cur = self.sessions.get(chip)
            if cur is not None and cur.alive():
                raise RuntimeError(f"a {cur.backend.name} session by {cur.owner} is already driving {chip}")
            s = ChatSession(chip, backend, owner=owner, mode=mode, until=until, record=self.record,
                            instance_path=self.instance_path)
            self.sessions[chip] = s
            return s.start(prompt, resume=resume)

    def send(self, chip: str, text: str) -> dict:
        s = self.sessions.get(chip)
        if s is None:
            return {"error": "no session"}
        return s.send(text)

    def stop(self, chip: str, *, now: bool) -> bool:
        s = self.sessions.get(chip)
        if s is None:
            return False
        s.stop(now=now)
        return True

    def end(self, chip: str) -> bool:
        s = self.sessions.get(chip)
        if s is None:
            return False
        s.end()
        return True

    def ask(self, chip: str, backend: ab.Backend, text: str) -> dict:
        """A question: its own short-lived read-only process, never the driving
        session. Events carry ``ask_id`` so the page can pair the answer."""
        ask_id = "ask-" + uuid.uuid4().hex[:10]

        def rec(e: dict) -> None:
            e["ask_id"] = ask_id
            e["origin"] = "ask"
            e.setdefault("chip", chip)
            self.record(e)
        if backend.one_turn_per_process:
            p = ab.AgentProcess(backend, on_event=rec, prompt=text, local_id=ask_id, chip=chip)
        else:
            p = ab.AgentProcess(backend, on_event=rec, local_id=ask_id, chip=chip)
            p.send(text)
            p.close_stdin()
        self.asks[ask_id] = p
        for k in [k for k, v in list(self.asks.items()) if not v.alive() and v.ended and time.time() - v.ended > 3600]:
            self.asks.pop(k, None)
        return {"ask_id": ask_id, "pid": p.pid}

    def ask_alive(self, ask_id: str) -> bool | None:
        p = self.asks.get(ask_id)
        return None if p is None else p.alive()

    def status(self, chip: str) -> dict | None:
        s = self.sessions.get(chip)
        return s.status() if s else None
