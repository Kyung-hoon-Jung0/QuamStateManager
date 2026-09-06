"""The Agent home, the card feed and plans (docs/173 S6).

The feed is SM's own record: a person's message and the agent's answer are
events on disk, a plan is data the agent proposed or a /run line made, its
progress is what run_node reported. Rule 0 is a route: only a person's
click starts a plan, and that click arms, snapshots and tells the agent.
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import agent_plans, agent_session, limits, scheduler
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.web import chat_api
from quam_state_manager.web.app import create_app
from tests.test_agent_runs import NODE_SRC, FakeRun, AGENT, HUMAN, _arm, _wait
from tests.test_chat_api import _FakeClaude, _FakeCodex


@pytest.fixture
def inst(tmp_path):
    return tmp_path / "_app_instance"


@pytest.fixture
def fakes(monkeypatch):
    monkeypatch.setitem(chat_api.BACKEND_CLASSES, "claude", _FakeClaude)
    monkeypatch.setitem(chat_api.BACKEND_CLASSES, "codex", _FakeCodex)
    monkeypatch.setattr(chat_api.ab, "detect", lambda exe: {"found": True, "version": "fake"})
    for k in ("FAKE_FAIL", "FAKE_LIMIT", "FAKE_CRASH", "FAKE_TOOL", "FAKE_ECHO_STDIN", "FAKE_LINGER"):
        monkeypatch.delenv(k, raising=False)


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
def cal(tmp_path):
    d = tmp_path / "calibrations"
    d.mkdir()
    (d / "05_power_rabi.py").write_text(NODE_SRC, encoding="utf-8")
    return d


@pytest.fixture
def c(app, synth_folder, cal):
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    with app.app_context():
        from quam_state_manager.web import routes as r
        scheduler.save_settings(r._sched_inst(), {"env_python": sys.executable, "calibrations_folder": str(cal),
                                                  "global_simulate": False, "default_timeout_s": 60})
    yield client
    mgr = app.config.get("agent_chat")
    if mgr:
        for s in mgr.sessions.values():
            s.stop(now=True)
    reg = app.config.get("agent_run_registry")
    if reg:
        for m in reg.runs.values():
            reg.wait(m["key"], 15)


def _chip(c):
    """The records' KEY (session / approvals / plans / limits): <name>-<path hash>."""
    return c.get("/api/agent/chip").get_json()["chip_key"]


def _name(c):
    """The journal's name (what a person reads)."""
    return c.get("/api/agent/chip").get_json()["name"]


def _journal(c, inst):
    return journal_mod.read(str(inst), _name(c), datetime.now().strftime("%Y-%m-%d")) or ""


def _feed(c, after=0):
    return c.get(f"/api/agent/chat/cards?after={after}").get_json()


# ------------------------------------------------------------------ feed

class TestFeed:
    def test_a_persons_message_and_the_answer_are_cards_on_disk(self, c, inst):
        c.post("/api/agent/chat/start", json={"prompt": "hello there agent"}, headers=HUMAN)
        assert _wait(lambda: any(k["kind"] == "answer" for k in _feed(c)["cards"]))
        d = _feed(c)
        kinds = [k["kind"] for k in d["cards"]]
        assert kinds[0] == "user" and d["cards"][0]["text"] == "hello there agent" and d["cards"][0]["who"] == "human:kyunghoon"
        ans = [k for k in d["cards"] if k["kind"] == "answer"][0]
        assert ans["text"] == "answer 1: hello there agent" and "<p>answer 1: hello there agent</p>" in ans["html"]
        assert "tool" in kinds and [k for k in d["cards"] if k["kind"] == "tool"][0]["tool"] == "mcp__sm__sm_status"
        assert d["session"]["alive"] and d["file"]["backend"] == "claude" and d["qubits"] >= 1
        assert d["now"]["state"] in ("between", "running")
        # the cursor
        assert _feed(c, after=d["last"])["cards"] == []
        # on disk (a restart replays it)
        day = inst / "agent_events" / (datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        lines = [json.loads(l) for l in day.read_text(encoding="utf-8").splitlines()]
        assert lines[0]["hook_event_name"] == "User" and lines[0]["origin"] == "chat"
        # a second message
        c.post("/api/agent/chat/send", json={"text": "and again"}, headers=HUMAN)
        assert _wait(lambda: sum(1 for k in _feed(c)["cards"] if k["kind"] == "user") == 2)

    def test_feed_without_a_chip_is_empty_but_ok(self, app):
        d = app.test_client().get("/api/agent/chat/cards").get_json()
        assert d["ok"] and d["chip"] is None and d["cards"] == [] and d["live"]["plans"] == []


# ----------------------------------------------------------------- plans

class TestPlans:
    def test_run_line_makes_a_draft_card_with_what_may_change(self, c, inst):
        r = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1 num_shots=200"}, headers=HUMAN).get_json()
        assert r["ok"], r
        p = r["plan"]
        assert p["status"] == "draft" and p["source"] == "run_cmd" and p["title"] == "/run 05_power_rabi qA1"
        assert p["steps"][0]["node"] == "05_power_rabi" and p["steps"][0]["targets"] == ["qA1"] and p["steps"][0]["params"] == {"num_shots": 200}
        assert p["may_change"], "the families' own update targets, filled per target"
        assert all(m["target"] == "qA1" and m["path"].startswith("qubits.qA1.") for m in p["may_change"])
        assert "waiting for Start" in _journal(c, inst)
        assert c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi nope"}, headers=HUMAN).status_code == 400
        assert c.post("/api/agent/plans", json={"run_line": "/run"}, headers=HUMAN).status_code == 400
        assert _feed(c)["live"]["plans"][0]["id"] == p["id"]
        assert c.get("/api/agent/chip").get_json()["plan"]["status"] == "draft"

    def test_the_agent_proposes_and_a_person_starts(self, c, inst, app):
        chip = _chip(c)
        r = c.post("/api/agent/plans", json={"title": "1Q bringup q1", "why": "the user asked",
                                              "steps": [{"node": "05_power_rabi", "targets": ["qA1"], "why": "rabi first"},
                                                        {"node": "05_power_rabi", "targets": ["qA1"], "params": {"num_shots": 400}}]}, headers=AGENT).get_json()
        assert r["ok"] and "nothing runs until a person presses Start" in r["how"]
        pid = r["plan"]["id"]
        assert "plan `1Q bringup q1` proposed (2 step(s))" in _journal(c, inst)
        assert c.post("/api/agent/plans", json={"title": "x", "steps": []}, headers=AGENT).status_code == 400
        assert c.post("/api/agent/plans", json={"title": "x", "steps": [{"node": "n", "targets": ["zz"]}]}, headers=AGENT).status_code == 400
        # the agent cannot start it
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=AGENT).status_code == 403
        # mode on the card
        assert c.post(f"/api/agent/plans/{pid}/mode", json={"mode": "auto"}, headers=HUMAN).get_json()["plan"]["mode"] == "auto"
        assert c.post(f"/api/agent/plans/{pid}/mode", json={"mode": "nope"}, headers=HUMAN).status_code == 400
        # THE click
        d = c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()
        assert d["ok"] and d["session_started"] is True and d["pre_ts"]
        p = d["plan"]
        assert p["status"] == "running" and p["started_by"] == "human:kyunghoon" and p["mode"] == "auto"
        rec = agent_session.load(str(inst), chip)
        assert rec["start_token"] and rec["plan_id"] == pid and rec["mode"] == "auto", "Start arms; the session carries the plan's mode"
        assert limits.load(str(inst), chip)["mode"] == "ask-writes", "review R1-M4: the chip's default never changes"
        assert "STARTED by human:kyunghoon (mode auto" in _journal(c, inst)
        # the snapshot the plan can be reverted to, labelled
        with app.app_context():
            from quam_state_manager.web import routes as rt
            snaps = rt._history().list_snapshots(rt._active_path())
        labels = [getattr(s, "label", None) or (s.get("label") if isinstance(s, dict) else None) for s in snaps]
        assert any(l and l.startswith("before plan 1Q bringup") for l in labels), labels
        # the agent got the plan as its first message (the fake echoes the first 20 chars)
        assert _wait(lambda: any(k["kind"] == "answer" and k["text"].startswith("answer 1: The human (") for k in _feed(c)["cards"]))
        assert any(k["kind"] == "user" and k["text"].startswith("[Start] plan") for k in _feed(c)["cards"])
        # not twice
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).status_code == 409

    def test_run_node_reports_into_the_plan_card(self, c, inst, monkeypatch):
        fr = FakeRun()
        monkeypatch.setattr(scheduler, "_run_item", fr)
        chip = _chip(c)
        r = c.post("/api/agent/plans", json={"title": "p", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]}, headers=AGENT).get_json()
        pid = r["plan"]["id"]
        c.post(f"/api/agent/plans/{pid}/mode", json={"mode": "auto"}, headers=HUMAN)
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        body = {"node": "05_power_rabi", "targets": ["qA1"], "reason": "step 0", "wait_s": 20, "plan_id": pid, "step": 0}
        res = c.post("/api/agent/run-node", json=body, headers=AGENT).get_json()
        assert res["ok"] and res["result"]["applied"] is True
        p = c.get(f"/api/agent/plans/{pid}").get_json()["plan"]
        st = p["steps"][0]
        assert st["status"] == "done" and st["run_key"] == res["key"] and st["n_writes"] == 1 and st["applied"] is True
        assert p["status"] == "done" and p["counts"]["done"] == 1 and p["counts"]["applied"] == 1 and p["ended"]
        assert c.get("/api/agent/chip").get_json()["plan"]["counts"]["done"] == 1
        feed = _feed(c)
        assert feed["live"]["runs"][-1]["key"] == res["key"] and feed["live"]["plans"][-1]["status"] == "done"

    def test_cancel_and_stop_leave_the_record(self, c, inst):
        r = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1"}, headers=HUMAN).get_json()
        pid = r["plan"]["id"]
        d = c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN).get_json()
        assert d["plan"]["status"] == "cancelled" and d["plan"]["steps"][0]["status"] == "cancelled"
        assert "cancelled by human:kyunghoon" in _journal(c, inst)
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).status_code == 409
        assert c.get("/api/agent/plans/nope").status_code == 404
        # a running plan closes on Stop: "Stopped by <who>", pending steps cancelled
        r = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1"}, headers=HUMAN).get_json()
        pid2 = r["plan"]["id"]
        assert c.post(f"/api/agent/plans/{pid2}/start", json={}, headers=HUMAN).get_json()["ok"]
        assert c.post("/api/agent/session/stop", json={"mode": "now"}, headers=HUMAN).get_json()["ok"]
        p = c.get(f"/api/agent/plans/{pid2}").get_json()["plan"]
        assert p["status"] == "stopped" and p["ended_by"] == "human:kyunghoon" and p["note"] == "stop now"
        assert p["steps"][0]["status"] == "cancelled"
        assert c.get("/api/agent/chip").get_json()["plan"]["status"] == "stopped"

    def test_parse_run_line(self):
        assert agent_plans.parse_run_line("/run 05_power_rabi q1 q2 num_shots=200 simulate=true amp=0.5 name=x") == \
            {"node": "05_power_rabi", "targets": ["q1", "q2"], "params": {"num_shots": 200, "simulate": True, "amp": 0.5, "name": "x"}}
        assert agent_plans.parse_run_line("/run 02_res q1,q2")["targets"] == ["q1", "q2"]
        assert agent_plans.parse_run_line("hello") is None
        assert "usage" in agent_plans.parse_run_line("/run")["error"]

    def test_plan_model(self, tmp_path):
        rec = agent_plans.add(tmp_path, "PJ", title="t", steps=[{"node": "a", "targets": ["q1"]}, {"node": "b", "targets": ["q1"]}],
                              mode="auto", created_by="by_claude")
        assert rec["status"] == "draft" and agent_plans.counts(rec)["pending"] == 2
        agent_plans.update(tmp_path, "PJ", rec["id"], status="running")
        assert agent_plans.step_for(agent_plans.get(tmp_path, "PJ", rec["id"]), step=None, node="b", targets=["q1"])["i"] == 1
        agent_plans.step_update(tmp_path, "PJ", rec["id"], 0, status="done", n_writes=2, applied=True)
        assert agent_plans.get(tmp_path, "PJ", rec["id"])["status"] == "running", "one step left"
        agent_plans.step_update(tmp_path, "PJ", rec["id"], 1, status="failed")
        p = agent_plans.get(tmp_path, "PJ", rec["id"])
        assert p["status"] == "failed" and p["summary"]["failed"] == 1 and p["ended"]
        assert agent_plans.running(tmp_path, "PJ") is None
        with pytest.raises(ValueError):
            agent_plans.normalize_steps([{"targets": ["q1"]}])
        with pytest.raises(ValueError):
            agent_plans.normalize_steps([{"node": "n"}] * (agent_plans.MAX_STEPS + 1))


# ------------------------------------------------------------------ home

class TestHome:
    def test_home_is_the_agent_home_when_a_chip_is_open(self, c):
        html = c.get("/").get_data(as_text=True)
        assert 'id="agent-home"' in html and 'id="agent-popover"' in html and 'class="sidebar-tool agent-btn"' in html
        assert "agent.js" in html and '"label": "Agent home"' in html

    def test_home_without_a_chip_is_the_landing_with_the_current_project_button(self, app, monkeypatch):
        from quam_state_manager.core import qualibrate_config
        monkeypatch.setattr(qualibrate_config, "tray_status", lambda: {"config_exists": True, "active": "PJ_10082026",
                                                                       "state_raw": None, "state_native": None, "state_exists": False})
        html = app.test_client().get("/").get_data(as_text=True)
        assert 'id="agent-home"' not in html and 'class="landing-projects"' in html
        assert "Open QUAlibrate's current project: PJ_10082026" in html and 'name="project" value="PJ_10082026"' in html
        assert 'id="agent-popover"' in html, "the floating panel is on every page"

    def test_the_floats_home_link_is_a_plain_navigation(self, c):
        """review R2-8: the Agent home is a full page, so the float's 'home' is a
        plain href -- an hx-get into #table-pane put the landing INSIDE a pane."""
        import re
        html = c.get("/").get_data(as_text=True)
        m = re.search(r'<a class="agent-home-link"[^>]*>', html)
        assert m and 'href="/"' in m.group(0) and "hx-get" not in m.group(0) and "hx-target" not in m.group(0)

    def test_the_setup_page_is_wired(self, c):
        """docs/173 S7: /agent/setup renders the shell inside the shell page with
        its own bundle; the HX form is the partial alone."""
        html = c.get("/agent/setup").get_data(as_text=True)
        assert 'id="agent-setup"' in html and "agent-setup.js" in html and 'id="as-body"' in html
        assert '"agent_setup": ["agent-setup.js"]' in html and '"agent_setup": ["agent_setup"]' in html, "bundle + page table"
        part = c.get("/agent/setup", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert 'id="agent-setup"' in part and "<html" not in part


    def test_bridge_knows_the_plan_tools(self):
        from quam_state_manager import mcp
        assert "plan_propose" in mcp.TOOLS and "plan_status" in mcp.TOOLS
        assert "plan_propose" in mcp.WRITE_TOOLS and "plan_status" not in mcp.WRITE_TOOLS
        props = mcp.TOOLS["run_node"][0]["inputSchema"]["properties"]
        assert "step" in props and "plan_id" in props
