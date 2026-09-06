"""The agent behind the chat (docs/173 S4): one abstraction, two CLIs.

SM does not talk to a model. It spawns the user's own CLI -- Claude Code or
Codex, on the user's own login -- with SM's MCP server attached, feeds it
the user's words, and turns the CLI's JSON stream into ONE event shape the
page renders as cards and the pill/journal consume like hook events.

Facts this is built on (measured on this machine, 2026-09-06, spike scripts
in the session notes):

  Claude Code 2.1.261
    claude -p --input-format stream-json --output-format stream-json
    * one long-lived process; a second user message on stdin continues the
      same conversation (turn 2 answered from memory in 1.4 s)
    * lines: {"type":"system"...}, {"type":"assistant","message":{"content":[
      {"type":"thinking"|"text"|"tool_use","name":...}]}}, {"type":"user"} for
      tool results, {"type":"result","subtype":"success","num_turns","session_id",
      "usage","total_cost_usd","is_error","result"}
    * --resume <session_id> continues after a restart; --allowedTools limits
      the tool set (a question gets the read tools only, no Bash)

  Codex 0.153.4
    codex exec --json [-C dir] [resume <thread_id>] <prompt>
    * ONE TURN per process; the thread id is in {"type":"thread.started",
      "thread_id"}; `codex exec resume <id> --json <prompt>` continues it
    * items: {"type":"item.started|item.completed","item":{"type":
      "agent_message"|"mcp_tool_call"|"command_execution"|"reasoning",...}},
      {"type":"turn.completed","usage":{...}} / "turn.failed"
    * MCP tools are BLOCKED under the default approval policy; they run with
      --dangerously-bypass-approvals-and-sandbox (the driving session -- SM's
      own gates hold the permissions) or --approve-for-me (a question, under
      the workspace-write sandbox, with SM's read-only MCP mode)
    * there is no stdin channel mid-turn: a message while a turn runs waits

Normalized event (one dict per line in the same agent_events jsonl the
hook writes, so the pill, the journal and the story see chat and terminal
alike):
    {ts, session_id, backend, hook_event_name: PreToolUse|PostToolUse|
     PostToolUseFailure|Stop|Text|Init|Result|Error, tool_name, tool_use_id,
     summary, failed, error, limited, limited_until, usage, text, seq}
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

BACKENDS = ("claude", "codex")
READ_TOOLS = ("sm_status", "state_get", "state_search", "tray", "versions", "field_history", "runs", "run",
              "diagnostics", "check_fit", "families", "family_manual", "journal_read", "approvals")
MCP_TOOL_TIMEOUT_S = 30 * 60      # run_node blocks up to wait_s (<= 60 min); both CLIs default far lower
_LIMIT_RE = re.compile(r"(limit|quota|rate).{0,80}?(resets?|until|at)\s*(\d{1,2}:\d{2}\s*(?:am|pm)?)", re.I)


def _mcp_name(tool: str) -> str:
    return f"mcp__sm__{tool}"


def toml_str(v) -> str:
    """A TOML string that survives a Windows path: literal ('...') carries
    backslashes verbatim; a value holding a quote falls back to a basic
    string with real escapes. Measured 2026-09-06: "D:\\work" in a basic
    string is an invalid escape, Codex then read the whole -c value as a
    raw string and refused its own config."""
    s = str(v)
    if "'" not in s and "\n" not in s:
        return "'" + s + "'"
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


# ----------------------------------------------------------------- config

def mcp_config(python: str, repo: str | None, sm_url: str, *, readonly: bool = False, chip: str | None = None) -> dict:
    env = {"PYTHONUTF8": "1", "SM_URL": sm_url}
    if repo:
        env["PYTHONPATH"] = repo
    if readonly:
        env["SM_MCP_MODE"] = "readonly"
    if chip:
        env["SM_CHIP"] = chip
    return {"mcpServers": {"sm": {"command": python, "args": ["-m", "quam_state_manager.mcp"], "env": env}}}


def write_mcp_config(path: Path, cfg: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


class Backend:
    """What differs between the two CLIs. Everything else is AgentProcess."""
    name = "?"
    one_turn_per_process = False

    def __init__(self, exe: str, mcp_json: Path, *, cwd: str | None = None, model: str | None = None,
                 system_prompt: str | None = None, readonly: bool = False, sm_url: str = "", repo: str | None = None,
                 python: str = sys.executable, chip: str | None = None):
        self.exe, self.mcp_json, self.cwd, self.model = exe, mcp_json, cwd, model
        self.system_prompt, self.readonly = system_prompt, readonly
        self.sm_url, self.repo, self.python, self.chip = sm_url, repo, python, chip

    def command(self, *, resume: str | None = None, prompt: str | None = None) -> list[str]:
        raise NotImplementedError

    def encode_user(self, text: str) -> str | None:
        """What to write on stdin for a user turn (None: not supported)."""
        return None

    def initial_input(self, prompt: str, resume: str | None = None) -> str | None:
        """What to write on stdin right after the process starts, for the
        first message. NEVER argv: a newline in any argv element truncates
        the whole command line through an npm .cmd shim on Windows
        (measured 2026-09-06), and a prompt has newlines."""
        return self.encode_user(prompt)

    def normalize(self, ev: dict, ctx: dict) -> list[dict]:
        raise NotImplementedError


class ClaudeBackend(Backend):
    name = "claude"

    def command(self, *, resume=None, prompt=None):
        cmd = [self.exe, "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
               "--mcp-config", str(self.mcp_json), "--strict-mcp-config"]
        if self.readonly:
            cmd += ["--allowedTools", ",".join(_mcp_name(t) for t in READ_TOOLS)]
        else:
            cmd += ["--permission-mode", "bypassPermissions"]
        if self.model:
            cmd += ["--model", self.model]
        if self.system_prompt:
            cmd += ["--append-system-prompt", self.system_prompt]
        if resume:
            cmd += ["--resume", resume]
        return cmd

    def encode_user(self, text):
        return json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "text", "text": text}]}})

    def normalize(self, ev, ctx):
        out = []
        t = ev.get("type")
        if t == "system":
            if ev.get("subtype") == "init":
                ctx["session_id"] = ev.get("session_id") or ctx.get("session_id")
                out.append(_mk(ctx, "Init", summary=f"model {ev.get('model')}"))
            return out
        if t == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                bt = b.get("type")
                if bt == "text" and (b.get("text") or "").strip():
                    out.append(_mk(ctx, "Text", text=b["text"]))
                elif bt == "tool_use":
                    ctx["open"][b.get("id")] = b.get("name")
                    out.append(_mk(ctx, "PreToolUse", tool_name=b.get("name"), tool_use_id=b.get("id"),
                                   summary=_tool_summary(b.get("name"), b.get("input"))))
            return out
        if t == "user":
            for b in (ev.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    tid = b.get("tool_use_id")
                    name = ctx["open"].pop(tid, None)
                    failed = bool(b.get("is_error"))
                    body = b.get("content")
                    txt = body if isinstance(body, str) else json.dumps(body, default=str)[:400]
                    out.append(_mk(ctx, "PostToolUseFailure" if failed else "PostToolUse", tool_name=name,
                                   tool_use_id=tid, summary=ctx["summaries"].get(tid, ""), failed=failed,
                                   error=txt[-300:] if failed else None))
            return out
        if t == "result":
            text = str(ev.get("result") or "")
            lim = _limited(text) if ev.get("is_error") else None
            out.append(_mk(ctx, "Result", summary=text[:1200], failed=bool(ev.get("is_error")), usage=ev.get("usage"),
                           limited=bool(lim), limited_until=lim, turns=ev.get("num_turns"),
                           error=text[-300:] if ev.get("is_error") else None))
            out.append(_mk(ctx, "Stop", summary=text[:1200]))
            return out
        return out


class CodexBackend(Backend):
    name = "codex"
    one_turn_per_process = True

    def command(self, *, resume=None, prompt=None):
        cmd = [self.exe, "exec", "--json", "--skip-git-repo-check"]
        if self.cwd:
            cmd += ["-C", self.cwd]
        cfg = mcp_config(self.python, self.repo, self.sm_url, readonly=self.readonly, chip=self.chip)["mcpServers"]["sm"]
        env_toml = ",".join(f"{k}={toml_str(v)}" for k, v in cfg["env"].items())
        cmd += ["-c", f"mcp_servers.sm.command={toml_str(cfg['command'])}",
                "-c", "mcp_servers.sm.args=[" + ",".join(toml_str(a) for a in cfg["args"]) + "]",
                "-c", "mcp_servers.sm.env={" + env_toml + "}",
                "-c", f"mcp_servers.sm.tool_timeout_sec={MCP_TOOL_TIMEOUT_S}"]
        cmd += ["--approve-for-me"] if self.readonly else ["--dangerously-bypass-approvals-and-sandbox"]
        if self.model:
            cmd += ["-m", self.model]
        if resume:
            cmd += ["resume", resume]
        return cmd                                  # the prompt goes over stdin (initial_input)

    def encode_user(self, text):
        return text

    def initial_input(self, prompt, resume=None):
        if self.system_prompt and not resume:
            return self.system_prompt + "\n\n" + prompt
        return prompt

    def normalize(self, ev, ctx):
        out = []
        t = ev.get("type")
        if t == "thread.started":
            ctx["session_id"] = ev.get("thread_id") or ctx.get("session_id")
            out.append(_mk(ctx, "Init"))
            return out
        item = ev.get("item") or {}
        it = item.get("type")
        if t == "item.started" and it in ("mcp_tool_call", "command_execution", "function_call"):
            tid = item.get("id") or uuid.uuid4().hex[:8]
            name = _codex_tool_name(item)
            ctx["open"][tid] = name
            out.append(_mk(ctx, "PreToolUse", tool_name=name, tool_use_id=tid, summary=_codex_tool_summary(item)))
            return out
        if t == "item.completed":
            if it in ("mcp_tool_call", "command_execution", "function_call"):
                tid = item.get("id")
                name = ctx["open"].pop(tid, None) or _codex_tool_name(item)
                status = str(item.get("status") or "").lower()
                failed = status in ("failed", "error", "declined") or bool(item.get("error")) \
                    or (it == "command_execution" and item.get("exit_code") not in (None, 0))
                err = item.get("error") or (item.get("aggregated_output") or "")[-300:] if failed else None
                out.append(_mk(ctx, "PostToolUseFailure" if failed else "PostToolUse", tool_name=name, tool_use_id=tid,
                               summary=_codex_tool_summary(item), failed=failed, error=str(err)[-300:] if err else None,
                               exit_code=item.get("exit_code")))
            elif it == "agent_message" and (item.get("text") or "").strip():
                out.append(_mk(ctx, "Text", text=item["text"]))
            return out
        if t == "turn.completed":
            out.append(_mk(ctx, "Result", usage=ev.get("usage"), failed=False))
            out.append(_mk(ctx, "Stop", summary=ctx.get("last_text", "")))
            return out
        if t in ("turn.failed", "error"):
            msg = str((ev.get("error") or {}).get("message") or ev.get("message") or ev)
            lim = _limited(msg)
            out.append(_mk(ctx, "Result", failed=True, error=msg[-300:], limited=bool(lim), limited_until=lim))
            out.append(_mk(ctx, "Stop", summary=msg[:400]))
            return out
        return out


def _codex_tool_name(item: dict) -> str:
    it = item.get("type")
    if it == "mcp_tool_call":
        return f"mcp__{item.get('server') or 'sm'}__{item.get('tool') or item.get('name') or '?'}"
    if it == "command_execution":
        return "Bash"
    return str(item.get("name") or it)


def _codex_tool_summary(item: dict) -> str:
    if item.get("type") == "command_execution":
        return str(item.get("command") or "")[:400]
    args = item.get("arguments") or item.get("input") or {}
    return json.dumps(args, default=str)[:400] if not isinstance(args, str) else args[:400]


def _tool_summary(name: str | None, inp: Any) -> str:
    if not isinstance(inp, dict):
        return str(inp)[:400]
    if name == "Bash":
        return str(inp.get("command") or "")[:400]
    if name in ("Read", "Edit", "Write", "MultiEdit"):
        return str(inp.get("file_path") or "")[:400]
    return json.dumps(inp, default=str)[:400]


def _limited(text: str) -> str | None:
    """A usage-limit message -> the reset time it names, or "" when it names none."""
    if not text:
        return None
    low = text.lower()
    if not any(k in low for k in ("usage limit", "rate limit", "session limit", "quota", "limit reached", "you've hit")):
        return None
    m = _LIMIT_RE.search(text)
    return m.group(3).strip() if m else ""


def reset_timestamp(text: str | None, now: float | None = None) -> float | None:
    """'2:50pm' / '14:50' (what the CLIs print) -> the next such wall-clock
    time as a timestamp; None when the text names no time."""
    if not text or text is True:
        return None
    m = re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", str(text), re.I)
    if not m:
        return None
    h, mnt, ap = int(m.group(1)), int(m.group(2)), (m.group(3) or "").lower()
    if ap == "pm" and h < 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mnt < 60):
        return None
    base = datetime.fromtimestamp(now or time.time())
    cand = base.replace(hour=h, minute=mnt, second=0, microsecond=0)
    if cand.timestamp() <= (now or time.time()):
        cand = cand + timedelta(days=1)
    return cand.timestamp()


def _mk(ctx: dict, kind: str, **fields) -> dict:
    ctx["seq"] = ctx.get("seq", 0) + 1
    rec = {"ts": time.time(), "session_id": ctx.get("session_id") or ctx.get("local_id"), "backend": ctx.get("backend"),
           "hook_event_name": kind, "seq": ctx["seq"], "origin": "chat", "chip": ctx.get("chip")}
    rec.update(fields)
    if kind == "Text":
        ctx["last_text"] = fields.get("text", "")
    if kind == "PreToolUse" and fields.get("tool_use_id"):
        ctx["summaries"][fields["tool_use_id"]] = fields.get("summary", "")
    return rec


# ---------------------------------------------------------------- process

def resolve_command(cmd: list[str]) -> list[str]:
    """argv[0] by PATH (a .cmd shim resolves too, and CreateProcess runs it
    without the shell); through a shim every element still crosses cmd.exe,
    where a newline ends the line -- those are flattened to spaces, so a
    system prompt arrives whole instead of cut at its first line."""
    if not cmd:
        return cmd
    exe = shutil.which(cmd[0]) or cmd[0]
    out = [exe] + list(cmd[1:])
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        out = [out[0]] + [a.replace("\r\n", " ").replace("\n", " ").replace("\r", " ") for a in out[1:]]
    return out


def kill_tree(pid: int) -> None:
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            os.kill(pid, 9)
    except Exception:  # noqa: BLE001
        logger.debug("kill_tree failed", exc_info=True)


class AgentProcess:
    """One backend process (Claude: the whole conversation; Codex: one turn),
    its reader thread, and the normalized events it produced."""

    def __init__(self, backend: Backend, *, on_event: Callable[[dict], None] | None = None,
                 on_exit: Callable[[Any], None] | None = None,
                 resume: str | None = None, prompt: str | None = None, env: dict | None = None,
                 local_id: str | None = None, chip: str | None = None):
        self.backend = backend
        self.on_event = on_event
        self.on_exit = on_exit                # called once, on the reader thread, after the process is gone
        self.ctx = {"open": {}, "summaries": {}, "backend": backend.name, "session_id": resume,
                    "local_id": local_id or uuid.uuid4().hex[:12], "chip": chip, "seq": 0}
        self.events: deque = deque(maxlen=2000)
        self.raw_tail: deque = deque(maxlen=50)
        self.stderr_tail: deque = deque(maxlen=30)
        self.started = time.time()
        self.ended: float | None = None
        self.returncode: int | None = None
        cmd = resolve_command(backend.command(resume=resume, prompt=prompt))
        full_env = dict(os.environ, PYTHONUTF8="1", MCP_TOOL_TIMEOUT=str(MCP_TOOL_TIMEOUT_S * 1000))
        full_env.update(env or {})
        self.cmd = cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", env=full_env,
                                     cwd=backend.cwd or None, shell=False)
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._err = threading.Thread(target=self._read_err, daemon=True)
        self._err.start()
        if prompt is not None:
            first = backend.initial_input(prompt, resume)
            if first is not None:
                try:
                    self.proc.stdin.write(first + "\n")
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    pass
            if backend.one_turn_per_process:
                self.close_stdin()                  # Codex reads the prompt to EOF

    @property
    def pid(self) -> int:
        return self.proc.pid

    @property
    def session_id(self) -> str | None:
        return self.ctx.get("session_id")

    def alive(self) -> bool:
        return self.proc.poll() is None

    def send(self, text: str) -> bool:
        enc = self.backend.encode_user(text)
        if enc is None or not self.alive():
            return False
        try:
            self.proc.stdin.write(enc + "\n")
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        if self.alive():
            kill_tree(self.proc.pid)
        self._emit(_mk(self.ctx, "Stop", summary="stopped by a human", stopped=True))

    def _emit(self, rec: dict) -> None:
        with self._lock:
            self.events.append(rec)
        if self.on_event:
            try:
                self.on_event(rec)
            except Exception:  # noqa: BLE001
                logger.debug("on_event failed", exc_info=True)

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                self.raw_tail.append(line[:300])
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                for rec in self.backend.normalize(ev, self.ctx):
                    self._emit(rec)
        finally:
            self.returncode = self.proc.wait()
            self.ended = time.time()
            if self.returncode not in (0, None) and not any(e.get("hook_event_name") == "Result" for e in self.events):
                err = "\n".join(self.stderr_tail)[-400:]
                self._emit(_mk(self.ctx, "Error", failed=True, error=err or f"exit {self.returncode}",
                               limited=bool(_limited(err)), limited_until=_limited(err)))
                self._emit(_mk(self.ctx, "Stop", summary=err[:400]))
            if self.on_exit:
                try:
                    self.on_exit(self)
                except Exception:  # noqa: BLE001
                    logger.debug("on_exit failed", exc_info=True)

    def _read_err(self) -> None:
        try:
            for line in self.proc.stderr:
                self.stderr_tail.append(line.rstrip()[:300])
        except Exception:  # noqa: BLE001
            pass


def detect(exe: str) -> dict:
    """Is this CLI here, and which version? Never raises."""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20, shell=(os.name == "nt"))
        ver = (r.stdout or r.stderr or "").strip().splitlines()[0] if (r.stdout or r.stderr) else ""
        return {"found": r.returncode == 0, "version": ver}
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "version": "", "error": str(exc)[:120]}
