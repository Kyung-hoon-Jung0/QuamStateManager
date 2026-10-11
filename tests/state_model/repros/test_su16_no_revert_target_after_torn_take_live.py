"""SU-16: after the live chip was unreadable or missing for a while, the next Apply leaves no Revert target.

Harness (two long-run sequences, each minimized to 7 ops, replayed here through
the harness's own ops): the live file torn (or the live folder deleted), a
wholesale press refused in that window (Take live / Stage), the same content
put back, EDIT, APPLY -> lands, but ``ctx["last_apply"]`` is gone: "Revert
last apply" has nothing to restore. The exact trigger inside the history store
is not isolated (a plain torn + repair + Apply keeps its target).
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


OPS_GONE = [
    ["REDO", {"n": 2}],
    ["TAKE_LIVE", {"force": True, "mode": "reapply"}],
    ["OUTSIDE", {"kind": "delete_folder", "changes": {}}],
    ["STAGE", {"k": 4, "force": False}],
    ["OUTSIDE", {"kind": "repair", "changes": {}}],
    ["EDIT_BATCH", {"rows": [["qubit_pairs.q1-2.macros.cz_flattop.phase_shift_control", "0.0954937346", 0.0954937346],
                             ["qubit_pairs.q2-3.macros.cz_flattop.phase_shift_control", "0.0954937346", 0.0954937346]],
                    "independent": True}],
]


def _replay(tmp_path, monkeypatch, seed, ops):
    from quam_state_manager.core import history
    from quam_state_manager.web import routes
    from tests import _ram_chip
    monkeypatch.setattr(routes, "_LIVE_HASH_RECHECK_S", 0.0)
    monkeypatch.setattr(routes, "_LIVE_UNREADABLE_AFTER_S", 0.0)
    monkeypatch.setattr(history, "_HIST_SEQ_RESOLVE_TTL_S", 1e9)
    state, wiring = _ram_chip.build(3, seed)
    rig = Rig(tmp_path, state, wiring)
    for name, args in ops:
        rig.run(name, dict(args))
        rig.status()                       # the every-page poll between presses
    before = rig.sm_L()
    r = rig.ca.post("/state/apply-to-live")
    assert "Applied to the live chip" in r.get_data(as_text=True)
    la = rig.ctx().get("last_apply") or {}
    assert la.get("pre_ts"), f"no Revert target after a landed Apply: {la!r}"
    assert rig.snapshot_doc(la["pre_ts"]) == before


@pytest.mark.xfail(strict=True, reason="SU-16: the pre-apply backup is lost after the missing folder")
def test_an_apply_after_a_missing_live_folder_keeps_its_revert_target(tmp_path, monkeypatch):
    _replay(tmp_path, monkeypatch, 1479, OPS_GONE)


@pytest.mark.xfail(strict=True, reason="SU-16: the pre-apply backup is lost after the torn read")
def test_an_apply_after_a_torn_read_keeps_its_revert_target(tmp_path, monkeypatch):
    _replay(tmp_path, monkeypatch, 1222, OPS)
