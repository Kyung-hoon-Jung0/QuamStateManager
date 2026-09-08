"""docs/173 S3: the Agent pill's one state, and how the page learns of it.

Precedence: waiting > limited > stalled > failed > running > between >
human-ran > idle. Liveness is the recorded pid or transcript growth, never a
fixed hour. A human's run on the OPX is a fact in the past tense. An agent
event bumps the run watcher, so every open tab's live-wake wakes; the wait
answer carries agent_seq so the pill knows whether to re-fetch.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]

from quam_state_manager.core import agent_session, story
from quam_state_manager.core.dataset import DatasetStore
from quam_state_manager.web.app import create_app
from tests.test_story import _run_folder

_H = {"Origin": "http://localhost"}


@pytest.fixture
def app(tmp_path):
    return create_app(testing=True, instance_path=str(tmp_path / "inst"))


@pytest.fixture
def client(app):
    return app.test_client()


def _ev(client, **rec):
    rec.setdefault("session_id", "s")
    rec.setdefault("ts", time.time())
    return client.post("/api/agent/event", json=rec, headers=_H)


def _now(client):
    return client.get("/api/agent/now").get_json()


class TestPrecedence:
    def test_idle_then_between_then_running(self, client):
        assert _now(client)["state"] == "idle"
        _ev(client, hook_event_name="PostToolUse", tool_name="mcp__sm__state_get", tool_use_id="a", summary="{}")
        assert _now(client)["state"] == "between"
        _ev(client, hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="b", summary="python 05_power_rabi.py --qubits q3",
            backend="claude")
        d = _now(client)
        assert d["state"] == "running" and d["running"]["node"] == "05_power_rabi" and d["running"]["backend"] == "claude"
        assert d["mode"] == "ask-writes", "a new chip's mode is ask-writes until someone flips it"

    def test_failed_outranks_between_and_running_outranks_failed(self, client):
        _ev(client, hook_event_name="PostToolUse", tool_name="Bash", tool_use_id="a", summary="python 05_power_rabi.py",
            failed=True, error="KeyError")
        d = _now(client)
        assert d["state"] == "failed" and d["failures_today"] == 1
        _ev(client, hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="b", summary="python 07_ramsey.py")
        d = _now(client)
        assert d["state"] == "running" and d["failures_today"] == 1, "the count rides along"

    def test_stalled_is_no_life_not_a_fixed_hour(self, tmp_path):
        inst = tmp_path / "inst"
        (inst / "agent_events").mkdir(parents=True)
        f = inst / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        f.write_text(json.dumps({"ts": time.time() - 20 * 60, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                 "session_id": "s", "tool_use_id": "old", "summary": "python 12_T1.py"}) + "\n",
                     encoding="utf-8")
        c = create_app(testing=True, instance_path=str(inst)).test_client()
        assert _now(c)["state"] == "stalled"
        # the same event, but the session's transcript is still growing: alive -> running
        tp = tmp_path / "t.jsonl"
        tp.write_text("x", encoding="utf-8")
        f.write_text(json.dumps({"ts": time.time() - 20 * 60, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                 "session_id": "s2", "tool_use_id": "old2", "summary": "python 12_T1.py",
                                 "transcript_path": str(tp)}) + "\n", encoding="utf-8")
        c = create_app(testing=True, instance_path=str(inst / "x")).test_client()
        c2 = create_app(testing=True, instance_path=str(inst)).test_client()
        d = _now(c2)
        assert d["state"] == "running", d

    def test_a_recorded_worker_pid_keeps_a_long_node_alive(self, client, app):
        agent_session.save(app.instance_path, _key(client), backend="codex", owner="이OO", pid=os.getpid())
        _ev(client, hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="t", summary="python 12_T1.py",
            ts=time.time() - 40 * 60)
        d = _now(client)
        assert d["state"] == "running", "a 40-minute T1 with a live pid is running, not stalled"
        assert d["session"]["owner"] == "이OO" and d["session"]["alive"] is True

    def test_waiting_and_limited_outrank_everything(self, client, app, monkeypatch):
        _ev(client, hook_event_name="PreToolUse", tool_name="Bash", tool_use_id="t", summary="python 12_T1.py")
        _ev(client, hook_event_name="PostToolUseFailure", tool_name="Bash", tool_use_id="t", summary="python 12_T1.py",
            failed=True, error="boom", limited=True, limited_until=time.time() + 3600)
        d = _now(client)
        assert d["state"] == "limited" and d["limited_resets"]
        from quam_state_manager.web import agent_api
        monkeypatch.setattr(agent_api, "_waiting_count", lambda: 2)
        d = _now(client)
        assert d["state"] == "waiting" and d["waiting"] == 2

    def test_human_ran_is_a_past_tense_fact_from_the_run_folder(self, tmp_path, app, client):
        root = tmp_path / "data"
        day = datetime.now().strftime("%Y-%m-%d")
        hms = (datetime.now() - timedelta(minutes=5)).strftime("%H:%M:%S")
        _run_folder_today(root, 7, "12_T1", hms, qubits=["q3"])
        app.config["dataset_store"] = DatasetStore(root)
        d = _now(client)
        assert d["state"] == "human-ran" and d["human_ran"]["node"] == "12_T1" and d["human_ran"]["run_id"] == 7
        # the same run, but SM ran it: no human
        story.record_agent_run(app.instance_path, {"run_id": 7, "backend": "claude", "plan_id": "p"})
        assert _now(client)["state"] == "idle"

    def test_an_old_human_run_is_not_recent(self, tmp_path, app, client):
        root = tmp_path / "data"
        hms = (datetime.now() - timedelta(hours=2)).strftime("%H:%M:%S")
        if hms > datetime.now().strftime("%H:%M:%S"):
            pytest.skip("crosses midnight")
        _run_folder_today(root, 9, "12_T1", hms, qubits=["q3"])
        app.config["dataset_store"] = DatasetStore(root)
        d = _now(client)
        assert d["state"] == "idle" and d["human_ran"] is None, "two hours ago is not 'recently'"

    def test_a_hooked_run_is_not_a_human_run(self, tmp_path, app, client):
        root = tmp_path / "data"
        when = datetime.now() - timedelta(minutes=5)
        _run_folder_today(root, 8, "12_T1", when.strftime("%H:%M:%S"), qubits=["q3"])
        app.config["dataset_store"] = DatasetStore(root)
        _ev(client, hook_event_name="PostToolUse", tool_name="Bash", tool_use_id="t", summary="python 12_T1.py",
            ts=when.timestamp() + 30, session_id="paper")     # not a relevant session by itself...
        d = _now(client)
        assert d["state"] != "human-ran", d


def _key(client):
    """The session file's key is the chip's records key, not its name (review R3-1);
    with no chip open the routes fall back to the bare 'chip'."""
    return client.get("/api/agent/chip").get_json().get("chip_key") or "chip"


def _run_folder_today(root, run_id, node, hms, *, qubits):
    from tests import test_story
    day = datetime.now().strftime("%Y-%m-%d")
    old = test_story.DAY
    test_story.DAY = day
    try:
        return _run_folder(root, run_id, node, hms, qubits=qubits)
    finally:
        test_story.DAY = old


class TestTheWake:
    def test_an_event_bumps_the_watcher_and_the_wait_answer_carries_agent_seq(self, client, app):
        from quam_state_manager.core import run_watch
        w = run_watch.RunWatcher()
        app.config["run_watcher"] = w
        seen = {}

        def waiter():
            seen["tick"] = w.wait(w.tick, 5.0)
        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.2)
        before = w.tick
        _ev(client, hook_event_name="Stop", summary="done")
        t.join(3.0)
        assert not t.is_alive() and seen["tick"] == before + 1, "the agent event woke the waiter"
        d = client.get("/datasets/wait?since=-1").get_json()
        assert d["agent_seq"] >= 1 and "tick" in d

    def test_a_limits_change_wakes_too(self, client, app):
        from quam_state_manager.core import run_watch
        w = run_watch.RunWatcher()
        app.config["run_watcher"] = w
        before = w.tick
        r = client.post("/api/agent/limits", json={"mode": "auto"}, headers={**_H, "X-SM-Actor": "박OO"})
        assert r.status_code == 200 and w.tick == before + 1


class TestSessionAndStop:
    def test_stop_is_recorded_before_anything_is_killed(self, client, app):
        assert client.post("/api/agent/session/stop", json={}, headers=_H).status_code == 409
        agent_session.save(app.instance_path, _key(client), backend="claude", owner="김OO", pid=1)
        r = client.post("/api/agent/session/stop", json={"mode": "now"}, headers={**_H, "X-SM-Actor": "박OO"})
        assert r.status_code == 200
        rec = agent_session.load(app.instance_path, _key(client))
        assert rec["agent_stop"]["mode"] == "now" and rec["agent_stop"]["who"] == "human:박OO"
        assert agent_session.stopped(rec)
        s = client.get("/api/agent/session").get_json()["session"]
        assert s["stopped"] is True and s["owner"] == "김OO"
        from quam_state_manager.core import journal
        assert "Stop (now) pressed by human:박OO" in journal.read(app.instance_path, "chip")


class TestThePillFitsANarrowWindow:
    """Customer feedback 2026-09-09 (screenshot): on a narrow window everything
    after the Agent pill was clipped and the page scrolled sideways. Measured in
    Chrome before the fix: the pill was 366 px wide and the document 1195 px
    inside an 885 px viewport. The pill gives way now -- owner, then mode, then
    the state text truncates -- and the bar itself never pushes past the window.
    The widths are CSS, so they are pinned as rules; what they DO was measured in
    a real browser at 1500 / 1280 / 1100 / 980 / 900 px."""

    def test_the_markup_carries_the_mode_chip(self, client):
        html = client.get("/").get_data(as_text=True)
        i = html.index('id="agent-pill"')
        pill = html[i:i + 700]
        for cls in ("agent-pill-dot", "agent-pill-text", "agent-pill-mode", "agent-pill-res"):
            assert cls in pill, cls
        assert pill.index("agent-pill-mode") < pill.index("agent-pill-res")

    def test_the_pill_and_the_bar_give_way(self):
        css = (_ROOT / "quam_state_manager" / "web" / "static" / "style.css").read_text(encoding="utf-8")
        i = css.index(".agent-pill { list-style: none;")
        blk = css[i:i + 2200]
        # the pill has a ceiling, truncates, and sheds its secondary parts first
        assert re.search(r"\.agent-pill-link \{[^}]*max-width: min\(20rem, 26vw\);[^}]*overflow: hidden;", blk, re.S)
        assert re.search(r"\.agent-pill-text \{[^}]*text-overflow: ellipsis;", blk)
        assert re.search(r"@media \(max-width: 1400px\) \{ \.agent-pill-res \{ display: none; \} \}", blk)
        assert re.search(r"@media \(max-width: 1150px\) \{ \.agent-pill-mode \{ display: none; \} \}", blk)
        assert re.search(r"@media \(max-width: 1000px\) \{ \.agent-pill-link \{ max-width: 11rem; \} \}", blk)
        # the left group shrinks and wraps INSIDE its row; wrapping the whole nav
        # put the tools on a third row and doubled the bar's height (measured)
        assert ".topbar > nav > ul { flex-wrap: wrap; min-width: 0; }" in blk
        assert ".topbar > nav > ul:first-child { flex: 1 1 auto; }" in blk
        assert ".topbar > nav > ul.topbar-right { flex: 0 0 auto; }" in blk
        assert ".topbar > nav { flex-wrap: wrap; }" not in css
        # the 280 px search box shrinks before the left group is pushed to a new row
        assert re.search(r"@media \(max-width: 1000px\) \{ \.topbar \.search-box \{ flex: 0 1 8rem; \} \}", blk)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_agent_pill_selfcheck():
    """The compact form is executed against the REAL agent-pill.js."""
    proc = subprocess.run(["node", str(_ROOT / "tests" / "agent_pill_selfcheck.cjs")],
                          capture_output=True, text=True, cwd=str(_ROOT), timeout=120)
    if proc.returncode == 2 and "jsdom not installed" in (proc.stderr or ""):
        pytest.skip("jsdom not installed")
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
