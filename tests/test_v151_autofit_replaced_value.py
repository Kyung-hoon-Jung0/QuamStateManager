"""v1.5.1 SA-F8: Auto Calibrate's result names the value its write replaced.

When the writer's first live write is refused as stale, it pulls live and
stages its rows again -- by design an outside write to the same field is then
overwritten. Its result reported the PRE-pull value as ``old``, a value the
chip no longer held, so the ledger (and a later revert built from it) named
the wrong "before". Pinned for the forward write and the plan-end restore.
"""
from __future__ import annotations

import threading

import pytest

from quam_state_manager.core import hub, safe_io, working_copy
from quam_state_manager.core.autofit import synth, writer
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver

OUTSIDE = 7.7e9


@pytest.fixture
def world(tmp_path):
    chip = synth.make_sim_chip(("qA1",), (), seed=5)
    live = tmp_path / "live"
    live.mkdir()
    safe_io.write_state_wiring(live, chip.state, chip.wiring)
    wc = working_copy.create(tmp_path / "inst", live)
    store = QuamStore(wc.working_folder)
    handle = writer.ChipHandle(store=store, modifier=Modifier(store),
                               saver=Saver(store), wc=wc,
                               build_lock=threading.RLock(),
                               live_path=str(live))
    return live, handle


def _outside_write(live, value=OUTSIDE):
    state, wiring = safe_io.read_state_wiring(live)
    state["qubits"]["qA1"]["f_01"] = value
    safe_io.write_state_wiring(live, state, wiring)


def test_a_forward_write_over_an_outside_write_reports_what_it_replaced(world):
    live, handle = world
    pre_pull = handle.store.state["qubits"]["qA1"]["f_01"]
    _outside_write(live)
    out = writer.apply_rows(handle, [{"path": "qubits.qA1.f_01", "value": 5.2e9}],
                            apply_live=True, label="t")
    assert out.ok, out.error
    state, _ = safe_io.read_state_wiring(live)
    assert state["qubits"]["qA1"]["f_01"] == 5.2e9
    assert out.paths == [{"path": "qubits.qA1.f_01", "old": OUTSIDE, "new": 5.2e9}], (
        f"reported old={out.paths[0]['old']!r} (pre-pull {pre_pull!r}); the write "
        f"replaced {OUTSIDE!r}")


def test_a_plan_end_restore_over_an_outside_write_reports_what_it_replaced(world):
    live, handle = world
    pre = handle.store.state["qubits"]["qA1"]["f_01"]
    handle.store.state["qubits"]["qA1"]["f_01"] = 9.9e9
    handle.saver.save()
    working_copy.apply_to_live(handle.wc, record=hub.unrecorded(
        "replaced-value pin: the plan's own write, simulated through SM's writer"))
    _outside_write(live)
    out = writer.restore_values(handle, [{"path": "qubits.qA1.f_01", "value": pre}],
                                apply_live=True, label="end")
    assert out.ok, out.error
    state, _ = safe_io.read_state_wiring(live)
    assert state["qubits"]["qA1"]["f_01"] == pre
    assert out.paths[0]["old"] == OUTSIDE, out.paths
    assert out.paths[0]["new"] == pre


def test_without_an_outside_write_the_report_is_unchanged(world):
    live, handle = world
    before = handle.store.state["qubits"]["qA1"]["f_01"]
    out = writer.apply_rows(handle, [{"path": "qubits.qA1.f_01", "value": 5.3e9}],
                            apply_live=True, label="t")
    assert out.ok, out.error
    assert out.paths == [{"path": "qubits.qA1.f_01", "old": before, "new": 5.3e9}]
