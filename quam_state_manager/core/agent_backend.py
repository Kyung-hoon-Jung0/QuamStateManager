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
    * MCP tools are BLOCKED under the default approval policy; docs/247: they
      run because the ONE server SM attaches says
      default_tools_approval_mode='approve' -- never by a bypass flag
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

from quam_state_manager.core import agent_live

logger = logging.getLogger(__name__)

BACKENDS = ("claude", "codex")
# docs/247 (C-01): the in-app session's rules are ENFORCED by the CLI's tool configuration, not by
# the prompt. Claude gets only these built-in tools (read files to look at figures and node code,
# ToolSearch to load the deferred MCP tools); Bash / PowerShell / Edit / Write / NotebookEdit / Task
# do not EXIST in the session, so `python node.py` or a one-liner on state.json is impossible, not
# refused. The deny list is belt and braces against an allow rule in a settings file (Connect used
# to write `Bash(python *)` into the calibrations folder the session runs in).
CLAUDE_TOOLS = ("Read", "Glob", "Grep", "ToolSearch")
CLAUDE_DENY = ("Bash", "PowerShell", "Edit", "Write", "NotebookEdit")
# Codex features that reach outside SM (account connectors, browser/computer use, sub-agents, image
# generation, plugins) are switched off for the in-app session; Codex ignores an unknown name.
CODEX_OFF_FEATURES = ("apps", "plugins", "browser_use", "computer_use", "multi_agent", "image_generation")
CODEX_REQUIRED_FLAGS = ("--ignore-user-config", "--ignore-rules")
READ_TOOLS = ("sm_status", "state_get", "state_search", "tray", "versions", "field_history", "runs", "run",
              "diagnostics", "check_fit", "families", "family_manual", "journal_read", "approvals", "plan_status")
MCP_TOOL_TIMEOUT_S = 30 * 60      # run_node blocks up to wait_s (<= 60 min); both CLIs default far lower
IN_APP_ENV = "SM_IN_APP_SESSION"  # set on every CLI process SM launches; quam_state_manager.hook reads it
_LIMIT_RE = re.compile(r"(limit|quota|rate).{0,80}?(resets?|until|at)\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)?)", re.I)


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


_TOML_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")


def _toml_key(k) -> str:
    k = str(k)
    return k if _TOML_BARE_KEY.match(k) else '"' + k.replace("\\", "\\\\").replace('"', '\\"') + '"'


def toml_inline(v) -> str:
    """One TOML value on one line (a ``-c key=value`` override): tables become
    inline tables, strings go through :func:`toml_str`."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, dict):
        return "{" + ",".join(f"{_toml_key(k)}={toml_inline(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(toml_inline(x) for x in v) + "]"
    return toml_str(v)


def codex_user_provider(codex_home: str | Path | None = None) -> tuple[list[str], str | None]:
    """The lab's OWN model provider, carried into the isolated in-app session.

    docs/247 follow-up: ``--ignore-user-config`` keeps the user's MCP servers and
    sandbox defaults out of the in-app session -- and with them, a lab's custom
    provider (Azure OpenAI, a proxy, a local endpoint) and its deployment name,
    without which Codex cannot answer at all in that lab. So exactly these are
    read from the user's config and passed back as ``-c`` overrides:
    ``model_provider`` and that provider's ``[model_providers.<name>]`` table.
    Nothing else crosses (no MCP servers, no sandbox/approval settings). Returns
    ``(override args, the user's model or None)``; an unreadable config -- or a
    Python without a TOML reader -- carries nothing."""
    base = Path(codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
    try:
        text = (base / "config.toml").read_text(encoding="utf-8")
    except OSError:
        return [], None
    try:
        import tomllib as _toml                      # Python 3.11+
    except ImportError:                              # pragma: no cover - 3.10
        try:
            import tomli as _toml                    # type: ignore[no-redef]
        except ImportError:
            return [], None
    try:
        cfg = _toml.loads(text)
    except Exception:  # noqa: BLE001 -- a broken user config carries nothing
        return [], None
    out: list[str] = []
    prov = cfg.get("model_provider")
    if isinstance(prov, str) and prov.strip():
        out += ["-c", f"model_provider={toml_str(prov)}"]
        table = (cfg.get("model_providers") or {}).get(prov)
        if isinstance(table, dict) and table:
            out += ["-c", f"model_providers.{_toml_key(prov)}={toml_inline(table)}"]
    model = cfg.get("model")
    return out, (model if isinstance(model, str) and model.strip() else None)


# ----------------------------------------------------------------- config

def mcp_config(python: str, repo: str | None, sm_url: str, *, readonly: bool = False, chip: str | None = None,
               session: str | None = None) -> dict:
    env = {"PYTHONUTF8": "1", "SM_URL": sm_url}
    if session:
        env["SM_SESSION"] = session          # docs/253: the in-app session's bridge says whose it is
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

    def preflight(self) -> str | None:
        """Why this CLI cannot run an ENFORCED session (docs/247), or None."""
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
        # docs/247: an EXPLICIT mode always -- with none, a user's own `defaultMode: bypassPermissions`
        # in ~/.claude/settings.json applied here too. dontAsk = anything not allowed below is denied
        # without a prompt, so a headless session never hangs on one.
        mcp_allow = [_mcp_name(t) for t in READ_TOOLS] if self.readonly else ["mcp__sm"]
        cmd += ["--permission-mode", "dontAsk",
                "--tools", ",".join(CLAUDE_TOOLS),
                "--allowedTools", ",".join(mcp_allow + list(CLAUDE_TOOLS)),
                "--disallowedTools", ",".join(CLAUDE_DENY)]
        if self.model:
            cmd += ["--model", self.model]
        if self.system_prompt:
            cmd += ["--append-system-prompt", self.system_prompt]
        # docs/289: the answer as it is written, and the phase between whole messages, for the
        # person waiting (agent_live). Only when this CLI names the flag -- an older one would
        # refuse the whole launch over an unknown option.
        if PARTIAL_FLAG in (help_flags(self.exe, ttl=3600.0) or ()):      # ~0.5 s, once an hour
            cmd += [PARTIAL_FLAG]
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
        if t == "rate_limit_event":
            # review R4-3: Claude says so in DATA -- {"status": allowed|allowed_warning|rejected, "resetsAt": epoch}
            info = ev.get("rate_limit_info") or {}
            if str(info.get("status") or "").lower() == "rejected":
                until = info.get("resetsAt")
                out.append(_mk(ctx, "Result", failed=True, limited=True, limited_until=until,
                               error=f"usage limit ({info.get('rateLimitType') or 'rate'}) -- resets "
                                     f"{datetime.fromtimestamp(float(until)).strftime('%H:%M') if until else '?'}",
                               summary="usage limit reached"))
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
        # docs/247 (C-01, C-02): --ignore-user-config = the user's own ~/.codex/config.toml is not read,
        # so neither its MCP servers (Setup's `quam-state-manager`, write tools, no chip pin) nor its
        # sandbox/approval defaults reach this session; auth still comes from CODEX_HOME, and so do the
        # thread files `exec resume` needs. --ignore-rules = no execpolicy allow-rules either.
        cmd = [self.exe, "exec", "--json", "--skip-git-repo-check", *CODEX_REQUIRED_FLAGS]
        if self.cwd:
            cmd += ["-C", self.cwd]
        # read-only sandbox + approval `never`: a shell command that would write, and any escalation,
        # is refused by Codex itself (measured on 0.159.2: "blocked by policy"; apply_patch: "writing
        # is blocked by read-only sandbox"). The MCP server runs OUTSIDE the sandbox, so SM's tools --
        # and SM's own gates -- are the only door to the chip.
        cmd += ["-s", "read-only", "-c", "approval_policy='never'"]
        for f in CODEX_OFF_FEATURES:
            cmd += ["-c", f"features.{f}=false"]
        cfg = mcp_config(self.python, self.repo, self.sm_url, readonly=self.readonly, chip=self.chip,
                         session=getattr(self, "session_secret", None))["mcpServers"]["sm"]
        env_toml = ",".join(f"{k}={toml_str(v)}" for k, v in cfg["env"].items())
        cmd += ["-c", f"mcp_servers.sm.command={toml_str(cfg['command'])}",
                "-c", "mcp_servers.sm.args=[" + ",".join(toml_str(a) for a in cfg["args"]) + "]",
                "-c", "mcp_servers.sm.env={" + env_toml + "}",
                "-c", f"mcp_servers.sm.tool_timeout_sec={MCP_TOOL_TIMEOUT_S}",
                # without this every MCP call is "blocked: requires approval" under `never` (measured)
                "-c", "mcp_servers.sm.default_tools_approval_mode='approve'"]
        # docs/247 follow-up: the lab's own provider (and its model, when SM's setup names none)
        prov_args, user_model = codex_user_provider()
        cmd += prov_args
        model = self.model or user_model
        if model:
            cmd += ["-m", model]
        if resume:
            cmd += ["resume", resume]
        return cmd                                  # the prompt goes over stdin (initial_input)

    def encode_user(self, text):
        return text

    def preflight(self):
        """An older Codex without --ignore-user-config / --ignore-rules cannot be isolated from the
        user's own config (its MCP servers, its sandbox defaults): refuse it by name rather than run
        it open (docs/247)."""
        flags = exec_flags(self.exe)
        if flags is None:
            return None                      # could not ask: the launch itself will say what is wrong
        missing = [f for f in CODEX_REQUIRED_FLAGS if f not in flags]
        if missing:
            return (f"this Codex ({self.exe}) has no {' / '.join(missing)}, so SM cannot keep its in-app "
                    "session away from your own Codex config and MCP servers -- update Codex "
                    "(npm i -g @openai/codex) to use it inside SM")
        return None

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
                err = None
                if failed:
                    err = item.get("error") or (item.get("aggregated_output") or "")[-300:]
                    if not err:                  # review R4-4: an MCP isError result carries its reason in content
                        res = item.get("result") or {}
                        parts = res.get("content") if isinstance(res, dict) else None
                        if isinstance(parts, list):
                            err = " ".join(str(p.get("text") or "") for p in parts if isinstance(p, dict))[-300:]
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
        if t == "error":
            # review R4-5: a real failure prints BOTH `error` and `turn.failed`; remember the text, emit once
            ctx["errored"] = str(ev.get("message") or (ev.get("error") or {}).get("message") or ev)[-300:]
            return out
        if t == "turn.failed":
            msg = str((ev.get("error") or {}).get("message") or ev.get("message") or ctx.get("errored") or ev)
            lim = _limited(msg)
            out.append(_mk(ctx, "Result", failed=True, error=msg[-300:], limited=bool(lim), limited_until=lim))
            out.append(_mk(ctx, "Stop", summary=msg[:400]))
            ctx.pop("errored", None)
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
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text) if float(text) > 1e9 else None      # an epoch (Claude's resetsAt)
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", str(text), re.I) or \
        re.search(r"(\d{1,2}):(\d{2})\s*(am|pm)?", str(text), re.I)
    if not m:
        return None
    h, mnt, ap = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower()
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
    if kind == "Result":
        ctx["turn_open"] = False                 # the turn answered
        if fields.get("failed") and fields.get("error"):
            ctx["failed_reason"] = str(fields["error"])
    if kind == "Text":
        ctx["last_text"] = fields.get("text", "")
    if kind == "PreToolUse" and fields.get("tool_use_id"):
        ctx["summaries"][fields["tool_use_id"]] = fields.get("summary", "")
    return rec


# ---------------------------------------------------------------- process

def _vendored_exe(shim: str) -> str | None:
    """npm's ``codex.cmd`` -> ``node_modules/@openai/codex-win32-x64/vendor/.../codex.exe`` when present."""
    try:
        root = Path(shim).resolve().parent / "node_modules" / "@openai"
        for cand in sorted(root.glob("codex-win32-*/vendor/*/bin/codex.exe")):
            return str(cand)
        for cand in sorted(root.glob("codex/node_modules/@openai/codex-win32-*/vendor/*/bin/codex.exe")):
            return str(cand)
    except OSError:
        pass
    return None


def resolve_command(cmd: list[str]) -> list[str]:
    """argv[0] by PATH (a .cmd shim resolves too, and CreateProcess runs it
    without the shell); through a shim every element still crosses cmd.exe,
    where a newline ends the line -- those are flattened to spaces, so a
    system prompt arrives whole instead of cut at its first line."""
    if not cmd:
        return cmd
    exe = shutil.which(cmd[0]) or cmd[0]
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        # review R4-7: through the shim cmd.exe also expands %VAR% and strips ^ -- the vendored exe
        # the npm package ships takes argv verbatim
        real = _vendored_exe(exe)
        if real:
            exe = real
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
        self.live = agent_live.LiveState(backend.name)     # docs/289: what it is doing now, for the page
        cmd = resolve_command(backend.command(resume=resume, prompt=prompt))
        full_env = dict(os.environ, PYTHONUTF8="1", MCP_TOOL_TIMEOUT=str(MCP_TOOL_TIMEOUT_S * 1000))
        full_env.update(env or {})
        # B-09/C-27: SM already reads this process's own event stream and journals its turns. The
        # user's Claude Code hooks still fire inside it and would journal every turn a second time
        # (and a setup Test into the lab's notes); the hook skips a process that carries this mark.
        full_env[IN_APP_ENV] = self.ctx["local_id"]
        self.cmd = cmd
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", env=full_env,
                                     cwd=backend.cwd or None, shell=False)
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        self._err = threading.Thread(target=self._read_err, daemon=True)
        self._err.start()
        self.stopped_by_human = False
        if prompt is not None:
            self.live.turn(prompt)
            first = backend.initial_input(prompt, resume)
            if first is not None:
                try:
                    self.proc.stdin.write(first + "\n")
                    self.proc.stdin.flush()
                    self.ctx["turn_open"] = True
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
            self.ctx["turn_open"] = True
            self.live.turn(text)
            return True
        except (OSError, ValueError):
            return False

    def close_stdin(self) -> None:
        try:
            self.proc.stdin.close()
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        self.stopped_by_human = True             # review R2-10: the kill's exit code is not a failure
        if self.alive():
            kill_tree(self.proc.pid)
        self._emit(_mk(self.ctx, "Stop", summary="stopped by a person", stopped=True))

    def _emit(self, rec: dict) -> None:
        self.live.event(rec)
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
                try:
                    ev = json.loads(line)
                except ValueError:
                    self.raw_tail.append(line[:300])
                    continue
                if not (isinstance(ev, dict) and ev.get("type") == "stream_event"):
                    self.raw_tail.append(line[:300])     # the partial chunks would push out what a crash needs
                self.live.raw(ev)
                for rec in self.backend.normalize(ev, self.ctx):
                    self._emit(rec)
        finally:
            self.returncode = self.proc.wait()
            self.ended = time.time()
            # review R4-2: a turn still OPEN at exit (no Result since the last user send) is a crash,
            # whatever earlier turns did; a person's Stop now is not (R2-10)
            turn_open = self.ctx.get("turn_open", False)
            crashed = (self.returncode not in (0, None) or turn_open) and not getattr(self, "stopped_by_human", False)
            if crashed and not (self.returncode == 0 and not turn_open):
                # docs/247: a turn that already FAILED with a reason (Codex "model at capacity") names it;
                # the stderr tail then only says "Reading prompt from stdin..."
                err = self.ctx.get("failed_reason") or "\n".join(self.stderr_tail)[-400:] or self.ctx.get("errored") or ""
                self._emit(_mk(self.ctx, "Error", failed=True, error=err or f"exit {self.returncode}",
                               limited=bool(_limited(err)), limited_until=_limited(err)))
                self._emit(_mk(self.ctx, "Stop", summary=err[:400]))
            if not getattr(self, "stopped_by_human", False):
                self.live.finish(self.returncode)        # docs/289: after the crash verdict above
            if self.on_exit:
                try:
                    self.on_exit(self)
                except Exception:  # noqa: BLE001
                    logger.debug("on_exit failed", exc_info=True)

    def _read_err(self) -> None:
        try:
            for line in self.proc.stderr:
                self.stderr_tail.append(line.rstrip()[:300])
                self.live.stderr(line)
        except Exception:  # noqa: BLE001
            pass


_FLAGS_CACHE: dict[tuple, tuple[float, frozenset | None]] = {}
PARTIAL_FLAG = "--include-partial-messages"


def help_flags(exe: str, sub: tuple = (), ttl: float = 600.0) -> frozenset | None:
    """The long flags ``<exe> [sub...] --help`` names (cached); None when it cannot be asked."""
    key = (exe, tuple(sub))
    hit = _FLAGS_CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    try:
        r = subprocess.run(resolve_command([exe, *sub, "--help"]), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
        text = (r.stdout or "") + (r.stderr or "")
        out = frozenset(re.findall(r"--[a-z][a-z0-9-]+", text)) if r.returncode == 0 else None
    except Exception:  # noqa: BLE001
        out = None
    _FLAGS_CACHE[key] = (time.time(), out)
    return out


def exec_flags(exe: str, ttl: float = 600.0) -> frozenset | None:
    """The long flags ``<exe> exec --help`` names (cached); None when it cannot be asked."""
    return help_flags(exe, ("exec",), ttl)


def detect(exe: str) -> dict:
    """Is this CLI here, and which version? Never raises."""
    try:
        r = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20, shell=(os.name == "nt"))
        ver = (r.stdout or r.stderr or "").strip().splitlines()[0] if (r.stdout or r.stderr) else ""
        return {"found": r.returncode == 0, "version": ver}
    except Exception as exc:  # noqa: BLE001
        return {"found": False, "version": "", "error": str(exc)[:120]}
