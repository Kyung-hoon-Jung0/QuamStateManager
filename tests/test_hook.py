"""docs/172: the Claude Code hook script -- disk first, SM best-effort.

Run the module the way Claude Code runs it: a JSON event on stdin, exit 0
whatever happens. With no SM running the event still lands in
``<instance>/agent_events/<date>.jsonl`` and the journal line still lands in
the .md -- the morning-after view depends on exactly that.
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


def _run(tmp_path, event: dict | str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONUTF8="1", SM_INSTANCE=str(tmp_path), PYTHONPATH=str(ROOT), SM_CHIP="c")
    env.pop("SM_URL", None)
    return subprocess.run([sys.executable, "-m", "quam_state_manager.hook"],
                          input=event if isinstance(event, str) else json.dumps(event),
                          capture_output=True, text=True, encoding="utf-8", env=env, cwd=str(ROOT), timeout=60)


class TestDiskFirst:
    def test_an_event_lands_on_disk_with_no_sm_running(self, tmp_path):
        r = _run(tmp_path, {"hook_event_name": "PreToolUse", "session_id": "s1", "tool_name": "Bash",
                            "tool_use_id": "t1", "tool_input": {"command": "python 05_power_rabi.py --q q1"},
                            "cwd": "D:/lab", "transcript_path": "D:/t.jsonl"})
        assert r.returncode == 0, r.stderr
        f = tmp_path / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        rec = json.loads(f.read_text(encoding="utf-8").splitlines()[-1])
        assert rec["tool_name"] == "Bash" and rec["tool_use_id"] == "t1"
        assert rec["summary"] == "python 05_power_rabi.py --q q1"
        assert rec["transcript_path"] == "D:/t.jsonl" and rec["ts"] > 0

    def test_a_completed_node_run_is_journaled_even_with_sm_closed(self, tmp_path):
        _run(tmp_path, {"hook_event_name": "PostToolUse", "session_id": "s1", "tool_name": "Bash",
                        "tool_use_id": "t1", "tool_input": {"command": "python calibrations/05_power_rabi.py"}})
        md = list((tmp_path / "journal" / "c").glob("*.md"))
        assert md, "the journal line went to disk"
        text = md[0].read_text(encoding="utf-8")
        assert "`hook` ran `05_power_rabi`" in text

    def test_the_turns_final_message_is_journaled_as_claude(self, tmp_path):
        _run(tmp_path, {"hook_event_name": "Stop", "session_id": "s1",
                        "last_assistant_message": "Power Rabi on q1 looks clean; moving to Ramsey."})
        text = next((tmp_path / "journal" / "c").glob("*.md")).read_text(encoding="utf-8")
        assert "Claude: Power Rabi on q1 looks clean; moving to Ramsey." in text

    def test_a_pre_event_and_a_read_are_not_journal_noise(self, tmp_path):
        _run(tmp_path, {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_use_id": "t",
                        "tool_input": {"command": "python x.py"}})
        _run(tmp_path, {"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_use_id": "t2",
                        "tool_input": {"file_path": "a.png"}})
        assert not (tmp_path / "journal").exists()

    def test_garbage_stdin_exits_zero(self, tmp_path):
        assert _run(tmp_path, "not json{").returncode == 0
        assert _run(tmp_path, "").returncode == 0


class TestSummaries:
    def test_summary_by_tool(self):
        assert hook._summary({"tool_name": "Bash", "tool_input": {"command": "ls"}}) == "ls"
        assert hook._summary({"tool_name": "Edit", "tool_input": {"file_path": "a.py"}}) == "a.py"
        assert hook._summary({"tool_name": "mcp__sm__state_edit", "tool_input": {"path": "p"}}) == '{"path": "p"}'
        assert len(hook._summary({"tool_name": "Bash", "tool_input": {"command": "x" * 1000}})) == 400

    def test_journal_line_rules(self):
        assert hook._journal_line({"hook_event_name": "PostToolUse", "tool_name": "Edit", "summary": "a.py"}) == "edited `a.py`"
        assert hook._journal_line({"hook_event_name": "PostToolUse", "tool_name": "mcp__sm__state_edit", "summary": "{}"}) is None, \
            "the MCP tool journals itself, with the reason"
        assert hook._journal_line({"hook_event_name": "PostToolUse", "tool_name": "Bash", "summary": "git status"}) == "ran `git status`"
