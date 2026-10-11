"""SU-16: after a torn live read and a refused Take live, the next Apply leaves no Revert target.

Harness (synthetic seed 1222, minimized to 7 ops, replayed here through the
harness's own ops): a refused pulse delete, an Apply with nothing pending,
EDIT, OUTSIDE torn, TAKE_LIVE (refused: the live file is unreadable), OUTSIDE
repair, APPLY -> lands, but ``ctx["last_apply"]`` is gone: "Revert last apply"
has nothing to restore.
Contract: I6 / docs/65 -- every Apply takes (or content-matches) a pre-apply
snapshot and ``last_apply.pre_ts`` names it.
"""
import pytest

from tests.state_model.ops import Rig

OPS = [
    ["PULSE", {"action": "delete", "path": "qubits.q3.xy.operations.x90_DragCosine",
               "new_name": "p4662x1", "force": False}],
    ["APPLY", {"window": "b", "seen": "fresh", "json_accept": True}],
    ["EDIT_CELL", {"path": "qubits.q3.resonator.operations.readout.threshold", "text": "0.00134756251",
                   "value": 0.00134756251, "window": "a", "agent": False}],
    ["OUTSIDE", {"kind": "torn", "changes": {}}],
    ["TAKE_LIVE", {"force": False, "mode": "discard"}],
    ["OUTSIDE", {"kind": "repair", "changes": {}}],
]


@pytest.mark.xfail(strict=True, reason="SU-16: the pre-apply backup is lost after the torn read")
def test_an_apply_after_a_torn_read_keeps_its_revert_target(tmp_path, monkeypatch):
    from quam_state_manager.core import history
    from quam_state_manager.web import routes
    from tests import _ram_chip
    monkeypatch.setattr(routes, "_LIVE_HASH_RECHECK_S", 0.0)
    monkeypatch.setattr(routes, "_LIVE_UNREADABLE_AFTER_S", 0.0)
    monkeypatch.setattr(history, "_HIST_SEQ_RESOLVE_TTL_S", 1e9)
    state, wiring = _ram_chip.build(3, 1222)
    rig = Rig(tmp_path, state, wiring)
    for name, args in OPS:
        rig.run(name, dict(args))
        rig.status()                       # the every-page poll between presses
    before = rig.sm_L()
    r = rig.ca.post("/state/apply-to-live")
    assert "Applied to the live chip" in r.get_data(as_text=True)
    la = rig.ctx().get("last_apply") or {}
    assert la.get("pre_ts"), f"no Revert target after a landed Apply: {la!r}"
    assert rig.snapshot_doc(la["pre_ts"]) == before
