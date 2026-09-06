"""docs/172: the Claude Code hook script -- records, never interprets.

Run the module the way Claude Code runs it: a JSON event on stdin, exit 0
whatever happens. The record lands in ``<instance>/agent_events/<date>.jsonl``
FIRST with the facts SM needs (failure, the session's state path); the hook
never writes the journal -- SM derives the lines, so there is one writer per
.md and no duplicate when SM was merely slow (a review finding).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager import hook

ROOT = Path(__file__).resolve().parent.parent


def _run(tmp_path, event: dict | str, **env_extra) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1", SM_INSTANCE=str(tmp_path), PYTHONPATH=str(ROOT))
    env.pop("SM_URL", None)
    env.pop("QUAM_STATE_PATH", None)
    env.update(env_extra)
    return subprocess.run([sys.executable, "-m", "quam_state_manager.hook"],
                          input=event if isinstance(event, str) else json.dumps(event),
                          capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT), timeout=60)


def _last(tmp_path) -> dict:
    f = tmp_path / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
    return json.loads(f.read_text(encoding="utf-8").splitlines()[-1])


class TestDiskFirst:
    def test_an_event_lands_on_disk_with_no_sm_running(self, tmp_path):
        r = _run(tmp_path, {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash",
                            "tool_use_id": "t1", "tool_input": {"command": "python 05_power_rabi.py --q q1"},
                            "cwd": "D:/lab", "transcript_path": "D:/t.jsonl"})
        assert r.returncode == 0, r.stderr
        rec = _last(tmp_path)
        assert rec["tool_name"] == "Bash" and rec["tool_use_id"] == "t1"
        assert rec["summary"] == "python 05_power_rabi.py --q q1"
        assert rec["transcript_path"] == "D:/t.jsonl" and rec["ts"] > 0
        assert rec["failed"] is False

    def test_the_hook_never_writes_the_journal(self, tmp_path):
        _run(tmp_path, {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash",
                        "tool_use_id": "t1", "tool_input": {"command": "python calibrations/05_power_rabi.py"},
                        "tool_response": {"stdout": "ok", "exit_code": 0}})
        assert not (tmp_path / "journal").exists(), "SM derives journal lines; the hook only records"
        assert _last(tmp_path)["failed"] is False

    def test_a_failed_tool_is_recorded_as_such(self, tmp_path):
        _run(tmp_path, {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash",
                        "tool_use_id": "t2", "tool_input": {"command": "python 05_power_rabi.py"},
                        "tool_response": {"stdout": "", "stderr": "Traceback...\nKeyError: 'q9'", "exit_code": 1}})
        rec = _last(tmp_path)
        assert rec["failed"] is True and rec["exit_code"] == "1" and "KeyError" in rec["error"]
        _run(tmp_path, {"hook_event_name": "PostToolUseFailure", "session_id": "s1", "tool_name": "Bash",
                        "tool_use_id": "t3", "tool_input": {"command": "python x.py"}, "error": "timed out"})
        rec = _last(tmp_path)
        assert rec["failed"] is True and rec["error"] == "timed out"

    def test_the_sessions_state_path_travels(self, tmp_path):
        chip = tmp_path / "lab" / "quam_state"
        chip.mkdir(parents=True)
        (chip / "state.json").write_text("{}", encoding="utf-8")
        _run(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t",
                        "tool_input": {"command": "python x.py"}, "cwd": str(tmp_path / "lab")})
        assert Path(_last(tmp_path)["quam_state_path"]) == chip
        _run(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t",
                        "tool_input": {"command": "python x.py"}, "cwd": str(tmp_path)}, QUAM_STATE_PATH="D:/x/quam_state")
        assert _last(tmp_path)["quam_state_path"] == "D:/x/quam_state", "the env var qualibrate honours wins"
        _run(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t",
                        "tool_input": {"command": "python x.py"}, "cwd": str(tmp_path / "nowhere")})
        assert _last(tmp_path)["quam_state_path"] is None, "unknown is None, never an invented chip"

    def test_the_turns_final_message_is_recorded(self, tmp_path):
        _run(tmp_path, {"hook_event_name": "Stop", "session_id": "s1",
                        "last_assistant_message": "Power Rabi on q1 looks clean; moving to Ramsey."})
        assert _last(tmp_path)["summary"].startswith("Power Rabi on q1")

    def test_instance_flag_beats_the_env(self, tmp_path):
        """docs/173 S7: SM's setup writes `--instance <dir>` into the hook
        line when SM runs on a custom instance dir."""
        other = tmp_path / "other_inst"
        env = dict(os.environ, PYTHONUTF8="1", SM_INSTANCE=str(tmp_path), PYTHONPATH=str(ROOT))
        env.pop("SM_URL", None)
        r = subprocess.run([sys.executable, "-m", "quam_state_manager.hook", "--backend", "codex", "--instance", str(other)],
                           input=json.dumps({"hook_event_name": "Stop", "session_id": "s", "last_assistant_message": "hi"}),
                           capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT), timeout=60)
        assert r.returncode == 0
        f = other / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        assert f.exists() and json.loads(f.read_text(encoding="utf-8").splitlines()[-1])["backend"] == "codex"
        assert not (tmp_path / "agent_events").exists(), "the env's dir was not used"

    def test_garbage_stdin_exits_zero(self, tmp_path):
        assert _run(tmp_path, "not json{").returncode == 0
        assert _run(tmp_path, "").returncode == 0


class TestSummaries:
    def test_summary_by_tool(self):
        assert hook._summary({"tool_name": "Bash", "tool_input": {"command": "ls"}}) == "ls"
        assert hook._summary({"tool_name": "Edit", "tool_input": {"file_path": "a.py"}}) == "a.py"
        assert hook._summary({"tool_name": "mcp__sm__state_edit", "tool_input": {"path": "p"}}) == '{"path": "p"}'
        assert len(hook._summary({"tool_name": "Bash", "tool_input": {"command": "x" * 1000}})) == 400

    def test_failure_rules(self):
        assert hook._failure({"hook_event_name": "PostToolUse", "tool_response": {"exit_code": 0}}) == (False, "0", None)
        assert hook._failure({"hook_event_name": "PostToolUse", "tool_response": {"is_error": True, "stderr": "boom"}})[0]
        assert hook._failure({"hook_event_name": "PostToolUse", "tool_response": {"interrupted": True}})[0]
        assert hook._failure({"hook_event_name": "PostToolUse", "tool_response": "Error: no such file"})[0]
        assert hook._failure({"hook_event_name": "PostToolUse", "tool_response": "all good"}) == (False, None, None)
