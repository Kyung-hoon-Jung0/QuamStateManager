"""A flux claim outside the swept window (docs/78 §26).

Found by asking whether node 06's writes are actually RIGHT — not just whether
our automation reproduces them. Across every archived node-06 target, **12
claims name a sweet spot the sweep never visited**, and all 12 were marked
`successful` by the node, which then declined to write them.

Nothing on our side stopped them. G1 follows the node's outcome; the metric
gates skip a metric the run reports as `None` (all 12 do); the plausibility
band is the flux line's +-10 V envelope; and this family's feature check is
span mode, which asks whether a signal EXISTS and never where the claim sits.
The engine's forward path would have written a value the data does not contain.

Two-sided on the corpus that found it: fires on exactly those 12, silent on all
148 others INCLUDING every one of the 74 the node itself wrote — 0 false
rejects. The gate and the instrument agree target for target.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit import gates as gates_mod

CHECK = fam_mod._claim_inside_swept_flux
WIN = {"min_flux_offset_in_v": -0.5, "max_flux_offset_in_v": 0.5,
       "num_flux_points": 101}


class TestTheCheck:
    @pytest.mark.parametrize("v", [0.0, 0.02, -0.4, 0.499])
    def test_a_claim_inside_the_sweep_passes(self, v):
        assert CHECK({"idle_offset": v}, WIN) is None

    @pytest.mark.parametrize("v", [-0.94776, 0.64348, 5.0])
    def test_a_claim_outside_the_sweep_is_flagged(self, v):
        why = CHECK({"idle_offset": v}, WIN)
        assert why and "outside the flux range" in why

    def test_the_edge_is_allowed_within_one_flux_step(self):
        """A small overshoot at the boundary is normal fitting behaviour, and
        the tolerance is the run's OWN step — not a constant."""
        step = 1.0 / 100
        assert CHECK({"idle_offset": 0.5 + step * 0.9}, WIN) is None
        assert CHECK({"idle_offset": 0.5 + step * 3}, WIN) is not None

    def test_a_narrow_sweep_is_judged_by_its_own_window(self):
        """A chip swept over +-0.2 V must not be judged by one swept over
        +-2.5 V — the bound is run-derived or it is nothing."""
        narrow = {"min_flux_offset_in_v": -0.2, "max_flux_offset_in_v": 0.2,
                  "num_flux_points": 41}
        assert CHECK({"idle_offset": 0.35}, narrow) is not None
        assert CHECK({"idle_offset": 0.35}, WIN) is None

    def test_an_asymmetric_window_is_honoured(self):
        """One real run swept [-2.5, -0.5]; a claim near zero is outside it."""
        w = {"min_flux_offset_in_v": -2.5, "max_flux_offset_in_v": -0.5,
             "num_flux_points": 101}
        assert CHECK({"idle_offset": 0.0768}, w) is not None
        assert CHECK({"idle_offset": -1.5}, w) is None

    def test_min_offset_is_checked_too(self):
        assert CHECK({"idle_offset": 0.0, "min_offset": 9.0}, WIN) is not None

    def test_a_run_that_does_not_report_its_window_gets_no_opinion(self):
        """An unverifiable premise is not a satisfied one — but it is not a
        violation either. The gate abstains rather than invent a window."""
        assert CHECK({"idle_offset": 99.0}, {}) is None
        assert CHECK({"idle_offset": 99.0},
                     {"min_flux_offset_in_v": 0.5,
                      "max_flux_offset_in_v": -0.5}) is None

    @pytest.mark.parametrize("v", [None, "0.1", True, float("nan")])
    def test_non_numbers_are_not_flagged(self, v):
        assert CHECK({"idle_offset": v}, WIN) is None


class TestItIsWiredIntoTheFamily:
    def test_the_family_declares_it(self):
        f = fam_mod.family_for("06_resonator_spectroscopy_vs_flux")
        assert CHECK in f.consistency_checks

    def test_the_flat_response_check_still_works(self):
        f = fam_mod.family_for("06_resonator_spectroscopy_vs_flux")
        hits = []
        for c in f.consistency_checks:
            try:
                r = c({"flat_response": True, "idle_offset": 0.0}, WIN)
            except TypeError:
                r = c({"flat_response": True, "idle_offset": 0.0})
            if r:
                hits.append(r)
        assert any("flat flux response" in h for h in hits)


class TestTheGatePipelinePassesRunParameters:
    """The window is knowable only from the run's own parameters, so the
    pipeline had to start handing them to consistency checks. One-argument
    checks are the majority and must keep working untouched."""

    def _run(self, entry, params):
        return {"experiment_name": "06_resonator_spectroscopy_vs_flux",
                "fit_results": {"qA1": entry}, "outcomes": {"qA1": "successful"},
                "parameters": params, "patches": []}

    def test_an_out_of_window_claim_becomes_a_suspect(self):
        fam = fam_mod.family_for("06_resonator_spectroscopy_vs_flux")
        entry = {"idle_offset": 5.0, "frequency_shift": 1e6,
                 "ridge_amp_snr": 30.0, "ridge_coverage": 1.0, "ridge_r2": 0.99}
        v = gates_mod.evaluate_run(self._run(entry, WIN), fam, ["qA1"],
                                   current_value_of=lambda p: None)["qA1"]
        assert v.verdict != "pass"
        assert any("outside the flux range" in r for r in v.reasons)

    def test_an_in_window_claim_is_not_flagged_by_THIS_check(self):
        """The synthetic run has no ds_raw, so G3 honestly reports
        `unverifiable` — that is the raw-data gate doing its job, not this one.
        What matters here is that the metrics gate stays clean and the window
        reason is absent."""
        fam = fam_mod.family_for("06_resonator_spectroscopy_vs_flux")
        entry = {"idle_offset": 0.02, "frequency_shift": 1e6,
                 "ridge_amp_snr": 30.0, "ridge_coverage": 1.0, "ridge_r2": 0.99}
        v = gates_mod.evaluate_run(self._run(entry, WIN), fam, ["qA1"],
                                   current_value_of=lambda p: None)["qA1"]
        assert v.checks.get("G2_metrics") == "ok"
        assert not any("outside the flux range" in r for r in v.reasons)

    def test_a_one_argument_check_is_unaffected(self):
        """power_rabi's checks take only the entry."""
        fam = fam_mod.family_for("power_rabi")
        run = {"experiment_name": "11_power_rabi",
               "fit_results": {"qA1": {"opt_amp": 0.3,
                                       "prefactor_extrapolated": True}},
               "outcomes": {"qA1": "successful"}, "parameters": {},
               "patches": []}
        v = gates_mod.evaluate_run(run, fam, ["qA1"],
                                   current_value_of=lambda p: None)["qA1"]
        assert any("extrapolated" in r for r in v.reasons)
