"""The chat inside SM (docs/173 S4): routes over the manager over the fake CLI.

Every test drives the real ``/api/agent/chat/*`` routes with the real
``ChatManager`` and ``AgentProcess``; only the CLI is ``tests/fake_agent_cli.py``
(swapped in through ``chat_api.BACKEND_CLASSES``). Timing waits are bounded
polls, never sleeps of a guessed length.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import agent_backend as ab
from quam_state_manager.core import agent_session
from quam_state_manager.web import chat_api
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parent.parent
FAKE = [sys.executable, str(ROOT / "tests" / "fake_agent_cli.py")]


class _FakeClaude(ab.ClaudeBackend):
    def command(self, *, resume=None, prompt=None):
        return FAKE + super().command(resume=resume, prompt=prompt)[1:]


class _FakeCodex(ab.CodexBackend):
    def command(self, *, resume=None, prompt=None):
        return FAKE + super().command(resume=resume, prompt=prompt)[1:]


def _wait(pred, timeout=15.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    return pred()


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setitem(chat_api.BACKEND_CLASSES, "claude", _FakeClaude)
    monkeypatch.setitem(chat_api.BACKEND_CLASSES, "codex", _FakeCodex)
    monkeypatch.setattr(chat_api.ab, "detect", lambda exe: {"found": True, "version": "fake 0.0.1"})
    for k in ("FAKE_FAIL", "FAKE_LIMIT", "FAKE_CRASH", "FAKE_TOOL", "FAKE_ECHO_STDIN", "FAKE_LINGER"):
        monkeypatch.delenv(k, raising=False)


@pytest.fixture
def inst(tmp_path):
    return tmp_path / "_app_instance"


@pytest.fixture
def app(inst, fakes):
    return create_app(testing=True, instance_path=str(inst))


@pytest.fixture
def synth_folder(tmp_path):
    from tests.test_web import _make_state, _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return d


@pytest.fixture
def c(app, synth_folder):
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    yield client
    mgr = app.config.get("agent_chat")
    if mgr is not None:
        for s in mgr.sessions.values():
            s.stop(now=True)
        for p in mgr.asks.values():
            p.stop()


def _chip(c):
    """The records' KEY (session / limits / the manager): <name>-<path hash>."""
    return c.get("/api/agent/chat/status").get_json()["chip_key"]


def _name(c):
    """The chip's NAME (the journal, the SM_CHIP pin, the system prompt)."""
    return c.get("/api/agent/chat/status").get_json()["chip"]


def _events(c, after=0):
    return c.get(f"/api/agent/chat/events?after={after}").get_json()["events"]


def _texts(c):
    return [e["text"] for e in _events(c) if e["hook_event_name"] == "Text"]


def _journal(c, inst):
    from quam_state_manager.core import journal as jm
    return jm.read(str(inst), _name(c), datetime.now().strftime("%Y-%m-%d")) or ""


# ------------------------------------------------------------------ chip

class TestNoChip:
    def test_everything_but_status_and_backends_needs_a_chip(self, app):
        c = app.test_client()
        st = c.get("/api/agent/chat/status").get_json()
        assert st["ok"] and st["chip"] is None and st["session"] is None
        assert c.get("/api/agent/chat/backends").get_json()["backends"]["claude"]["found"] is True
        assert c.post("/api/agent/chat/start", json={"prompt": "hi"}).status_code == 409
        assert c.post("/api/agent/chat/send", json={"text": "hi"}).status_code == 409
        assert c.post("/api/agent/chat/ask", json={"text": "hi"}).status_code == 409


# ---------------------------------------------------------------- claude

class TestClaudeDriving:
    def test_start_records_session_events_disk_and_journal(self, c, inst):
        r = c.post("/api/agent/chat/start", json={"prompt": "calibrate q1 readout", "backend": "claude"})
        assert r.status_code == 200, r.get_json()
        d = r.get_json()
        assert d["session"]["backend"] == "claude" and d["session"]["alive"] and d["session"]["turns"] == 1
        # wait for the Stop, not just the Text answer: Claude emits Result+Stop
        # right AFTER Text, so reading on the answer alone races the turn's close
        assert _wait(lambda: any(e["hook_event_name"] == "Stop" for e in _events(c)))
        assert "answer 1: calibrate q1 readout" in _texts(c)
        ev = _events(c)
        kinds = [e["hook_event_name"] for e in ev]
        # review R4-6: the person's own message is an event too, and it precedes the CLI's Init
        assert kinds[:3] == ["User", "Init", "PreToolUse"] and "PostToolUse" in kinds and "Stop" in kinds
        assert all(e["origin"] == "chat" and e["chip"] == _name(c) and e["owner"] == "human" for e in ev)
        assert all(ev[i]["n"] < ev[i + 1]["n"] for i in range(len(ev) - 1)), "the page's cursor is monotonic"
        # the session file: who / what / pid, and the CLI's own session id merged in (not overwritten)
        rec = agent_session.load(str(inst), _chip(c))
        assert rec["backend"] == "claude" and rec["owner"] == "human" and rec["window"] == "chat"
        assert rec["mode"] == "ask-writes" and rec["pid"] == d["session"]["pid"]
        assert _wait(lambda: (agent_session.load(str(inst), _chip(c)) or {}).get("session_id") == "sess-fresh")
        # disk first: today's jsonl carries the same events the hook would have written
        day = inst / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        lines = [json.loads(l) for l in day.read_text(encoding="utf-8").splitlines()]
        assert [l["hook_event_name"] for l in lines][:3] == ["User", "Init", "PreToolUse"]
        assert all(l["origin"] == "chat" for l in lines)
        # the journal names the start, the mode, the presser
        j = _journal(c, inst)
        assert "claude session started in SM by human (mode ask-writes)" in j
        # and the chip endpoint's pill sees a claude session (an mcp__sm tool made it relevant)
        now = c.get("/api/agent/chip").get_json()["now"]
        assert now["state"] in ("between", "running") and now["session"]["backend"] == "claude"

    def test_second_message_keeps_the_context_and_counts_usage(self, c):
        c.post("/api/agent/chat/start", json={"prompt": "first"})
        assert _wait(lambda: len(_texts(c)) == 1)
        r = c.post("/api/agent/chat/send", json={"text": "second"}).get_json()
        assert r["ok"] and r["sent"] and r["session"]["turns"] == 2
        assert _wait(lambda: len(_texts(c)) == 2)
        assert _texts(c)[1] == "answer 2: second"
        st = c.get("/api/agent/chat/status").get_json()["session"]
        assert st["usage"]["input_tokens"] == 30 and st["usage"]["output_tokens"] == 10 and st["alive"]
        assert st["busy"] is False, "a Stop closed the turn"

    def test_events_after_cursor_and_chip_filter(self, c):
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        assert _wait(lambda: any(e["hook_event_name"] == "Stop" for e in _events(c)))
        allev = _events(c)
        last = c.get("/api/agent/chat/events").get_json()["last"]
        assert last == allev[-1]["n"]
        assert _events(c, after=last) == []
        assert _events(c, after=allev[1]["n"]) == allev[2:]

    def test_start_twice_refuses_while_alive(self, c):
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        r = c.post("/api/agent/chat/start", json={"prompt": "y"})
        assert r.status_code == 409 and "already driving" in r.get_json()["error"]

    def test_a_foreign_alive_session_file_refuses(self, c, inst):
        agent_session.save(str(inst), _chip(c), backend="claude", owner="terminal", pid=os.getpid())
        r = c.post("/api/agent/chat/start", json={"prompt": "y"})
        assert r.status_code == 409 and "is alive on" in r.get_json()["error"]
        assert c.get("/api/agent/chat/status").get_json()["foreign_alive"] is True

    def test_end_then_resume_last_hands_the_session_id_back(self, c, inst):
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        assert _wait(lambda: len(_texts(c)) == 1)
        r = c.post("/api/agent/chat/end").get_json()
        assert r["ok"]
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        assert agent_session.load(str(inst), _chip(c))["pid"] is None
        assert "claude session ended by human" in _journal(c, inst)
        assert c.post("/api/agent/chat/send", json={"text": "z"}).status_code == 409
        st = c.get("/api/agent/chat/status").get_json()
        assert st["resumable"] is True
        r = c.post("/api/agent/chat/start", json={"prompt": "again", "resume": "last"}).get_json()
        assert r["ok"] and r["resumed"] is True and r["session"]["session_id"] == "sess-fresh"
        assert _wait(lambda: any(e["hook_event_name"] == "Init" and e["session_id"] == "sess-fresh"
                                 and e["n"] > 0 for e in _events(c)[-8:]))
        assert ", resumed)" in _journal(c, inst)

    def test_resume_prepends_what_happened_while_away(self, c, inst, app, monkeypatch):
        """docs/173 §3.3: a run that landed after the session's last event
        is told to the resumed agent before its own message."""
        from quam_state_manager.web import agent_api as aa
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        assert _wait(lambda: len(_texts(c)) == 1)
        c.post("/api/agent/chat/end")
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        rec = agent_session.load(str(inst), _chip(c))
        later = datetime.fromtimestamp(rec["updated"] + 120)

        class _DS:
            def list_runs(self):
                return [{"run_id": 501, "experiment_name": "05_power_rabi", "qubits": ["q1"], "outcome": "successful",
                         "date": later.strftime("%Y-%m-%d"), "time": later.strftime("%H:%M:%S")},
                        {"run_id": 400, "experiment_name": "02_res_spec", "qubits": ["q2"], "outcome": "successful",
                         "date": "2020-01-01", "time": "00:00:00"}]
        monkeypatch.setattr(aa, "_ds", lambda: _DS())
        r = c.post("/api/agent/chat/start", json={"prompt": "carry on", "resume": "last"}).get_json()
        assert r["ok"] and r["away"] is True
        drive = app.config["agent_chat"].get(_chip(c)).proc
        # the fake echoes the first 20 chars of the user turn: the block comes first
        assert _wait(lambda: any(t.startswith("answer 1: [While you were") for t in _texts(c)))
        monkeypatch.setattr(aa, "_ds", lambda: None)
        c.post("/api/agent/chat/end")
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        r = c.post("/api/agent/chat/start", json={"prompt": "again", "resume": "last"}).get_json()
        assert r["ok"] and r["away"] is False

    def test_cwd_is_the_calibrations_folder_when_it_exists(self, c, inst, tmp_path, app):
        from quam_state_manager.core import scheduler
        home = str(Path(str(inst)) / "agent_home")
        assert c.get("/api/agent/chat/backends").get_json()["cwd"] == home and Path(home).is_dir()
        assert not (Path(home) / "CLAUDE.md").exists() and Path(home).resolve() != Path(__file__).resolve().parent.parent
        lab = tmp_path / "lab"
        lab.mkdir()
        # machine-wide settings live one level above every chip scope (docs/80): <instance>/scheduler/_shared.json
        shared = Path(str(inst)) / "scheduler" / "_shared.json"
        assert shared == scheduler.shared_settings_path(Path(str(inst)) / "scheduler" / "any-scope")
        shared.parent.mkdir(parents=True, exist_ok=True)
        shared.write_text(json.dumps({"calibrations_folder": str(lab)}), encoding="utf-8")
        assert c.get("/api/agent/chat/backends").get_json()["cwd"] == str(lab)
        r = c.post("/api/agent/chat/start", json={"prompt": "x"}).get_json()
        assert r["cwd"] == str(lab) and r["session"]["cwd"] == str(lab)
        assert app.config["agent_chat"].get(_chip(c)).proc.backend.cwd == str(lab)
        assert f"Working folder: {lab}" in app.config["agent_chat"].get(_chip(c)).proc.backend.system_prompt
        shared.write_text(json.dumps({"calibrations_folder": str(tmp_path / "gone")}), encoding="utf-8")
        assert c.get("/api/agent/chat/backends").get_json()["cwd"] == home, "a missing folder falls back to the home"

    def test_resume_last_with_nothing_is_honest(self, c):
        r = c.post("/api/agent/chat/start", json={"prompt": "x", "resume": "last"})
        assert r.status_code == 409 and "nothing to resume" in r.get_json()["error"]

    def test_mode_given_at_start_is_written_to_limits(self, c, inst):
        from quam_state_manager.core import limits
        r = c.post("/api/agent/chat/start", json={"prompt": "x", "mode": "auto"}).get_json()
        assert r["session"]["mode"] == "auto"
        assert limits.load(str(inst), _chip(c))["mode"] == "auto"
        assert c.post("/api/agent/chat/start", json={"prompt": "x", "mode": "nope"}).status_code in (400, 409)

    def test_until_is_kept_and_journaled(self, c, inst):
        until = time.time() + 3600
        r = c.post("/api/agent/chat/start", json={"prompt": "x", "until": until}).get_json()
        assert abs(r["session"]["until"] - until) < 1
        assert ", until " in _journal(c, inst)
        assert agent_session.load(str(inst), _chip(c))["until"] == pytest.approx(until)


class TestStop:
    def test_stop_now_kills_and_records_first(self, c, inst):
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        assert _wait(lambda: len(_texts(c)) == 1)
        # docs/173 S8: agent_says is ON by default and the line is LABELLED by_claude
        assert _wait(lambda: "`by_claude` answer 1: x" in _journal(c, inst)), "the agent's own words are journaled, labelled"
        r = c.post("/api/agent/session/stop", json={"mode": "now"}).get_json()
        assert r["ok"] and r["session"]["stopped"]
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        ev = _events(c)
        assert ev[-1]["hook_event_name"] == "Stop" and ev[-1].get("stopped") is True
        j = _journal(c, inst)
        assert "Stop (now) pressed by human" in j
        assert "Claude: stopped by a human" not in j, "the human's Stop is journaled by the door, not as agent speech"
        assert c.post("/api/agent/chat/send", json={"text": "z"}).status_code == 409

    def test_stop_after_run_keeps_the_process_and_a_new_message_resumes(self, c, inst):
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        assert _wait(lambda: len(_texts(c)) == 1)
        c.post("/api/agent/session/stop", json={"mode": "after_run"})
        rec = agent_session.load(str(inst), _chip(c))
        assert rec["agent_stop"]["mode"] == "after_run"
        assert c.get("/api/agent/chat/status").get_json()["session"]["alive"] is True
        r = c.post("/api/agent/chat/send", json={"text": "go on"}).get_json()
        assert r["ok"]
        assert agent_session.load(str(inst), _chip(c))["agent_stop"] is None
        assert "resumed by human (Stop cleared)" in _journal(c, inst)
        assert _wait(lambda: len(_texts(c)) == 2)


class TestLimits:
    def test_usage_limit_marks_the_session_and_the_pill(self, c, inst, monkeypatch):
        monkeypatch.setenv("FAKE_LIMIT", "1")
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        assert _wait(lambda: any(e["hook_event_name"] == "Result" for e in _events(c)))
        res = [e for e in _events(c) if e["hook_event_name"] == "Result"][-1]
        assert res["limited"] is True and isinstance(res["limited_until"], float)
        rec = agent_session.load(str(inst), _chip(c))
        assert isinstance(rec["limited_until"], float) and rec["limited_until"] > time.time()
        now = c.get("/api/agent/chip").get_json()["now"]
        assert now["state"] == "limited" and now["limited_resets"] == "14:50"

    def test_a_clock_string_from_the_hook_path_never_500s(self, c, inst):
        """The terminal hook's event could carry the CLI's own words."""
        agent_session.save(str(inst), _chip(c), limited_until="resets 2:50pm (Asia/Seoul)")
        now = c.get("/api/agent/chip").get_json()["now"]
        assert now["state"] == "limited" and now["limited_resets"] == "14:50"
        agent_session.save(str(inst), _chip(c), limited_until="soon-ish")
        now = c.get("/api/agent/chip").get_json()["now"]
        assert now["state"] == "limited" and now["limited_resets"] == "soon-ish"

    def test_a_failed_tool_counts_as_a_failure(self, c, monkeypatch):
        monkeypatch.setenv("FAKE_FAIL", "1")
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        assert _wait(lambda: any(e["hook_event_name"] == "PostToolUseFailure" for e in _events(c)))
        e = [e for e in _events(c) if e["hook_event_name"] == "PostToolUseFailure"][0]
        assert e["failed"] and "no chip" in e["error"]
        assert c.get("/api/agent/chip").get_json()["now"]["failures_today"] >= 1


# ----------------------------------------------------------------- codex

class TestCodex:
    def test_one_turn_per_process_and_resume_by_thread(self, c, inst):
        r = c.post("/api/agent/chat/start", json={"prompt": "hello", "backend": "codex"}).get_json()
        assert r["ok"] and r["session"]["one_turn_per_process"] is True
        assert _wait(lambda: "codex answer: hello" in _texts(c))
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        assert agent_session.load(str(inst), _chip(c))["session_id"] == "thread-fresh"
        r = c.post("/api/agent/chat/send", json={"text": "next"}).get_json()
        assert r["ok"] and r["queued"] == 0 and r["session"]["turns"] == 2
        assert _wait(lambda: "codex answer: next" in _texts(c))
        inits = [e for e in _events(c) if e["hook_event_name"] == "Init"]
        assert [i["session_id"] for i in inits] == ["thread-fresh", "thread-fresh"], "turn 2 resumed the thread"
        assert "codex session started in SM by human" in _journal(c, inst)

    def test_codex_needs_a_first_message(self, c):
        r = c.post("/api/agent/chat/start", json={"backend": "codex"})
        assert r.status_code == 400 and "first message" in r.get_json()["error"]

    def test_a_message_during_a_turn_is_queued_then_sent_when_the_process_exits(self, c, app, monkeypatch):
        """Measured on the real CLI: the Codex process outlives its own
        turn.completed by a moment; a message sent then was queued and,
        with the Stop event already gone by, never drained."""
        monkeypatch.setenv("FAKE_LINGER", "1")
        c.post("/api/agent/chat/start", json={"prompt": "one", "backend": "codex"})
        assert _wait(lambda: any(e["hook_event_name"] == "Stop" for e in _events(c)))
        st = c.get("/api/agent/chat/status").get_json()["session"]
        assert st["alive"] is True, "the fake lingers after its turn like the real CLI"
        r = c.post("/api/agent/chat/send", json={"text": "two"}).get_json()
        assert r["queued"] == 1
        assert _wait(lambda: "codex answer: two" in _texts(c)), "drained by the process exit, no hand nudge"
        assert app.config["agent_chat"].get(_chip(c)).turns == 2

    def test_stop_clears_the_queue(self, c, app, inst):
        c.post("/api/agent/chat/start", json={"prompt": "one", "backend": "codex"})
        assert _wait(lambda: "codex answer: one" in _texts(c))
        s = app.config["agent_chat"].get(_chip(c))
        s.queue.append("never")
        c.post("/api/agent/session/stop", json={"mode": "after_run"})
        assert s.queue == type(s.queue)()
        s._next_turn()
        assert "codex answer: never" not in _texts(c)
        # the flag written without the door (a tool's own refusal path): the drain refuses too
        s.queue.append("never2")
        agent_session.request_stop(str(inst), _chip(c), who="run_node", mode="after_run")
        s._next_turn()
        time.sleep(0.5)
        assert "codex answer: never2" not in _texts(c) and not s.queue


# ------------------------------------------------------------------- ask

class TestAsk:
    def test_a_question_is_read_only_and_never_the_driving_session(self, c, inst):
        r = c.post("/api/agent/chat/ask", json={"text": "what is q1 f_01?"}).get_json()
        assert r["ok"] and r["ask_id"].startswith("ask-") and r["backend"] == "claude"
        a = _wait(lambda: (lambda d: d if d["done"] else None)(c.get(f"/api/agent/chat/ask/{r['ask_id']}").get_json()))
        assert a and a["answer"] == "answer 1: what is q1 f_01?" and a["failed"] is False
        assert a["tools"] == [{"tool": "mcp__sm__sm_status", "summary": '{"q": "what is q1 f_01?"}', "failed": False}]
        # the read-only MCP config + read-only allow list
        mj = inst / "agent_mcp" / f"{_name(c)}-claude-ro.json"
        cfg = json.loads(mj.read_text(encoding="utf-8"))
        assert cfg["mcpServers"]["sm"]["env"]["SM_MCP_MODE"] == "readonly"
        assert cfg["mcpServers"]["sm"]["env"]["SM_CHIP"] == _name(c)
        # the driving door is untouched: no session, no chat events, nothing on disk, nothing in the journal
        assert c.get("/api/agent/chat/status").get_json()["session"] is None
        assert _events(c) == []
        assert not (inst / "agent_events").exists() or not list((inst / "agent_events").glob("*.jsonl"))
        assert "answer 1" not in _journal(c, inst)
        assert c.get("/api/agent/chat/ask/nope").status_code == 404

    def test_the_driving_config_is_not_read_only(self, c, inst):
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        cfg = json.loads((inst / "agent_mcp" / f"{_name(c)}-claude.json").read_text(encoding="utf-8"))
        assert "SM_MCP_MODE" not in cfg["mcpServers"]["sm"]["env"]
        assert cfg["mcpServers"]["sm"]["env"]["SM_URL"] == "http://localhost"
        assert cfg["mcpServers"]["sm"]["args"] == ["-m", "quam_state_manager.mcp"]

    def test_the_question_carries_the_ask_rules_and_the_driver_the_driving_rules(self, c, app):
        from quam_state_manager.core import agent_chat
        c.post("/api/agent/chat/ask", json={"text": "q"})
        c.post("/api/agent/chat/start", json={"prompt": "x"})
        mgr = app.config["agent_chat"]
        askp = next(iter(mgr.asks.values()))
        drive = mgr.get(_chip(c)).proc
        assert agent_chat.ASK_RULES in askp.backend.system_prompt and "--allowedTools" in askp.cmd
        assert agent_chat.DEFAULT_RULES in drive.backend.system_prompt and "bypassPermissions" in drive.cmd
        assert f"Chip: {_name(c)}. Mode: ask-writes" in drive.backend.system_prompt

    def test_the_driving_rules_end_with_the_concision_rule(self):
        """customer feedback 2026-09-08: the person reads the agent in a small panel, and the
        answers were long and came with a translated summary. The rule is prompt text, so a
        string pin is the pin -- and it is the LAST thing the model reads."""
        from quam_state_manager.core import agent_chat
        r = agent_chat.DEFAULT_RULES
        assert "Answer in at most ~120 words unless the human asks for detail" in r
        assert "lead with the fact or the decision; bullets over paragraphs" in r
        assert ("never repeat the answer in a second language or add a translated summary "
                "(the person reads the language they wrote in)") in r
        assert "cite runs as #N and fields as `dot.paths`" in r
        assert "reads you in a small panel" not in r and "Answer briefly" not in r
        assert r.rstrip().endswith("`dot.paths`.")


# ---------------------------------------------------------------- replay

class TestReplay:
    def test_chat_events_survive_a_restart(self, c, inst, synth_folder, fakes):
        c.post("/api/agent/chat/start", json={"prompt": "remember me"})
        assert _wait(lambda: len(_texts(c)) == 1)
        app2 = create_app(testing=True, instance_path=str(inst))
        c2 = app2.test_client()
        c2.post("/load", data={"folder": str(synth_folder)})
        ev = c2.get("/api/agent/events?n=100").get_json()["events"]
        assert any(e.get("origin") == "chat" and e["hook_event_name"] == "Text"
                   and e["text"] == "answer 1: remember me" for e in ev)
        st = c2.get("/api/agent/chat/status").get_json()
        assert st["session"] is None and st["file"]["backend"] == "claude"
        # the cursor is monotonic across the restart: a client holding the replayed
        # `last` still sees every new event (a counter restarting at 1 hid three turns)
        replayed_last = c2.get("/api/agent/chat/events").get_json()["last"]
        assert replayed_last >= 5
        # the first app's process is still alive: app2 refuses to drive the same chip (the docs/80 rule)
        assert c2.post("/api/agent/chat/start", json={"prompt": "after restart"}).status_code == 409
        c.post("/api/agent/chat/end")
        assert _wait(lambda: not c.get("/api/agent/chat/status").get_json()["session"]["alive"])
        assert c2.post("/api/agent/chat/start", json={"prompt": "after restart"}).status_code == 200
        ok = _wait(lambda: any(e["hook_event_name"] == "Text" and e["text"] == "answer 1: after restart"
                               for e in c2.get(f"/api/agent/chat/events?after={replayed_last}").get_json()["events"]))
        app2.config["agent_chat"].get(_chip(c2)).stop(now=True)
        assert ok, "new events must carry n > the replayed maximum"

    def test_a_chat_event_names_its_own_chip(self, app):
        from quam_state_manager.web import agent_api as aa
        with app.app_context():
            assert aa._chip_for_event({"origin": "chat", "chip": "other-chip"}) == "other-chip"
            assert aa._chip_for_event({"origin": "chat", "chip": "unassigned"}) == "unassigned"

    def test_the_cursor_is_unique_under_two_recording_threads(self, app):
        """review R4-7: the request thread records the person's User event while
        the process reader thread records Init -- both allocate n at once. The
        read-modify-write is under a lock, so no two events share an n (a shared
        n hides one of them behind a page's after=)."""
        seen: list[int] = []
        lk = threading.Lock()

        def worker(k):
            with app.app_context():
                for i in range(400):
                    rec = {"ts": time.time(), "origin": "chat", "chip": "X", "hook_event_name": "Text", "text": f"{k}-{i}"}
                    chat_api._record(rec)
                    with lk:
                        seen.append(rec["n"])
        ts = [threading.Thread(target=worker, args=(k,)) for k in range(3)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        dup = len(seen) - len(set(seen))
        assert dup == 0, f"{dup} duplicate n out of {len(seen)} under three concurrent recorders"

    def test_the_cursor_clears_the_evicted_ring(self, inst):
        """review R4: a burst of hook events evicts the chat event whose n we must
        exceed from the ring, so the counter is lifted off the DAY FILE, not the
        ring -- a page holding after=<that n> still sees the next chat event."""
        (inst / "agent_events").mkdir(parents=True)
        day = inst / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        lines = [json.dumps({"ts": time.time() - 3600, "origin": "chat", "chip": "X", "n": 50,
                             "hook_event_name": "User", "text": "hi", "session_id": None})]
        lines += [json.dumps({"ts": time.time() - 3600 + i, "hook_event_name": "PreToolUse", "tool_name": "Read",
                              "session_id": "s1", "tool_use_id": f"t{i}", "summary": "x"}) for i in range(900)]
        day.write_text("\n".join(lines) + "\n", encoding="utf-8")
        app = create_app(testing=True, instance_path=str(inst))
        with app.app_context():
            rec = {"ts": time.time(), "origin": "chat", "chip": "X", "hook_event_name": "User", "text": "new", "session_id": None}
            chat_api._record(rec)
        assert rec["n"] > 50, f"next chat n={rec['n']} did not clear the evicted event's n=50"
