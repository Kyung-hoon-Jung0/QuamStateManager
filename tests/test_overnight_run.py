"""The overnight run (docs/261): ONE envelope a person approves before leaving.

Inside the envelope writes that pass the gates apply on their own; outside it
the write is held for the morning and that target stops while the others go
on; too many gate fails halt the plan. The phone hears failures, waits and the
plan's end; the morning reads one summary that survives a restart.

Everything runs through the shipped routes. The node subprocess is a fake that
edits the item's scratch state per TARGET the way a node's update_state would
(tests/test_agent_runs.FakeRun, extended); the webhook is captured at
``limits.notify`` (the chip's own Limits gate) or at the HTTP post itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from quam_state_manager.core import (agent_grant, agent_overnight, agent_plans, agent_runs, agent_session, approvals,
                                     limits, scheduler, undo_journal)
from quam_state_manager.core import journal as journal_mod
from quam_state_manager.web import agent_api as aa
from quam_state_manager.web.app import create_app
from tests.test_agent_runs import AGENT, HUMAN, NODE_SRC, FakeRun, _wait

_ROOT = Path(__file__).resolve().parent.parent
UNREACHABLE = ("ConnectionError: Failed to connect to Quantum Machines Manager: Failed to detect to QuantumMachines "
               "server, failed to connect to cluster 'night'. Tried connecting to 127.0.0.1:1.")
FAM = "power_rabi"                      # agent_runs.family_key("05_power_rabi")


# ---------------------------------------------------------------- fixtures

def _state3():
    from tests.test_web import _make_state
    st = _make_state()
    for i, t in enumerate(("qA2", "qA3"), start=1):
        q = json.loads(json.dumps(st["qubits"]["qA1"]))
        q["id"] = t
        q["f_01"] = 6.25e9 + i * 1e8
        q["xy"].pop("opx_output", None)
        st["qubits"][t] = q
    st["active_qubit_names"] = ["qA1", "qA2", "qA3"]
    return st


@pytest.fixture
def inst(tmp_path):
    return tmp_path / "_app_instance"


@pytest.fixture
def app(inst):
    return create_app(testing=True, instance_path=str(inst))


@pytest.fixture
def synth_folder(tmp_path):
    from tests.test_web import _make_wiring
    d = tmp_path / "chip"
    d.mkdir()
    (d / "state.json").write_text(json.dumps(_state3(), indent=2), encoding="utf-8")
    (d / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return d


@pytest.fixture
def cal(tmp_path):
    d = tmp_path / "calibrations"
    d.mkdir()
    (d / "05_power_rabi.py").write_text(NODE_SRC, encoding="utf-8")
    return d


def _settings(app, cal, simulate=False):
    with app.app_context():
        from quam_state_manager.web import routes as r
        scheduler.save_settings(r._sched_inst(), {"env_python": sys.executable, "calibrations_folder": str(cal),
                                                  "global_simulate": simulate, "default_timeout_s": 60})


def _drain(app):
    reg = app.config.get("agent_run_registry")
    with app.app_context():
        from quam_state_manager.web import routes as r
        try:
            scheduler.cancel(r._sched_inst())
        except Exception:  # noqa: BLE001
            pass
    if reg:
        for m in list(reg.runs.values()):
            reg.wait(m["key"], 15)


@pytest.fixture
def c(app, synth_folder, cal):
    client = app.test_client()
    client.post("/load", data={"folder": str(synth_folder)})
    _settings(app, cal)
    yield client
    _drain(app)


class TargetRun(FakeRun):
    """A node per TARGET: ``fail`` targets fail with ``error``; every other target's
    ``qubits.<t>.f_01`` moves by ``deltas[t]`` (default ``delta``) -- unless ``write``
    names the only targets it writes. ``until_cancel`` lingers like a long node."""

    def __init__(self, *, fail=(), error=UNREACHABLE, delta=1e3, deltas=None, write=None, until_cancel=False):
        super().__init__(delta=delta, until_cancel=until_cancel)
        self.fail_t, self.error, self.deltas, self.write = set(fail), error, dict(deltas or {}), write

    def __call__(self, instance_path, item, settings, runner):
        self.calls.append(dict(item))
        t0 = time.time()
        while self.until_cancel and not runner["cancel"].is_set() and time.time() - t0 < 30:
            time.sleep(0.05)
        if runner["cancel"].is_set():
            return {"status": "cancelled", "error": "cancelled by user", "returncode": None, "log_file": None}
        targets = list(item.get("targets") or [])
        if set(targets) & self.fail_t:
            return {"status": "failed", "error": self.error, "returncode": 1, "log_file": None}
        p = Path(item["state_path"]) / "state.json"
        st = json.loads(p.read_text(encoding="utf-8"))
        for t in targets:
            if self.write is not None and t not in self.write:
                continue
            st["qubits"][t]["f_01"] = st["qubits"][t]["f_01"] + self.deltas.get(t, self.delta)
        p.write_text(json.dumps(st), encoding="utf-8")
        return {"status": "done", "error": None, "returncode": 0, "log_file": None}


@pytest.fixture
def notified(monkeypatch):
    got: list = []
    monkeypatch.setattr(limits, "notify",
                        lambda inst_, chip_, event, payload=None: got.append((event, payload)) or {"sent": []})
    return got


def _events(got, name):
    return [p for e, p in got if e == name]


def _key(c):
    return c.get("/api/agent/chip").get_json()["chip_key"]


def _name(c):
    return c.get("/api/agent/chip").get_json()["name"]


def _journal(c, inst):
    return journal_mod.read(str(inst), _name(c), datetime.now().strftime("%Y-%m-%d")) or ""


def _plan(c, targets, *, mode="auto", node="05_power_rabi", title="night", start=True, envelope=None):
    """A terminal agent proposes one step per entry of ``targets`` (a name or a list),
    a person sets the mode and presses Start."""
    steps = [{"node": node, "targets": t if isinstance(t, list) else [t]} for t in targets]
    r = c.post("/api/agent/plans", json={"title": title, "steps": steps}, headers=AGENT)
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["plan"]["id"]
    assert c.post(f"/api/agent/plans/{pid}/mode", json={"mode": mode}, headers=HUMAN).status_code == 200
    if start:
        body = {"envelope": envelope} if envelope is not None else {}
        d = c.post(f"/api/agent/plans/{pid}/start", json=body, headers=HUMAN).get_json()
        assert d["ok"], d
    return pid


def _step(c, pid, i, targets, node="05_power_rabi", **kw):
    body = {"node": node, "targets": targets if isinstance(targets, list) else [targets],
            "reason": "the night's plan", "plan_id": pid, "step": i, "wait_s": 20}
    body.update(kw)
    return c.post("/api/agent/run-node", json=body, headers=AGENT)


def _plan_rec(c, inst, pid):
    return agent_plans.get(str(inst), _key(c), pid)


# ================================================================ the gate-fail definition (pure)

class TestWhatAGateFailIs:
    def test_a_failed_run_is_a_gate_fail_on_every_target_it_ran_on(self):
        res = {"status": "failed", "classification": "host_unreachable",
               "failure": {"what": "QM host unreachable at 127.0.0.1:1 (connection refused)"}}
        g = agent_overnight.gate_verdicts(res, ["qA1", "qA2"])
        assert set(g) == {"qA1", "qA2"} and {v["v"] for v in g.values()} == {"fail"}
        assert g["qA1"]["why"] == "host_unreachable: QM host unreachable at 127.0.0.1:1 (connection refused)"
        for cls in ("hardware_contention", "timeout", "node_error", "interrupted"):
            assert agent_overnight.gate_verdicts({"status": "failed", "classification": cls, "error": "x"},
                                                 ["qA1"])["qA1"]["v"] == "fail", cls

    def test_a_persons_stop_and_a_skipped_item_are_not_the_runs_failure(self):
        assert agent_overnight.gate_verdicts({"status": "cancelled", "classification": "cancelled"}, ["qA1"]) is None
        assert agent_overnight.gate_verdicts({"status": "skipped", "classification": "skipped"}, ["qA1"]) is None

    def test_the_nodes_own_failed_outcome_fails_that_target_only(self):
        res = {"status": "done", "classification": "ok", "mode": "auto", "applied": True,
               "run": {"outcomes": {"qA1": "successful", "qA2": "failed"}}}
        g = agent_overnight.gate_verdicts(res, ["qA1", "qA2"])
        assert g["qA1"] == {"v": "pass"} and g["qA2"]["v"] == "fail" and g["qA2"]["why"].startswith("fit:")

    def test_a_write_the_envelope_held_is_a_gate_fail_but_the_modes_own_hold_is_not(self):
        held = {"status": "done", "classification": "ok", "approval": {"id": "ap-1"}}
        g = agent_overnight.gate_verdicts(dict(held, mode="auto", why_held="max_delta for power_rabi: ..."), ["qA1"])
        assert g["qA1"]["v"] == "held" and "max_delta" in g["qA1"]["why"]
        assert agent_overnight.gate_verdicts(dict(held, mode="ask-writes", why_held="mode ask-writes"),
                                             ["qA1"])["qA1"] == {"v": "pass"}, "ask-writes holds every write: its mode"
        assert agent_overnight.gate_verdicts(dict(held, mode="auto", why_held="DRY RUN values (simulate ON ...)"),
                                             ["qA1"])["qA1"] == {"v": "pass"}

    def test_the_stop_loss_counts_in_the_order_steps_ended_and_a_pass_resets_it(self):
        def step(i, ended, v):
            return {"i": i, "ended": ended, "gate": {"qA1": {"v": v, "why": f"s{i}"}}}
        # ended order: s4(fail) s0(fail) s1(pass) s2(fail) s3(fail) -- the index order would read 3 in a row
        plan = {"mode": "auto", "steps": [step(0, 2, "fail"), step(1, 3, "pass"), step(2, 4, "fail"),
                                          step(3, 5, "fail"), step(4, 1, "fail")]}
        st = agent_overnight.stoploss(plan, {"stoploss_target": 3, "stoploss_plan": 8})
        assert st["targets"]["qA1"]["consecutive"] == 2 and st["fails"] == 4 and not st["halt_targets"]
        plan["steps"].append(step(5, 6, "fail"))
        st = agent_overnight.stoploss(plan, {"stoploss_target": 3, "stoploss_plan": 8})
        assert "3 gate fails in a row (stoploss_target 3)" in st["halt_targets"]["qA1"]
        assert st["halt_plan"] is None
        assert agent_overnight.stoploss(plan, {"stoploss_target": 3, "stoploss_plan": 5})["halt_plan"] == \
            "5 gate fails in this plan (stoploss_plan 5)"

    def test_zero_turns_a_stop_loss_off(self):
        plan = {"mode": "auto", "steps": [{"i": i, "ended": i, "gate": {"qA1": {"v": "fail"}}} for i in range(20)]}
        st = agent_overnight.stoploss(plan, {"stoploss_target": 0, "stoploss_plan": 0})
        assert st["fails"] == 20 and not st["halt_targets"] and st["halt_plan"] is None

    def test_a_held_write_halts_its_target_at_once(self):
        plan = {"mode": "auto", "steps": [{"i": 0, "ended": 1, "gate": {"qA1": {"v": "held", "why": "held: max_delta"}}}]}
        st = agent_overnight.stoploss(plan, {"stoploss_target": 3, "stoploss_plan": 8})
        assert "held for a person" in st["halt_targets"]["qA1"]

    def test_a_failed_fit_holds_only_a_write_on_that_target(self):
        run = {"outcomes": {"qA1": "failed", "qA2": "successful"}}
        assert agent_overnight.fit_hold(run, [{"path": "qubits.qA10.f_01"}, {"path": "qubits.qA2.f_01"}]) is None, \
            "qA10 is not qA1: a segment, never a prefix"
        why = agent_overnight.fit_hold(run, [{"path": "qubits.qA2.f_01"}, {"path": "qubits.qA1.xy.RF_frequency"}])
        assert why and "fit failed for qA1" in why and "qubits.qA1.xy.RF_frequency" in why

    def test_an_undo_unit_names_the_group_it_came_from(self):
        from quam_state_manager.core.modifier import ChangeEntry
        e = ChangeEntry(dot_path="qubits.qA1.f_01", old_value=1, new_value=2, source_file="state",
                        group_id="agent:20261003-010101-abcdef")
        assert undo_journal.make_unit([e])["gid"] == "agent:20261003-010101-abcdef"
        e2 = ChangeEntry(dot_path="qubits.qA1.f_01", old_value=1, new_value=2, source_file="state", group_id=None)
        assert "gid" not in undo_journal.make_unit([e2]), "no group, no gid -- never invented"


# ================================================================ stop-loss, through run_node

class TestATargetHalts:
    def test_three_failed_runs_in_a_row_halt_that_target_and_the_others_go_on(self, c, inst, monkeypatch, notified):
        fr = TargetRun(fail={"qA1"})
        monkeypatch.setattr(scheduler, "_run_item", fr)
        pid = _plan(c, ["qA1", "qA1", "qA1", "qA1", "qA2"])
        for i in range(2):
            r = _step(c, pid, i, "qA1").get_json()
            assert r["result"]["classification"] == "host_unreachable" and not r["result"].get("stop_loss"), r
        r = _step(c, pid, 2, "qA1").get_json()
        assert r["result"]["stop_loss"]["halted"] == {"qA1": r["result"]["stop_loss"]["halted"]["qA1"]}
        assert "3 gate fails in a row (stoploss_target 3)" in r["result"]["stop_loss"]["halted"]["qA1"]
        p = _plan_rec(c, inst, pid)
        assert p["status"] == "running" and p["halted"]["qA1"]["skipped"] == [3]
        assert p["steps"][3]["status"] == "skipped" and p["steps"][3]["halted"] == "qA1"
        assert p["steps"][4]["status"] == "pending", "qA2 goes on"
        j = _journal(c, inst)
        assert "stop-loss: target qA1 halted in plan `night` -- 3 gate fails in a row (stoploss_target 3)" in j
        assert "its 1 remaining step(s) are skipped, the other targets continue" in j
        refused = _step(c, pid, 3, "qA1")
        assert refused.status_code == 409 and refused.get_json()["refused"] == "target_halted", refused.get_json()
        assert "qA1 was halted by plan `night`'s stop-loss" in refused.get_json()["how"]
        r = _step(c, pid, 4, "qA2").get_json()
        assert r["result"]["status"] == "done" and r["result"]["applied"] is True, r
        assert len(fr.calls) == 4, "the halted step never reached the node"
        p = _plan_rec(c, inst, pid)
        assert p["status"] == "failed", "every step ended: the plan ends (3 failed, 1 skipped, 1 done)"
        done = _wait(lambda: _events(notified, "plan_done"))
        assert done[0]["reason"] == "failed" and done[0]["halted_targets"][0]["target"] == "qA1"

    def test_a_refusal_on_a_halted_target_is_journaled_once(self, c, inst, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun(fail={"qA1"}))
        limits.save(str(inst), _key(c), {"stoploss_target": 1})
        pid = _plan(c, ["qA1", "qA1", "qA2"])
        _step(c, pid, 0, "qA1")
        for _ in range(3):
            assert _step(c, pid, 1, "qA1").get_json()["refused"] == "target_halted"
        assert _journal(c, inst).count("refused `05_power_rabi` on qA1: qA1 halted by the plan's stop-loss") == 1

    def test_a_step_on_a_halted_target_and_another_is_skipped_whole(self, c, inst, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun(fail={"qA1"}))
        limits.save(str(inst), _key(c), {"stoploss_target": 1})
        pid = _plan(c, ["qA1", ["qA1", "qA2"], "qA2"])
        _step(c, pid, 0, "qA1")
        p = _plan_rec(c, inst, pid)
        assert p["steps"][1]["status"] == "skipped" and p["steps"][2]["status"] == "pending"
        assert _step(c, pid, 1, ["qA1", "qA2"]).get_json()["refused"] == "target_halted"


class TestThePlanHalts:
    def test_too_many_gate_fails_halt_the_plan_through_the_grants_end(self, c, inst, monkeypatch, notified):
        fr = TargetRun(fail={"qA1", "qA2", "qA3"})
        monkeypatch.setattr(scheduler, "_run_item", fr)
        limits.save(str(inst), _key(c), {"stoploss_target": 0, "stoploss_plan": 3})
        pid = _plan(c, ["qA1", "qA2", "qA3", "qA1", "qA2"])
        for i, t in enumerate(("qA1", "qA2")):
            _step(c, pid, i, t)
        assert _plan_rec(c, inst, pid)["status"] == "running"
        r = _step(c, pid, 2, "qA3").get_json()
        assert r["result"]["stop_loss"]["plan_halt"].startswith("stop-loss: 3 gate fails in this plan (stoploss_plan 3)")
        rec = agent_session.load(str(inst), _key(c))
        assert not rec.get("start_token") and rec["last_grant"]["code"] == "stop_loss", rec.get("last_grant")
        p = _plan_rec(c, inst, pid)
        assert p["status"] == "stopped" and p["end_code"] == "stop_loss"
        assert "disarmed: stop-loss: 3 gate fails in this plan (stoploss_plan 3)" in _journal(c, inst)
        nxt = _step(c, pid, 3, "qA1")
        assert nxt.status_code == 409 and nxt.get_json()["refused"] == "stop_loss", nxt.get_json()
        assert len(fr.calls) == 3
        done = _wait(lambda: _events(notified, "plan_done"))
        assert len(done) == 1 and done[0]["reason"] == "stop-loss" and done[0]["counts"]["gate_fails"] == 3


class TestTheEnvelopeHolds:
    def test_a_jump_past_max_delta_is_held_and_its_target_stops_the_others_go_on(self, c, inst, monkeypatch, notified):
        assert agent_runs.family_key("05_power_rabi") == FAM
        monkeypatch.setattr(scheduler, "_run_item", TargetRun(deltas={"qA1": 5e6, "qA2": 1e3}))
        limits.save(str(inst), _key(c), {"max_delta": {FAM: 1e5}})
        pid = _plan(c, ["qA1", "qA1", "qA2"])
        r = _step(c, pid, 0, "qA1").get_json()
        assert r["result"]["applied"] is False and r["result"]["why_held"].startswith("max_delta"), r
        assert "held for a person" in r["result"]["stop_loss"]["halted"]["qA1"], "held -> that target stops at once"
        p = _plan_rec(c, inst, pid)
        assert p["steps"][1]["status"] == "skipped" and p["steps"][2]["status"] == "pending"
        r = _step(c, pid, 2, "qA2").get_json()
        assert r["result"]["applied"] is True, r
        nh = _wait(lambda: _events(notified, "needs_human"))
        assert nh[0]["what"] == "held_write" and nh[0]["halted_targets"] == ["qA1"] and nh[0]["plan_id"] == pid
        done = _wait(lambda: _events(notified, "plan_done"))
        assert done[0]["counts"]["applied"] == 1 and done[0]["counts"]["held"] == 1 and done[0]["reason"] == "finished"

    def test_a_write_from_a_failed_fit_is_held_in_auto(self, c, inst, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun())
        monkeypatch.setattr(agent_runs, "_attribute",
                            lambda *a, **k: {"run_id": 41, "outcomes": {"qA1": "failed"}})
        pid = _plan(c, ["qA1", "qA2"])
        res = _step(c, pid, 0, "qA1").get_json()["result"]
        assert res["applied"] is False and "fit failed for qA1" in res["why_held"], res
        assert _plan_rec(c, inst, pid)["steps"][0]["gate"]["qA1"]["v"] == "held"

    def test_a_failed_fit_that_wrote_nothing_on_its_target_is_a_fail_not_a_hold(self, c, inst, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun(write={"qA1"}))
        monkeypatch.setattr(agent_runs, "_attribute",
                            lambda *a, **k: {"run_id": 42, "outcomes": {"qA1": "successful", "qA2": "failed"}})
        pid = _plan(c, [["qA1", "qA2"], "qA3"])
        res = _step(c, pid, 0, ["qA1", "qA2"]).get_json()["result"]
        assert res["applied"] is True, res
        g = _plan_rec(c, inst, pid)["steps"][0]["gate"]
        assert g["qA1"] == {"v": "pass"} and g["qA2"]["v"] == "fail"


# ================================================================ the webhooks (D-14)

class TestThePhoneHears:
    def test_plan_done_fires_once_when_the_plan_finishes(self, c, inst, monkeypatch, notified):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun())
        pid = _plan(c, ["qA1"])
        _step(c, pid, 0, "qA1")
        done = _wait(lambda: _events(notified, "plan_done"))
        time.sleep(0.3)
        assert len(_events(notified, "plan_done")) == 1
        d = done[0]
        assert d["plan_id"] == pid and d["reason"] == "finished" and d["status"] == "done"
        assert d["counts"]["applied"] == 1 and d["counts"]["held"] == 0 and d["counts"]["failed"] == 0
        assert d["link"] == f"/agent/summary?plan={pid}" and d["started_at"] and d["ended_at"] >= d["started_at"]

    def test_a_cancelled_plan_says_cancelled(self, c, inst, monkeypatch, notified):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun())
        pid = _plan(c, ["qA1", "qA2"])
        _step(c, pid, 0, "qA1")
        assert c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN).status_code == 200
        done = _wait(lambda: _events(notified, "plan_done"))
        assert done[0]["reason"] == "cancelled" and "human:kyunghoon" in done[0]["why"]

    def test_a_persons_stop_is_no_agent_failure_and_plan_done_says_stopped(self, c, inst, monkeypatch, notified):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun(until_cancel=True))
        pid = _plan(c, ["qA1", "qA2"])
        r = _step(c, pid, 0, "qA1", wait_s=1).get_json()
        assert r["status"] == "running", r
        assert c.post("/api/agent/session/stop", json={"mode": "now"}, headers=HUMAN).get_json()["ok"]
        m = c.get(f"/api/agent/run/{r['key']}?wait_s=15").get_json()
        assert m["result"]["classification"] == "cancelled"
        done = _wait(lambda: _events(notified, "plan_done"))
        assert done[0]["reason"] == "stopped" and done[0]["status"] == "stopped"
        time.sleep(0.3)
        assert not _events(notified, "agent_failure"), "a person's Stop is not a failure (D-14)"

    def test_a_real_failure_says_which_plan(self, c, inst, monkeypatch, notified):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun(fail={"qA1"}))
        pid = _plan(c, ["qA1", "qA2"])
        _step(c, pid, 0, "qA1")
        f = _wait(lambda: _events(notified, "agent_failure"))
        assert f[0]["classification"] == "host_unreachable" and f[0]["plan_id"] == pid and f[0]["step"] == 0
        assert f[0]["link"] == f"/agent/summary?plan={pid}" and f[0]["halted_targets"] == []

    def test_an_ask_all_run_request_waits_for_a_person_and_the_phone_hears_it_once(self, c, inst, monkeypatch, notified):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun())
        pid = _plan(c, ["qA1"], mode="ask-all")
        for _ in range(2):
            r = _step(c, pid, 0, "qA1").get_json()
            assert r["refused"] == "awaiting_approval" and r["needs"] == "run", r
        nh = _wait(lambda: _events(notified, "needs_human"))
        time.sleep(0.3)
        assert len(_events(notified, "needs_human")) == 1, "the same ask twice is one request, one alert"
        assert nh[0]["what"] == "run_request" and nh[0]["approval"] == r["approval"]["id"] and nh[0]["step"] == 0
        assert nh[0]["plan_id"] == pid and nh[0]["link"] == f"/agent/summary?plan={pid}"

    def test_a_persons_stop_expires_the_plans_run_requests(self, c, inst, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun())
        pid = _plan(c, ["qA1"], mode="ask-all")
        aid = _step(c, pid, 0, "qA1").get_json()["approval"]["id"]
        assert c.post("/api/agent/session/stop", json={"mode": "after_run"}, headers=HUMAN).get_json()["ok"]
        assert _plan_rec(c, inst, pid)["status"] == "stopped"
        a = approvals.get(str(inst), _key(c), aid)
        assert a["status"] == "expired" and "plan stopped" in a["note"], a

    def test_the_payloads_carry_no_secret(self, c, inst, monkeypatch):
        from quam_state_manager.core.autofit import notify as nmod
        bodies: list = []
        monkeypatch.setattr(nmod, "_post", lambda url, body, timeout: bodies.append(body) or True)
        limits.save(str(inst), _key(c), {"webhook_url": "https://hooks.example/T00/B00/SECRETTOKEN",
                                         "max_delta": {FAM: 1e5}})
        monkeypatch.setattr(scheduler, "_run_item", TargetRun(deltas={"qA1": 5e6}, fail={"qA2"}))
        pid = _plan(c, ["qA1", "qA2"])
        _step(c, pid, 0, "qA1")
        _step(c, pid, 1, "qA2")
        _wait(lambda: {b["event"] for b in bodies} >= {"needs_human", "agent_failure", "plan_done"})
        text = json.dumps(bodies)
        assert "SECRETTOKEN" not in text and "hooks.example" not in text
        assert "app_session" not in text and "start_token" not in text
        assert all(set(b) == {"event", "chip", "at", "payload"} for b in bodies)
        # a state value reaches the phone only inside a sentence the journal already says (the held jump)
        import re
        journal = _journal(c, inst)
        texts: list = []

        def walk(x, key=None):
            if isinstance(x, dict):
                for k, v in x.items():
                    walk(v, k)
            elif isinstance(x, list):
                for v in x:
                    walk(v, key)
            elif isinstance(x, str) and key in ("why", "what", "error", "why_held"):
                texts.append(x)
        walk([b["payload"] for b in bodies])
        values = {v for t in texts for v in re.findall(r"\d+\.\d+|\d{5,}", t)}
        assert "6255000000.0" in values and all(v in journal for v in values), (values, journal)

    def test_stop_by_ends_the_plan_with_nobody_reading_and_says_stop_by(self, c, app, inst, monkeypatch, notified):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun())
        limits.save(str(inst), _key(c), {"stop_by": "06:00"})
        pid = _plan(c, ["qA1", "qA2"])
        _step(c, pid, 0, "qA1")
        k = _key(c)
        armed = datetime(2026, 10, 3, 18, 0).timestamp()
        rec = agent_session.load(str(inst), k)
        agent_session.save(str(inst), k, armed_at=armed, grant=dict(rec["grant"], at=armed))

        class _Clock(datetime):
            t = datetime(2026, 10, 4, 0, 30)

            @classmethod
            def now(cls, tz=None):
                return cls.t
        monkeypatch.setattr(agent_grant, "datetime", _Clock)
        assert aa.watch_grants_once(app) == [], "half past midnight: still armed"
        _Clock.t = datetime(2026, 10, 4, 6, 0, 30)
        assert aa.watch_grants_once(app) == [k], "06:00 -- the watch ends it, no page open"
        p = _plan_rec(c, inst, pid)
        assert p["status"] == "stopped" and p["end_code"] == "past_stop_by"
        done = _wait(lambda: _events(notified, "plan_done"))
        assert done[0]["reason"] == "stop_by" and "stop time (06:00) was reached" in done[0]["why"]

    def test_the_watch_leaves_another_windows_grant_alone(self, c, app, inst, monkeypatch):
        monkeypatch.setattr(scheduler, "_run_item", TargetRun())
        limits.save(str(inst), _key(c), {"stop_by": "06:00"})
        _plan(c, ["qA1"])
        k = _key(c)
        rec = agent_session.load(str(inst), k)
        agent_session.save(str(inst), k, armed_at=1.0, grant=dict(rec["grant"], at=1.0, sm_boot="another-window"))
        assert aa.watch_grants_once(app) == []
        assert agent_session.load(str(inst), k).get("start_token"), "not this process's grant: untouched"

    def test_a_restart_says_restart_even_when_failing_the_step_ends_the_plan(self, c, app, inst, synth_folder,
                                                                            monkeypatch, notified):
        pid = _plan(c, ["qA1"])
        k = _key(c)
        agent_plans.step_update(str(inst), k, pid, 0, status="running", started=time.time())
        _drain(app)
        monkeypatch.setattr(agent_grant, "BOOT", "another-boot")      # the process that armed it is gone
        create_app(testing=True, instance_path=str(inst))
        p = agent_plans.get(str(inst), k, pid)
        assert p["status"] == "failed" and p["end_code"] == "restart", p
        done = _wait(lambda: _events(notified, "plan_done"))
        assert done[0]["reason"] == "restart" and done[0]["why"] == "SM restarted"

    def test_a_restart_found_by_the_run_record_says_restart_too(self, tmp_path, notified):
        """docs/249's path: the run registry finds a run that was in flight and fails its step."""
        inst, chip = tmp_path / "inst", "chip-0001"
        rec = agent_plans.add(str(inst), chip, title="night", steps=[{"node": "11_power_rabi", "targets": ["qB2"]}],
                              mode="auto", created_by="by_claude")
        agent_plans.update(str(inst), chip, rec["id"], status="running", started_at=time.time() - 60,
                           started_by="human:tester")
        agent_plans.step_update(str(inst), chip, rec["id"], 0, status="running")
        key = "20261003-160201-aa201c"
        (inst / "agent_runs" / key).mkdir(parents=True)
        (inst / "agent_runs" / key / "meta.json").write_text(json.dumps(
            {"key": key, "chip": chip, "node": "11_power_rabi", "targets": ["qB2"], "status": "running",
             "since": time.time() - 30, "plan_id": rec["id"], "result": None}), encoding="utf-8")
        agent_runs.Registry(inst)
        p = agent_plans.get(str(inst), chip, rec["id"])
        assert p["status"] == "failed" and p["end_code"] == "restart", p
        done = _wait(lambda: _events(notified, "plan_done"))
        assert done[0]["reason"] == "restart"


# ================================================================ Start shows the envelope

class TestStartShowsTheEnvelope:
    def test_the_envelope_names_every_value_a_start_approves(self, c, app, cal, inst):
        limits.save(str(inst), _key(c), {"stop_by": "06:00", "max_writes_per_plan": 50, "max_delta": {FAM: 0.05},
                                         "stoploss_target": 2, "stoploss_plan": 5,
                                         "webhook_url": "https://hooks.example/T/SECRET"})
        pid = _plan(c, ["qA1", "qA2"], start=False)
        e = c.get(f"/api/agent/plans/{pid}/envelope").get_json()
        assert e["envelope"] == {"mode": "auto", "stop_by": "06:00", "max_writes_per_plan": 50,
                                 "max_delta": {FAM: 0.05}, "stoploss_target": 2, "stoploss_plan": 5}
        assert abs(e["deadline"] - limits.stop_deadline(limits.load(str(inst), _key(c)), time.time())) < 5
        text = {ln["label"]: ln["text"] for ln in e["lines"]}
        assert text["stop by"].startswith("06:00 -- the first 06:00 after Start")
        assert text["max writes"].startswith("50 per plan") and text["max |Δ|"].startswith(f"{FAM} 0.05")
        assert text["stop-loss"].startswith("a target halts after 2 gate fails in a row; the plan halts after 5")
        assert "https://hooks.example/..." in text["alerts"] and "SECRET" not in json.dumps(e)
        assert [s["targets"] for s in e["steps"]] == [["qA1"], ["qA2"]] and e["warnings"] == []
        _settings(app, cal, simulate=True)
        e = c.get(f"/api/agent/plans/{pid}/envelope").get_json()
        assert e["warnings"] and "Dry run is ON" in e["warnings"][0]

    def test_a_start_arms_the_envelope_it_showed_and_keeps_it(self, c, inst):
        limits.save(str(inst), _key(c), {"stop_by": "06:00", "stoploss_plan": 4})
        pid = _plan(c, ["qA1"], start=False)
        e = c.get(f"/api/agent/plans/{pid}/envelope").get_json()
        d = c.post(f"/api/agent/plans/{pid}/start", json={"envelope": e["envelope"]}, headers=HUMAN).get_json()
        assert d["ok"], d
        p = _plan_rec(c, inst, pid)
        assert p["envelope"]["stop_by"] == "06:00" and p["envelope"]["stoploss_plan"] == 4
        assert p["envelope"]["since"] == p["started_at"], "counted from the Start click itself, as the grant is"
        assert p["envelope"]["deadline"] == limits.stop_deadline(limits.load(str(inst), _key(c)), p["started_at"])
        assert ("envelope: stop_by 06:00, max_writes_per_plan 200, max_delta none, stop-loss 3 per target / 4 per plan"
                in _journal(c, inst))

    def test_a_start_whose_envelope_changed_since_arms_nothing(self, c, inst):
        pid = _plan(c, ["qA1"], start=False)
        e = c.get(f"/api/agent/plans/{pid}/envelope").get_json()
        limits.save(str(inst), _key(c), {"max_writes_per_plan": 5})
        r = c.post(f"/api/agent/plans/{pid}/start", json={"envelope": e["envelope"]}, headers=HUMAN)
        assert r.status_code == 409 and r.get_json()["refused"] == "envelope_changed", r.get_json()
        assert r.get_json()["differs"] == [{"field": "max_writes_per_plan", "seen": 200, "now": 5}]
        assert r.get_json()["envelope"]["values"]["max_writes_per_plan"] == 5
        assert _plan_rec(c, inst, pid)["status"] == "draft"
        assert not (agent_session.load(str(inst), _key(c)) or {}).get("start_token")

    def test_the_envelope_is_read_only_and_limits_stay_the_persons(self, c, inst):
        pid = _plan(c, ["qA1"], start=False)
        assert c.post(f"/api/agent/plans/{pid}/envelope", json={}, headers=HUMAN).status_code == 405
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=AGENT).status_code == 403
        r = c.post("/api/agent/limits", json={"stoploss_plan": 0}, headers=AGENT)
        assert r.status_code == 403 and limits.load(str(inst), _key(c))["stoploss_plan"] == 8


# ================================================================ the morning summary

def _night(c, inst, monkeypatch):
    """qA1 applies, qA2's jump is held (and qA2 halts), qA3 cannot reach the host."""
    monkeypatch.setattr(scheduler, "_run_item", TargetRun(deltas={"qA1": 1e3, "qA2": 5e6}, fail={"qA3"}))
    limits.save(str(inst), _key(c), {"max_delta": {FAM: 1e5}, "stop_by": "06:00"})
    pid = _plan(c, ["qA1", "qA2", "qA3", "qA2"])
    for i, t in enumerate(("qA1", "qA2", "qA3")):
        _step(c, pid, i, t)
    return pid


class TestTheMorningSummary:
    def test_the_summary_lists_the_night_and_reads_the_same_after_a_restart(self, c, app, inst, synth_folder,
                                                                           monkeypatch):
        pid = _night(c, inst, monkeypatch)
        s = c.get("/api/agent/summary").get_json()
        assert s["plan"]["id"] == pid and s["plan"]["started_at"] and s["plan"]["envelope"]["stop_by"] == "06:00"
        assert [a["targets"] for a in s["applied"]] == [["qA1"]]
        a = s["applied"][0]
        assert a["writes"][0]["path"] == "qubits.qA1.f_01" and a["writes"][0]["new"] == a["writes"][0]["old"] + 1e3
        assert a["undo"]["state"] == "available" and a["undo"]["unit_id"]
        assert a["pre_apply_ts"], "the version the door took right before this apply"
        assert [(h["kind"], h["status"], h["targets"], h["step"]) for h in s["held"]] == [("writes", "pending", ["qA2"], 1)]
        assert [(f["target"], f["class"]) for f in s["failures"]] == [("qA3", "host_unreachable")]
        assert s["failures"][0]["what"] == "QM host unreachable at 127.0.0.1:1"
        assert [h["target"] for h in s["halted"]] == ["qA2"] and s["halted"][0]["skipped"] == 1
        assert s["end"]["reason"] == "failed" and s["counts"]["applied"] == 1 and s["counts"]["held"] == 1
        _drain(app)
        monkeypatch.setattr(agent_grant, "BOOT", "another-boot")
        app2 = create_app(testing=True, instance_path=str(inst))
        c2 = app2.test_client()
        c2.post("/load", data={"folder": str(synth_folder)})
        s2 = c2.get(f"/api/agent/summary?plan={pid}").get_json()
        for k in ("applied", "held", "failures", "halted", "end", "counts"):
            assert s2[k] == s[k], k

    def test_the_page_shows_each_section_with_its_doors(self, c, inst, monkeypatch):
        pid = _night(c, inst, monkeypatch)
        html = c.get("/agent/summary", headers={"HX-Request": "true"}).get_data(as_text=True)
        for h in ("Night summary", "Held for you", "Applied", "Failed", "Halted targets"):
            assert h in html, h
        assert f'data-plan="{pid}"' in html and 'class="ts-local" data-utc="' in html and "(1 waiting)" in html
        assert 'data-ns-act="approve"' in html and 'data-ns-act="reject"' in html
        assert 'hx-post="/auto-apply/revert"' in html and "QM host unreachable at 127.0.0.1:1" in html
        assert "its write is held for a person" in html and ">failed</span>" in html
        full = c.get("/agent/summary").get_data(as_text=True)
        assert 'id="agent-summary"' in full and "agent-summary.js" in full

    def test_undo_from_the_summary_stages_the_old_value_and_says_so(self, c, inst, monkeypatch):
        _night(c, inst, monkeypatch)
        a = c.get("/api/agent/summary").get_json()["applied"][0]
        r = c.post("/auto-apply/revert", data={"unit_id": a["undo"]["unit_id"]})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]
        tray = c.get("/api/agent/tray").get_json()
        assert [(x["path"], x["new"]) for x in tray["entries"]] == [("qubits.qA1.f_01", a["writes"][0]["old"])], \
            "the old value waits in the tray; Apply writes it (the one door, docs/107)"
        assert c.get("/api/agent/summary").get_json()["applied"][0]["undo"]["state"] == "reverted"

    def test_an_applied_value_changed_since_offers_no_undo(self, c, inst, monkeypatch):
        _night(c, inst, monkeypatch)
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.f_01", "value": "6111000000"}).status_code == 200
        assert c.get("/api/agent/summary").get_json()["applied"][0]["undo"]["state"] == "changed"

    def test_a_held_write_is_approved_from_the_summary(self, c, inst, monkeypatch):
        _night(c, inst, monkeypatch)
        held = c.get("/api/agent/summary").get_json()["held"][0]
        r = c.post(f"/api/agent/approvals/{held['id']}/approve", json={}, headers=HUMAN)
        assert r.status_code == 200 and r.get_json()["stage"]["applied"] is True, r.get_json()
        h = c.get("/api/agent/summary").get_json()["held"][0]
        assert h["status"] == "approved" and h["decided_by"] == "human:kyunghoon"
        html = c.get("/agent/summary", headers={"HX-Request": "true"}).get_data(as_text=True)
        assert "(0 waiting, 1 decided)" in html and 'data-ns-act="approve"' not in html, "a decided item is not a wait"

    def test_the_newest_started_plan_is_the_default_and_the_others_are_linked(self, c, inst, monkeypatch):
        import re
        monkeypatch.setattr(scheduler, "_run_item", TargetRun())
        p1 = _plan(c, ["qA1"], title="first night")
        _step(c, p1, 0, "qA1")
        _plan(c, ["qA2"], title="draft only", start=False)               # never started: not a night
        p2 = _plan(c, ["qA2"], title="second night")
        _step(c, p2, 0, "qA2")
        s = c.get("/api/agent/summary").get_json()
        assert s["plan"]["id"] == p2 and [o["title"] for o in s["plans"]] == ["second night", "first night"]
        html = c.get("/agent/summary", headers={"HX-Request": "true"}).get_data(as_text=True)
        others = re.search(r'<p class="ns-others muted">(.*?)</p>', html, re.S).group(1)
        assert f"/agent/summary?plan={p1}" in others and f"/agent/summary?plan={p2}" not in others
        assert not others.rstrip().endswith("·"), "no dangling separator"

    def test_with_no_started_plan_it_says_so(self, c):
        assert c.get("/api/agent/summary").get_json()["plan"] is None
        assert "No plan has been started" in c.get("/agent/summary", headers={"HX-Request": "true"}).get_data(as_text=True)


# ================================================================ the real agent.js / agent-summary.js

@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_overnight_selfcheck():
    """The REAL agent.js and agent-summary.js under jsdom (tests/agent_overnight_selfcheck.cjs):
    an auto plan's Start shows the envelope first and posts what it showed; a changed envelope
    is shown again, never armed; ask-writes starts as before; the strip links the summary; the
    summary's buttons press the right doors and re-read the page."""
    node = shutil.which("node")
    try:
        subprocess.run([node, "-e", "require('jsdom')"], check=True, capture_output=True, timeout=30)
    except Exception:
        pytest.skip("jsdom not installed")
    r = subprocess.run([node, str(_ROOT / "tests" / "agent_overnight_selfcheck.cjs")], capture_output=True, text=True,
                       encoding="utf-8", timeout=180, cwd=str(_ROOT))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.count("ok - ") >= 20, r.stdout
