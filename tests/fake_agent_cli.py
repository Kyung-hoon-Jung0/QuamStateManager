"""A fake `claude` / `codex` for the backend pins (docs/173 S4).

Speaks the two JSON dialects agent_backend.py is built on (recorded from the
real CLIs on 2026-09-06). Which one is picked by argv: ``-p`` = Claude
stream-json (reads user turns from stdin until EOF), ``exec`` = Codex one turn
per process (the prompt on stdin to EOF, the last paragraph being the user's). Knobs via env: FAKE_FAIL=1 (the tool call fails), FAKE_LIMIT=1
(the turn ends on a usage limit), FAKE_CRASH=1 (exit 3 with stderr), FAKE_TOOL
(the tool name to call, default mcp__sm__sm_status), FAKE_ECHO_STDIN=1 (codex: the
answer is the whole stdin, so a pin can see the system prompt), FAKE_LINGER=1 (codex:
stay alive 1.5 s after turn.completed, like the real CLI).
"""

from __future__ import annotations

import json
import os
import sys
import time


def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def claude():
    args = sys.argv[1:]
    sid = "sess-" + ("resumed" if "--resume" in args else "fresh")
    if "--resume" in args:
        sid = args[args.index("--resume") + 1]
    out({"type": "system", "subtype": "init", "session_id": sid, "model": "fake-haiku",
         "tools": ["Read"], "mcp_servers": [{"name": "sm", "status": "connected"}]})
    tool = os.environ.get("FAKE_TOOL", "mcp__sm__sm_status")
    n = 0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        text = "".join(b.get("text", "") for b in msg.get("message", {}).get("content", []) if isinstance(b, dict))
        n += 1
        if os.environ.get("FAKE_CRASH") == "1":
            sys.stderr.write("fatal: boom\n")
            sys.exit(3)
        if os.environ.get("FAKE_LIMIT") == "1":
            out({"type": "result", "subtype": "error_during_execution", "is_error": True, "num_turns": n,
                 "session_id": sid, "usage": {"input_tokens": 1, "output_tokens": 1},
                 "result": "You've hit your usage limit · resets 2:50pm (Asia/Seoul)"})
            continue
        tid = f"toolu_{n}"
        out({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "..."},
            {"type": "tool_use", "id": tid, "name": tool, "input": {"q": text[:20]}}]}, "session_id": sid})
        failed = os.environ.get("FAKE_FAIL") == "1"
        out({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tid, "is_error": failed,
             "content": "Error: no chip" if failed else "{\"chip\": \"PJ\"}"}]}, "session_id": sid})
        out({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": f"answer {n}: {text[:20]}"}]}, "session_id": sid})
        out({"type": "result", "subtype": "success", "is_error": False, "num_turns": n, "session_id": sid,
             "usage": {"input_tokens": 10 * n, "output_tokens": 5}, "total_cost_usd": 0.01 * n,
             "result": f"answer {n}: {text[:20]}"})


def codex():
    args = sys.argv[1:]
    stdin_all = sys.stdin.read().strip()                      # stdin, like the real `codex exec`
    prompt = stdin_all.split("\n\n")[-1]
    resume = args[args.index("resume") + 1] if "resume" in args else None
    tid = resume or "thread-fresh"
    out({"type": "thread.started", "thread_id": tid})
    out({"type": "turn.started"})
    if os.environ.get("FAKE_CRASH") == "1":
        sys.stderr.write("fatal: boom\n")
        sys.exit(3)
    if os.environ.get("FAKE_LIMIT") == "1":
        out({"type": "turn.failed", "error": {"message": "usage limit reached, resets at 14:50"}})
        return
    tool = os.environ.get("FAKE_TOOL", "sm_status")
    item = {"id": "item_1", "type": "mcp_tool_call", "server": "sm", "tool": tool, "arguments": {"q": prompt[:20]}, "status": "in_progress"}
    out({"type": "item.started", "item": item})
    failed = os.environ.get("FAKE_FAIL") == "1"
    done = dict(item, status="failed" if failed else "completed", error="tool blew up" if failed else None)
    out({"type": "item.completed", "item": done})
    whole = os.environ.get("FAKE_ECHO_STDIN") == "1"
    out({"type": "item.completed", "item": {"id": "item_2", "type": "agent_message",
                                            "text": f"codex saw: {stdin_all}" if whole else f"codex answer: {prompt[:20]}"}})
    out({"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 7}})
    if os.environ.get("FAKE_LINGER") == "1":
        time.sleep(1.5)                          # the real CLI outlives its turn.completed by a moment


if __name__ == "__main__":
    if "-p" in sys.argv[1:]:
        claude()
    elif "exec" in sys.argv[1:]:
        codex()
    elif "--version" in sys.argv[1:]:
        print("fake-cli 0.0.1")
    else:
        sys.exit(2)
