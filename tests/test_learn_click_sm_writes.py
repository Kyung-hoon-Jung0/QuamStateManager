"""F12 (final walk): a run SM ran whose writes a person approved is credited
with those writes; click-to-apply no longer says it wrote nothing."""
from __future__ import annotations

import json

from quam_state_manager.core import approvals, story
from quam_state_manager.core.interactive_plots import learn_click
from quam_state_manager.web.app import create_app


def _setup(tmp_path, status="approved"):
    inst = tmp_path / "_inst"
    run = tmp_path / "data" / "2026-11-04" / "#5_ramsey_155831"
    run.mkdir(parents=True)
    (run / "node.json").write_text(json.dumps({"id": 5, "data": {}}), encoding="utf-8")
    ap = approvals.add(inst, "chip-a", kind="writes", node="ramsey", targets=["q1"],
                       writes=[{"path": "qubits.q1.f_01", "old": 5.0e9, "new": 5.0000015e9}],
                       reason="r", why_held="mode", actor="by_claude", plan_id=None,
                       run_key="k", run_id=5, params={})
    approvals.decide(inst, "chip-a", ap["id"], status=status, who="human")
    story.record_agent_run(inst, {"run_id": 5, "chip": "chip-a", "folder": str(run),
                                  "approval": ap["id"]})
    return inst, run


def test_approved_writes_are_the_run_evidence(tmp_path):
    inst, run = _setup(tmp_path)
    app = create_app(testing=True, instance_path=str(inst))
    assert app is not None
    ev = learn_click.evidence(run)
    assert [(e["path"], e["value"], e["old"]) for e in ev] == [("qubits.q1.f_01", 5.0000015e9, 5.0e9)]
    fig = {}
    learn_click.learn(run, fig)
    assert fig["display_only_reason"] != "this run wrote no state: click-to-apply off"


def test_a_rejected_card_wrote_nothing(tmp_path):
    inst, run = _setup(tmp_path, status="rejected")
    create_app(testing=True, instance_path=str(inst))
    assert learn_click.evidence(run) == []
