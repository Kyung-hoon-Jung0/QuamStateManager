"""Runner+agent P8 (docs/78): offline scoring — the harness, and its metric.

The honest metric is NOT "the agent agrees with the human". A human who burned
three drive-power attempts before refining the step is the BASELINE, not the
ground truth.
"""
from __future__ import annotations

from quam_state_manager.core.autofit import replay_score as R


def _run(ok, **params):
    return {"outcomes": {"qA1": "successful" if ok else "failed"},
            "parameters": params, "family": "qubit_spectroscopy"}


SESSION = [
    _run(False, frequency_span_in_mhz=40, num_shots=100),
    _run(False, frequency_span_in_mhz=80, num_shots=100),   # widened
    _run(False, frequency_span_in_mhz=160, num_shots=100),  # widened again
    _run(True, frequency_span_in_mhz=160, num_shots=400),   # finally averaged
]


class TestBuildPoints:
    def test_points_stop_at_the_humans_first_success(self):
        """After the operator succeeded there is no decision left to score."""
        pts = R.build_points("s1", SESSION, "qA1")
        assert [p.index for p in pts] == [1, 2, 3]
        assert all(p.resolved_at == 3 for p in pts)

    def test_runs_to_success_counts_forward_from_the_point(self):
        pts = {p.index: p for p in R.build_points("s1", SESSION, "qA1")}
        assert pts[1].human_runs_to_success == 3
        assert pts[3].human_runs_to_success == 1

    def test_a_session_that_never_succeeded_yields_nothing(self):
        assert R.build_points("s", [_run(False), _run(False)], "qA1") == []

    def test_an_instantly_solved_session_yields_nothing(self):
        assert R.build_points("s", [_run(True), _run(True)], "qA1") == []

    def test_each_point_sees_only_the_runs_before_it(self):
        for p in R.build_points("s1", SESSION, "qA1"):
            assert len(p.seen) == p.index


class TestCompareToHuman:
    def test_same_knob_same_direction_is_agreement(self):
        p = R.build_points("s1", SESSION, "qA1")[0]   # human widened 40 → 80
        out = R.compare_to_human(p, {"frequency_span_in_mhz": 60})
        assert out["matches_human"] is True
        assert out["changed"] == ["frequency_span_in_mhz"]

    def test_the_opposite_direction_is_disagreement(self):
        p = R.build_points("s1", SESSION, "qA1")[0]
        out = R.compare_to_human(p, {"frequency_span_in_mhz": 20})
        assert out["matches_human"] is False
        assert out["disagree_on"] == ["frequency_span_in_mhz"]

    def test_direction_not_magnitude(self):
        """The exact number is the node's business; what is interesting is
        whether the agent turned the same knob the same way."""
        p = R.build_points("s1", SESSION, "qA1")[0]
        for v in (41, 80, 4000):
            assert R.compare_to_human(p, {"frequency_span_in_mhz": v})[
                "matches_human"] is True

    def test_a_different_knob_entirely_is_not_scored_as_disagreement(self):
        """Turning a knob the human never touched is not 'wrong' — it may be
        the shortcut. It is simply not comparable."""
        p = R.build_points("s1", SESSION, "qA1")[0]
        out = R.compare_to_human(p, {"num_shots": 400})
        assert out["matches_human"] is None and out["changed"] == []


class TestRunsSaved:
    """docs/78 §20.4 — the metric that IS computable offline. Re-measuring a
    chip from an archive is impossible, so "did the proposal work?" cannot be
    answered; "the operator only reached this k+n runs later" can."""

    POINTS = R.build_points("s1", SESSION, "qA1")

    def test_proposing_the_eventual_winner_early_scores_the_saving(self):
        """The operator averaged only at step 4 after widening three times.
        An agent that proposes it at step 1 saved those runs."""
        p = self.POINTS[0]                       # k=1, three runs still to go
        out = R.runs_saved(p, {"num_shots": 400}, p.future)
        assert out and out["runs_saved"] == 2 and out["matched_at"] == 3

    def test_doing_what_the_operator_did_next_saves_nothing(self):
        p = self.POINTS[0]
        out = R.runs_saved(p, {"frequency_span_in_mhz": 80}, p.future)
        assert out and out["runs_saved"] == 0
        assert "no time saved" in out["note"]

    def test_the_same_decision_counts_even_at_a_different_number(self):
        """Demanding equality would score a correct call as a miss because the
        agent said 78 where the operator typed 80."""
        p = self.POINTS[0]
        assert R.runs_saved(p, {"frequency_span_in_mhz": 78}, p.future)

    def test_a_proposal_the_operator_never_made_is_unscoreable_not_wrong(self):
        """It may have been better or nonsense — the archive cannot say, so the
        harness says so rather than guessing."""
        p = self.POINTS[0]
        assert R.runs_saved(p, {"frequency_span_in_mhz": 3.3}, p.future) is None

    def test_an_empty_proposal_scores_nothing(self):
        p = self.POINTS[0]
        assert R.runs_saved(p, {}, p.future) is None
        assert R.runs_saved(p, None, p.future) is None


class TestScore:
    POINTS = R.build_points("s1", SESSION, "qA1")

    def test_without_measured_outcomes_it_offers_the_offline_metric(self):
        """No re-measurement is possible from an archive, so `measured` stays 0
        — but `runs_saved` still answers the real question."""
        out = R.score(self.POINTS, {("s1", 1): {"num_shots": 400}})
        assert out["measured"] == 0
        assert "NOT the metric" in out["caveat"]
        assert out["early_moves"] == 1 and out["runs_saved"] == 2

    def test_skipping_a_dead_end_scores_BETTER_than_reproducing_it(self):
        """The reference case: the operator burned three attempts widening
        before averaging. An agent that averages first is better, even though
        it AGREES with the human less."""
        proposals = {("s1", 1): {"num_shots": 400}}       # not what they did
        outcomes = {("s1", 1): 1}                          # solved in one run
        out = R.score(self.POINTS, proposals, outcomes)
        assert out["faster"] == 1 and out["slower"] == 0
        assert "runs-to-success" in out["caveat"]
        row = next(r for r in out["rows"] if r["index"] == 1)
        assert row["comparison"]["matches_human"] is None   # disagreed…
        assert row["agent_runs_to_success"] < row["human_runs_to_success"]

    def test_reproducing_the_dead_end_is_not_rewarded(self):
        proposals = {("s1", 1): {"frequency_span_in_mhz": 80}}   # copies them
        outcomes = {("s1", 1): 3}
        out = R.score(self.POINTS, proposals, outcomes)
        assert out["faster"] == 0 and out["same"] == 1
        row = next(r for r in out["rows"] if r["index"] == 1)
        assert row["comparison"]["matches_human"] is True   # agreed…
        assert row["agent_runs_to_success"] == row["human_runs_to_success"]

    def test_counts_are_reported_beside_every_rate(self):
        out = R.score(self.POINTS, {("s1", 1): {"frequency_span_in_mhz": 80}})
        assert "(" in out["agreement_rate"]        # rate carries its n
        assert out["n_points"] == 3 and out["n_answered"] == 1

    def test_no_answers_at_all_is_reported_not_crashed(self):
        out = R.score(self.POINTS, {})
        assert out["n_answered"] == 0 and out["agreement_rate"].startswith("n/a")


class TestTheToleranceWasMeasured:
    """docs/78 §22.1 — 0.25 erased 20.2% of 644 real class-A parameter
    changes and sat ON an operator step mode rather than in a gap. 0.075 keeps
    the docstring's own 78-vs-80 case and stops swallowing deliberate steps."""

    def test_the_canonical_operator_steps_are_no_longer_erased(self):
        for ratio in (1.2, 1.25, 1.3333):
            assert not R._close(80.0, 80.0 * ratio, 0.075), ratio

    def test_the_78_vs_80_case_the_docstring_promises_still_passes(self):
        assert R._close(78.0, 80.0, 0.075)

    def test_a_log_unit_key_uses_an_absolute_tolerance(self):
        """A relative tolerance on dBm is reference-arbitrary: the SAME 10 dB
        step scores 0.125, 0.25 or 0.50 depending only on where it sits. No
        constant fixes that — the unit does."""
        assert not R._close(-40.0, -30.0, 0.075, key="max_power_dbm")
        assert not R._close(10.0, 20.0, 0.075, key="max_power_dbm")
        assert not R._close(-80.0, -70.0, 0.075, key="max_power_dbm")

    def test_the_same_10_db_step_is_judged_identically_wherever_it_sits(self):
        near = R._close(-40.0, -30.0, 0.075, key="min_power_dbm")
        far = R._close(-80.0, -70.0, 0.075, key="min_power_dbm")
        assert near == far

    def test_a_sub_db_nudge_on_a_log_key_is_still_the_same_decision(self):
        assert R._close(-30.0, -30.5, 0.075, key="max_power_dbm")

    def test_a_linear_key_is_untouched_by_the_log_rule(self):
        assert R._close(400.0, 410.0, 0.075, key="num_shots")

    def test_the_end_to_end_match_honours_the_log_rule(self):
        pt = R.build_points("s", [
            _run(False, max_power_dbm=-40),
            _run(False, max_power_dbm=-30),
            _run(True, max_power_dbm=-30)], "qA1")[0]
        # proposing -39 dBm is NOT the operator's -30 dBm move
        assert R.runs_saved(pt, {"max_power_dbm": -39.0}, pt.future) is None
        assert R.runs_saved(pt, {"max_power_dbm": -30.4}, pt.future)
