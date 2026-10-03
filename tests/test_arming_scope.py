"""Arming is a scoped grant (docs/253): it belongs to ONE plan, is driven by
ONE agent, and it ends -- with a journal line -- when that plan finishes,
fails or is cancelled, at End session, on an SM restart, and when the
driving agent's process is gone.

Findings pinned here (agent validation campaign, 2026-10-03):
D-06 / C-20 (an open-ended arm), C-05 (a blank Arm; a terminal run filed
under a dead in-app session), D-09 (two agents drive one plan), D-16
(``stop_by`` read against today only), C-22 (the strip's doors).

Everything runs through the shipped routes; the agent CLI is the fake of
tests/test_chat_api.py and the node subprocess is the FakeRun of
tests/test_agent_runs.py.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import agent_plans, agent_session, limits, scheduler
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.web import chat_api
from quam_state_manager.web.app import create_app
from tests.test_agent_runs import NODE_SRC, FakeRun, HUMAN, _wait
from tests.test_chat_api import _FakeClaude, _FakeCodex

TERM = {"X-SM-Agent": "claude", "X-SM-Session": "t-terminal01"}


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


def _setup(client, app, cal):
    with app.app_context():
        from quam_state_manager.web import routes as r
        scheduler.save_settings(r._sched_inst(), {"env_python": sys.executable, "calibrations_folder": str(cal),
                                                  "global_simulate": False, "default_timeout_s": 60})


def _teardown(app):
    mgr = app.config.get("agent_chat")
    if mgr:
        for s in mgr.sessions.values():
            s.stop(now=True)
    reg = app.config.get("agent_run_registry")
    if reg:
        for m in reg.runs.values():
            reg.wait(m["key"], 15)


@pytest.fixture
def c(app, synth_folder, cal):
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    _setup(client, app, cal)
    yield client
    _teardown(app)


@pytest.fixture
def fake_run(monkeypatch):
    fr = FakeRun()
    monkeypatch.setattr(scheduler, "_run_item", fr)
    return fr


def _key(c):
    return c.get("/api/agent/chip").get_json()["chip_key"]


def _name(c):
    return c.get("/api/agent/chip").get_json()["name"]


def _journal(c, inst):
    return journal_mod.read(str(inst), _name(c), datetime.now().strftime("%Y-%m-%d")) or ""


def _file(c):
    """The session as every reader sees it (the strip, the pill, an agent)."""
    return c.get("/api/agent/chat/status").get_json()["file"] or {}


def _armed(c) -> bool:
    return bool(_file(c).get("armed"))


def _in_app(c, inst):
    """The headers of SM's own in-app session's bridge (its SM_SESSION)."""
    rec = agent_session.load(str(inst), _key(c)) or {}
    h = {"X-SM-Agent": "claude"}
    if rec.get("app_session"):
        h["X-SM-Session"] = rec["app_session"]
    return h


def _run(c, headers, **kw):
    body = {"node": "05_power_rabi", "targets": ["qA1"], "reason": "a pin", "wait_s": 20}
    body.update(kw)
    return c.post("/api/agent/run-node", json=body, headers=headers)


def _run_plan(c, line="/run 05_power_rabi qA1"):
    """A person's /run line and its Start: the in-app session drives."""
    pid = c.post("/api/agent/plans", json={"run_line": line}, headers=HUMAN).get_json()["plan"]["id"]
    d = c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()
    assert d["ok"], d
    return pid


def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


# ------------------------------------------------------------- D-06 / C-20

class TestTheGrantEndsWithThePlan:
    def test_an_unplanned_node_after_the_plan_finished_is_refused(self, c, inst, fake_run):
        """D-06: one Start used to arm the session open-ended -- an unplanned
        node ran after the plan ended."""
        pid = _run_plan(c)
        assert _armed(c)
        k, h = _key(c), _in_app(c, inst)
        r = _run(c, h, plan_id=pid, step=0).get_json()
        assert r["ok"] and r["result"]["status"] == "done", r
        rec = agent_session.load(str(inst), k)          # the FILE, before any route reads (a read reconciles)
        assert not rec.get("start_token") and rec["last_grant"]["plan_id"] == pid, \
            "the plan's last step ended it -- on the record at once, not when someone next looks"
        assert _wait(lambda: c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["status"] == "done")
        assert not _armed(c), "the plan finished: the grant ended with it"
        assert "disarmed" in _journal(c, inst) and "finished" in _journal(c, inst)
        r = _run(c, _in_app(c, inst)).get_json()
        assert r.get("refused") == "no_start_token", r
        assert len(fake_run.calls) == 1, "nothing ran after the plan"

    def test_a_failed_plan_disarms_with_a_journal_line(self, c, inst, monkeypatch):
        """C-20: the session stayed armed after its plan FAILED."""
        monkeypatch.setattr(scheduler, "_run_item", FakeRun(fail="boom"))
        pid = _run_plan(c)
        r = _run(c, _in_app(c, inst), plan_id=pid, step=0).get_json()
        assert r["ok"] and r["result"]["status"] == "failed", r
        assert c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["status"] == "failed"
        assert not _armed(c)
        j = _journal(c, inst)
        assert "disarmed" in j and "failed" in j.split("disarmed", 1)[1].splitlines()[0], j

    def test_a_node_outside_the_plan_is_refused_under_its_plan_id(self, c, inst, cal, fake_run):
        """D-06: plan_id accepted nodes that are not in the plan.

        Each request is WELL-FORMED and only off the plan: a malformed one (a pair
        on a qubit node) is refused 400 by the request check before any arming gate
        (docs/254), which is not what this pin is about."""
        (cal / "06_ramsey.py").write_text(NODE_SRC.replace("05_power_rabi", "06_ramsey"), encoding="utf-8")
        pid = _run_plan(c, "/run 05_power_rabi qA1 num_shots=200")
        h = _in_app(c, inst)
        for kw in ({"node": "06_ramsey", "params": {"num_shots": 200}}, {"params": {"num_shots": 9999}}, {"params": {}}):
            r = _run(c, h, plan_id=pid, step=0, **kw)
            body = r.get_json()
            assert r.status_code == 409 and body["refused"] == "not_in_plan", (kw, body)
            assert body["pending"][0]["params"] == {"num_shots": 200}, "the refusal names what the card armed"
        assert fake_run.calls == []
        assert c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["steps"][0]["status"] == "pending"
        r = _run(c, h, plan_id=pid, step=0, params={"num_shots": 200}).get_json()
        assert r["ok"] and r["result"]["status"] == "done", r

    def test_the_same_run_is_compared_the_way_an_approval_compares_it(self):
        """Targets are a set; a bool is not a number, a string is not a number, 100.0 is 100."""
        from quam_state_manager.core.agent_grant import same_terms
        assert same_terms(["qA1", "qA2"], {"n": 200}, ["qA2", "qA1"], {"n": 200.0})
        assert not same_terms(["qA1"], {"flag": True}, ["qA1"], {"flag": 1}), "True is not 1"
        assert not same_terms(["qA1"], {"n": "100"}, ["qA1"], {"n": 100})
        assert not same_terms(["qA1"], {"n": 100}, ["qA1"], {})
        assert not same_terms(["qA1"], {}, ["qA1", "qA2"], {})

    def test_without_a_step_index_the_matching_step_is_the_one_that_moves(self, c, inst, fake_run):
        """Two steps with the same node and targets but different params: the run
        reports into the step whose params it carried, never the first one."""
        pid = c.post("/api/agent/plans", json={"title": "two", "steps": [
            {"node": "05_power_rabi", "targets": ["qA1"]},
            {"node": "05_power_rabi", "targets": ["qA1"], "params": {"num_shots": 400}}]}, headers=TERM).get_json()["plan"]["id"]
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        r = _run(c, TERM, params={"num_shots": 400})
        assert r.status_code == 409 and r.get_json()["armed_plan"] == pid, \
            "armed for ONE plan: a run naming no plan is refused, told which plan is armed"
        r = _run(c, TERM, plan_id=pid, params={"num_shots": 400}).get_json()
        assert r["ok"], r
        steps = c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["steps"]
        assert steps[0]["status"] == "pending" and steps[1]["status"] == "done", steps
        assert _armed(c), "one step still pending: the grant holds"

    def test_a_step_index_names_the_step_even_when_it_is_0(self, c, inst, fake_run):
        """Two identical steps: ``step`` says which one runs. ``step=0`` used to read as "no step"
        (``0 or ""``), so the gate and the card fell back to "the first look-alike"."""
        pid = c.post("/api/agent/plans", json={"title": "twice", "steps": [
            {"node": "05_power_rabi", "targets": ["qA1"]}, {"node": "05_power_rabi", "targets": ["qA1"]}]},
            headers=TERM).get_json()["plan"]["id"]
        c.post(f"/api/agent/plans/{pid}/mode", json={"mode": "auto"}, headers=HUMAN)   # no held writes stop the chain
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        assert _run(c, TERM, plan_id=pid, step=0).get_json()["ok"]
        steps = c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["steps"]
        assert [s["status"] for s in steps] == ["done", "pending"], steps
        r = _run(c, TERM, plan_id=pid, step=0)
        assert r.status_code == 409 and r.get_json()["refused"] == "not_in_plan", \
            "step 0 is done: naming it again is refused, not run as step 1"
        assert _run(c, TERM, plan_id=pid, step=1).get_json()["ok"]
        assert c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["status"] == "done" and not _armed(c)

    def test_cancel_disarms_in_one_line(self, c, inst):
        pid = _run_plan(c)
        assert c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN).get_json()["plan"]["status"] == "cancelled"
        assert not _armed(c)
        j = _journal(c, inst)
        line = next(ln for ln in j.splitlines() if "cancelled by human:kyunghoon" in ln)
        assert "disarmed" in line, "the cancel line says the arming ended (one press, one line)"


class TestTheGrantEndsWithTheSession:
    def test_end_session_disarms_and_closes_the_plan(self, c, inst):
        """D-06: arming survived End session."""
        pid = _run_plan(c)
        assert _armed(c)
        assert c.post("/api/agent/chat/end", json={}, headers=HUMAN).get_json()["ok"]
        assert not _armed(c)
        p = c.get(f"/api/agent/plans/{pid}").get_json()["plan"]
        assert p["status"] in ("stopped", "stopping") and p["steps"][0]["status"] == "cancelled", p
        j = _journal(c, inst)
        line = next(ln for ln in j.splitlines() if "session ended by human:kyunghoon" in ln)
        assert "disarmed" in line, line
        assert _run(c, _in_app(c, inst), plan_id=pid, step=0).get_json()["refused"] == "no_start_token"

    def test_a_restart_disarms(self, c, inst, app, synth_folder, cal, monkeypatch):
        """D-06 / A-14: arming survived an SM restart (a new process)."""
        from quam_state_manager.core import agent_grant
        pid = _run_plan(c)
        assert _armed(c)
        k = _key(c)
        _teardown(app)
        monkeypatch.setattr(agent_grant, "BOOT", "another-boot")     # the process that armed it is gone
        app2 = create_app(testing=True, instance_path=str(inst))
        rec = agent_session.load(str(inst), k)
        assert not rec.get("start_token") and rec["last_grant"]["why"] == "SM restarted", \
            "the new SM ends it at start -- before anyone opens the chip"
        c2 = app2.test_client()
        c2.post("/load", data={"folder": str(synth_folder)})
        assert not _armed(c2)
        p = c2.get(f"/api/agent/plans/{pid}").get_json()["plan"]
        assert p["status"] == "stopped" and "SM restarted" in (p.get("note") or ""), p
        assert "disarmed: SM restarted" in _journal(c2, inst)
        assert _run(c2, {"X-SM-Agent": "claude"}, plan_id=pid, step=0).get_json()["refused"] == "no_start_token"

    def test_the_in_app_sessions_exit_disarms(self, c, inst, app):
        """SM's in-app agent drives the plan and its process dies (a crash, a kill) without End
        session: the grant ends on the next read, and the plan closes."""
        pid = _run_plan(c)
        assert _armed(c)
        cur = app.config["agent_chat"].get(_key(c))
        cur.stop(now=True)
        assert _wait(lambda: not cur.alive())
        assert not _armed(c)
        j = _journal(c, inst)
        assert "disarmed: SM's in-app claude session that drove plan" in j and "is gone" in j, j
        assert c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["status"] == "stopped"

    def test_a_plan_closed_by_another_hand_disarms(self, c, inst):
        """Whatever closes the plan -- here a writer that is not the route (docs/249's restart
        path, another window) -- its arming does not outlive it."""
        pid = _run_plan(c)
        agent_plans.update(str(inst), _key(c), pid, status="failed")
        assert not _armed(c)
        assert "disarmed: plan `/run 05_power_rabi qA1` failed" in _journal(c, inst)

    def test_stop_says_it_disarmed_in_its_one_line(self, c, inst):
        _run_plan(c)
        assert c.post("/api/agent/session/stop", json={"mode": "now"}, headers=HUMAN).get_json()["ok"]
        line = next(ln for ln in _journal(c, inst).splitlines() if "Stop (now) pressed by human:kyunghoon" in ln)
        assert "disarmed (plan `/run 05_power_rabi qA1`)" in line, line
        assert not _armed(c)

    def test_the_driving_processs_exit_disarms(self, c, inst):
        """A terminal agent's bridge that is gone drives nothing."""
        dead = _dead_pid()
        h = {**TERM, "X-SM-Bridge-Pid": str(os.getpid())}
        pid = c.post("/api/agent/plans", json={"title": "t", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]},
                     headers=h).get_json()["plan"]["id"]
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        assert _armed(c)
        k = _key(c)
        rec = agent_session.load(str(inst), k)
        g = dict(rec["grant"])
        g["driver"] = dict(g["driver"], pid=dead)
        agent_session.save(str(inst), k, grant=g)
        assert not _armed(c), "the strip's next read notices the driver is gone"
        assert "disarmed" in _journal(c, inst) and f"PID {dead}" in _journal(c, inst)


# ------------------------------------------------------------------- C-05

class TestNoBlankArm:
    def test_a_stale_session_files_arm_arms_nothing(self, c, inst):
        """C-05: Arm on a stale session file armed ANY agent, no confirm."""
        agent_session.save(str(inst), _key(c), backend="claude", owner="human:old", pid=_dead_pid(),
                           session_id="dead-in-app-session", window="chat")
        r = c.post("/api/agent/session/arm", json={}, headers=HUMAN)
        assert r.status_code == 409 and r.get_json().get("refused") == "arm_is_per_plan", r.get_json()
        assert not _armed(c)
        assert _run(c, TERM).get_json()["refused"] == "no_start_token"

    def test_a_token_from_before_is_withdrawn(self, c, inst):
        """An old SM's blank arm (a token with no plan behind it) is ended on the first read."""
        agent_session.save(str(inst), _key(c), start_token="legacy00", armed_by="human:old")
        assert not _armed(c)
        assert "disarmed" in _journal(c, inst)

    def test_a_terminal_run_is_never_filed_under_the_in_app_sessions_id(self, c, inst, fake_run):
        """C-05: a terminal `claude` run was recorded under a dead in-app
        session's id and looked identical to an in-app run."""
        agent_session.save(str(inst), _key(c), backend="claude", owner="human:old", session_id="dead-in-app-session",
                           window="chat")
        pid = c.post("/api/agent/plans", json={"title": "t", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]},
                     headers=TERM).get_json()["plan"]
        assert pid.get("session_id") != "dead-in-app-session"
        assert c.post(f"/api/agent/plans/{pid['id']}/start", json={}, headers=HUMAN).get_json()["ok"]
        r = _run(c, TERM, plan_id=pid["id"], step=0).get_json()
        assert r["ok"], r
        assert r["driver"]["kind"] == "terminal" and r["driver"]["actor"] == "by_claude", r
        meta = json.loads((Path(str(inst)) / "agent_runs" / r["key"] / "meta.json").read_text(encoding="utf-8"))
        assert meta["session_id"] != "dead-in-app-session" and meta["driver"]["kind"] == "terminal"

    def test_the_session_door_reads_the_open_chips_record(self, c, inst):
        """P3: GET /api/agent/session looked the record up by NAME and said null."""
        agent_session.save(str(inst), _key(c), backend="claude", owner="human:k")
        assert c.get("/api/agent/session").get_json()["session"]["owner"] == "human:k"


# -------------------------------------------------------------------- D-09

class TestOneDriver:
    def test_a_terminal_agents_plan_is_driven_by_it_alone(self, c, inst, fake_run):
        """D-09: a terminal agent's plan + a person's Start = SM also told its
        in-app agent to go -- two agents on one plan."""
        assert c.post("/api/agent/chat/start", json={"prompt": "hello"}, headers=HUMAN).get_json()["ok"]
        h = {**TERM, "X-SM-Bridge-Pid": str(os.getpid())}
        r = c.post("/api/agent/plans", json={"title": "terminal plan", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]},
                   headers=h).get_json()
        assert "you drive it" in r["how"], r["how"]
        pid = r["plan"]["id"]
        d = c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()
        assert d["ok"] and d["session_started"] is False and d["driver"]["kind"] == "terminal", d
        cards = c.get("/api/agent/chat/cards").get_json()["cards"]
        assert not any(k["kind"] == "user" and k["text"].startswith("[Start] plan") for k in cards), \
            "the in-app agent was not told to run a plan someone else drives"
        r = _run(c, _in_app(c, inst), plan_id=pid, step=0)
        assert r.status_code == 409 and r.get_json()["refused"] == "not_the_driver", r.get_json()
        assert fake_run.calls == []
        r = _run(c, h, plan_id=pid, step=0).get_json()
        assert r["ok"] and r["result"]["status"] == "done", r
        assert "driven by by_claude in a terminal" in _journal(c, inst)

    def test_a_gone_proposer_hands_the_plan_to_the_in_app_agent(self, c, inst):
        """The proposer exited before Start (claude -p ends after plan_propose):
        SM's in-app agent drives it, and the journal says so."""
        h = {**TERM, "X-SM-Bridge-Pid": str(_dead_pid())}
        pid = c.post("/api/agent/plans", json={"title": "orphan", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]},
                     headers=h).get_json()["plan"]["id"]
        d = c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()
        assert d["ok"] and d["session_started"] is True and d["driver"]["kind"] == "app", d
        assert "has exited" in _journal(c, inst)
        assert _run(c, h, plan_id=pid, step=0).get_json()["refused"] == "not_the_driver"

    def test_the_in_app_secret_is_never_served(self, c, inst):
        """The in-app driver is told apart by a value only its own bridge holds;
        no read hands it to another agent."""
        _run_plan(c)
        secret = agent_session.load(str(inst), _key(c))["app_session"]
        assert secret
        for url in ("/api/agent/chat/status", "/api/agent/chat/cards", "/api/agent/session", "/api/agent/plans",
                    "/api/agent/now", "/api/agent/chip"):
            assert secret not in c.get(url).get_data(as_text=True), url


class TestTheRecordReadsWhatWasWritten:
    def test_a_reader_never_sees_an_armed_session_as_absent(self, tmp_path):
        """A read that landed inside another thread's replace of the session file came back
        as "no record", and run_node's gate read an armed chip as not armed (measured: one
        in about ten runs of this file). Readers wait for the writers' lock now."""
        import threading
        inst = tmp_path / "i"
        agent_session.save(inst, "K", start_token="tok", grant={"plan_id": "pl-x"})
        stop = threading.Event()

        def writer():
            n = 0
            while not stop.is_set():
                n += 1
                agent_session.save(inst, "K", session_id=f"s{n}")
        t = threading.Thread(target=writer, daemon=True)
        t.start()
        try:
            misses = sum(1 for _ in range(3000) if not (agent_session.load(inst, "K") or {}).get("start_token"))
        finally:
            stop.set()
            t.join(5)
        assert misses == 0, f"{misses} of 3000 reads saw no armed record"


class TestToldOnlyTheDriver:
    def test_allow_run_tells_the_in_app_agent_only_when_it_drives(self, c, inst):
        """D-09: "Allow run" on a terminal agent's plan used to tell SM's in-app agent to call
        run_node -- a second agent on the plan, refused, reporting the refusal to the person."""
        from quam_state_manager.core import approvals
        assert c.post("/api/agent/chat/start", json={"prompt": "hello"}, headers=HUMAN).get_json()["ok"]
        pid = c.post("/api/agent/plans", json={"title": "t", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]},
                     headers=TERM).get_json()["plan"]["id"]
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        ap = approvals.add(str(inst), _key(c), kind="run", node="05_power_rabi", targets=["qA1"], writes=None,
                           reason="r", why_held="mode ask-all", actor="by_claude", plan_id=pid, params={})
        d = c.post(f"/api/agent/approvals/{ap['id']}/approve", json={}, headers=HUMAN).get_json()
        assert d["ok"] and d["agent_told"] is False, d
        assert "by_claude in a terminal" in d["told_note"] and ap["id"] in d["told_note"], \
            "the person reads who runs it (docs/254)"

    def test_allow_run_tells_the_in_app_driver_the_exact_call_and_the_plan_finishes(self, c, inst, fake_run,
                                                                                    monkeypatch):
        """docs/254 x docs/253, C-04: an ask-all plan the in-app session drives. Start arms the plan;
        the step's run still files a run request; Allow tells THE DRIVER the exact call (the approval
        id + the params); that call runs once, and the plan's end disarms."""
        monkeypatch.setenv("FAKE_ECHO_STDIN", "1")
        limits.save(str(inst), _key(c), {"mode": "ask-all"})
        pid = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1 num_shots=200"},
                     headers=HUMAN).get_json()["plan"]["id"]
        d = c.post(f"/api/agent/plans/{pid}/start", json={"backend": "codex"}, headers=HUMAN).get_json()
        assert d["ok"] and d["plan"]["mode"] == "ask-all", d
        h = _in_app(c, inst)
        r = _run(c, h, plan_id=pid, step=0, params={"num_shots": 200}).get_json()
        assert r["refused"] == "awaiting_approval" and r["needs"] == "run" and r["approval"]["step"] == 0, r
        aid = r["approval"]["id"]
        assert fake_run.calls == [], "Start armed the plan; ask-all still waits for the person's Allow"
        # a request of ANOTHER plan (not the armed one): the driver is never told to run it
        from quam_state_manager.core import approvals
        other = approvals.add(str(inst), _key(c), kind="run", node="05_power_rabi", targets=["qA1"], writes=None,
                              reason="r", why_held="mode ask-all", actor="by_claude", plan_id="pl-not-armed",
                              params={"num_shots": 200})
        d = c.post(f"/api/agent/approvals/{other['id']}/approve", json={}, headers=HUMAN).get_json()
        assert d["ok"] and d["agent_told"] is False and "not armed" in d["told_note"], d
        d = c.post(f"/api/agent/approvals/{aid}/approve", json={}, headers=HUMAN).get_json()
        assert d["ok"] and d["agent_told"] is True and "told_note" not in d, d
        said = f'"approval_id": "{aid}"'
        assert _wait(lambda: any(said in (e.get("text") or "") for e in
                                 c.get("/api/agent/chat/events?after=0").get_json()["events"]
                                 if e["hook_event_name"] == "Text")), "the driver was told the exact call"
        r = _run(c, h, plan_id=pid, step=0, params={"num_shots": 200}, approval_id=aid).get_json()
        assert r["ok"] and r["result"]["status"] == "done", r
        assert len(fake_run.calls) == 1
        assert _wait(lambda: c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["status"] == "done")
        assert not _armed(c), "the plan finished: the grant ended with it"
        rec = approvals.get(str(inst), _key(c), aid)
        assert rec["status"] == "approved" and rec["used_by_run"] == r["key"], "a spent Allow stays what it was"


class TestTheBridgeSaysWhoseItIs:
    def test_a_terminal_bridge_names_itself_and_its_process(self, monkeypatch):
        from quam_state_manager.core import agent_link
        monkeypatch.setattr(agent_link, "BRIDGE_SESSION", None)
        link = agent_link.SMLink("http://127.0.0.1:1", agent_id="claude")
        h = link._headers({"Content-Type": "application/json"})
        assert h["X-SM-Session"].startswith("t-") and h["X-SM-Bridge-Pid"] == str(os.getpid())
        assert link._headers()["X-SM-Session"] == h["X-SM-Session"], "one id for the bridge's whole life"
        monkeypatch.setattr(agent_link, "BRIDGE_SESSION", "app-xyz")      # what SM_SESSION sets at import
        assert link._headers()["X-SM-Session"] == "app-xyz"

    def test_the_in_app_sessions_bridge_carries_its_value(self, c, inst):
        """SM writes the in-app session's value into THAT session's MCP config only."""
        _run_plan(c)
        rec = agent_session.load(str(inst), _key(c))
        cfg = json.loads((Path(str(inst)) / "agent_mcp" / f"{_key(c)}-claude.json").read_text(encoding="utf-8"))
        assert rec["app_session"] and cfg["mcpServers"]["sm"]["env"]["SM_SESSION"] == rec["app_session"]
        ro = Path(str(inst)) / "agent_mcp" / f"{_key(c)}-claude-ro.json"
        assert not ro.exists() or "SM_SESSION" not in ro.read_text(encoding="utf-8"), "a question's bridge drives nothing"


# -------------------------------------------------------------------- D-16

class TestStopBy:
    def test_a_night_stop_counts_from_the_start(self):
        lim = {"stop_by": "06:00"}
        start = datetime(2026, 10, 3, 20, 0).timestamp()
        assert not limits.past_stop_by(lim, datetime(2026, 10, 3, 20, 30), since=start), "the evening is before 06:00"
        assert not limits.past_stop_by(lim, datetime(2026, 10, 4, 5, 59), since=start)
        assert limits.past_stop_by(lim, datetime(2026, 10, 4, 6, 0), since=start)
        early = datetime(2026, 10, 3, 5, 0).timestamp()
        assert limits.past_stop_by(lim, datetime(2026, 10, 3, 6, 1), since=early), "a 05:00 start stops at 06:00 that day"
        assert limits.past_stop_by(lim, datetime(2026, 10, 3, 6, 1)), "no start known: today's 06:00, as before"

    def test_unarmed_the_refusal_is_the_missing_click_not_the_stop_time(self, c, inst):
        """With nothing armed there is no night to end: the agent is told what is missing (a
        person's Start), not that a stop time it never had has passed."""
        limits.save(str(inst), _key(c), {"stop_by": "00:00"})
        r = _run(c, TERM).get_json()
        assert r["refused"] == "no_start_token", r

    def test_the_gate_reads_it_from_the_grant(self, c, inst, fake_run):
        hhmm = datetime.now().strftime("%H:%M")
        limits.save(str(inst), _key(c), {"stop_by": hhmm})
        pid = c.post("/api/agent/plans", json={"title": "t", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}]},
                     headers=TERM).get_json()["plan"]["id"]
        # armed a minute AFTER stop_by: the stop is tomorrow's, not this morning's
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        k = _key(c)
        rec = agent_session.load(str(inst), k)
        agent_session.save(str(inst), k, armed_at=float(rec["armed_at"]) + 61)
        r = _run(c, TERM, plan_id=pid, step=0).get_json()
        assert r.get("refused") != "past_stop_by", r

    def test_an_overnight_plan_is_armed_through_midnight_and_ends_at_stop_by(self, c, inst, fake_run, monkeypatch):
        """The overnight envelope: armed at 18:00 with stop_by 06:00, the plan's arming holds
        through midnight and ends at 06:00 -- one journal line, the plan closed, and a run asked
        after that is refused past_stop_by."""
        from quam_state_manager.core import agent_grant
        k = _key(c)
        limits.save(str(inst), k, {"stop_by": "06:00"})
        pid = c.post("/api/agent/plans", json={"title": "night", "steps": [{"node": "05_power_rabi", "targets": ["qA1"]}] * 3},
                     headers=TERM).get_json()["plan"]["id"]
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        armed = datetime(2026, 10, 3, 18, 0).timestamp()
        rec = agent_session.load(str(inst), k)
        agent_session.save(str(inst), k, armed_at=armed, grant=dict(rec["grant"], at=armed))
        assert limits.stop_deadline(limits.load(str(inst), k), armed) == datetime(2026, 10, 4, 6, 0).timestamp()

        class _Clock(datetime):
            t = datetime(2026, 10, 4, 0, 30)

            @classmethod
            def now(cls, tz=None):
                return cls.t
        monkeypatch.setattr(agent_grant, "datetime", _Clock)
        assert _armed(c), "half past midnight: still armed"
        _Clock.t = datetime(2026, 10, 4, 6, 0)
        assert not _armed(c), "06:00: the arming ended"
        assert "stop time (06:00) was reached" in _journal(c, inst)
        assert c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["status"] == "stopped"
        r = _run(c, TERM, plan_id=pid).get_json()
        assert r["refused"] == "past_stop_by", r

    def test_a_grant_end_is_announced_to_listeners_once(self, c, inst):
        """The hook the overnight cluster hangs plan_done / the morning summary on."""
        from quam_state_manager.core import agent_grant
        seen = []
        agent_grant.ON_END.append(lambda i, k, g, why: seen.append((g.get("plan_id"), why)))
        try:
            pid = _run_plan(c)
            c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN)
            _armed(c)
            assert seen == [(pid, f"plan `/run 05_power_rabi qA1` was cancelled by human:kyunghoon")], seen
        finally:
            agent_grant.ON_END.pop()
