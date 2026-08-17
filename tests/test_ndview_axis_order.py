"""The Raw-data tab agrees with the Interactive tab about which sweep is x
(docs/122 item 1).

The customer reported 2-D maps transposed between the two tabs and confirmed the
direction: Interactive is correct, because it uses the same axis scheme as the
qualibrate-generated figure. ``ndview._default_view`` chose x by ARRAY SIZE while
the interactive recipes orient by NAME, so the two agreed only when the lab's x
dim happened to be the larger array — measured over 53 executed 2-D runs across
10 families: 30/30 agreement when it was larger, **0/20** when it was smaller,
3/3 when equal. Total separation, and the reason it looked intermittent.

These pins guard the three properties that make the ranking safe rather than the
53 measurements themselves (those live in docs/122):

* it is applied only where the evidence is complete (all sweeps ranked),
* it cannot resurrect a shot axis (docs/82 sits above it),
* and the one rank that was placed by analogy rather than by an observed figure
  stays out.
"""
from quam_state_manager.core import ndview


def _dim(name, size, kind="sweep"):
    return {"name": name, "size": size, "kind": kind, "has_coord": True}


class TestTheOrderIsDerivedNotInvented:
    def test_probe_flux_is_deliberately_unranked(self):
        """It was in the derived order and was removed by the adversarial pass.

        On 50a/50b_flux_crosstalk_dc run with a probe, the lab REDUCES that axis
        away (``isel(probe_flux=k)``) rather than plotting it, so a rank would
        have put it on y in place of ``detuning``. Unranked, the cube keeps
        today's behaviour instead of becoming newly wrong — and because the whole
        cube then contains an unranked sweep, the size rule governs it entirely.
        """
        assert "probe_flux" not in ndview._AXIS_RANK
        # aggressor_flux, which a real figure DOES put on x, stays ranked.
        assert "aggressor_flux" in ndview._AXIS_RANK

    def test_no_name_is_both_ranked_and_a_shot_or_entity(self):
        """docs/82's shot rule sits structurally above this ordering: a shot dim
        never enters the ``sweeps`` bucket at all. That only stays true while the
        two name sets are disjoint."""
        assert not (set(ndview._AXIS_RANK) & set(ndview._SHOT_DIM_NAMES))
        assert not (set(ndview._AXIS_RANK) & set(ndview._ENTITY_DIM_NAMES))

    def test_capital_N_is_not_the_averaging_n(self):
        """JAZZ's ``N`` is a swept repetition COUNTER the lab plots; ``n`` is an
        averaging index it never does. The distinction is the spelling, and it is
        load-bearing."""
        assert "N" in ndview._AXIS_RANK
        assert "n" in ndview._SHOT_DIM_NAMES
        assert "n" not in ndview._AXIS_RANK


class TestTheLabConventionWins:
    def test_flux_beats_frequency_even_when_frequency_is_bigger(self):
        """06_resonator_spectroscopy_vs_flux: the lab plots x=flux_bias,
        y=freq. The real runs sweep 101 flux points against 150 frequencies, so
        the old size rule put frequency on x — the customer's exact report."""
        v = ndview._default_view([_dim("qubit", 20, "entity"),
                                  _dim("detuning", 150), _dim("flux_bias", 101)])
        assert v["x"] == "flux_bias"
        assert v["y"] == "detuning"

    def test_and_also_when_flux_is_bigger(self):
        """The point is that size stopped deciding, not that it got inverted."""
        v = ndview._default_view([_dim("detuning", 60), _dim("flux_bias", 400)])
        assert v["x"] == "flux_bias"
        assert v["y"] == "detuning"

    def test_frequency_beats_power(self):
        """05_resonator_spectroscopy_vs_power keeps frequency on x — the lab
        draws THAT one the other way round, which is why a physical-quantity
        ordering cannot express the convention and the ranking is by spelling."""
        v = ndview._default_view([_dim("power", 400), _dim("detuning", 150)])
        assert v["x"] == "detuning"
        assert v["y"] == "power"

    def test_two_members_of_one_physical_group_are_ordered(self):
        """18a_coupler_zero_point plots x=qubit_flux, y=coupler_flux. A ranking
        grouped by physical quantity could not say this at all."""
        v = ndview._default_view([_dim("coupler_flux", 90), _dim("qubit_flux", 30)])
        assert v["x"] == "qubit_flux"
        assert v["y"] == "coupler_flux"


class TestIncompleteEvidenceChangesNothing:
    def test_an_unranked_pair_keeps_the_size_rule(self):
        v = ndview._default_view([_dim("depths", 33), _dim("nb_of_sequences", 100)])
        assert v["x"] == "nb_of_sequences"      # bigger wins, exactly as before
        assert v["y"] == "depths"

    def test_a_MIXED_cube_keeps_the_size_rule_wholesale(self):
        """Half-converting on partial evidence would be a guess. With one sweep
        unranked the whole cube stays on the legacy rule — measured as free on
        the customer archive, where 1,803 of 1,805 two-sweep cubes have every
        sweep ranked and none are mixed."""
        v = ndview._default_view([_dim("detuning", 10), _dim("something_new", 400)])
        assert v["x"] == "something_new"
        assert v["y"] == "detuning"
        # ...and swapping the sizes swaps the axes, i.e. size still decides here
        v2 = ndview._default_view([_dim("detuning", 400), _dim("something_new", 10)])
        assert v2["x"] == "detuning"

    def test_order_sweeps_is_stable_for_equal_ranks_absent(self):
        """A single sweep is unambiguous whether ranked or not."""
        for name in ("flux_bias", "totally_unknown"):
            v = ndview._default_view([_dim(name, 50)])
            assert v["x"] == name and v["y"] is None


class TestShotRuleStillWins:
    def test_a_shot_axis_is_still_averaged_away_not_ranked(self):
        """docs/82: a repetition index is not a quantity. It is bucketed by KIND
        before any ordering runs, so no ranking can promote it."""
        v = ndview._default_view([_dim("qubit", 20, "entity"),
                                  _dim("n_runs", 2000, "shot"),
                                  _dim("amp_prefactor", 10)])
        assert v["x"] == "amp_prefactor"
        assert [r["name"] for r in v["reduced"]] == ["n_runs"]

    def test_a_ranked_name_never_reaches_x_from_the_shot_bucket(self):
        """The iq_blobs case: nothing else to plot against, so the shot axis IS
        the view — and it gets there through the shot branch, never the rank."""
        v = ndview._default_view([_dim("qubit", 20, "entity"),
                                  _dim("n_runs", 2000, "shot")])
        assert v["x"] == "n_runs"
        assert v["reduced"] == []
