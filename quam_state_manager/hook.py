"""The Claude Code hook that feeds SM's live strip (docs/172).

Registered in the user's Claude Code settings (SM shows the snippet):

    "hooks": {
      "PreToolUse":  [{"matcher": "", "hooks": [{"type": "command", "command": "python -m quam_state_manager.hook", "async": true}]}],
      "PostToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "python -m quam_state_manager.hook", "async": true}]}],
      "Stop":        [{"hooks": [{"type": "command", "command": "python -m quam_state_manager.hook", "async": true}]}]
    }

Claude Code pipes one JSON object on stdin per event (hook_event_name,
session_id, transcript_path, cwd, tool_name, tool_input, tool_response,
last_assistant_message). Two rules, both from the red team:

  1. WRITE TO DISK FIRST. SM may be closed overnight; the file under
     ``<instance>/agent_events/<date>.jsonl`` is what the morning-after view
     replays. The POST to a running SM is best-effort with a 1 s timeout.
  2. Only Bash / Write / Edit completions and the turn's final message go
     into the JOURNAL (what ran, with what, and what Claude said). Every
     event feeds the "now" strip. Nothing here is the "why" -- that is the
     journal_append MCP tool, which the agent is asked to call itself.

Stdlib only; never raises (a hook that fails can block the agent's turn).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

_MAX = 400


def _summary(ev: dict) -> str:
    tool = ev.get("tool_name") or ""
    inp = ev.get("tool_input") or {}
    if not isinstance(inp, dict):
        return str(inp)[:_MAX]
    if tool == "Bash":
        return str(inp.get("command") or "")[:_MAX]
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Read"):
        return str(inp.get("file_path") or inp.get("notebook_path") or "")[:_MAX]
    if tool.startswith("mcp__"):
        return json.dumps(inp, default=str)[:_MAX]
    for k in ("query", "pattern", "url", "prompt", "description"):
        if inp.get(k):
            return str(inp[k])[:_MAX]
    return json.dumps(inp, default=str)[:_MAX]


def _journal_line(ev: dict) -> str | None:
    h = ev.get("hook_event_name")
    tool = ev.get("tool_name") or ""
    s = ev.get("summary") or ""
    if h == "Stop" and s:
        return "Claude: " + s.replace("\n", " ")[:600]
    if h != "PostToolUse":
        return None
    if tool == "Bash":
        m = re.search(r"python[^\s]*\s+([^\s;&|]+\.py)", s)
        node = Path(m.group(1)).stem if m else None
        return f"ran `{node}`" + (f" — `{s[:160]}`" if node else f"`{s[:160]}`") if node else f"ran `{s[:200]}`"
    if tool in ("Edit", "Write", "MultiEdit"):
        return f"edited `{s}`"
    if tool.startswith("mcp__") and "state_edit" in tool:
        return None            # the MCP tool journals itself with the reason
    return None


def main() -> int:
    try:
        raw = sys.stdin.read()
        ev = json.loads(raw) if raw.strip() else {}
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(ev, dict):
        return 0
    try:
        from quam_state_manager.core import agent_link
        inst = agent_link.instance_dir()
    except Exception:  # noqa: BLE001
        return 0
    rec = {
        "ts": time.time(),
        "hook_event_name": ev.get("hook_event_name"),
        "session_id": ev.get("session_id"),
        "transcript_path": ev.get("transcript_path"),
        "cwd": ev.get("cwd"),
        "tool_name": ev.get("tool_name"),
        "tool_use_id": ev.get("tool_use_id"),
        "summary": (ev.get("last_assistant_message") or "")[:1200] if ev.get("hook_event_name") == "Stop" else _summary(ev),
    }
    # 1. disk first
    try:
        d = inst / "agent_events"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / (datetime.now().strftime("%Y-%m-%d") + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass
    # 2. the journal's automatic lines (what ran / what Claude said)
    line = _journal_line(rec)
    # 3. the live wake, best effort
    try:
        link = agent_link.connect(inst, timeout=1.0)
        if link is not None:
            link.post_json("/api/agent/event", rec)
            if line:
                link.post_json("/api/agent/journal", {"kind": "hook", "text": line})
                line = None
    except Exception:  # noqa: BLE001
        pass
    # SM closed: journal straight to the file so the morning-after log is whole
    if line:
        try:
            from quam_state_manager.core import journal
            chip = os.environ.get("SM_CHIP") or "chip"
            journal.append(inst, chip, line, kind="hook")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
