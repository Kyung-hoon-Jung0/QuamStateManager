"""Stop-loss tiers 2 and 3, now with a caller (docs/78 §22.4 item 2).

The audit found `should_stop` / `no_progress` / `metric_trend` / `harm` had
**no caller at all** — the engine used only `stoploss.Budget`, so every
constant in that module was inert. Wiring them naively introduces two bugs
that had to be fixed first, and both are pinned here:

1. four of the nine families emit none of the eight progress metrics, ever.
   "No metric improved" is then indistinguishable from "no metric exists", and
   tier 2a would stop every metric-blind family after three attempts.
2. tier 2 must not pre-empt an untried **escalation** rung. "We are not
   learning" is a statement about what we have tried; a cross-node
   re-calibration we have not attempted is not one of those things — and it is
   exactly the fix for the case the rung exists for.
"""
from __future__ import annotations

from quam_state_manager.core.autofit import stoploss


class TestMetricBlindnessIsNotFlatness:
    def test_a_family_reporting_no_metric_does_not_read_as_stalled(self):
        history = [{"unrelated": 1}, {"unrelated": 2}, {"unrelated": 3}]
        assert stoploss.metric_trend(history)["present"] is False
        assert stoploss.no_progress(history) is None

    def test_a_family_that_does_report_still_stalls(self):
        history = [{"peak_snr": 8.0}, {"peak_snr": 8.1}, {"peak_snr": 8.05}]
        assert stoploss.metric_trend(history)["present"] is True
        assert "no progress" in (stoploss.no_progress(history) or "")

    def test_a_blind_family_still_stops_when_the_PICTURE_degrades(self):
        """2a has no opinion, so 2b is the only signal left — and it is
        allowed to speak alone here, because it is not being asked to agree
        with a metric that does not exist."""
        history = [{}, {}, {}]
        why = stoploss.no_progress(history, ["worse", "worse", "worse"])
        assert why and "degrading" in why

    def test_a_blind_family_does_not_stop_on_same(self):
        assert stoploss.no_progress([{}, {}, {}], ["same", "same", "same"]) is None


class TestTheEscalationCarveOut:
    HISTORY = [{"peak_snr": 8.0}, {"peak_snr": 8.0}, {"peak_snr": 8.0}]

    def test_tier_2_fires_when_nothing_is_left_to_try(self):
        out = stoploss.should_stop(history=self.HISTORY)
        assert out and out["tier"] == 2

    def test_tier_2_is_suppressed_while_an_escalation_is_untried(self):
        assert stoploss.should_stop(history=self.HISTORY,
                                    allow_no_progress=False) is None

    def test_harm_still_fires_with_an_escalation_untried(self):
        """A budget is a fact and harm is harm, whatever remains untried."""
        out = stoploss.should_stop(history=self.HISTORY,
                                   allow_no_progress=False,
                                   upstream_escalations=2)
        assert out and out["tier"] == 3

    def test_the_budget_still_fires_with_an_escalation_untried(self):
        b = stoploss.Budget(max_steps=1)
        b.note_step()
        out = stoploss.should_stop(history=self.HISTORY, budget=b,
                                   allow_no_progress=False)
        assert out and out["tier"] == 1


class TestTheEngineCallsIt:
    def test_the_engine_imports_and_uses_should_stop(self):
        """The finding was 'no caller'. This asserts the caller exists, in the
        one place a retry is granted."""
        import inspect

        from quam_state_manager.core.autofit import engine

        src = inspect.getsource(engine.PlanEngine._run_step_inner)
        assert "stoploss.should_stop" in src
        assert "allow_no_progress" in src

    def test_a_stop_defers_the_target_rather_than_halting_the_plan(self):
        """D-8: revert this target, continue to the next — never a lost
        night."""
        import inspect

        from quam_state_manager.core.autofit import engine

        src = inspect.getsource(engine.PlanEngine._run_step_inner)
        i = src.index("stoploss.should_stop")
        after = src[i:i + 700]
        assert "_defer(" in after and "target_stopped" in after

    def test_escalations_are_counted_per_target(self):
        import inspect

        from quam_state_manager.core.autofit import engine

        src = inspect.getsource(engine.PlanEngine._adapt)
        assert "_escalations[" in src
