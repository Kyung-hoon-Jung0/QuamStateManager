"""docs/289: what the agent's CLI is doing now, for the person waiting.

The stream shapes below are the ones measured on claude 2.1.289 with
``--include-partial-messages`` (2026-10-05): a thinking block whose text is
EMPTY, the tool input as ``input_json_delta`` chunks, the answer as
``text_delta`` chunks, each before the whole ``assistant`` message.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from quam_state_manager.core import agent_backend as ab
from quam_state_manager.core import agent_live as al

ROOT = Path(__file__).resolve().parent.parent
FAKE = [sys.executable, str(ROOT / "tests" / "fake_agent_cli.py")]


def _se(event):
    return {"type": "stream_event", "event": event, "session_id": "s", "parent_tool_use_id": None}


# one turn as the real CLI prints it: think, call state_get, read the result, write the answer
TURN = [
    {"type": "system", "subtype": "init", "session_id": "s", "model": "m-1"},
    _se({"type": "message_start"}),
    _se({"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}),
    _se({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": ""}}),
    _se({"type": "content_block_stop", "index": 0}),
    _se({"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "t1", "name": "mcp__sm__state_get", "input": {}}}),
    _se({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"path": '}}),
    _se({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '"qubits.q1.T1"}'}}),
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "mcp__sm__state_get",
                                                   "input": {"path": "qubits.q1.T1"}}]}},
    _se({"type": "content_block_stop", "index": 1}),
    {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": False,
                                              "content": [{"type": "text", "text": '{"value": 4.2e-05}'}]}]}},
    _se({"type": "message_start"}),
    _se({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
    _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "T1 of q1 "}}),
    _se({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "is 42 us."}}),
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "T1 of q1 is 42 us."}]}},
    {"type": "result", "subtype": "success", "is_error": False, "num_turns": 2, "result": "T1 of q1 is 42 us."},
]


def _feed(stream, backend=None):
    b = backend or ab.ClaudeBackend("claude", Path("m"))
    ctx = {"open": {}, "summaries": {}, "backend": b.name, "session_id": None, "local_id": "x", "chip": "c", "seq": 0}
    live = al.LiveState(b.name)
    live.turn("what is the T1 of q1?")
    seen = []
    for ev in stream:
        live.raw(ev)
        for rec in b.normalize(ev, ctx):
            live.event(rec)
        snap = live.snapshot()
        cur = (snap["phase"], snap["tool"])
        if not seen or seen[-1] != cur:
            seen.append(cur)
    return live, seen


class TestTheClaudeTurn:
    def test_the_phases_follow_the_stream(self):
        _, seen = _feed(TURN)
        assert seen == [("waiting", None), ("thinking", None), ("tool", "state_get"), ("waiting", None),
                        ("writing", None), ("done", None)]

    def test_the_tool_call_is_a_step_with_its_readable_input_and_time(self):
        live, _ = _feed(TURN)
        steps = live.snapshot()["steps"]
        assert [(s["tool"], s["detail"], s["ok"]) for s in steps] == [("state_get", "path=qubits.q1.T1", True)]
        assert steps[0]["t1"] >= steps[0]["t0"]

    def test_the_log_reads_like_a_terminal_and_the_answer_is_one_line(self):
        live, _ = _feed(TURN)
        rows = live.snapshot()["lines"]
        kinds = [r[1] for r in rows]
        assert kinds == ["you", "init", "call", "ok", "text", "done"], kinds
        assert "model m-1" in rows[1][2]
        assert rows[3][2].startswith("state_get") and '{"value": 4.2e-05}' in rows[3][2], "the result preview rides the ok line"
        assert rows[4][2] == "T1 of q1 is 42 us."
        assert "2 model turns" in rows[5][2]

    def test_the_answer_streams_into_one_line_while_it_is_written(self):
        live, _ = _feed(TURN[:-2])                    # stop after the deltas, before the whole message
        snap = live.snapshot()
        assert snap["phase"] == "writing" and snap["text"] == "T1 of q1 is 42 us." and snap["partial"]
        assert [r[1] for r in snap["lines"]].count("text") == 1

    def test_empty_thinking_is_never_shown_as_words(self):
        live, _ = _feed(TURN)
        assert not any(r[1] == "think" for r in live.snapshot()["lines"])

    def test_a_failed_tool_says_so(self):
        bad = [dict(e) for e in TURN]
        bad[10] = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "is_error": True,
                                                             "content": "Error: no chip"}]}}
        live, _ = _feed(bad[:11])
        snap = live.snapshot()
        assert snap["steps"][0]["ok"] is False
        assert snap["lines"][-1][1] == "fail" and "Error: no chip" in snap["lines"][-1][2]

    def test_a_cli_that_dies_mid_turn_reads_failed_with_its_exit_code(self):
        live, _ = _feed(TURN[:9])
        live.finish(3)
        snap = live.snapshot()
        assert snap["phase"] == "failed" and "code 3" in snap["lines"][-1][2] and snap["ended"]

    def test_a_clean_exit_nobody_explained_is_an_end_not_a_failure(self):
        live = al.LiveState("claude")
        live.event({"hook_event_name": "Init", "summary": "model m"})
        live.finish(0)
        snap = live.snapshot()
        assert snap["phase"] == "done" and snap["lines"][-1][1] == "end"

    def test_a_person_stop_reads_stopped(self):
        live, _ = _feed(TURN[:9])
        live.event({"hook_event_name": "Stop", "stopped": True})
        assert live.snapshot()["phase"] == "stopped"

    def test_without_partial_messages_the_whole_messages_still_drive_it(self):
        whole = [e for e in TURN if e["type"] != "stream_event"]
        live, seen = _feed(whole)
        assert seen[-1] == ("done", None) and ("tool", "state_get") in seen
        assert live.snapshot()["partial"] is False


class TestCodex:
    def test_reasoning_tools_and_the_message(self):
        stream = [
            {"type": "thread.started", "thread_id": "th"},
            {"type": "item.started", "item": {"id": "r1", "type": "reasoning"}},
            {"type": "item.completed", "item": {"id": "r1", "type": "reasoning", "text": "look up T1 first"}},
            {"type": "item.started", "item": {"id": "i1", "type": "mcp_tool_call", "server": "sm", "tool": "state_get",
                                              "arguments": {"path": "qubits.q1.T1"}, "status": "in_progress"}},
            {"type": "item.completed", "item": {"id": "i1", "type": "mcp_tool_call", "server": "sm", "tool": "state_get",
                                                "arguments": {"path": "qubits.q1.T1"}, "status": "completed",
                                                "result": {"content": [{"type": "text", "text": "4.2e-05"}]}}},
            {"type": "item.completed", "item": {"id": "m1", "type": "agent_message", "text": "42 us"}},
            {"type": "turn.completed", "usage": {}},
        ]
        live, seen = _feed(stream, ab.CodexBackend("codex", Path("x"), sm_url="u"))
        assert ("thinking", None) in seen and seen[-1] == ("done", None)
        snap = live.snapshot()
        assert [s["ok"] for s in snap["steps"]] == [True]
        kinds = [r[1] for r in snap["lines"]]
        assert "think" in kinds and "call" in kinds and "ok" in kinds and kinds[-1] == "done"
        assert any("4.2e-05" in r[2] for r in snap["lines"] if r[1] == "ok")


class TestToolArgs:
    @pytest.mark.parametrize("name,summary,want", [
        ("state_get", '{"path": "qubits.q1.T1"}', "path=qubits.q1.T1"),
        ("state_search", '{"query": "T1", "limit": 100}', "query=T1 limit=100"),
        ("sm_status", "{}", ""),
        ("runs", '{"qubit": "q1", "tags": [], "n": 3}', "qubit=q1 n=3"),
        ("ToolSearch", '{"query": "select:mcp__sm__state_get,mcp__sm__runs", "max_results": 2}', "loads state_get, runs"),
        ("Read", "D:/lab/node.py", "D:/lab/node.py"),
    ])
    def test_a_person_reads_the_input(self, name, summary, want):
        assert al.tool_args(name, summary) == want


class TestTheFlag:
    def test_the_flag_rides_only_when_this_cli_names_it(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ab, "help_flags", lambda exe, sub=(), ttl=600.0: frozenset({ab.PARTIAL_FLAG, "--verbose"}))
        assert ab.PARTIAL_FLAG in ab.ClaudeBackend("claude", tmp_path / "m").command()
        monkeypatch.setattr(ab, "help_flags", lambda exe, sub=(), ttl=600.0: frozenset({"--verbose"}))
        assert ab.PARTIAL_FLAG not in ab.ClaudeBackend("claude", tmp_path / "m").command(), \
            "an older CLI would refuse the whole launch over an unknown option"
        monkeypatch.setattr(ab, "help_flags", lambda exe, sub=(), ttl=600.0: None)
        assert ab.PARTIAL_FLAG not in ab.ClaudeBackend("claude", tmp_path / "m").command()


class _FakeClaude(ab.ClaudeBackend):
    def command(self, *, resume=None, prompt=None):
        return FAKE + super().command(resume=resume, prompt=prompt)[1:]


def _wait(pred, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.03)
    return pred()


class TestOverTheFakeCli:
    def test_a_turn_in_progress_is_visible_and_the_crash_tail_stays_clean(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ab, "help_flags", lambda exe, sub=(), ttl=600.0: frozenset({ab.PARTIAL_FLAG}))
        monkeypatch.setenv("FAKE_SLOW", "1.5")
        b = _FakeClaude("claude", tmp_path / "m.json", readonly=True)
        p = ab.AgentProcess(b, local_id="t")
        try:
            p.send("hello there")
            mid = _wait(lambda: (lambda s: s if s["phase"] == "writing" and s["text"]
                                 and not any(e.get("hook_event_name") == "Text" for e in list(p.events)) else None)(p.live.snapshot()))
            assert mid and mid["text"] == "answer 1: hello there", "the answer is on screen before the whole message"
            assert [s["tool"] for s in mid["steps"]] == ["sm_status"] and mid["steps"][0]["ok"] is True
            done = _wait(lambda: (lambda s: s if s["phase"] == "done" else None)(p.live.snapshot()))
            assert done
            assert not any('"stream_event"' in ln for ln in p.raw_tail), "partial chunks never push out what a crash needs"
        finally:
            p.close_stdin()
            p.stop()


class TestTheExit:
    def test_a_cli_that_ends_without_a_turn_reads_ended(self, monkeypatch, tmp_path):
        """The reader calls finish(): without it the view would sit on "Working" forever."""
        monkeypatch.setattr(ab, "help_flags", lambda exe, sub=(), ttl=600.0: None)
        p = ab.AgentProcess(_FakeClaude("claude", tmp_path / "m.json", readonly=True), local_id="t")
        p.close_stdin()
        assert _wait(lambda: not p.alive() and p.live.snapshot()["ended"])
        snap = p.live.snapshot()
        assert snap["phase"] == "done" and snap["lines"][-1][1] == "end"

    def test_a_crash_mid_turn_reads_failed_with_the_cli_s_own_words(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ab, "help_flags", lambda exe, sub=(), ttl=600.0: None)
        monkeypatch.setenv("FAKE_CRASH", "1")
        p = ab.AgentProcess(_FakeClaude("claude", tmp_path / "m.json", readonly=True), local_id="t")
        p.send("hello")
        assert _wait(lambda: not p.alive() and p.live.snapshot()["phase"] == "failed")
        assert any(r[1] == "fail" and "boom" in r[2] for r in p.live.snapshot()["lines"])


class TestTheRoute:
    @pytest.fixture
    def c(self, tmp_path, monkeypatch):
        from quam_state_manager.web import chat_api
        from quam_state_manager.web.app import create_app
        from tests.test_web import _make_state, _make_wiring
        monkeypatch.setitem(chat_api.BACKEND_CLASSES, "claude", _FakeClaude)
        monkeypatch.setattr(chat_api.ab, "detect", lambda exe: {"found": True, "version": "fake 0.0.1"})
        monkeypatch.setattr(ab, "help_flags", lambda exe, sub=(), ttl=600.0: frozenset({ab.PARTIAL_FLAG}))
        monkeypatch.setattr(chat_api, "_LIVE_LINGER_S", 1.5)
        for k in ("FAKE_FAIL", "FAKE_LIMIT", "FAKE_CRASH", "FAKE_TOOL", "FAKE_ECHO_STDIN", "FAKE_LINGER"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("FAKE_SLOW", "1.5")
        d = tmp_path / "chip"
        d.mkdir()
        (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
        (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        client = app.test_client()
        client.post("/load", data={"folder": str(d)})
        yield client
        mgr = app.config.get("agent_chat")
        if mgr is not None:
            for p in mgr.asks.values():
                p.stop()

    def test_a_panel_question_is_live_until_it_is_answered(self, c):
        assert c.get("/api/agent/chat/live").get_json()["items"] == []
        r = c.post("/api/agent/chat/ask", json={"text": "how many qubits?", "backend": "claude", "feed": 1}).get_json()
        aid = r["ask_id"]
        mid = _wait(lambda: next((it for it in c.get("/api/agent/chat/live").get_json()["items"]
                                  if it["id"] == aid and it["phase"] == "writing"), None))
        assert mid and mid["kind"] == "ask" and mid["alive"] and mid["backend"] == "claude"
        assert mid["text"].startswith("answer 1"), "the answer as it is written"
        assert any(r[1] == "you" and "how many qubits?" in r[2] for r in mid["lines"])
        done = _wait(lambda: next((it for it in c.get("/api/agent/chat/live").get_json()["items"]
                                   if it["id"] == aid and it["phase"] == "done"), None))
        assert done, "the finished state is seen"
        assert _wait(lambda: next((it for it in c.get("/api/agent/chat/live").get_json()["items"]
                                   if it["id"] == aid and not it["alive"]), None)), \
            "it lingers after the process is gone, so its last state is seen"
        assert _wait(lambda: c.get("/api/agent/chat/live").get_json()["items"] == []), "then it goes"
        again = c.get(f"/api/agent/chat/live?id={aid}").get_json()["items"]
        assert [it["id"] for it in again] == [aid] and again[0]["phase"] == "done", "by id it can still be read"

    def test_a_question_from_elsewhere_is_not_on_the_panel(self, c):
        aid = c.post("/api/agent/chat/ask", json={"text": "setup test", "backend": "claude"}).get_json()["ask_id"]
        time.sleep(0.3)
        assert all(it["id"] != aid for it in c.get("/api/agent/chat/live").get_json()["items"])
