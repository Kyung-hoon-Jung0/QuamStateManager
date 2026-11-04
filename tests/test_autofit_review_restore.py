"""Review autonomy puts the chip back, on every way a plan can end.

The shape these tests reproduce: a node of the current generation saves its
values into the live state and records NO ``patches`` in node.json, so the
only record of what a run wrote is the chip before and after it. The engine
reads that difference; a gate-rejected value and an unjudged run (node
failed, aborted) are put back in every autonomy; review autonomy restores
the pre-plan values at plan end, after an Abort, and after a crash, CAS-
guarded so an outside save made after the plan wrote a path is kept, and the
board says what was restored.
"""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.core.autofit import synth
from quam_state_manager.core.autofit.auditor import Auditor
from quam_state_manager.core.autofit.engine import PlanEngine, StepRunResult
from quam_state_manager.core.autofit.plan import validate_plan
from quam_state_manager.core.autofit.writer import RealWriter

from tests.test_autofit_e2e import STEPS_1Q, _mk_world, _ReconcilingBackend


class _NoPatchBackend(_ReconcilingBackend):
    """The chassis boundary as the real scheduler presents it: the node saved
    the live files, its node.json has no ``patches``. ``fail_steps`` makes the
    item end failed AFTER the node saved (what a node whose own analysis fails
    does), ``abort_on`` sets the plan's abort while that step runs."""

    def __init__(self, *a, fail_steps=(), abort_on=None, engine_box=None,
                 **kw):
        super().__init__(*a, **kw)
        self.fail_steps = set(fail_steps)
        self.abort_on = abort_on
        self.engine_box = engine_box if engine_box is not None else {}

    def run_step(self, step, targets, params, attempt, abort):
        res = super().run_step(step, targets, params, attempt, abort)
        if res.run is not None:
            res.run = dict(res.run, patches=[])
        if step.id == self.abort_on:
            self.engine_box["eng"].abort_event.set()
            return StepRunResult(status="aborted")
        if step.id in self.fail_steps:
            return StepRunResult(status="failed",
                                 error="item ended failed")
        return res


def _plan(tmp_path, steps, *, autonomy="review", corruption=None,
          before_restore=None, crash_after=None, **bk):
    chip, live, inst, handle, reconcile = _mk_world(tmp_path)
    pre = json.loads(json.dumps(safe_io.read_state_wiring(live)[0]))
    box: dict = {}
    backend = _NoPatchBackend(chip, tmp_path / "data", live, seed=3,
                              corruption_plan=corruption, reconcile=reconcile,
                              engine_box=box, **bk)
    writer = RealWriter(handle, apply_live=True)
    plan = validate_plan({"name": "t", "targets_kind": "qubits",
                          "autonomy": autonomy, "targets": ["qA1", "qA2"],
                          "steps": steps})
    eng = PlanEngine(inst, plan, ["qA1", "qA2"], backend, writer,
                     Auditor({"provider": "off"}), autonomy=autonomy)
    box["eng"] = eng
    if before_restore is not None:
        orig = eng._end_restore

        def _hooked():
            before_restore(live, reconcile)
            orig()
        eng._end_restore = _hooked
    if crash_after is not None:
        orig_step = eng._run_step

        def _crashing(step, alive, queue=None):
            orig_step(step, alive, queue)
            if step.id == crash_after:
                raise RuntimeError("planted crash")
        eng._run_step = _crashing
    eng.start()
    eng._thread.join(90)
    assert not eng.is_running()
    return eng.status(), pre, live


def _q(state, t, *keys):
    node = state["qubits"][t]
    for k in keys:
        node = node[k]
    return node


def _live(live):
    return safe_io.read_state_wiring(live)[0]


PATHS = [("f_01",), ("T1",), ("xy", "operations", "x180", "amplitude")]


def _assert_back(live, pre):
    now = _live(live)
    for t in ("qA1", "qA2"):
        for keys in PATHS:
            assert _q(now, t, *keys) == pytest.approx(_q(pre, t, *keys)), \
                (t, keys)


class TestReviewRestoresWithoutNodePatches:
    def test_plan_end(self, tmp_path):
        st, pre, live = _plan(tmp_path, STEPS_1Q)
        assert st["status"] == "done"
        _assert_back(live, pre)
        assert st["restore"]["status"] == "restored"
        assert st["restore"]["restored"] > 0

    def test_abort(self, tmp_path):
        st, pre, live = _plan(tmp_path, STEPS_1Q, abort_on="ramsey")
        assert st["status"] == "aborted"
        _assert_back(live, pre)
        assert st["restore"]["status"] == "restored"

    def test_crash(self, tmp_path):
        st, pre, live = _plan(tmp_path, STEPS_1Q, crash_after="power_rabi")
        assert st["status"] == "failed"
        _assert_back(live, pre)
        assert st["restore"]["status"] == "restored"

    def test_outside_save_after_the_plan_is_kept_and_named(self, tmp_path):
        outside = 4.321e9

        def _outside(live, reconcile):
            state, wiring = safe_io.read_state_wiring(live)
            state["qubits"]["qA1"]["f_01"] = outside
            safe_io.write_state_wiring(live, state, wiring)
            reconcile()

        st, pre, live = _plan(tmp_path, STEPS_1Q, before_restore=_outside)
        now = _live(live)
        assert _q(now, "qA1", "f_01") == outside
        assert _q(now, "qA2", "f_01") == pytest.approx(_q(pre, "qA2", "f_01"))
        assert st["restore"]["status"] == "partial"
        assert "qubits.qA1.f_01" in st["restore"]["kept"]


class TestUnjudgedAndRejectedNeverStay:
    @pytest.mark.parametrize("autonomy", ["full", "review"])
    def test_a_failed_item_that_saved_is_put_back(self, tmp_path, autonomy):
        st, pre, live = _plan(tmp_path, STEPS_1Q[:1], autonomy=autonomy,
                              fail_steps={"qubit_spec"})
        assert _q(_live(live), "qA1", "f_01") == \
            pytest.approx(_q(pre, "qA1", "f_01"))
        assert any(r["target"] == "(run)" for r in st["review_queue"])

    def test_full_mode_reject_is_reverted_without_node_patches(self, tmp_path):
        st, pre, live = _plan(
            tmp_path, [dict(STEPS_1Q[0], retry_max=0)], autonomy="full",
            corruption={("qubit_spec", "qA1"): ["wrong_peak"]})
        assert st["board"]["qubit_spec"]["qA1"]["state"] == "reverted"
        assert _q(_live(live), "qA1", "f_01") == \
            pytest.approx(_q(pre, "qA1", "f_01"))

    def test_full_mode_abort_puts_back_only_the_aborted_runs_values(
            self, tmp_path):
        st, pre, live = _plan(tmp_path, STEPS_1Q[:2], autonomy="full",
                              abort_on="power_rabi")
        now = _live(live)
        amp = ("xy", "operations", "x180", "amplitude")
        assert _q(now, "qA1", *amp) == pytest.approx(_q(pre, "qA1", *amp))
        # the judged, passed qubit_spec step stays (full autonomy)
        assert _q(now, "qA1", "f_01") != pytest.approx(_q(pre, "qA1", "f_01"))
