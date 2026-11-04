"""The Agent surfaces, as a person meets them on a real chip (v1.7.0 GUI walk).

Each class pins one defect the walk found:
  * a run uses the env the person chose for the project; a refused step is said on the plan card,
    and a refusal no retry can clear ends the plan;
  * an approval never writes a value from a target the node itself marked failed unless a person
    ticks it;
  * the human_active wait honours a saved 0 and has a person's release;
  * the strip does not say "thinking" while the agent waits for the person;
  * "Finish setup (N left)" counts only the steps the in-app agent needs;
  * an edit refused during an agent run reads as a sentence with the way out;
  * the agent can end its own plan, and a refused Start names the plan in the way;
  * Reject is an in-page sheet, never a native prompt().
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from quam_state_manager.core import agent_overnight, agent_plans, agent_session, approvals, project_env, scheduler
from quam_state_manager.web import agent_api as aa
from tests.test_agent_runs import (  # noqa: F401  (fixtures)
    AGENT, HUMAN, _arm, _chip, _run, app, c, cal, fake_run, inst, synth_folder,
)

STATIC = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "static"


def _settings(c, **kw):
    with c.application.app_context():
        from quam_state_manager.web import routes as r
        scheduler.save_settings(r._sched_inst(), kw)


class TestTheRunUsesTheProjectEnv:
    def test_no_runner_env_falls_back_to_the_projects_chosen_env(self, inst):
        inst.mkdir(parents=True, exist_ok=True)
        project_env.remember(str(inst), "proj", sys.executable)
        got = aa._run_settings({"env_python": "", "calibrations_folder": "x"}, {"qualibrate_project": "proj"}, str(inst))
        assert got["env_python"] == sys.executable
        assert got["env_source"] == "project"

    def test_a_runner_env_of_its_own_wins(self, inst):
        inst.mkdir(parents=True, exist_ok=True)
        project_env.remember(str(inst), "proj", sys.executable)
        got = aa._run_settings({"env_python": "C:/own/python.exe"}, {"qualibrate_project": "proj"}, str(inst))
        assert got["env_python"] == "C:/own/python.exe"

    def test_the_queue_item_carries_the_env_the_gate_resolved(self, tmp_path):
        item = scheduler._new_item({"file": "x.py", "name": "x", "kind": "node", "env_python": "py.exe"}, ["q1"])
        assert item["env_python"] == "py.exe"
        assert scheduler._new_item({"file": "x.py", "name": "x", "kind": "node"}, ["q1"])["env_python"] is None

    def test_the_no_env_remedy_names_no_hidden_page(self):
        from quam_state_manager.core import agent_runs
        how = agent_runs.check_gates(agent_runs.RunRequest(node="n", targets=["q"], params={}, reason="r"),
                                     session=None, lim={}, settings={}, pending=[], human=None, queue_state={},
                                     own_running=False, run_active=None, node_info=None, available=[])["how"]
        assert "Experiment Runner" not in how and "landing" in how


class TestARefusedStepIsOnTheCard:
    def test_no_env_ends_the_plan_and_says_why(self, c, inst, fake_run):
        _arm(c, steps=2)
        _settings(c, env_python="")
        r = _run(c)
        assert r.status_code == 409 and r.get_json()["refused"] == "no_env"
        pid = [p for p in agent_plans.load(str(inst), _chip(c))][-1]["id"]
        rec = agent_plans.get(str(inst), _chip(c), pid)
        assert rec["status"] == "stopped", "a refusal no retry can clear ends the card"
        assert rec["end_code"] == "no_env" and rec["refusal"]["code"] == "no_env"

    def test_a_transient_refusal_keeps_the_plan_and_shows_it(self, c, inst, fake_run, monkeypatch):
        _arm(c, steps=2)
        monkeypatch.setattr(aa, "_human_ran_recently", lambda *a, **k: {"ts": time.time() - 5, "node": "x"})
        r = _run(c)
        assert r.get_json()["refused"] == "human_active"
        rec = agent_plans.load(str(inst), _chip(c))[-1]
        assert rec["status"] == "running" and rec["refusal"]["code"] == "human_active"


class TestFailedTargetWrites:
    RUN = {"outcomes": {"qA1": "failed", "qA2": "successful"}}

    def test_writes_from_a_failed_target_are_marked(self):
        w = agent_overnight.mark_failed_writes(self.RUN, [
            {"path": "qubits.qA1.f_01", "old": 1, "new": 2}, {"path": "qubits.qA2.f_01", "old": 1, "new": 2}])
        assert w[0]["failed_target"] == "qA1" and w[0]["failed_outcome"] == "failed"
        assert "failed_target" not in w[1]

    def _held(self, c, inst, writes):
        return approvals.add(str(inst), _chip(c), kind="writes", node="05_power_rabi", targets=["qA1", "qA2"],
                             writes=writes, reason="r", why_held="mode ask-writes", actor="by_claude")

    def test_approve_without_a_list_never_writes_a_failed_value(self, c, inst):
        f01 = c.get("/api/agent/state?path=qubits.qA1.f_01").get_json()
        old = f01.get("value", f01.get("raw"))
        ap = self._held(c, inst, [
            {"path": "qubits.qA1.f_01", "old": old, "new": old + 3e7, "failed_target": "qA1", "failed_outcome": "failed"},
            {"path": "qubits.qA1.T1", "old": None, "new": 4.2e-5}])
        r = c.post(f"/api/agent/approvals/{ap['id']}/approve", json={}, headers=HUMAN)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["stage"]["staged"] == 1, "only the ticked-by-default value is written"

    def test_a_card_of_only_failed_values_is_not_written_by_a_bare_press(self, c, inst):
        ap = self._held(c, inst, [{"path": "qubits.qA1.f_01", "old": 1, "new": 2, "failed_target": "qA1"}])
        r = c.post(f"/api/agent/approvals/{ap['id']}/approve", json={}, headers=HUMAN)
        assert r.status_code == 409 and "failed" in r.get_json()["error"]
        assert approvals.get(str(inst), _chip(c), ap["id"])["status"] == "pending"

    def test_the_card_does_not_tick_a_failed_value(self):
        src = (STATIC / "agent.js").read_text(encoding="utf-8")
        assert '(failed ? "" : " checked")' in src
        assert "if (on ? !on.checked : !!w.failed_target) return;" in src


class TestHumanActive:
    def test_a_saved_zero_is_zero(self):
        assert aa._human_window_min({"human_recent_min": 0}) == 0.0
        assert aa._human_window_min({}) == 30.0

    def test_a_person_releases_the_wait(self, c, inst, fake_run, monkeypatch):
        _arm(c, steps=2)
        monkeypatch.setattr(aa, "_human_ran_recently", lambda *a, **k: {"ts": time.time() - 5, "node": "x"})
        assert _run(c).get_json()["refused"] == "human_active"
        assert c.post("/api/agent/session/release-human", json={}, headers=AGENT).status_code == 403
        assert c.post("/api/agent/session/release-human", json={}, headers=HUMAN).status_code == 200
        r = _run(c)
        assert r.status_code == 200, r.get_json()


class TestEditRefusalIsASentence:
    def test_the_lock_answer_is_readable(self):
        from quam_state_manager.web import routes as r
        txt = r._agent_running_text({"node": "05_power_rabi"})
        assert "agent_running" not in txt and "Stop" in txt and "05_power_rabi" in txt


class TestPlanEnd:
    def test_the_agent_ends_its_plan(self, c, inst):
        _arm(c, steps=2)
        pid = agent_plans.load(str(inst), _chip(c))[-1]["id"]
        assert c.post(f"/api/agent/plans/{pid}/end", json={"why": "x"}, headers=HUMAN).status_code == 403
        assert c.post(f"/api/agent/plans/{pid}/end", json={}, headers=AGENT).status_code == 400
        r = c.post(f"/api/agent/plans/{pid}/end", json={"why": "the fit failed twice"}, headers=AGENT)
        assert r.status_code == 200 and r.get_json()["plan"]["status"] == "stopped"
        assert agent_plans.running(str(inst), _chip(c)) is None

    def test_the_mcp_bridge_offers_it(self):
        from quam_state_manager import mcp
        assert "plan_end" in mcp.TOOLS and "plan_end" in mcp.WRITE_TOOLS

    def test_a_refused_start_names_the_plan_in_the_way(self, c, inst):
        _arm(c, steps=2)
        first = agent_plans.load(str(inst), _chip(c))[-1]
        pid = c.post("/api/agent/plans", json={"title": "second", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]},
                     headers=AGENT).get_json()["plan"]["id"]
        r = c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN)
        assert r.status_code == 409
        assert r.get_json()["running_plan"]["id"] == first["id"]

    def test_the_card_shows_the_refused_start(self):
        src = (STATIC / "agent.js").read_text(encoding="utf-8")
        assert "S.startErr[id] = {" in src and "ui-agent-said" in src


class TestSurfaces:
    def test_reject_is_not_a_native_prompt(self):
        src = (STATIC / "agent.js").read_text(encoding="utf-8")
        body = src[src.index("  function reject(id)"):src.index("  function stop(mode)")]
        assert "prompt(" not in body and "ui-agent-reject-note" in src

    def test_the_pill_says_your_turn(self):
        src = (STATIC / "agent-pill.js").read_text(encoding="utf-8")
        assert re.search(r'st === "your-turn"\) \{ text = "Agent: waiting for you"', src)

    def test_a_turn_ended_session_is_not_thinking(self):
        assert aa._turn_ended([{"hook_event_name": "PreToolUse"}, {"hook_event_name": "Stop"}])
        assert not aa._turn_ended([{"hook_event_name": "Stop"}, {"hook_event_name": "PreToolUse"}])

    def test_setup_counts_only_what_the_in_app_agent_needs(self, c):
        s = c.get("/api/agent/setup").get_json()
        assert set(s["todo_required"]) | set(s["todo_optional"]) == set(s["todo"])
        assert not set(s["todo_required"]) & {"connect_claude", "connect_codex", "allow"}
        src = (STATIC / "agent.js").read_text(encoding="utf-8")
        assert "su.todo_required || su.todo" in src and "WIRE_FRESH_MS" in src


class TestOnlyThePlansDriverEndsIt:
    """One plan, one driver (docs/253): another agent neither ends the plan by its
    door nor by a refusal of its own run_node."""
    OTHER = {"X-SM-Agent": "codex"}

    def _pid(self, c, inst):
        _arm(c, steps=2)
        return agent_plans.load(str(inst), _chip(c))[-1]["id"]

    def test_another_agent_cannot_end_the_plan(self, c, inst):
        pid = self._pid(c, inst)
        r = c.post(f"/api/agent/plans/{pid}/end", json={"why": "not mine"}, headers=self.OTHER)
        assert r.status_code == 403, r.get_json()
        assert agent_plans.get(str(inst), _chip(c), pid)["status"] == "running"
        r = c.post(f"/api/agent/plans/{pid}/end", json={"why": "the fit failed"}, headers=AGENT)
        assert r.status_code == 200 and r.get_json()["plan"]["status"] == "stopped"

    def _refuse(self, app, inst, chip, plan, driver, sess):
        class Req:
            step = 0
        with app.test_request_context("/"):
            aa._plan_refused(str(inst), chip, "chip", plan, Req(), {"refused": "not_a_node", "how": "x"},
                             driver=driver, sess=sess)

    def test_another_agents_refusal_does_not_end_the_plan(self, app, c, inst):
        assert "not_a_node" in aa._PLAN_ENDING_REFUSALS
        pid = self._pid(c, inst)
        chip = _chip(c)
        sess = agent_session.load(str(inst), chip)
        own = dict(sess["grant"]["driver"])
        other = dict(own, id="someone-else", actor="by_codex")
        assert not aa._drives_plan(sess, pid, other)
        self._refuse(app, inst, chip, agent_plans.get(str(inst), chip, pid), other, sess)
        assert agent_plans.get(str(inst), chip, pid)["status"] == "running"
        self._refuse(app, inst, chip, agent_plans.get(str(inst), chip, pid), own, sess)
        assert agent_plans.get(str(inst), chip, pid)["status"] == "stopped"
