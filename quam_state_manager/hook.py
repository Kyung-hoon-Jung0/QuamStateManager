"""The Claude Code hook that feeds SM's live strip and journal (docs/172).

Registered in the user's Claude Code settings (SM's setup page shows the
snippet with the resolved command -- `python -m quam_state_manager.hook` on a
Python install, `quam-manager.exe --hook` on the frozen one):

    "hooks": {
      "PreToolUse":         [{"matcher": "Bash|Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": "<cmd>", "async": true}]}],
      "PostToolUse":        [{"matcher": "Bash|Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": "<cmd>", "async": true}]}],
      "PostToolUseFailure": [{"matcher": "Bash|Edit|Write|MultiEdit", "hooks": [{"type": "command", "command": "<cmd>", "async": true}]}],
      "Stop":               [{"hooks": [{"type": "command", "command": "<cmd>", "async": true}]}]
    }

Claude Code pipes one JSON object on stdin per event (hook_event_name,
session_id, transcript_path, cwd, tool_name, tool_use_id, tool_input,
tool_response / error, last_assistant_message).

Three rules, all from the reviews:

  1. RECORD, NEVER INTERPRET. The hook appends one line to
     ``<instance>/agent_events/<date>.jsonl`` and, if SM is up, POSTs the same
     record with a 300 ms timeout. It never writes the journal -- SM derives
     journal lines from the record (on the POST, or at the next start from
     the file), so there is ONE writer per .md and no duplicate when SM was
     merely slow.
  2. CARRY THE FACTS SM NEEDS: the tool's failure (``failed``, exit code,
     the stderr tail), and the chip the session is on (``quam_state_path``
     from the environment or a ``quam_state/state.json`` under the cwd) so
     a night with SM closed is filed under the right chip.
  3. Never raise, exit 0: a hook that fails can block the agent's turn.

Stdlib only. ~0.3 s per call on this machine (interpreter start); the
snippet runs it ``async`` so the agent does not wait.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

_MAX = 400
_ERR_TAIL = 300


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


def _failure(ev: dict) -> tuple[bool, str | None, str | None]:
    """(failed, exit_code, error tail) read off whatever the CLI handed us."""
    h = ev.get("hook_event_name")
    if h == "PostToolUseFailure":
        err = ev.get("error") or ev.get("tool_response") or ""
        return True, None, str(err)[-_ERR_TAIL:] or "tool failed"
    resp = ev.get("tool_response")
    if isinstance(resp, dict):
        code = resp.get("exit_code", resp.get("exitCode"))
        is_err = bool(resp.get("is_error") or resp.get("isError") or resp.get("interrupted"))
        try:
            bad = code is not None and int(code) != 0
        except (TypeError, ValueError):
            bad = False
        if is_err or bad:
            tail = str(resp.get("stderr") or resp.get("error") or resp.get("stdout") or "")[-_ERR_TAIL:]
            return True, (str(code) if code is not None else None), tail or "tool reported an error"
        return False, (str(code) if code is not None else None), None
    if isinstance(resp, str) and resp.lstrip().lower().startswith(("error", "traceback")):
        return True, None, resp[-_ERR_TAIL:]
    return False, None, None


def _state_path(ev: dict) -> str | None:
    """Which chip is this session on? The env var qualibrate honours, else a
    ``quam_state/state.json`` near the cwd (depth ≤ 3). None when unknown --
    SM then files the night under 'unassigned', never under an invented name."""
    p = os.environ.get("QUAM_STATE_PATH")
    if p:
        return p
    cwd = ev.get("cwd")
    if not cwd:
        return None
    try:
        base = Path(cwd)
        for cand in (base / "quam_state", base / "configuration" / "quam_state",
                     base / "quam_config" / "quam_state"):
            if (cand / "state.json").exists():
                return str(cand)
        for d in base.iterdir() if base.is_dir() else []:
            if d.is_dir() and (d / "quam_state" / "state.json").exists():
                return str(d / "quam_state")
    except OSError:
        return None
    return None


def record(ev: dict) -> dict:
    failed, code, err = _failure(ev)
    h = ev.get("hook_event_name")
    return {
        "ts": time.time(),
        "hook_event_name": h,
        "session_id": ev.get("session_id"),
        "transcript_path": ev.get("transcript_path"),
        "cwd": ev.get("cwd"),
        "quam_state_path": _state_path(ev),
        "tool_name": ev.get("tool_name"),
        "tool_use_id": ev.get("tool_use_id"),
        "summary": (ev.get("last_assistant_message") or "")[:1200] if h == "Stop" else _summary(ev),
        "failed": failed,
        "exit_code": code,
        "error": err,
    }


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
    rec = record(ev)
    # 1. disk first -- the record SM replays whether or not it was up
    try:
        d = inst / "agent_events"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / (datetime.now().strftime("%Y-%m-%d") + ".jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass
    # 2. the live wake, best effort, one short POST, no probe
    try:
        agent_link.fire_event(inst, rec)
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
