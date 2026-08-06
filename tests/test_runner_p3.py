"""Runner+agent P3a/P3b (docs/78 §16): the vision judge's family knowledge and
the two number-free asks that terminate the loop and stop it.

The judge's *calibration* (D-7, two-sided, per family with sample counts) is
P3c/P3d and needs a real provider; what is pinned here is everything that must
hold BEFORE any model is called — Clause B, the schemas, the numeric guard, and
the safe defaults when the judge is unavailable.
"""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core.autofit import auditor, families, judge_pack


# ---------------------------------------------------------------------------
# P3a — the exemplar pack
# ---------------------------------------------------------------------------

class TestClauseBLint:
    """docs/47 Clause B: a feature's position inside the sweep window is an
    artefact of the window the experimenter chose, not physics. An exemplar
    that teaches position transfers a falsehood to the next chip."""

    @pytest.mark.parametrize("text", [
        "the dip sits near the middle of the window",
        "the peak is about 30% of the way across the sweep",
        "a narrow dip at 7.2 GHz",
        "the ridge spans 0.5 V of flux",
        "the feature is in the left third",
        "the optimum is at x = 0.31",
        "see D:/work_laptop/archive/run/figures.amplitude.png",
        "as in run #575_1Q_08b_qubit_spectroscopy_vs_power",
    ])
    def test_position_and_unit_claims_are_caught(self, text):
        assert judge_pack.lint_text(text), text

    @pytest.mark.parametrize("text", [
        "the tallest narrow feature in the trace",
        "a sidelobe under half the height of the main feature and more than "
        "two linewidths away from it",
        "fringes that converge toward a single drive amplitude as the pulse "
        "count grows",
        "the ridge curves smoothly and has exactly one extremum",
        "the feature is cut off at the edge of the sweep",
        "three or more full oscillations are visible",
    ])
    def test_relative_geometry_is_allowed(self, text):
        assert judge_pack.lint_text(text) == [], text

    def test_direction_language_survives(self):
        """Our own schema reports direction=left|right, and edge truncation is
        a real measurement fact — banning it would gut the seed-shift hint."""
        assert judge_pack.lint_text(
            "the feature appears to continue beyond the left edge") == []

    @pytest.mark.parametrize("text", [
        "the band is many times narrower than the swept frequency range",
        "excursions that are a small fraction of the plotted frequency window",
        "the trough is narrow compared with the plotted frequency window",
        "a few pixels wide and far narrower than the swept frequency span",
    ])
    def test_window_sizing_is_caught_even_with_no_digits(self, text):
        """The same Clause-B error written entirely in words. The critic pass
        found five of these in the first-draft pack and no number/unit check
        could see any of them: identical physics, zoomed in, becomes 'a large
        fraction' and gets rejected."""
        assert judge_pack.lint_text(text), text

    @pytest.mark.parametrize("text", [
        "its minimum sits on the measured minimum within a fraction of the "
        "notch's own width",
        "scatter is a large fraction of the notch depth",
        "the fitted width is far narrower than the visible hump",
        "a hairline spike narrower than the sample spacing",
        "the fringes break up over part of the sweep",
        "its width relative to the swept window carries no information",
    ])
    def test_feature_relative_sizing_and_coverage_survive(self, text):
        """Sizing against the FEATURE is the correct form the pack is supposed
        to use, and 'over part of the sweep' is a COVERAGE statement — itself
        one of the deterministic gates. Flagging either would delete real
        knowledge, which the loader does silently by design."""
        assert judge_pack.lint_text(text) == [], text


class TestPackContents:
    def test_every_scoped_family_has_an_entry(self):
        pack = judge_pack.load_pack()
        for key in ("resonator_spectroscopy", "resonator_spectroscopy_vs_power",
                    "resonator_spectroscopy_vs_flux",
                    "resonator_spectroscopy_vs_coupler_flux",
                    "qubit_spectroscopy", "qubit_spectroscopy_vs_power",
                    "qubit_spectroscopy_vs_flux",
                    "qubit_spectroscopy_vs_coupler_flux", "power_rabi"):
            assert key in pack, f"no judge-pack entry for {key}"
            assert key in families.FAMILIES, f"{key} is not a real family"

    def test_shipped_pack_is_clause_b_clean(self):
        """The shipped pack must need no scrubbing. A violation here is not a
        style nit — it is an exemplar that teaches the judge a falsehood."""
        for key, entry in judge_pack.load_pack().items():
            assert entry.get("lint_dropped") == [], (key, entry["lint_dropped"])

    def test_two_d_map_families_declare_no_localizer(self):
        """docs/47: the 2-D map families have no single axis the feature sits
        on — they are signature-trust-only, and saying otherwise would invite
        the judge to point at a position."""
        pack = judge_pack.load_pack()
        for key in ("resonator_spectroscopy_vs_flux",
                    "resonator_spectroscopy_vs_coupler_flux",
                    "qubit_spectroscopy_vs_flux",
                    "qubit_spectroscopy_vs_coupler_flux",
                    "qubit_spectroscopy_vs_power"):
            assert pack[key]["localizer"] == "none", key

    def test_power_rabi_covers_the_error_amplification_map(self):
        """docs/78 P3a names this specifically: a correct 2-D power-rabi figure
        is an error-amplification map — fringes CONVERGING on one amplitude as
        the pulse count grows — not a single sine. The archived figure is
        per-qubit 1-D, so the entry must carry BOTH and say which bullets apply
        to which; describing only the sine would leave the PoC's own success
        criterion untaught."""
        entry = judge_pack.load_pack()["power_rabi"]
        text = " ".join(entry["correct_signature"]).lower()
        assert "converge" in text and "fringe" in text and "pulse count" in text
        # the sine bullets must be scoped, or a judge shown the map looks for
        # the wrong thing entirely
        assert "1-d sweep layout only" in text

    def test_a_hand_edited_violation_is_dropped_not_taught(self, tmp_path,
                                                           monkeypatch):
        d = tmp_path / "v9"
        d.mkdir()
        (d / "fam.json").write_text(json.dumps({
            "family": "fam", "axes": "drive amplitude versus response",
            "correct_signature": ["the tallest narrow feature",
                                  "the peak sits near the middle of the window"],
            "failure_appearance": {"wrong_peak": "a second feature at 7.2 GHz",
                                   "no_signal": "flat noise", "noisy": None,
                                   "drifted": None,
                                   "feature_present_fit_failed": None},
            "abstain_when": ["the figure is unreadable"], "localizer": "1d",
            "notes": "",
        }), encoding="utf-8")
        monkeypatch.setattr(judge_pack, "_PACK_ROOT", tmp_path)
        judge_pack.clear_cache()
        entry = judge_pack.load_pack("v9")["fam"]
        assert entry["correct_signature"] == ["the tallest narrow feature"]
        assert entry["failure_appearance"]["wrong_peak"] is None
        assert len(entry["lint_dropped"]) == 2
        block = judge_pack.prompt_block(entry)
        assert "middle of the window" not in block and "7.2 GHz" not in block
        judge_pack.clear_cache()

    def test_prompt_block_states_the_rule_even_for_a_clean_entry(self):
        block = judge_pack.prompt_block(judge_pack.entry_for("power_rabi"))
        assert "artefact of the sweep" in block

    def test_unknown_family_teaches_nothing(self):
        assert judge_pack.entry_for("no_such_family") is None
        assert judge_pack.prompt_block(None) == ""

    def test_the_pack_is_declared_as_package_data(self):
        """The pack is DATA inside the package. Left out of the wheel it fails
        SILENTLY — load_pack() returns {} and the judge rules on figures it was
        taught nothing about, with no error anywhere."""
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        assert "judge_packs" in (root / "MANIFEST.in").read_text(encoding="utf-8")
        assert "judge_packs" in (root / "pyproject.toml").read_text(encoding="utf-8")

    def test_no_lab_or_chip_identifier_shapes_reach_the_pack(self):
        """The pack ships publicly. The lint already catches paths and run ids;
        this catches the other mechanical shape a lab/chip/env name takes — a
        token carrying a digit or an underscore inside it. Physics prose never
        does. (A blocklist of the real names is impossible here: it would be
        the leak. Name review stays human, as judge_pack's docstring says.)"""
        import re
        blob = json.dumps(judge_pack.load_pack())
        ident = re.compile(r"\b(?![0-9])[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+\b"
                           r"|\b[A-Z][a-z]+[0-9]+[A-Za-z0-9]*\b")
        suspects = {m.group(0) for m in ident.finditer(blob)
                    # the pack's own schema keys are the only legitimate ones
                    if m.group(0) not in {
                        "correct_signature", "failure_appearance",
                        "abstain_when", "wrong_peak", "no_signal",
                        "feature_present_fit_failed", "figures_opened",
                        "lint_dropped", "power_rabi", "qubit_spectroscopy",
                        "resonator_spectroscopy",
                        "qubit_spectroscopy_vs_power",
                        "qubit_spectroscopy_vs_flux",
                        "qubit_spectroscopy_vs_coupler_flux",
                        "resonator_spectroscopy_vs_power",
                        "resonator_spectroscopy_vs_flux",
                        "resonator_spectroscopy_vs_coupler_flux"}}
        assert not suspects, f"identifier-shaped tokens in the pack: {suspects}"


# ---------------------------------------------------------------------------
# P3b — the signature ask (the §1.3 terminator)
# ---------------------------------------------------------------------------

def _auditor(script=None):
    return auditor.Auditor({"provider": "fake"},
                           auditor.FakeProvider(script or {}))


class TestSignatureAsk:
    def test_clear_is_the_only_acceptance(self):
        assert auditor.parse_signature('{"signature":"clear"}').accepted
        assert not auditor.parse_signature('{"signature":"unclear"}').accepted
        assert not auditor.parse_signature('{"signature":"absent"}').accepted

    @pytest.mark.parametrize("text", [
        "", "not json at all", '{"signature":"probably"}', '{"verdict":"accept"}',
        '{"signature":true}',
    ])
    def test_unusable_replies_never_terminate_the_loop(self, text):
        v = auditor.parse_signature(text)
        assert v.signature == "unclear" and not v.accepted

    def test_the_bundle_carries_family_knowledge_but_no_fit_numbers(self):
        b = auditor.build_signature_bundle(
            family_key="qubit_spectroscopy", family_label="Qubit spectroscopy",
            target="qA1", figure_path=None)
        blob = json.dumps(b["context"])
        assert b["context"]["taught"] is True
        assert "A CORRECT signature" in b["context"]["family_knowledge"]
        # the claim is deliberately absent: handing it over invites reasoning
        # backwards from the number to "clear"
        assert "claimed_fit" not in blob and "fit_entry" not in blob

    def test_an_unavailable_judge_answers_unclear(self):
        off = auditor.Auditor({"provider": "off"})
        b = auditor.build_signature_bundle(
            family_key="power_rabi", family_label="Power Rabi", target="qA1",
            figure_path=None)
        v = off.signature(b)
        assert v.signature == "unclear" and not v.accepted

    def test_budget_exhaustion_answers_unclear_not_clear(self):
        a = _auditor({("signature", "qA1"): {"signature": "clear"}})
        a.settings["max_calls_per_plan"] = 1
        b = auditor.build_signature_bundle(
            family_key="power_rabi", family_label="Power Rabi", target="qA1",
            figure_path=None)
        assert a.signature(b).accepted
        assert not a.signature(b).accepted        # budget spent ⇒ unclear

    def test_numeric_emissions_are_discarded_and_flagged(self):
        v = auditor.parse_signature(
            '{"signature":"clear","reason":"ok","corrected_frequency":7.2e9}')
        assert v.accepted and v.discarded_numeric
        assert not hasattr(v, "corrected_frequency")


class TestComparisonAsk:
    @pytest.mark.parametrize("word", ["better", "worse", "same"])
    def test_the_three_verdicts_parse(self, word):
        assert auditor.parse_comparison(
            json.dumps({"comparison": word})).comparison == word

    @pytest.mark.parametrize("text", ["", "nope", '{"comparison":"improved"}',
                                      '{"better":true}'])
    def test_unusable_replies_read_as_same(self, text):
        """`same` is the safe unknown: it neither claims progress (which would
        keep a hopeless target running) nor manufactures a regression (which
        would trip the stop-loss on a good run)."""
        assert auditor.parse_comparison(text).comparison == "same"

    def test_image_order_is_previous_then_current(self, tmp_path):
        prev, cur = tmp_path / "a.png", tmp_path / "b.png"
        prev.write_bytes(b"PREVIOUS")
        cur.write_bytes(b"CURRENT")
        b = auditor.build_comparison_bundle(
            family_label="Power Rabi", target="qA1",
            previous_figure=prev, current_figure=cur)
        import base64
        assert [base64.b64decode(x) for x in b["images_b64"]] == \
            [b"PREVIOUS", b"CURRENT"]
        assert "PREVIOUS attempt" in b["context"]["note"]

    def test_missing_figures_do_not_shift_the_order(self, tmp_path):
        """A dropped image must not silently turn 'previous' into 'current' —
        with only one readable figure the pair is incomplete, and the caller
        can see that from the length."""
        cur = tmp_path / "b.png"
        cur.write_bytes(b"CURRENT")
        b = auditor.build_comparison_bundle(
            family_label="f", target="qA1", previous_figure=tmp_path / "gone",
            current_figure=cur)
        assert len(b["images_b64"]) == 1

    def test_numeric_emissions_are_discarded_and_flagged(self):
        v = auditor.parse_comparison(
            '{"comparison":"better","reason":"cleaner","snr":12.4}')
        assert v.comparison == "better" and v.discarded_numeric


class TestSchemasStayNumberFree:
    def test_no_ask_declares_a_numeric_field(self):
        """docs/47: the schemas are structurally number-free. This pins the
        contract at the source rather than trusting each parser."""
        for system in (auditor._SYSTEM, auditor._SIGNATURE_SYSTEM,
                       auditor._COMPARE_SYSTEM):
            assert "NEVER estimate, correct, or emit any numeric value" in system
        assert auditor.SIGNATURES == ("clear", "unclear", "absent")
        assert auditor.COMPARISONS == ("better", "worse", "same")

    def test_the_guard_is_one_implementation(self):
        for text, allowed in (
                ('{"verdict":"accept","x":1}', ("verdict",)),
                ('{"signature":"clear","x":1}', ("signature",)),
                ('{"comparison":"same","x":1}', ("comparison",))):
            assert auditor._numeric_emission(json.loads(text), allowed)
        # booleans are not numbers — feature_visible must survive
        assert not auditor._numeric_emission({"feature_visible": True}, ())
