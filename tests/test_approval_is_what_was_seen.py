"""docs/254 -- an approval is what the person saw.

An ask-all "Allow run" is a promise about exactly one run: the node, its
targets and its params, read through ONE normalization (``core/run_terms``).
Any difference is a new request on its own card; the allowed one stays
unused. Every card and plan step shows its params; an empty target list, an
SM-owned param, a target of the wrong kind and a replay of another node's
run are refused before any gate; a person's Allow tells the agent the exact
call; each agent applies and undoes only its own rows.

Reconciled with docs/253 (arming is scoped to a plan): every run here goes
plan -> a person's Start -> the driver's step run -> (ask-all) a run request
-> Allow -> the run. Start arms the plan; ask-all still asks per step; the
allowed request is spent by exactly that step's run.

Fixtures are test_agent_runs's: the real chassis, the real routes, only the
node subprocess is a fake that edits the scratch state.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.core import approvals, run_terms
from quam_state_manager.web import agent_api as aa
from tests.test_agent_runs import (  # noqa: F401 -- fixtures by import
    AGENT, HUMAN, NODE_SRC, _arm, _chip, _journal, _run, app, c, cal, fake_run, inst, synth_folder)

CODEX = {"X-SM-Agent": "codex"}
PAIR_NODE_SRC = '''"""A cz-like node."""
from typing import ClassVar
from qualibrate import QualibrationNode

class Parameters(NodeParameters):
    targets_name: ClassVar[str] = "qubit_pairs"
    num_averages: int = 100

node = QualibrationNode[Parameters, Quam](name="31_cz_chevron", parameters=Parameters())

@node.run_action(skip_if=node.modes.external)
def custom_param(node):
    pass
'''


def _plan(c, steps, mode="ask-all"):
    """A terminal agent (the pins' AGENT) proposes these steps of 05_power_rabi on qA1
    (each step dict may override node / targets / params), a person sets the mode and
    presses Start. The grant covers exactly these steps, for this agent (docs/253)."""
    body = {"title": "the approvals' plan",
            "steps": [dict({"node": "05_power_rabi", "targets": ["qA1"]}, **st) for st in steps]}
    r = c.post("/api/agent/plans", json=body, headers=AGENT)
    assert r.status_code == 200, r.get_json()
    pid = r.get_json()["plan"]["id"]
    if mode:
        assert c.post(f"/api/agent/plans/{pid}/mode", json={"mode": mode}, headers=HUMAN).status_code == 200
    d = c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()
    assert d["ok"], d
    return pid


def _ask(c, pid, i, params):
    """The driver runs step i as the card shows it; ask-all files its run request."""
    r = _run(c, plan_id=pid, step=i, params=params).get_json()
    assert r.get("refused") == "awaiting_approval" and r["needs"] == "run", r
    assert r["approval"]["step"] == i and r["approval"]["plan_id"] == pid
    return r["approval"]["id"]


def _allow(c, aid):
    r = c.post(f"/api/agent/approvals/{aid}/approve", json={}, headers=HUMAN)
    assert r.status_code == 200, r.get_json()
    return r.get_json()


# ------------------------------------------------------------------ pure

class TestRunTerms:
    def test_one_reading_of_a_run(self):
        assert run_terms.key("n", ["q2", "q1", "q1"], {"a": 100.0}) == run_terms.key("n", ["q1", "q2"], {"a": 100})
        assert run_terms.key("n", ["q1"], {"a": True}) != run_terms.key("n", ["q1"], {"a": 1}), \
            "a bool is never a number"
        assert run_terms.key("n", ["q1"], {"a": "100"}) != run_terms.key("n", ["q1"], {"a": 100})
        assert run_terms.key("n", ["q1"], {"a": [1, 2]}) != run_terms.key("n", ["q1"], {"a": [2, 1]})
        assert run_terms.key("n", ["q1"], {}, "pl-1") != run_terms.key("n", ["q1"], {}, None)

    def test_differences_name_each_field_and_param(self):
        d = run_terms.differences({"node": "n", "targets": ["q1"], "params": {"load_data_id": 9}},
                                  {"node": "n", "targets": ["q1"], "params": {"num_shots": 100000}})
        assert d == [{"field": "params.load_data_id", "allowed": 9, "asked": "(not set)"},
                     {"field": "params.num_shots", "allowed": "(not set)", "asked": 100000}]
        assert run_terms.differences({"node": "n", "targets": ["q1"], "params": {}},
                                     {"node": "n", "targets": ["q1"], "params": {}}) == []

    def test_params_text(self):
        assert run_terms.params_text({}) == "node defaults"
        assert run_terms.params_text({"num_shots": 100.0, "load_data_id": 9}) == "load_data_id=9 num_shots=100"

    def test_the_dedupe_reads_runs_the_same_way(self, tmp_path):
        """`{"a": True} == {"a": 1}` in Python: two different asks were one request."""
        a1, new1 = approvals.file_run_request(tmp_path, "k", node="n", targets=["q1"], params={"a": True},
                                              reason="r", actor="by_claude")
        a2, new2 = approvals.file_run_request(tmp_path, "k", node="n", targets=["q1"], params={"a": 1},
                                              reason="r", actor="by_claude")
        assert new1 and new2 and a1["id"] != a2["id"]
        a3, new3 = approvals.file_run_request(tmp_path, "k", node="n", targets=["q1"], params={"a": True},
                                              reason="r", actor="by_claude")
        assert not new3 and a3["id"] == a1["id"]
        assert approvals.find_pending_run(tmp_path, "k", node="n", targets=["q1"], params={"a": 1})["id"] == a2["id"]


# ------------------------------------------------------------------ D-05

class TestAnAllowCoversExactlyOneRun:
    def test_an_allow_for_one_params_set_is_never_spent_on_another(self, c, inst, fake_run):
        """D-05: a person allowed `{load_data_id: 9}`; the agent spent it on `{num_shots: 100000}`.
        Under docs/253 both runs are steps of the armed plan, so the grant covers each -- the
        approval is what tells them apart."""
        pid = _plan(c, [{"params": {"num_averages": 9}}, {"params": {"num_shots": 100000}}])
        aid = _ask(c, pid, 0, {"num_averages": 9})
        assert fake_run.calls == [], "Start armed the plan; ask-all still waits for the person's Allow"
        _allow(c, aid)
        r = _run(c, plan_id=pid, step=1, approval_id=aid, params={"num_shots": 100000}).get_json()
        assert r.get("refused") == "awaiting_approval", r
        assert fake_run.calls == [], "nothing ran on an approval for another run"
        assert approvals.get(str(inst), _chip(c), aid)["params"] == {"num_averages": 9}
        fields = {d["field"] for d in r["differs"]}
        assert fields == {"params.num_averages", "params.num_shots", "step"}
        assert r["not_covered_by"]["id"] == aid and r["approval"]["id"] != aid
        assert r["approval"]["params"] == {"num_shots": 100000} and r["approval"]["step"] == 1, \
            "what was asked is its own request, on its own step"
        rec = approvals.get(str(inst), _chip(c), aid)
        assert rec["status"] == "approved" and not rec.get("used_by_run"), "the allowed one stays unspent"
        # off the card altogether: the grant refuses it first, and no request is filed for it
        n = len(approvals.load(str(inst), _chip(c)))
        r = _run(c, plan_id=pid, step=0, approval_id=aid, params={"num_shots": 5})
        assert r.status_code == 409 and r.get_json()["refused"] == "not_in_plan"
        assert len(approvals.load(str(inst), _chip(c))) == n
        # the exact run the person allowed still runs, once
        r = _run(c, plan_id=pid, step=0, approval_id=aid, params={"num_averages": 9.0}).get_json()
        assert r["ok"] and r["status"] == "done", r
        assert fake_run.calls[0]["param_overrides"] == {"num_averages": 9.0}
        assert approvals.get(str(inst), _chip(c), aid)["used_by_run"] == r["key"]

    def test_another_step_a_dropped_param_or_another_plan_is_another_run(self, c, inst, fake_run):
        pid = _plan(c, [{"params": {"num_averages": 9}}, {"params": {"num_averages": 9}}, {},
                        {"params": {"num_averages": True}}])
        aid = _ask(c, pid, 0, {"num_averages": 9})
        _allow(c, aid)
        # the same terms on ANOTHER step: the card said "step 0", so step 1 asks for itself
        r = _run(c, plan_id=pid, step=1, approval_id=aid, params={"num_averages": 9}).get_json()
        assert r.get("refused") == "awaiting_approval", r
        assert r["differs"] == [{"field": "step", "allowed": 0, "asked": 1}] and r["approval"]["step"] == 1
        r = _run(c, plan_id=pid, step=2, approval_id=aid).get_json()                     # params dropped
        assert r.get("refused") == "awaiting_approval", r
        assert {d["field"] for d in r["differs"]} == {"params.num_averages", "step"}
        r = _run(c, plan_id=pid, step=3, approval_id=aid, params={"num_averages": True}).get_json()
        assert r["refused"] == "awaiting_approval" and "params.num_averages" in {d["field"] for d in r["differs"]}, \
            "True is not 9 and not 1"
        assert fake_run.calls == []
        # another plan: an approval never crosses into the next plan, even for the same terms --
        # the first plan's end expired it, and the binding names the plan besides
        assert run_terms.differences({"node": "n", "targets": ["qA1"], "params": {}, "plan_id": pid},
                                     {"node": "n", "targets": ["qA1"], "params": {}, "plan_id": "pl-next"}) == [
            {"field": "plan_id", "allowed": pid, "asked": "pl-next"}]
        assert c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN).status_code == 200
        pid2 = _plan(c, [{"params": {"num_averages": 9}}])
        r = _run(c, plan_id=pid2, step=0, approval_id=aid, params={"num_averages": 9}).get_json()
        assert r.get("refused") == "awaiting_approval" and "not yet used" in r["how"], r
        assert approvals.get(str(inst), _chip(c), aid)["status"] == "expired"
        assert fake_run.calls == []

    def test_two_steps_with_the_same_terms_are_two_requests(self, c, inst, fake_run):
        """"The same ask twice is one request" -- for the same STEP. Two steps that run the same
        terms are two runs, each with its own card ("plan · step i") and its own Allow."""
        pid = _plan(c, [{"params": {"num_averages": 9}}] * 2)
        a0 = _ask(c, pid, 0, {"num_averages": 9})
        assert _ask(c, pid, 0, {"num_averages": 9}) == a0, "step 0 asked twice: one request"
        a1 = _ask(c, pid, 1, {"num_averages": 9})
        assert a1 != a0
        assert [a["step"] for a in approvals.pending(str(inst), _chip(c))] == [0, 1]

    def test_a_plans_run_requests_end_with_its_arming(self, c, inst, fake_run):
        """docs/253 ended the grant with the plan; a card still offering "Allow run" for a step
        of a plan that can no longer run would promise a run that cannot happen."""
        pid = _plan(c, [{"params": {"num_averages": 9}}, {"params": {"num_averages": 7}}])
        a0 = _ask(c, pid, 0, {"num_averages": 9})
        a1 = _ask(c, pid, 1, {"num_averages": 7})
        _allow(c, a0)                                    # allowed, never spent
        assert {a["id"] for a in c.get("/api/agent/approvals").get_json()["pending"]} == {a1}
        assert c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN).status_code == 200
        recs = {a["id"]: a for a in approvals.load(str(inst), _chip(c))}
        assert recs[a0]["status"] == "expired" and recs[a1]["status"] == "expired", recs
        assert "cancelled" in recs[a1]["note"]
        assert c.get("/api/agent/approvals").get_json()["waiting"] == 0
        assert c.post(f"/api/agent/approvals/{a1}/approve", json={}, headers=HUMAN).status_code == 404
        assert fake_run.calls == []

    def test_a_plans_writes_approvals_outlive_its_arming(self, c, inst, fake_run):
        """Only RUN requests expire: values a run produced stay the person's to apply."""
        pid = _plan(c, [{}], mode="ask-writes")
        r = _run(c, plan_id=pid, step=0).get_json()
        wid = r["result"]["approval"]["id"]
        assert c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["status"] == "done"
        assert approvals.get(str(inst), _chip(c), wid)["status"] == "pending"

    def test_a_spent_approval_and_a_held_chain_say_so_in_the_mode_they_are_in(self, c, inst, fake_run):
        """Measured on the rig: a second press of a spent approval was answered "(mode ask-writes)"
        while the chip was in ask-all."""
        pid = _plan(c, [{"params": {"num_averages": 9}}] * 3)
        aid = _ask(c, pid, 0, {"num_averages": 9})
        _allow(c, aid)
        r = _run(c, plan_id=pid, step=0, approval_id=aid, params={"num_averages": 9}).get_json()
        assert r["ok"] and r["result"]["approval"], "ask-all: the writes wait too"
        r = _run(c, plan_id=pid, step=1, approval_id=aid, params={"num_averages": 9}).get_json()
        assert r["refused"] == "awaiting_approval" and r["needs"] == "writes" and "(mode ask-all)" in r["how"], r
        wid = [a for a in approvals.pending(str(inst), _chip(c)) if a["kind"] == "writes"][0]["id"]
        assert c.post(f"/api/agent/approvals/{wid}/reject", json={}, headers=HUMAN).status_code == 200
        r = _run(c, plan_id=pid, step=1, approval_id=aid, params={"num_averages": 9}).get_json()
        assert r["refused"] == "awaiting_approval" and "not yet used" in r["how"], "consumed by the one run"
        assert len(fake_run.calls) == 1

    def test_the_approval_card_and_the_recent_list_carry_the_params(self, c, inst, fake_run):
        pid = _plan(c, [{"params": {"num_averages": 9}}])
        aid = _ask(c, pid, 0, {"num_averages": 9})
        card = c.get("/api/agent/chat/cards").get_json()["live"]["approvals"][0]
        assert card["id"] == aid and card["params"] == {"num_averages": 9}
        _allow(c, aid)
        recent = c.get("/api/agent/approvals").get_json()["recent"]
        assert recent[-1]["params"] == {"num_averages": 9} and recent[-1]["decided_by"] == "human:user-a"

    def test_the_run_card_carries_its_params(self, c, inst, fake_run):
        pid = _plan(c, [{"params": {"num_averages": 3}}], mode="ask-writes")
        r = _run(c, plan_id=pid, step=0, params={"num_averages": 3}).get_json()
        assert r["params"] == {"num_averages": 3}
        runs = c.get("/api/agent/chat/cards").get_json()["live"]["runs"]
        assert runs[-1]["params"] == {"num_averages": 3}


# ------------------------------------------------------------------ C-04 / C-30

class TestAllowTellsTheExactCall:
    def test_allow_run_tells_the_agent_the_exact_arguments(self, c, inst, fake_run, monkeypatch):
        told = []
        monkeypatch.setattr(aa, "_tell_agent", lambda chip, msg, plan_id=None: told.append((msg, plan_id)) or True)
        pid = _plan(c, [{"params": {"num_averages": 9}}])
        aid = _ask(c, pid, 0, {"num_averages": 9})
        d = _allow(c, aid)
        assert d["agent_told"] is True and len(told) == 1
        assert told[0][1] == pid, "told for the plan the request belongs to"
        call = json.loads(told[0][0].split("(and your reason): ", 1)[1].rsplit(". The approval covers", 1)[0])
        assert call == {"node": "05_power_rabi", "targets": ["qA1"], "params": {"num_averages": 9},
                        "approval_id": aid, "plan_id": pid, "step": 0}
        # and that call, as told, runs
        r = _run(c, **call).get_json()
        assert r["ok"] and r["status"] == "done", r

    def test_the_journal_names_who_allowed_what(self, c, inst, fake_run):
        pid = _plan(c, [{"params": {"num_averages": 9}}])
        aid = _ask(c, pid, 0, {"num_averages": 9})
        _allow(c, aid)
        j = _journal(c, inst)
        assert "asked to run `05_power_rabi` on qA1 (num_averages=9)" in j
        assert "human:user-a allowed the run of `05_power_rabi` on qA1 (num_averages=9)" in j

    def test_an_edited_value_is_named_in_the_approve_line(self, c, inst, fake_run):
        """C-30: the line named neither the approver nor the value the person changed."""
        _arm(c)
        r = _run(c).get_json()
        ap = c.get("/api/agent/approvals").get_json()["pending"][0]
        proposed = ap["writes"][0]["new"]
        d = c.post(f"/api/agent/approvals/{ap['id']}/approve",
                   json={"writes": [dict(ap["writes"][0], new=6.3e9)]}, headers=HUMAN).get_json()
        assert d["ok"], d
        j = _journal(c, inst)
        assert "human:user-a approved 1 write(s) from `05_power_rabi`" in j
        assert f"edited before writing: `qubits.qA1.f_01` proposed {proposed} -> written {6.3e9}" in j
        assert r["ok"]

    def test_an_unedited_approve_names_no_edit(self, c, inst, fake_run):
        _arm(c)
        _run(c)
        ap = c.get("/api/agent/approvals").get_json()["pending"][0]
        assert c.post(f"/api/agent/approvals/{ap['id']}/approve", json={"writes": ap["writes"]},
                      headers=HUMAN).get_json()["ok"]
        assert "edited before writing" not in _journal(c, inst)


class TestThePlanStepSaysWhatItWaitsOn:
    def test_a_run_request_shows_on_its_step_until_the_run_takes_it(self, c, inst, fake_run, monkeypatch):
        monkeypatch.setattr(aa, "_tell_agent", lambda chip, msg, plan_id=None: True)
        from quam_state_manager.web import chat_api
        monkeypatch.setattr(chat_api, "session_open", lambda cur: True)
        monkeypatch.setattr(chat_api, "_manager", lambda: type("M", (), {"get": lambda s, k: None,
                                                                      "send": lambda s, k, m: {}})())
        p = c.post("/api/agent/plans", json={"title": "t", "steps": [
            {"node": "05_power_rabi", "targets": ["qA1"], "params": {"num_averages": 9}}]}, headers=AGENT).get_json()
        pid = p["plan"]["id"]
        assert p["plan"]["steps"][0]["params"] == {"num_averages": 9}
        assert c.post(f"/api/agent/plans/{pid}/mode", json={"mode": "ask-all"}, headers=HUMAN).status_code == 200
        assert c.post(f"/api/agent/plans/{pid}/start", json={}, headers=HUMAN).get_json()["ok"]
        r = _run(c, params={"num_averages": 9}, plan_id=pid, step=0).get_json()
        aid = r["approval"]["id"]
        assert r["approval"]["step"] == 0
        step = c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["steps"][0]
        assert step["request"] == {"id": aid, "status": "pending", "decided_by": None, "params": {"num_averages": 9}}
        _allow(c, aid)
        step = c.get(f"/api/agent/plans/{pid}").get_json()["plan"]["steps"][0]
        assert step["request"]["status"] == "approved" and step["request"]["decided_by"] == "human:user-a"
        r = _run(c, params={"num_averages": 9}, plan_id=pid, step=0, approval_id=aid).get_json()
        assert r["ok"], r
        plan = c.get(f"/api/agent/plans/{pid}").get_json()["plan"]
        assert "request" not in plan["steps"][0] and plan["steps"][0]["status"] == "done"


# ------------------------------------------------------------------ the request

class TestTheRequestIsWhatACardCanShow:
    def test_an_empty_target_list_is_refused(self, c, inst, fake_run):
        """A-13 / D-12: targets=[] passed every gate and the node ran on its own defaults."""
        _arm(c)
        r = _run(c, targets=[])
        assert r.status_code == 400 and r.get_json()["refused"] == "no_targets"
        assert "qA1" in r.get_json()["known"]
        assert _run(c, targets="  ").status_code == 400
        assert fake_run.calls == []

    def test_sm_owned_params_are_refused_not_dropped(self, c, inst, fake_run):
        _arm(c)
        for bad in ({"simulate": False}, {"qubits": ["qA1"]}, {"qubit_pairs": []}, {"targets": ["qA1"]}):
            r = _run(c, params=dict(bad, num_averages=5))
            assert r.status_code == 400 and r.get_json()["refused"] == "reserved_params", (bad, r.get_json())
            assert r.get_json()["params"] == list(bad)
        assert fake_run.calls == []

    def test_a_qubit_for_a_pair_node_and_a_pair_for_a_qubit_node(self, c, inst, cal, fake_run):
        (cal / "31_cz_chevron.py").write_text(PAIR_NODE_SRC, encoding="utf-8")
        r = _run(c, node="31_cz_chevron", targets=["qA1"])
        assert r.status_code == 400 and r.get_json()["refused"] == "wrong_target_kind" and r.get_json()["wrong"] == ["qA1"]
        r = _run(c, targets=["qA1-A2"])
        assert r.status_code == 400 and r.get_json()["refused"] == "wrong_target_kind"
        # the request check comes FIRST: under an armed plan a malformed request is still 400, never a gate
        pid = _plan(c, [{"node": "31_cz_chevron", "targets": ["qA1-A2"]}], mode="ask-writes")
        for kw in ({"node": "31_cz_chevron", "targets": ["qA1"]}, {"targets": ["qA1-A2"]}):
            r = _run(c, plan_id=pid, step=0, **kw)
            assert r.status_code == 400 and r.get_json()["refused"] == "wrong_target_kind", (kw, r.get_json())
        assert fake_run.calls == []
        assert _run(c, plan_id=pid, step=0, node="31_cz_chevron", targets=["qA1-A2"]).get_json()["ok"]

    def test_a_replay_of_another_nodes_run_is_refused(self, c, inst, fake_run, monkeypatch):
        runs = {9: {"run_id": 9, "experiment_name": "03_resonator_spectroscopy"},
                10: {"run_id": 10, "experiment_name": "05_power_rabi"}}

        class DS:
            def rescan_if_stale(self):
                pass

            def get_run(self, rid):
                return runs.get(rid)

            def list_runs(self, **kw):
                return []
        monkeypatch.setattr(aa, "_ds", lambda: DS())
        pid = _plan(c, [{"params": {"load_data_id": 10}}], mode="ask-writes")
        r = _run(c, plan_id=pid, step=0, params={"load_data_id": 9})
        assert r.status_code == 400 and r.get_json()["refused"] == "replay_other_node"
        assert r.get_json()["run_experiment"] == "03_resonator_spectroscopy"
        assert _run(c, plan_id=pid, step=0, params={"load_data_id": "nine"}).get_json()["refused"] == "bad_load_data_id"
        assert fake_run.calls == []
        assert _run(c, plan_id=pid, step=0, params={"load_data_id": 10}).get_json()["ok"], "its own run replays"

    def test_node_not_found_lists_every_node_and_the_closest(self, c, inst, cal, fake_run):
        """A-20: the refusal listed 40 of 187 names."""
        for i in range(50):
            (cal / f"{60 + i}_extra_node.py").write_text(NODE_SRC.replace("05_power_rabi", f"{60 + i}_extra_node"),
                                                       encoding="utf-8")
        _arm(c)
        r = _run(c, node="05_power_rabbi").get_json()
        assert r["refused"] == "node_not_found"
        assert len(r["available"]) >= 52 and "109_extra_node" in r["available"]
        assert r["closest"][0] == "05_power_rabi"

    def test_a_plan_names_real_nodes_and_runnable_steps(self, c, inst, fake_run):
        r = c.post("/api/agent/plans", json={"title": "t", "steps": [{"node": "nosuchnode_xyz", "targets": ["qA1"]}]},
                   headers=AGENT)
        assert r.status_code == 400 and r.get_json()["refused"] == "node_not_found"
        r = c.post("/api/agent/plans", json={"title": "t", "steps": [
            {"node": "05_power_rabi", "targets": ["qA1"], "params": {"simulate": True}}]}, headers=AGENT)
        assert r.status_code == 400 and r.get_json()["refused"] == "reserved_params"
        r = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1 simulate=false"}, headers=HUMAN)
        assert r.status_code == 400 and r.get_json()["refused"] == "reserved_params"
        # a string target list is read as the targets it names, and the node keeps the folder's own name
        r = c.post("/api/agent/plans", json={"title": "t", "steps": [{"node": "05_power", "targets": "qA1"}]},
                   headers=AGENT)
        assert r.status_code == 200, r.get_json()
        assert r.get_json()["plan"]["steps"][0]["node"] == "05_power_rabi"

    def test_cancelling_a_finished_plan_is_not_a_second_line(self, c, inst):
        pid = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi qA1"}, headers=HUMAN).get_json()["plan"]["id"]
        assert c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN).status_code == 200
        r = c.post(f"/api/agent/plans/{pid}/cancel", json={}, headers=HUMAN)
        assert r.status_code == 409 and r.get_json()["plan"]["status"] == "cancelled"
        assert _journal(c, inst).count("cancelled by") == 1

    def test_run_line_with_no_target_is_400_not_500(self, c):
        """C-08 (fixed in docs/247; pinned again on the door this doc changed)."""
        r = c.post("/api/agent/plans", json={"run_line": "/run 05_power_rabi"}, headers=HUMAN)
        assert r.status_code == 400 and "names no target" in r.get_json()["error"]


# ------------------------------------------------------------------ A-08

class TestEachAgentPressesForItsOwnRows:
    def _stage(self, c, path, value, headers):
        r = c.post("/field/edit", data={"dot_path": path, "value": value}, headers=headers)
        assert r.status_code == 200, r.get_data(as_text=True)[:300]

    def test_an_agent_never_applies_another_agents_rows(self, c, inst, synth_folder):
        before = (synth_folder / "state.json").read_bytes()
        self._stage(c, "qubits.qA1.T1", "3e-6", AGENT)
        self._stage(c, "qubits.qA1.T2ramsey", "4e-6", CODEX)
        for who, other in ((AGENT, "by_codex"), (CODEX, "by_claude")):
            r = c.post("/state/apply-to-live", data={"seen_changes": "2"}, headers={**who, "Accept": "application/json"})
            assert r.status_code == 409 and r.get_json()["conflict"] == "agent_groups", r.get_data(as_text=True)[:300]
            assert r.get_json()["actors"] == [other]
            assert f"staged by {other[3:]};" in r.get_json()["message"], "the person reads codex, not by_codex"
        assert (synth_folder / "state.json").read_bytes() == before, "nothing reached the chip"
        assert c.get("/api/agent/chip").get_json()["pending"] == 2
        # a person's Apply speaks for every row, as before
        r = c.post("/state/apply-to-live", data={"seen_changes": "2"}, headers={**HUMAN, "Accept": "application/json"})
        assert r.status_code == 200
        live = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert live["qubits"]["qA1"]["T1"] == 3e-6 and live["qubits"]["qA1"]["T2ramsey"] == 4e-6

    def test_its_own_rows_alone_still_apply(self, c, inst, synth_folder):
        self._stage(c, "qubits.qA1.T1", "3e-6", AGENT)
        r = c.post("/state/apply-to-live", data={"seen_changes": "1"}, headers={**AGENT, "Accept": "application/json"})
        assert r.status_code == 200
        assert json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))["qubits"]["qA1"]["T1"] == 3e-6

    def test_undo_and_undo_mine_take_back_only_the_agents_own(self, c, inst):
        self._stage(c, "qubits.qA1.T1", "3e-6", AGENT)
        self._stage(c, "qubits.qA1.T2ramsey", "4e-6", CODEX)
        r = c.post("/undo", data={}, headers=AGENT)
        assert r.status_code == 409 and r.get_json()["refused"] == "agent_group" and r.get_json()["actor"] == "by_codex"
        d = c.post("/api/agent/undo-mine", json={}, headers=AGENT).get_json()
        assert d["reverted"] == [] and d["stopped_at"] == {"path": "qubits.qA1.T2ramsey", "actor": "by_codex"}
        assert c.get("/api/agent/chip").get_json()["pending"] == 2
        d = c.post("/api/agent/undo-mine", json={}, headers=CODEX).get_json()
        assert d["reverted"] == ["qubits.qA1.T2ramsey"]
        d = c.post("/api/agent/undo-mine", json={}, headers=AGENT).get_json()
        assert d["reverted"] == ["qubits.qA1.T1"] and d["pending"] == 0

    def test_an_agent_run_whose_door_meets_another_agents_rows_parks_its_writes(self, c, inst, fake_run):
        _arm(c, mode="auto")                     # docs/253: the mode is the plan's, set before Start
        self._stage(c, "qubits.qA1.T1", "3e-6", CODEX)
        r = _run(c).get_json()
        res = r["result"]
        assert res["applied"] is False and res["approval"], r
        assert "staged by codex" in res["why_held"], res["why_held"]


# ------------------------------------------------------------------ the cards (jsdom)

def test_every_card_shows_its_params_selfcheck():
    """tests/agent_params_selfcheck.cjs: the real agent.js under jsdom -- params on the
    approval cards, the plan step rows and the run cards; the step's run request; the
    Allow-run toast says whether the agent heard it."""
    import shutil
    import subprocess
    from pathlib import Path
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run([node, str(root / "tests" / "agent_params_selfcheck.cjs")], capture_output=True,
                       text=True, encoding="utf-8", timeout=120, cwd=str(root))
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0 and " 0 failed" in r.stdout, r.stdout[-2000:] + r.stderr[-1000:]


# ------------------------------------------------------------------ A-08, the bridge's words

class TestTheBridgeSaysWhoseRowsTheyWere:
    @pytest.fixture
    def link(self, monkeypatch):
        from quam_state_manager import mcp
        from tests.test_mcp_bridge import CHIP, FakeLink
        fl = FakeLink({("GET", "/api/agent/chip"): (200, {**CHIP[1], "pending": 1}),
                       ("GET", "/api/agent/tray"): (200, {"ok": True, "count": 1, "seen_changes": 1,
                                                          "entries": [{"path": "qubits.q1.T1"}]}),
                       ("POSTJ", "/api/agent/journal"): (200, {"ok": True})})
        monkeypatch.setattr(mcp, "_link", fl)
        for k, v in {"_seen": None, "_chip": None, "_seen_key": None, "_seen_token": None, "_seen_sig": None,
                     "_seen_paths": [], "_pin_key": None}.items():
            monkeypatch.setattr(mcp, k, v)
        return fl

    def test_another_agents_rows_are_named_not_called_a_humans_edit(self, link):
        from quam_state_manager import mcp
        mcp.t_tray({})
        link.answers[("POST", "/state/apply-to-live")] = (409, {"ok": False, "conflict": "agent_groups",
                                                               "actors": ["by_codex"], "paths": ["qubits.q1.T2"]})
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and r["refused"]["actors"] == ["by_codex"]
        assert "another agent" in r["how"] and "human edited" not in r["how"]

    def test_rows_another_press_took_are_not_nothing_staged(self, link):
        from quam_state_manager import mcp
        mcp.t_tray({})
        link.answers[("GET", "/api/agent/chip")] = (200, {"ok": True, "loaded": True, "name": "c", "chip_token": "tok",
                                                          "pending": 0, "live_diverged": False})
        r = mcp.t_apply_to_live({})
        assert r["applied"] is False and "another press" in r["note"] and r["seen"] == ["qubits.q1.T1"]
        assert not any(c[1] == "/state/apply-to-live" for c in link.calls)
        assert mcp.t_apply_to_live({})["note"] == "nothing staged", "said once, then the picture is empty"

    def test_after_its_own_apply_an_empty_tray_is_nothing_staged(self, link):
        from quam_state_manager import mcp

        def door(d):
            link.answers[("GET", "/api/agent/chip")] = (200, {"ok": True, "loaded": True, "name": "c",
                                                              "chip_token": "tok", "pending": 0, "live_diverged": False})
            return 200, {"ok": True}
        link.answers[("POST", "/state/apply-to-live")] = door
        mcp.t_tray({})
        assert mcp.t_apply_to_live({})["applied"] is True
        assert mcp.t_apply_to_live({})["note"] == "nothing staged"


# ------------------------------------------------------------------ the words, after docs/253

class TestTheGateSaysStartNotArm:
    def test_the_token_gate_points_at_start_on_a_plan(self):
        """There is no Arm any more (docs/253): the gate's own text named a button that is gone."""
        from quam_state_manager.core import agent_runs

        class _Node:
            name, kind, has_hook, targets_name = "05_power_rabi", "node", True, "qubits"
        r = agent_runs.check_gates(agent_runs.RunRequest(node="05_power_rabi", targets=["qA1"]), session=None,
                                   lim={}, settings={"env_python": "py", "calibrations_folder": "cal"}, pending=[],
                                   human=None, queue_state={}, own_running=False, run_active=None,
                                   node_info=_Node(), available=[])
        assert r["refused"] == "no_start_token"
        assert "Arm" not in r["how"] and "plan_propose" in r["how"] and "Start" in r["how"], r["how"]

    def test_the_gate_vocabulary_names_run_nodes_plan_refusals(self):
        from quam_state_manager.core import agent_runs
        assert {"not_in_plan", "not_the_driver"} <= set(agent_runs.GATES)
