"""Runner+agent P5 (docs/78 D-8): a counter is not a stop-loss.

A budget says how much you spent. A stop-loss says you are not learning, or you
are making it worse.
"""
from __future__ import annotations

import time

from quam_state_manager.core.autofit import stoploss as S


class TestBudget:
    def test_unset_means_unlimited_not_zero(self):
        """An unset wall clock is not a zero wall clock — the failure direction
        of a missing budget must be 'no limit', never 'stop immediately'."""
        b = S.Budget()
        b.note_step()
        assert b.plan_exhausted() is None
        assert b.target_exhausted("qA1") is None

    def test_the_plan_step_cap_fires(self):
        b = S.Budget(max_steps=2)
        b.note_step(); b.note_step()
        assert "step cap" in b.plan_exhausted()

    def test_the_wall_clock_fires(self):
        b = S.Budget(wall_clock_s=0.0)
        time.sleep(0.01)
        assert "wall clock" in b.plan_exhausted()

    def test_retries_are_scoped_per_target(self):
        """A hopeless q3 must not eat the whole night — nor stop q4."""
        b = S.Budget(max_retries_per_target=2)
        b.note_retry("qA3"); b.note_retry("qA3")
        assert b.target_exhausted("qA3")
        assert b.target_exhausted("qA4") is None
        assert b.plan_exhausted() is None


class TestMetricTrend:
    def test_a_real_gain_reads_as_improving(self):
        t = S.metric_trend([{"peak_snr": 4.0}, {"peak_snr": 9.0}])
        assert t["improving"] and "peak_snr" in t["moved"]

    def test_noise_inside_the_floor_is_not_progress(self):
        """A metric wobbling by a couple of percent is not learning, and
        treating it as such is how a loop runs all night feeling productive."""
        t = S.metric_trend([{"r2": 0.900}, {"r2": 0.902}, {"r2": 0.899}])
        assert not t["improving"]

    def test_a_missing_metric_never_counts_as_a_gain(self):
        assert not S.metric_trend([{"r2": 0.9}, {}])["improving"]


class TestNoProgress:
    FLAT = [{"peak_snr": 5.0}, {"peak_snr": 5.05}, {"peak_snr": 4.98}]

    def test_too_few_attempts_never_stops(self):
        assert S.no_progress(self.FLAT[:2]) is None

    def test_both_signals_flat_stops(self):
        assert "no progress" in S.no_progress(
            self.FLAT, ["same", "same", "same"])

    def test_a_metric_gain_alone_keeps_it_running(self):
        rising = [{"peak_snr": 3.0}, {"peak_snr": 5.0}, {"peak_snr": 9.0}]
        assert S.no_progress(rising, ["same", "same", "same"]) is None

    def test_a_visible_improvement_alone_keeps_it_running(self):
        """Stopping on one signal alone would end runs that are genuinely
        improving on the other axis — the metric can miss what the picture
        shows, and vice versa."""
        assert S.no_progress(self.FLAT, ["same", "same", "better"]) is None

    def test_degrading_is_reported_differently_from_stalled(self):
        why = S.no_progress(self.FLAT, ["same", "worse", "worse"])
        assert why and "getting worse" in why

    def test_no_vision_available_still_stops_on_flat_metrics(self):
        why = S.no_progress(self.FLAT)
        assert why and "no vision comparison available" in why


class TestHarm:
    def test_two_upstream_escalations_stop_the_target(self):
        why = S.harm(upstream_escalations=2)
        assert why and "not where we think it is" in why

    def test_seeds_written_and_never_consumed(self):
        assert "never consumed" in S.harm(unconsumed_seeds=3)

    def test_drive_at_the_ceiling(self):
        assert "ceiling" in S.harm(drive_at_ceiling=True)

    def test_a_healthy_loop_is_not_flagged(self):
        assert S.harm(unconsumed_seeds=1, upstream_escalations=1) is None


class TestShouldStop:
    def test_harm_outranks_budget_and_progress(self):
        """Order matters: harm means we are making things WORSE, which is a
        different fact from having spent the budget."""
        out = S.should_stop(history=[{"r2": 0.9}] * 3,
                            comparisons=["same"] * 3,
                            budget=S.Budget(max_steps=1),
                            upstream_escalations=2)
        assert out["tier"] == 3

    def test_budget_outranks_no_progress(self):
        out = S.should_stop(history=[{"r2": 0.9}] * 3,
                            comparisons=["same"] * 3,
                            budget=S.Budget(max_steps=0))
        assert out["tier"] == 1 and out["scope"] == "plan"

    def test_a_target_budget_is_reported_as_target_scope(self):
        b = S.Budget(max_retries_per_target=1)
        b.note_retry("qA1")
        out = S.should_stop(budget=b, target="qA1")
        assert out["tier"] == 1 and out["scope"] == "target"

    def test_a_healthy_run_returns_none(self):
        assert S.should_stop(history=[{"peak_snr": 3.0}, {"peak_snr": 8.0}],
                             comparisons=["better"],
                             budget=S.Budget(max_steps=10)) is None

    def test_the_oscillation_case_is_caught(self):
        """widen → too coarse → refine → out of window → widen … every step is
        individually justified and a counter never fires. Only the flat trend
        catches it."""
        osc = [{"peak_snr": 4.0}, {"peak_snr": 3.9}, {"peak_snr": 4.1},
               {"peak_snr": 3.95}]
        out = S.should_stop(history=osc, comparisons=["same"] * 4,
                            budget=S.Budget(max_steps=99))
        assert out and out["tier"] == 2
