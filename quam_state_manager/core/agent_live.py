"""What the agent's CLI is doing RIGHT NOW (docs/289), for the person waiting.

A question or a task used to show nothing between Send and the answer: the
CLI's events reach the feed only when a whole message or tool call is done,
so a 20-second turn was 20 seconds of an unchanged screen ("is it even
running?"). This keeps one small in-memory record per CLI process -- the
current phase with its start time, the tool calls of this turn, the answer
as it is being written, and a short readable log of what the process printed
-- and the page polls it about once a second while a turn is open.

It is a VIEW of the stream, never a record: nothing here is journaled,
written to disk, or used to decide anything. The journal and the feed keep
reading the normalized events, exactly as before.

Facts it is built on (measured on claude 2.1.289, 2026-10-05):

  * with ``--include-partial-messages`` Claude prints ``{"type":
    "stream_event","event":{...}}`` lines carrying the Messages-API stream
    events: ``content_block_start`` (``thinking`` / ``text`` / ``tool_use``),
    ``content_block_delta`` (``text_delta`` / ``input_json_delta`` /
    ``thinking_delta``), ``content_block_stop``, ``message_start`` / ``_stop``.
    The whole ``assistant`` message still follows, so nothing else changes.
  * the ``thinking_delta`` text was EMPTY on every captured turn: the phase
    can say "Thinking", never what the model thinks. Nothing here pretends.
  * Codex (``exec --json``) streams no partial text; its ``item.started``
    lines give the phase (``reasoning`` / tool calls / ``agent_message``).
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any

MAX_LINES = 200          # the log a person can scroll (each line is capped below)
LINE_CAP = 600
TEXT_CAP = 4000          # the answer being written: its tail
STEP_CAP = 30            # tool calls listed for the current turn
PREVIEW_CAP = 160        # a tool result, as one line

PHASES = ("starting", "waiting", "thinking", "tool", "writing", "done", "failed", "stopped")


def short_tool(name: str | None) -> str:
    """``mcp__sm__state_get`` -> ``state_get`` (the name a person reads)."""
    n = str(name or "")
    if n.startswith("mcp__"):
        n = n.split("__", 2)[-1]
    return n or "tool"


def tool_args(name: str, summary: str) -> str:
    """A tool call's input as a person reads it: ``{"path": "qubits.qA6.T1"}``
    -> ``path=qubits.qA6.T1``; an empty ``{}`` -> ``""``; ToolSearch's
    ``select:mcp__sm__a,mcp__sm__b`` -> ``loads a, b``. Anything that is not a
    JSON object is returned as it came (a Bash command, a file path)."""
    try:
        obj = json.loads(summary) if summary and summary.lstrip().startswith("{") else None
    except ValueError:
        obj = None
    if not isinstance(obj, dict):
        return summary or ""
    if name == "ToolSearch" and isinstance(obj.get("query"), str):
        q = obj["query"]
        if q.startswith("select:"):
            return "loads " + ", ".join(short_tool(x.strip()) for x in q[7:].split(",") if x.strip())
        return q
    parts = []
    for k, v in obj.items():
        if v is None or v == "" or v == [] or v == {}:
            continue
        val = v if isinstance(v, str) else json.dumps(v, default=str, ensure_ascii=False)
        parts.append(f"{k}={val}")
    return " ".join(parts)


def _one_line(s: Any, cap: int) -> str:
    t = s if isinstance(s, str) else json.dumps(s, default=str, ensure_ascii=False)
    t = " ".join(str(t).split())
    return t if len(t) <= cap else t[: cap - 1] + "…"


def _result_text(content: Any) -> str:
    """A tool result's content (str, or a list of {type: text, text}) as text."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        content = content.get("content", content)
    if isinstance(content, list):
        parts = [str(p.get("text") or "") for p in content if isinstance(p, dict) and p.get("type") in (None, "text")]
        if any(parts):
            return " ".join(parts)
    return json.dumps(content, default=str, ensure_ascii=False) if content not in (None, "") else ""


class LiveState:
    """One CLI process's live view. Fed from the reader threads; read by the
    web layer. Every method takes the one lock, and none of them raises."""

    def __init__(self, backend: str):
        self._lock = threading.Lock()
        self.backend = backend
        self.started = time.time()
        self.turn_started = self.started
        self.ended: float | None = None
        self.phase = "starting"
        self.phase_since = self.started
        self.tool: str | None = None
        self.detail = ""
        self.text = ""
        self.steps: list[dict] = []
        self.lines: deque = deque(maxlen=MAX_LINES)
        self.seq = 0
        self.partial = False       # True once a stream_event was seen: the answer streams as it is written
        self._text_line: list | None = None
        self._tool_input = ""
        self._previews: dict[str, str] = {}

    # -- internals (lock held) ------------------------------------------------
    def _bump(self) -> None:
        self.seq += 1

    def _line(self, kind: str, text: str) -> list:
        row = [time.time(), kind, _one_line(text, LINE_CAP)]
        self.lines.append(row)
        self._bump()
        return row

    def _set_phase(self, phase: str, tool: str | None = None, detail: str = "") -> None:
        if phase != self.phase or tool != self.tool:
            self.phase_since = time.time()
        self.phase, self.tool, self.detail = phase, tool, detail
        self._bump()

    def _step(self, tid: str | None) -> dict | None:
        for s in reversed(self.steps):
            if tid and s.get("id") == tid:
                return s
        return None

    # -- feeding --------------------------------------------------------------
    def turn(self, text: str) -> None:
        """A person's message went in: a new turn starts."""
        with self._lock:
            self.turn_started = time.time()
            self.ended = None
            self.text, self.steps, self._text_line = "", [], None
            self._line("you", text)
            self._set_phase("waiting")

    def stderr(self, line: str) -> None:
        line = (line or "").strip()
        if not line:
            return
        with self._lock:
            self._line("stderr", line)

    def raw(self, ev: dict) -> None:
        """A raw CLI line: only what the normalized events do not carry
        (streamed text, the phase between events, tool-result previews)."""
        if not isinstance(ev, dict):
            return
        try:
            with self._lock:
                self._raw(ev)
        except Exception:  # noqa: BLE001 -- a view must never break the reader
            pass

    def _raw(self, ev: dict) -> None:
        t = ev.get("type")
        if t == "stream_event":
            self.partial = True
            e = ev.get("event") or {}
            et = e.get("type")
            if et == "message_start":
                if self.phase not in ("tool",):
                    self._set_phase("waiting")
            elif et == "content_block_start":
                cb = e.get("content_block") or {}
                ct = cb.get("type")
                if ct in ("thinking", "redacted_thinking"):
                    self._set_phase("thinking")
                elif ct == "text":
                    self._set_phase("writing")
                    self._text_line = self._line("text", "")
                elif ct in ("tool_use", "server_tool_use"):
                    self._tool_input = ""
                    self._set_phase("tool", short_tool(cb.get("name")), "")
            elif et == "content_block_delta":
                d = e.get("delta") or {}
                dt = d.get("type")
                if dt == "text_delta":
                    self.text = (self.text + str(d.get("text") or ""))[-TEXT_CAP:]
                    if self._text_line is not None:
                        self._text_line[2] = _one_line(self.text, LINE_CAP)
                    self._bump()
                elif dt == "input_json_delta" and self.phase == "tool":
                    self._tool_input = (self._tool_input + str(d.get("partial_json") or ""))[-2000:]
                    self.detail = _one_line(self._tool_input, 200)
                    self._bump()
            return
        if t == "user":                                   # Claude: tool results
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id"):
                    self._previews[b["tool_use_id"]] = _one_line(_result_text(b.get("content")), PREVIEW_CAP)
            return
        item = ev.get("item") if isinstance(ev.get("item"), dict) else None
        if item is not None:                              # Codex
            it = item.get("type")
            if t == "item.started":
                if it == "reasoning":
                    self._set_phase("thinking")
                elif it == "agent_message":
                    self._set_phase("writing")
            elif t == "item.completed":
                if it == "reasoning":
                    txt = item.get("text") or item.get("summary") or ""
                    if txt:
                        self._line("think", _result_text(txt))
                    self._set_phase("waiting")
                elif it in ("mcp_tool_call", "command_execution", "function_call") and item.get("id"):
                    res = item.get("result") if it == "mcp_tool_call" else item.get("aggregated_output")
                    if res not in (None, ""):
                        self._previews[item["id"]] = _one_line(_result_text(res), PREVIEW_CAP)

    def event(self, rec: dict) -> None:
        """A normalized event (agent_backend._mk) -- the same one the feed gets."""
        try:
            with self._lock:
                self._event(rec)
        except Exception:  # noqa: BLE001
            pass

    def _event(self, rec: dict) -> None:
        h = rec.get("hook_event_name")
        now = time.time()
        if h == "Init":
            self._line("init", f"{self.backend} started" + (f" · {rec['summary']}" if rec.get("summary") else ""))
            if self.phase == "starting":
                self._set_phase("waiting")
        elif h == "PreToolUse":
            name = short_tool(rec.get("tool_name"))
            summary = tool_args(name, str(rec.get("summary") or ""))
            self.steps.append({"id": rec.get("tool_use_id"), "tool": name, "detail": _one_line(summary, 200),
                               "t0": now, "t1": None, "ok": None})
            del self.steps[:-STEP_CAP]
            self._line("call", f"{name} {summary}".strip())
            self._set_phase("tool", name, _one_line(summary, 200))
        elif h in ("PostToolUse", "PostToolUseFailure"):
            tid = rec.get("tool_use_id")
            s = self._step(tid)
            failed = h == "PostToolUseFailure" or bool(rec.get("failed"))
            name = short_tool(rec.get("tool_name")) if rec.get("tool_name") else (s or {}).get("tool", "tool")
            secs = None
            if s is not None:
                s["t1"], s["ok"] = now, not failed
                secs = now - s["t0"]
            took = f" · {secs:.1f} s" if secs is not None else ""
            if failed:
                self._line("fail", f"{name}{took} · {rec.get('error') or 'failed'}")
            else:
                pv = self._previews.pop(tid, "") if tid else ""
                self._line("ok", f"{name}{took}" + (f" → {pv}" if pv else ""))
            self._set_phase("waiting")
        elif h == "Text":
            full = str(rec.get("text") or "")
            self.text = full[-TEXT_CAP:]
            if self._text_line is not None:
                self._text_line[2] = _one_line(full, LINE_CAP)
                self._text_line = None
                self._bump()
            else:
                self._line("text", full)
            self._set_phase("writing")
        elif h in ("Result", "Error"):
            if rec.get("failed"):
                self._line("fail", str(rec.get("error") or "failed"))
                self._set_phase("failed")
            else:
                took = now - self.turn_started
                turns = rec.get("turns")
                self._line("done", f"answered in {took:.1f} s" + (f" · {turns} model turns" if turns else ""))
                self._set_phase("done")
        elif h == "Stop":
            if rec.get("stopped"):
                self._line("stop", "stopped by a person")
                self._set_phase("stopped")
            elif self.phase not in ("done", "failed", "stopped"):
                self._set_phase("done")
            self.ended = now
            self._text_line = None

    def finish(self, returncode: int | None) -> None:
        """The process is gone. Called AFTER the reader classified the exit: a
        crash mid-turn already arrived as an Error event, so what is left here
        is an exit nobody explained -- a clean one (an idle session ended, a
        CLI that took no turn) is an end, not a failure."""
        with self._lock:
            if self.phase not in ("done", "failed", "stopped"):
                if returncode in (0, None):
                    self._line("end", "the CLI exited")
                    self._set_phase("done")
                else:
                    self._line("fail", f"the CLI exited (code {returncode})")
                    self._set_phase("failed")
            self.ended = self.ended or time.time()

    # -- reading --------------------------------------------------------------
    def snapshot(self, max_lines: int = 120) -> dict:
        with self._lock:
            lines = list(self.lines)[-max_lines:]
            return {"backend": self.backend, "phase": self.phase, "phase_since": self.phase_since,
                    "tool": self.tool, "detail": self.detail, "started": self.started,
                    "turn_started": self.turn_started, "ended": self.ended, "partial": self.partial,
                    "text": self.text[-1200:],
                    "steps": [dict(s) for s in self.steps],
                    "lines": [list(r) for r in lines], "n_lines": len(self.lines), "seq": self.seq}
