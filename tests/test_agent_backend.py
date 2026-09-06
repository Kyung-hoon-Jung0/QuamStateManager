"""docs/173 S4: two CLIs, one event shape.

Command lines are pinned against the facts measured on the real CLIs
(readonly = read tools only / approve-for-me; driving = bypass; resume;
system prompt), the normalizers are pinned on recorded event shapes, and
AgentProcess is driven over a FAKE CLI that speaks both dialects.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

from quam_state_manager.core import agent_backend as ab

ROOT = Path(__file__).resolve().parent.parent
FAKE = [sys.executable, str(ROOT / "tests" / "fake_agent_cli.py")]


class _FakeClaude(ab.ClaudeBackend):
    def command(self, *, resume=None, prompt=None):
        real = super().command(resume=resume, prompt=prompt)
        return FAKE + real[1:]


class _FakeCodex(ab.CodexBackend):
    def command(self, *, resume=None, prompt=None):
        real = super().command(resume=resume, prompt=prompt)
        return FAKE + real[1:]


def _wait(proc, pred, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.05)
    return False


class TestCommandLines:
    def test_claude_driving_vs_readonly(self, tmp_path):
        mj = tmp_path / "mcp.json"
        drv = ab.ClaudeBackend("claude", mj, model="haiku", system_prompt="rules").command()
        assert drv[:5] == ["claude", "-p", "--input-format", "stream-json", "--output-format"]
        assert "--permission-mode" in drv and "bypassPermissions" in drv and "--allowedTools" not in drv
        assert drv[drv.index("--model") + 1] == "haiku" and drv[drv.index("--append-system-prompt") + 1] == "rules"
        assert "--strict-mcp-config" in drv and drv[drv.index("--mcp-config") + 1] == str(mj)
        ro = ab.ClaudeBackend("claude", mj, readonly=True).command(resume="sess-1")
        allowed = ro[ro.index("--allowedTools") + 1].split(",")
        assert "--permission-mode" not in ro and all(a.startswith("mcp__sm__") for a in allowed)
        assert "mcp__sm__state_get" in allowed and "mcp__sm__state_edit" not in allowed and "Bash" not in allowed
        assert ro[ro.index("--resume") + 1] == "sess-1"

    def test_codex_driving_vs_readonly_and_resume(self, tmp_path):
        drv = ab.CodexBackend("codex", tmp_path / "x", cwd="D:/lab", sm_url="http://127.0.0.1:1", repo="R",
                              python="py.exe").command(prompt="go")
        assert drv[:4] == ["codex", "exec", "--json", "--skip-git-repo-check"] and drv[drv.index("-C") + 1] == "D:/lab"
        assert "--dangerously-bypass-approvals-and-sandbox" in drv and "--approve-for-me" not in drv
        assert "go" not in drv, "the prompt never rides argv (a newline there truncates the line through a .cmd shim)"
        cfg = " ".join(drv)
        assert "mcp_servers.sm.command='py.exe'" in cfg and "SM_URL='http://127.0.0.1:1'" in cfg and "PYTHONPATH='R'" in cfg
        ro = ab.CodexBackend("codex", tmp_path / "x", readonly=True, sm_url="u").command(prompt="q?")
        assert "--approve-for-me" in ro and "--dangerously-bypass-approvals-and-sandbox" not in ro
        assert "SM_MCP_MODE='readonly'" in " ".join(ro)

    def test_codex_config_values_are_valid_toml_for_windows_paths(self, tmp_path):
        """A basic string cannot hold D:\\work (invalid escape) -- Codex refused
        the config as a raw string. Every value is TOML that parses back."""
        import tomllib
        b = ab.CodexBackend("codex", tmp_path / "x", sm_url="http://127.0.0.1:5079", repo="D:\\work\\sm agent",
                            python="D:\\envs\\py.exe")
        cmd = b.command(prompt="x")
        vals = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-c"]
        parsed = {}
        for kv in vals:
            k, v = kv.split("=", 1)
            parsed[k] = tomllib.loads(f"v = {v}")["v"]
        assert parsed["mcp_servers.sm.command"] == "D:\\envs\\py.exe"
        assert parsed["mcp_servers.sm.args"] == ["-m", "quam_state_manager.mcp"]
        assert parsed["mcp_servers.sm.env"]["PYTHONPATH"] == "D:\\work\\sm agent"
        assert parsed["mcp_servers.sm.env"]["SM_URL"] == "http://127.0.0.1:5079"
        assert ab.toml_str("it's") == '"it\'s"' and tomllib.loads("v = " + ab.toml_str("a\\b'c"))["v"] == "a\\b'c"
        res = ab.CodexBackend("codex", tmp_path / "x", sm_url="u").command(resume="thread-9", prompt="more")
        assert res[-2:] == ["resume", "thread-9"]

    def test_codex_system_prompt_rides_only_the_first_turn(self, tmp_path):
        b = ab.CodexBackend("codex", tmp_path / "x", sm_url="u", system_prompt="RULES")
        assert b.initial_input("hi") == "RULES\n\nhi"
        assert b.initial_input("hi", resume="t") == "hi"
        assert "RULES" not in " ".join(b.command(prompt="hi"))

    def test_resolve_command_flattens_newlines_only_through_a_shim(self, tmp_path, monkeypatch):
        """Measured: through an npm .cmd shim (or shell=True) a newline in any
        argv element ends the command line. A real exe keeps every byte."""
        monkeypatch.setattr(ab.shutil, "which", lambda x: str(tmp_path / "codex.CMD"))
        out = ab.resolve_command(["codex", "exec", "rules\nline two", "x"])
        assert out[0].endswith("codex.CMD")
        assert out[1:] == ["exec", "rules line two", "x"] if os.name == "nt" else out[2] == "rules\nline two"
        monkeypatch.setattr(ab.shutil, "which", lambda x: str(tmp_path / "claude.exe"))
        out = ab.resolve_command(["claude", "-p", "--append-system-prompt", "a\nb"])
        assert out[-1] == "a\nb" and out[0].endswith("claude.exe")
        monkeypatch.setattr(ab.shutil, "which", lambda x: None)
        assert ab.resolve_command(["nope", "x"]) == ["nope", "x"]

    def test_codex_system_prompt_reaches_the_process_on_the_first_turn_only(self, tmp_path):
        got = []
        p = ab.AgentProcess(_FakeCodex("codex", tmp_path / "x", sm_url="u", system_prompt="RULES"),
                            on_event=got.append, prompt="hi", env={"FAKE_ECHO_STDIN": "1"})
        assert _wait(p, lambda: any(e["hook_event_name"] == "Stop" for e in got))
        assert [e["text"] for e in got if e["hook_event_name"] == "Text"] == ["codex saw: RULES\n\nhi"]
        got2 = []
        p2 = ab.AgentProcess(_FakeCodex("codex", tmp_path / "x", sm_url="u", system_prompt="RULES"),
                             on_event=got2.append, prompt="hi", resume="thread-fresh", env={"FAKE_ECHO_STDIN": "1"})
        assert _wait(p2, lambda: any(e["hook_event_name"] == "Stop" for e in got2))
        assert [e["text"] for e in got2 if e["hook_event_name"] == "Text"] == ["codex saw: hi"]

    def test_a_multiline_prompt_reaches_the_fake_codex_whole(self, tmp_path):
        got = []
        p = ab.AgentProcess(_FakeCodex("codex", tmp_path / "x", sm_url="u", system_prompt="R1\nR2"),
                            on_event=got.append, prompt="line one\nline two")
        assert _wait(p, lambda: any(e["hook_event_name"] == "Stop" for e in got))
        txt = [e["text"] for e in got if e["hook_event_name"] == "Text"]
        assert txt == ["codex answer: line one\nline two"]

    def test_mcp_config_shapes(self, tmp_path):
        cfg = ab.mcp_config("py", "R", "http://x", readonly=True, chip="PJ")
        sm = cfg["mcpServers"]["sm"]
        assert sm["command"] == "py" and sm["args"] == ["-m", "quam_state_manager.mcp"]
        assert sm["env"]["SM_MCP_MODE"] == "readonly" and sm["env"]["SM_CHIP"] == "PJ" and sm["env"]["PYTHONPATH"] == "R"
        assert "SM_MCP_MODE" not in ab.mcp_config("py", None, "u")["mcpServers"]["sm"]["env"]
        p = ab.write_mcp_config(tmp_path / "a" / "m.json", cfg)
        assert json.loads(p.read_text())["mcpServers"]["sm"]["env"]["SM_URL"] == "http://x"


class TestNormalizers:
    def _ctx(self, backend):
        return {"open": {}, "summaries": {}, "backend": backend, "local_id": "L", "seq": 0}

    def test_claude_events(self):
        b = ab.ClaudeBackend("claude", Path("m"))
        ctx = self._ctx("claude")
        ev = b.normalize({"type": "system", "subtype": "init", "session_id": "S1", "model": "m"}, ctx)
        assert ev[0]["hook_event_name"] == "Init" and ctx["session_id"] == "S1"
        ev = b.normalize({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "x"},
                          {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "python 05_power_rabi.py"}}]}}, ctx)
        assert [e["hook_event_name"] for e in ev] == ["PreToolUse"]
        assert ev[0]["tool_name"] == "Bash" and ev[0]["summary"] == "python 05_power_rabi.py" and ev[0]["session_id"] == "S1"
        ev = b.normalize({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True, "content": "Traceback KeyError"}]}}, ctx)
        assert ev[0]["hook_event_name"] == "PostToolUseFailure" and ev[0]["failed"] and "KeyError" in ev[0]["error"]
        assert ev[0]["tool_name"] == "Bash" and ev[0]["summary"] == "python 05_power_rabi.py", "the result names the tool it closes"
        ev = b.normalize({"type": "assistant", "message": {"content": [{"type": "text", "text": "done"}]}}, ctx)
        assert ev[0]["hook_event_name"] == "Text" and ev[0]["text"] == "done"
        ev = b.normalize({"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "usage": {"a": 1}, "result": "done"}, ctx)
        assert [e["hook_event_name"] for e in ev] == ["Result", "Stop"] and ev[0]["usage"] == {"a": 1} and not ev[0]["limited"]
        ev = b.normalize({"type": "result", "is_error": True, "result": "You've hit your usage limit · resets 2:50pm (Asia/Seoul)"}, ctx)
        assert ev[0]["limited"] is True and ev[0]["limited_until"] == "2:50pm" and ev[0]["failed"]
        assert b.normalize({"type": "stream_event", "event": {}}, ctx) == []

    def test_codex_events(self):
        b = ab.CodexBackend("codex", Path("m"), sm_url="u")
        ctx = self._ctx("codex")
        ev = b.normalize({"type": "thread.started", "thread_id": "T1"}, ctx)
        assert ev[0]["hook_event_name"] == "Init" and ctx["session_id"] == "T1"
        ev = b.normalize({"type": "item.started", "item": {"id": "i1", "type": "mcp_tool_call", "server": "sm", "tool": "run_node", "arguments": {"node": "x"}}}, ctx)
        assert ev[0]["hook_event_name"] == "PreToolUse" and ev[0]["tool_name"] == "mcp__sm__run_node"
        ev = b.normalize({"type": "item.completed", "item": {"id": "i1", "type": "mcp_tool_call", "server": "sm", "tool": "run_node", "status": "failed", "error": "refused"}}, ctx)
        assert ev[0]["hook_event_name"] == "PostToolUseFailure" and ev[0]["error"] == "refused"
        ev = b.normalize({"type": "item.started", "item": {"id": "i2", "type": "command_execution", "command": "python 12_T1.py"}}, ctx)
        assert ev[0]["tool_name"] == "Bash" and ev[0]["summary"] == "python 12_T1.py"
        ev = b.normalize({"type": "item.completed", "item": {"id": "i2", "type": "command_execution", "command": "python 12_T1.py", "exit_code": 1, "aggregated_output": "boom"}}, ctx)
        assert ev[0]["failed"] and ev[0]["exit_code"] == 1
        ev = b.normalize({"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": "hello"}}, ctx)
        assert ev[0]["hook_event_name"] == "Text"
        ev = b.normalize({"type": "turn.completed", "usage": {"input_tokens": 1}}, ctx)
        assert [e["hook_event_name"] for e in ev] == ["Result", "Stop"] and ev[1]["summary"] == "hello"
        ev = b.normalize({"type": "turn.failed", "error": {"message": "usage limit reached, resets at 14:50"}}, ctx)
        assert ev[0]["limited"] and ev[0]["limited_until"] == "14:50"


class TestProcessOverTheFakeCli:
    def test_claude_multi_turn_keeps_one_process_and_captures_the_session(self, tmp_path):
        got = []
        p = ab.AgentProcess(_FakeClaude("claude", tmp_path / "m.json"), on_event=got.append, chip="PJ")
        assert _wait(p, lambda: any(e["hook_event_name"] == "Init" for e in got))
        assert p.session_id == "sess-fresh"
        assert p.send("first question")
        assert _wait(p, lambda: sum(1 for e in got if e["hook_event_name"] == "Stop") == 1)
        assert p.send("second")
        assert _wait(p, lambda: sum(1 for e in got if e["hook_event_name"] == "Stop") == 2)
        kinds = [e["hook_event_name"] for e in got]
        assert kinds.count("PreToolUse") == 2 and kinds.count("PostToolUse") == 2 and kinds.count("Text") == 2
        assert all(e["backend"] == "claude" and e["chip"] == "PJ" and e["origin"] == "chat" for e in got)
        texts = [e["text"] for e in got if e["hook_event_name"] == "Text"]
        assert texts == ["answer 1: first question", "answer 2: second"]
        assert p.alive(), "one process for the whole conversation"
        p.close_stdin()
        assert _wait(p, lambda: not p.alive()) and p.returncode == 0

    def test_codex_is_one_turn_per_process(self, tmp_path):
        got = []
        p = ab.AgentProcess(_FakeCodex("codex", tmp_path / "x", sm_url="u"), on_event=got.append, prompt="hello there")
        assert _wait(p, lambda: not p.alive())
        kinds = [e["hook_event_name"] for e in got]
        assert kinds == ["Init", "PreToolUse", "PostToolUse", "Text", "Result", "Stop"]
        assert p.session_id == "thread-fresh" and not p.send("x"), "no stdin channel"
        p2 = ab.AgentProcess(_FakeCodex("codex", tmp_path / "x", sm_url="u"), on_event=got.append, resume="thread-fresh", prompt="more")
        assert _wait(p2, lambda: not p2.alive()) and p2.session_id == "thread-fresh"

    def test_a_failed_tool_and_a_limit_are_events_not_exceptions(self, tmp_path, monkeypatch):
        got = []
        p = ab.AgentProcess(_FakeClaude("claude", tmp_path / "m.json"), on_event=got.append, env={"FAKE_FAIL": "1"})
        p.send("q")
        assert _wait(p, lambda: any(e["hook_event_name"] == "Stop" for e in got))
        assert any(e["hook_event_name"] == "PostToolUseFailure" and e["failed"] for e in got)
        p.close_stdin()
        got2 = []
        p2 = ab.AgentProcess(_FakeCodex("codex", tmp_path / "x", sm_url="u"), on_event=got2.append, prompt="q", env={"FAKE_LIMIT": "1"})
        assert _wait(p2, lambda: not p2.alive())
        res = next(e for e in got2 if e["hook_event_name"] == "Result")
        assert res["limited"] and res["limited_until"] == "14:50" and res["failed"]

    def test_a_crash_becomes_an_error_event_with_the_stderr(self, tmp_path):
        got = []
        p = ab.AgentProcess(_FakeCodex("codex", tmp_path / "x", sm_url="u"), on_event=got.append, prompt="q", env={"FAKE_CRASH": "1"})
        assert _wait(p, lambda: not p.alive() and any(e["hook_event_name"] == "Stop" for e in got))
        err = next(e for e in got if e["hook_event_name"] == "Error")
        assert err["failed"] and "boom" in err["error"] and p.returncode == 3

    def test_stop_kills_the_tree_and_says_so(self, tmp_path):
        got = []
        p = ab.AgentProcess(_FakeClaude("claude", tmp_path / "m.json"), on_event=got.append)
        assert _wait(p, lambda: any(e["hook_event_name"] == "Init" for e in got))
        p.stop()
        assert _wait(p, lambda: not p.alive())
        assert got[-1]["hook_event_name"] == "Stop" and got[-1].get("stopped") is True

    def test_detect(self):
        d = ab.detect(sys.executable)
        assert d["found"] is True and d["version"]
        assert ab.detect("no-such-cli-xyz")["found"] is False
