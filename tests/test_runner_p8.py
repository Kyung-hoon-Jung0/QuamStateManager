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


class TestScore:
    POINTS = R.build_points("s1", SESSION, "qA1")

    def test_without_measured_outcomes_it_refuses_to_claim_the_metric(self):
        out = R.score(self.POINTS, {("s1", 1): {"num_shots": 400}})
        assert out["measured"] == 0
        assert "agreement measures the wrong thing" in out["caveat"]

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
