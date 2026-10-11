"""SU-06: a torn live read during the history-dir memo refresh moves the chip's ledger.

``HistoryManager.history_dir_cached`` (the memo /state/drift's ``hist_seq``
rider refreshes every 10 s) re-walks the identity ladder; if that walk meets a
torn state.json it falls to the path rung and memoizes the path-derived dir
for 10 s. The hub (``routes._hub_chip_dir``) routes WRITES through the same
memo, so an Apply in that window journals its event into a second ledger the
chip's history surfaces never read.
Harness: OUTSIDE torn + repair around the memo refresh (the long runs freeze
the memo; this repro opens the 10 s window deterministically with TTL 0).
Contract: docs/271 (every SM write recorded in the chip's journal), I8.
"""
import pytest

from tests.state_model.repros._rig import Chip


@pytest.mark.xfail(strict=True, reason="SU-06: a torn read is memoized as the chip's history dir")
def test_an_apply_after_a_torn_read_is_journaled_in_the_chips_ledger(tmp_path, monkeypatch):
    from quam_state_manager.core import history
    from quam_state_manager.web import routes
    chip = Chip(tmp_path)
    with chip.app.app_context():
        home = routes._hub_chip_dir(chip.ctx["path"]).name          # the chip's own dir
    raw = (chip.live / "state.json").read_bytes()
    monkeypatch.setattr(history, "_HIST_SEQ_RESOLVE_TTL_S", 0.0)   # the 10 s memo is due
    (chip.live / "state.json").write_bytes(raw[: len(raw) // 2])   # an experiment mid-write
    chip.c.get("/state/drift")                                      # the every-page poll
    monkeypatch.setattr(history, "_HIST_SEQ_RESOLVE_TTL_S", 10.0)
    (chip.live / "state.json").write_bytes(raw)                    # the save completes
    chip.edit("qubits.q1.T1", "3.2e-05")
    assert chip.c.post("/state/apply-to-live").status_code == 200
    snaps = sorted(p.parent.parent.name for p in (chip.inst / "history").glob("*/*/meta.json"))
    assert chip.history_dirs() == [home], (chip.history_dirs(), home, set(snaps))
