"""The Clause-B lint's recall half (docs/78 §22.4 item 6).

The audit found the lint silent, not clean: it catches units and explicit
window-fractions and almost none of the violations written in WORDS — which is
the form an author actually reaches for. So `lint_dropped == []` never meant an
entry was clean.

The prose rules are a separate WARN tier rather than more drop rules, and that
split is itself a measurement: run against the shipped v1 pack they flag ten
strings, and on inspection most are false positives ("one fringe runs vertical"
is shape, "instead of a narrow band" is contrast, "several periods is a
legitimate signature" exists to PREVENT a Clause-B misjudgement). P3c measured
the judge's weak side to be stinginess, so thinning the pack on a guess would
make the measured weakness worse.
"""
from __future__ import annotations

import pytest

from quam_state_manager.core.autofit import judge_pack as J


class TestTheProseRulesHaveRecall:
    @pytest.mark.parametrize("text", [
        "the dip sits near the centre of the trace",
        "the peak appears at the middle of the panel",
        "the feature lies in the left third",
        "the marker is at the right edge",
    ])
    def test_word_form_position_claims_are_caught(self, text):
        assert J.warn_text(text), text

    @pytest.mark.parametrize("text", [
        "a broad feature dominates the panel",
        "a very narrow dip is visible",
    ])
    def test_unqualified_size_adjectives_are_caught(self, text):
        assert J.warn_text(text), text

    @pytest.mark.parametrize("text", [
        "about ten oscillations are visible",
        "several periods fit across the scan",
    ])
    def test_window_dependent_counts_are_caught(self, text):
        assert J.warn_text(text), text


class TestItDoesNotFireOnLegitimatePhysics:
    @pytest.mark.parametrize("text", [
        # relative geometry — exactly what the pack is FOR
        "a peak broader than its own linewidth",
        "a sidelobe under half the height of the main feature",
        # coverage, which is itself a family gate
        "one continuous dark band spans the full width",
        # shape at the edges, not placement
        "the trace returns to a flat baseline at both edges",
        # a comparison that names its reference
        "the dip is deeper compared to the surrounding scatter",
    ])
    def test_relative_and_shape_statements_pass(self, text):
        assert J.warn_text(text) == [], text


class TestTheTiersStaySeparate:
    def test_a_prose_warning_never_drops_the_string(self):
        entry = {"family": "x",
                 "correct_signature": ["a broad feature dominates the panel"],
                 "abstain_when": [], "failure_appearance": {}}
        clean, dropped = J._scrub(entry)
        assert dropped == []
        assert clean["correct_signature"] == entry["correct_signature"]

    def test_a_drop_tier_violation_still_drops(self):
        entry = {"family": "x",
                 "correct_signature": ["the peak sits at 5 GHz"],
                 "abstain_when": [], "failure_appearance": {}}
        clean, dropped = J._scrub(entry)
        assert dropped and clean["correct_signature"] == []

    def test_the_shipped_pack_has_no_drop_tier_violation(self):
        """The existing pin, unchanged — the split must not have weakened it."""
        pack = J.load_pack()
        assert all(not e.get("lint_dropped") for e in pack.values())

    def test_the_shipped_pack_surfaces_its_prose_warnings(self):
        """…and the silence is now distinguishable from cleanliness."""
        pack = J.load_pack()
        total = sum(len(e.get("lint_warnings") or []) for e in pack.values())
        assert total > 0, ("the audit measured word-form violations in the "
                           "shipped pack; if this is 0 the recall regressed")

    def test_warnings_never_reach_the_judge_prompt(self):
        """They are maintainer output. The judge sees family knowledge only."""
        pack = J.load_pack()
        entry = next(e for e in pack.values() if e.get("lint_warnings"))
        block = J.prompt_block(entry) or ""
        assert "Clause B" not in block and "lint" not in block.lower()
