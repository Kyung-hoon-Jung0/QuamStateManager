"""Runner+agent P6c (docs/78 goal §1.1 #3): the cross-experiment review.

Every other gate judges one run against itself. The failure that survives all of
them is the PAIR that is each internally consistent and mutually impossible.
"""
from __future__ import annotations

from quam_state_manager.core.autofit import consistency as C
from quam_state_manager.core.autofit import families


class TestRegistry:
    def test_every_source_names_a_real_family(self):
        for check in C.CROSS_CHECKS:
            for fam, _key in check.sources:
                assert fam in families.FAMILIES, (check.quantity, fam)

    def test_a_check_needs_at_least_two_sources(self):
        for check in C.CROSS_CHECKS:
            assert len(check.sources) >= 2, check.quantity

    def test_different_knobs_are_not_cross_checked(self):
        """`frequency_shift` appears in four families, but node 06's is a
        qubit-flux response and node 07's is a coupler-flux response.
        Comparing them would manufacture disagreement — same key name is not
        the same quantity."""
        names = {c.quantity for c in C.CROSS_CHECKS}
        assert "frequency shift" not in names
        for check in C.CROSS_CHECKS:
            keys = {k for _f, k in check.sources}
            assert "frequency_shift" not in keys

    def test_the_factor_is_corpus_calibrated(self):
        """docs/78 §20. The first design used 2.0 everywhere and produced a
        37.5% false-contradiction rate on real accepted pairs. 20 is p99=13.2
        (measured over 133 gate-passing adjacent pairs) with margin, and yields
        ZERO false contradictions on that population."""
        rc = next(c for c in C.CROSS_CHECKS
                  if c.quantity == "resonator frequency")
        assert rc.scale_factor == 20.0

    def test_the_uncalibratable_checks_stayed_out(self):
        """A check usable only at 86-146 linewidths is WIDER than the spacing
        to a neighbouring line — it cannot catch the error it exists for. And
        the flux cross-check, the one this module was designed around, has
        exactly THREE gate-passing pairs in the whole corpus: three samples
        cannot calibrate a threshold, and shipping one anyway would be the
        invented number this module refuses everywhere else."""
        pairs = {(f, k) for c in C.CROSS_CHECKS for f, k in c.sources}
        assert not any(f.startswith("qubit_spectroscopy") for f, _k in pairs)
        assert ("resonator_spectroscopy_vs_flux", "idle_offset") not in pairs


class TestReconcile:
    def test_the_mutually_impossible_pair_is_caught(self):
        """The case no per-run gate and no judge can see: two clean fits on the
        same qubit naming resonators 100 MHz apart — 50 linewidths, i.e. one of
        them fitted a NEIGHBOURING resonator. Each figure looks fine alone."""
        rep = C.reconcile({
            ("resonator_spectroscopy", "qA1"): {"frequency": 7.200e9,
                                                "fwhm": 2e6},
            ("resonator_spectroscopy_vs_power", "qA1"):
                {"resonator_frequency": 7.300e9, "fwhm": 2e6},
        })
        assert len(rep.findings) == 1
        f = rep.findings[0]
        assert f.quantity == "resonator frequency" and f.target == "qA1"
        assert set(f.values) == {"resonator_spectroscopy",
                                 "resonator_spectroscopy_vs_power"}

    def test_the_flux_sweet_spot_case_is_NOT_covered_and_that_is_deliberate(self):
        """The case this module was designed around — 06 and 09 disagreeing
        about the same flux line — is currently NOT checked, because the corpus
        holds exactly THREE gate-passing pairs to calibrate against (docs/78
        §20.3). Shipping a threshold on three samples would be the invented
        number this module refuses everywhere else. This test exists so the gap
        is visible rather than forgotten: DELETE it when the data arrives."""
        rep = C.reconcile({
            ("resonator_spectroscopy_vs_flux", "qA1"): {"idle_offset": 0.08,
                                                        "dv_phi0": 1.0},
            ("qubit_spectroscopy_vs_flux", "qA1"): {"idle_offset": -0.30,
                                                    "dv_phi0": 1.0}})
        assert rep.compared == 0 and rep.findings == []

    def test_agreement_within_the_reported_scale_is_not_a_finding(self):
        rep = C.reconcile({
            ("resonator_spectroscopy", "qA1"):
                {"frequency": 7.20000e9, "fwhm": 2e6},
            ("resonator_spectroscopy_vs_power", "qA1"):
                {"resonator_frequency": 7.20050e9, "fwhm": 2e6},
        })
        assert rep.findings == [] and rep.compared == 1

    def test_the_tolerance_comes_from_the_runs_not_a_constant(self):
        """A hardcoded Hz would be the Clause-B mistake in numeric form: right
        for the chip it was written on, wrong for the next. A broad resonance
        tolerates a wider disagreement than a sharp one — same numbers, two
        different verdicts."""
        wide = C.reconcile({
            ("resonator_spectroscopy", "qA1"): {"frequency": 7.2000e9,
                                                "fwhm": 20e6},
            ("resonator_spectroscopy_vs_power", "qA1"):
                {"resonator_frequency": 7.2100e9, "fwhm": 20e6}})
        sharp = C.reconcile({
            ("resonator_spectroscopy", "qA1"): {"frequency": 7.2000e9,
                                                "fwhm": 0.2e6},
            ("resonator_spectroscopy_vs_power", "qA1"):
                {"resonator_frequency": 7.2100e9, "fwhm": 0.2e6}})
        assert wide.findings == []
        assert len(sharp.findings) == 1
        assert sharp.findings[0].scale_from == "fwhm"

    def test_a_single_source_is_never_a_contradiction(self):
        rep = C.reconcile({("resonator_spectroscopy", "qA1"):
                           {"frequency": 7.2e9, "fwhm": 2e6}})
        assert rep.compared == 0 and rep.findings == []

    def test_a_missing_scale_falls_back_and_SAYS_so(self):
        rep = C.reconcile({
            ("resonator_spectroscopy", "qA1"): {"frequency": 7.2e9},
            ("resonator_spectroscopy_vs_power", "qA1"):
                {"resonator_frequency": 7.9e9}})
        assert len(rep.findings) == 1
        assert rep.skipped and "fell back" in rep.skipped[0]

    def test_targets_are_never_mixed(self):
        rep = C.reconcile({
            ("resonator_spectroscopy", "qA1"): {"frequency": 7.2e9,
                                                "fwhm": 2e6},
            ("resonator_spectroscopy_vs_power", "qA2"):
                {"resonator_frequency": 9.9e9, "fwhm": 2e6}})
        assert rep.compared == 0 and rep.findings == []

    def test_non_numeric_and_empty_entries_are_ignored(self):
        rep = C.reconcile({
            ("resonator_spectroscopy", "qA1"): {"frequency": "nope"},
            ("resonator_spectroscopy_vs_power", "qA1"): {},
            ("resonator_spectroscopy_vs_flux", "qA1"): None})
        assert rep.compared == 0 and rep.findings == []


class TestSummary:
    def test_a_clean_review_says_how_much_it_compared(self):
        """'No contradictions' out of zero comparisons is not the same claim as
        'no contradictions' out of twenty."""
        rep = C.reconcile({
            ("resonator_spectroscopy", "qA1"): {"frequency": 7.2e9,
                                                "fwhm": 2e6},
            ("resonator_spectroscopy_vs_power", "qA1"):
                {"resonator_frequency": 7.2001e9, "fwhm": 2e6}})
        text = C.summarize(rep)
        assert "1 comparison" in text and "no contradictions" in text

    def test_a_finding_explains_itself(self):
        rep = C.reconcile({
            ("resonator_spectroscopy", "qA1"): {"frequency": 7.200e9,
                                                "fwhm": 2e6},
            ("resonator_spectroscopy_vs_power", "qA1"):
                {"resonator_frequency": 7.300e9, "fwhm": 2e6}})
        text = C.summarize(rep)
        assert "qA1" in text and "resonator frequency" in text
        assert "WRONG" in text or "wrong" in text     # names the failure mode
        assert "fwhm" in text                         # names the scale used
