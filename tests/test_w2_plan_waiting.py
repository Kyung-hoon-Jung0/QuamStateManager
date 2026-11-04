"""W2-5: a plan step whose held writes were approved and written no longer
reads "waiting" (the card's count and the step line)."""
from __future__ import annotations

import inspect

from quam_state_manager.core import agent_plans


def test_a_decided_approval_leaves_the_plan_count(tmp_path):
    inst = str(tmp_path)
    rec = agent_plans.add(inst, "c", title="t", mode="ask-writes", created_by="p",
                          steps=[{"node": "12_ramsey", "targets": ["qA1"]},
                                 {"node": "12_ramsey", "targets": ["qA2"]}])
    pid = rec["id"]
    agent_plans.step_update(inst, "c", pid, 0, status="done", n_writes=3, approval="ap-1")
    agent_plans.step_update(inst, "c", pid, 1, status="done", n_writes=2, approval="ap-2")
    got = agent_plans.get(inst, "c", pid)
    assert agent_plans.counts(got)["waiting"] == 2
    agent_plans.approval_settled(inst, "c", pid, "ap-1", applied=True)
    agent_plans.approval_settled(inst, "c", pid, "ap-2", applied=False)
    got = agent_plans.get(inst, "c", pid)
    c = agent_plans.counts(got)
    assert c["waiting"] == 0 and c["applied"] == 3, c
    assert got["summary"] is None or got["summary"]["waiting"] == 0


def test_the_decide_door_settles_the_step():
    from quam_state_manager.web import agent_api
    src = inspect.getsource(agent_api.approvals_decide)
    assert "approval_settled(" in src
