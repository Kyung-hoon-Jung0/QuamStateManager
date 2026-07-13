"""Unit tests for core/pulse_catalog.py (registry, lookup, templates, lengths)."""

from __future__ import annotations

import pytest

from quam_state_manager.core.pulse_catalog import (
    PULSE_CATALOG,
    build_template,
    by_qclass,
    infer_spec,
    inferred_length,
    resolve_length,
)

_QC = "quam.components.pulses."


class TestCatalogShape:
    def test_all_expected_classes_present(self):
        expected = {
            "SquarePulse", "SquareReadoutPulse", "GaussianPulse",
            "DragGaussianPulse", "DragCosinePulse",
            "FlatTopGaussianPulse", "FlatTopCosinePulse", "FlatTopTanhPulse",
            "FlatTopBlackmanPulse", "BlackmanIntegralPulse",
            "ErfSquarePulse", "SNZPulse",
            "GaussianFilteredSquarePulse", "GaussianFilteredSymmetricBipolarPulse",
            "WaveformPulse", "_FlatTopGaussianPulse", "_CosineBipolarPulse",
        }
        assert set(PULSE_CATALOG) == expected

    def test_qclass_strings_are_canonical(self):
        # The LabC quam loader needs these verbatim.
        for spec in PULSE_CATALOG.values():
            assert spec.qclass == _QC + spec.key

    def test_deprecated_not_creatable(self):
        assert not PULSE_CATALOG["_FlatTopGaussianPulse"].creatable
        assert not PULSE_CATALOG["_CosineBipolarPulse"].creatable
        assert PULSE_CATALOG["SquarePulse"].creatable

    def test_length_modes(self):
        assert PULSE_CATALOG["SquarePulse"].length_mode == "explicit"
        assert PULSE_CATALOG["SNZPulse"].length_mode == "inferred"
        assert PULSE_CATALOG["WaveformPulse"].length_mode == "derived"
        assert (PULSE_CATALOG["_FlatTopGaussianPulse"].length_pointer
                == "#./inferred_total_length")
        assert PULSE_CATALOG["SNZPulse"].length_pointer == "#./inferred_length"

    def test_readout_flags(self):
        assert PULSE_CATALOG["SquareReadoutPulse"].readout
        assert not PULSE_CATALOG["SquarePulse"].readout


class TestByQclass:
    def test_full_qclass(self):
        assert by_qclass(_QC + "DragCosinePulse").key == "DragCosinePulse"

    def test_bare_key(self):
        assert by_qclass("SquarePulse").key == "SquarePulse"

    def test_deprecated_aliases(self):
        assert by_qclass(_QC + "DragPulse").key == "DragGaussianPulse"
        assert by_qclass(_QC + "ConstantReadoutPulse").key == "SquareReadoutPulse"

    def test_unknown(self):
        assert by_qclass("quam_builder.custom.WeirdPulse") is None
        assert by_qclass(None) is None
        assert by_qclass(42) is None


class TestInferSpec:
    def test_explicit_class(self):
        spec = infer_spec({"__class__": _QC + "SquarePulse", "amplitude": 0.1})
        assert spec.key == "SquarePulse"

    def test_gate_flux_slot_defaults_to_square(self):
        # quam-builder's flux_pulse_qubit/coupler_flux_pulse default class
        spec = infer_spec({"amplitude": 0.05, "length": 100},
                          context_slot="flux_pulse_qubit")
        assert spec.key == "SquarePulse"
        spec = infer_spec({"amplitude": 0.0, "length": 100},
                          context_slot="coupler_flux_pulse")
        assert spec.key == "SquarePulse"

    def test_no_class_no_slot(self):
        assert infer_spec({"amplitude": 0.1}) is None

    def test_unknown_class(self):
        assert infer_spec({"__class__": "x.y.Custom"}) is None

    def test_non_dict(self):
        assert infer_spec("#./x180_DragCosine") is None
        assert infer_spec(None) is None


class TestBuildTemplate:
    def test_square_template(self):
        spec = PULSE_CATALOG["SquarePulse"]
        t = build_template(spec, {"amplitude": 0.2, "length": 80})
        assert t["__class__"] == _QC + "SquarePulse"
        assert t["amplitude"] == 0.2
        assert t["length"] == 80
        # id / digital_marker omitted when None
        assert "id" not in t and "digital_marker" not in t

    def test_inferred_length_pointer_written(self):
        spec = PULSE_CATALOG["SNZPulse"]
        t = build_template(spec, {"amplitude": 0.05, "flat_length": 20,
                                  "t_phi_eff": 2.0, "padding": 0})
        assert t["length"] == "#./inferred_length"

    def test_required_missing_falls_back_to_default(self):
        spec = PULSE_CATALOG["GaussianPulse"]
        t = build_template(spec, {"amplitude": 0.1, "length": 40})
        assert t["sigma"] == spec.param("sigma").default

    def test_whitelist_drops_unknown_fields(self):
        spec = PULSE_CATALOG["SquarePulse"]
        t = build_template(spec, {"amplitude": 0.1, "length": 80,
                                  "evil_extra": 1})
        assert "evil_extra" not in t

    def test_readout_keeps_digital_marker_default(self):
        spec = PULSE_CATALOG["SquareReadoutPulse"]
        t = build_template(spec, {"amplitude": 0.01, "length": 1000,
                                  "digital_marker": "ON"})
        assert t["digital_marker"] == "ON"


class TestInferredLength:
    @pytest.mark.parametrize("params,expected", [
        ({"flat_length": 100, "risetime_samples": 16}, 116),
        ({"flat_length": 100, "risetime_samples": 16,
          "post_zero_padding_length": 10}, 128),
        ({"flat_length": 1, "risetime_samples": 1}, 4),
    ])
    def test_erf(self, params, expected):
        assert inferred_length("ErfSquarePulse", params) == expected

    @pytest.mark.parametrize("params,expected", [
        ({"flat_length": 20}, 24),                       # 0+20+2+0=22 → 24
        ({"flat_length": 20, "t_phi_eff": 3.5}, 24),     # t_phi=2 → 24
        ({"flat_length": 20, "t_phi_eff": 2.0, "padding": 3}, 32),  # 6+20+2+2=30→32
    ])
    def test_snz(self, params, expected):
        assert inferred_length("SNZPulse", params) == expected

    def test_snz_negative_tphi(self):
        assert inferred_length("SNZPulse",
                               {"flat_length": 20, "t_phi_eff": -1}) is None

    def test_gaussian_filtered(self):
        assert inferred_length("GaussianFilteredSquarePulse",
                               {"pulse_length": 100}) == 100
        assert inferred_length("GaussianFilteredSquarePulse",
                               {"pulse_length": 101}) == 104

    def test_deprecated_total_length(self):
        assert inferred_length("_FlatTopGaussianPulse",
                               {"flat_length": 100, "smoothing_length": 20,
                                "post_zero_padding_length": 8}) == 128
        assert inferred_length("_CosineBipolarPulse",
                               {"flat_length": 96, "smoothing_length": 5,
                                "post_zero_padding_length": 3}) == 104

    def test_missing_params(self):
        assert inferred_length("SNZPulse", {}) is None

    def test_explicit_class_returns_none(self):
        assert inferred_length("SquarePulse", {"length": 100}) is None


class TestResolveLength:
    def test_explicit(self):
        spec = PULSE_CATALOG["SquarePulse"]
        assert resolve_length(spec, {"length": 100}) == 100

    def test_inferred(self):
        spec = PULSE_CATALOG["SNZPulse"]
        assert resolve_length(spec, {"length": "#./inferred_length",
                                     "flat_length": 20}) == 24

    def test_derived(self):
        spec = PULSE_CATALOG["WaveformPulse"]
        assert resolve_length(spec, {"waveform_I": [0.0, 0.1, 0.0]}) == 3

    def test_unresolvable_pointer_on_explicit_class(self):
        spec = PULSE_CATALOG["SquarePulse"]
        assert resolve_length(spec, {"length": "#../other/length"}) is None

    def test_unknown_spec_with_numeric_length(self):
        assert resolve_length(None, {"length": 48}) == 48

    def test_bool_is_not_a_length(self):
        assert resolve_length(None, {"length": True}) is None


# ---------------------------------------------------------------------------
# Class-churn hardening: resolve_qclass / infer_spec_ex / unmodeled_fields /
# chip_qclass (docs/… — Pulses page must survive QM-stack module-path churn)
# ---------------------------------------------------------------------------

from quam_state_manager.core.pulse_catalog import (  # noqa: E402
    chip_qclass,
    infer_spec_ex,
    resolve_qclass,
    unmodeled_fields,
)


class TestResolveQclass:
    def test_exact_full_path(self):
        spec, how = resolve_qclass(_QC + "SNZPulse")
        assert spec.key == "SNZPulse" and how == "exact"

    def test_bare_key_is_exact(self):
        spec, how = resolve_qclass("SNZPulse")
        assert spec.key == "SNZPulse" and how == "exact"

    def test_full_path_alias(self):
        spec, how = resolve_qclass(_QC + "DragPulse")
        assert spec.key == "DragGaussianPulse" and how == "alias"

    def test_foreign_prefix_resolves_by_leaf(self):
        spec, how = resolve_qclass(
            "quam_builder.architecture.superconducting.components.pulses.SNZPulse")
        assert spec.key == "SNZPulse" and how == "leaf"

    def test_foreign_prefix_alias_leaf(self):
        spec, how = resolve_qclass("x.y.DragPulse")
        assert spec.key == "DragGaussianPulse" and how == "leaf"
        spec, how = resolve_qclass("x.y.ConstantReadoutPulse")
        assert spec.key == "SquareReadoutPulse" and how == "leaf"

    def test_unknown_leaf_still_none(self):
        assert resolve_qclass("quam_builder.custom.WeirdPulse") == (None, None)
        assert resolve_qclass(None) == (None, None)
        assert resolve_qclass("") == (None, None)
        assert resolve_qclass(42) == (None, None)


class TestInferSpecEx:
    def test_explicit_leaf_match(self):
        spec, how = infer_spec_ex(
            {"__class__": "newstack.pulses.SquarePulse", "amplitude": 0.1})
        assert spec.key == "SquarePulse" and how == "leaf"

    def test_implicit_gate_slot(self):
        spec, how = infer_spec_ex({"amplitude": 0.05, "length": 100},
                                  context_slot="flux_pulse_qubit")
        assert spec.key == "SquarePulse" and how == "implicit"

    def test_no_class_no_slot(self):
        assert infer_spec_ex({"amplitude": 0.1}) == (None, None)


class TestUnmodeledFields:
    def test_stray_field_caught(self):
        spec = PULSE_CATALOG["_FlatTopGaussianPulse"]
        body = {"__class__": "new.pulses._FlatTopGaussianPulse",
                "amplitude": 0.1, "flat_length": 100, "smoothing_length": 20,
                "brand_new_knob": 3.0}
        assert unmodeled_fields(spec, body) == ["brand_new_knob"]

    def test_stock_snz_with_inferred_pointer_is_clean(self):
        # EVERY inferred-length pulse stores length="#./inferred_length" —
        # written by build_template and machine.save() alike. It must never
        # count as unmodeled or the caution fires on every healthy chip.
        spec = PULSE_CATALOG["SNZPulse"]
        body = {"__class__": _QC + "SNZPulse", "amplitude": 0.1,
                "flat_length": 20, "t_phi_eff": 2.0, "padding": 0,
                "length": "#./inferred_length", "id": None,
                "digital_marker": None, "axis_angle": None}
        assert unmodeled_fields(spec, body) == []

    def test_none_spec_or_non_dict(self):
        assert unmodeled_fields(None, {"x": 1}) == []
        assert unmodeled_fields(PULSE_CATALOG["SquarePulse"], "#./alias") == []


class TestChipQclass:
    SQ = "SquarePulse"

    @staticmethod
    def _chip(*classes, slot_class=None):
        ops = {
            f"op{i}": {"__class__": c, "amplitude": 0.1, "length": 40}
            for i, c in enumerate(classes)
        }
        merged = {"qubits": {"qA1": {"xy": {"operations": ops}}}}
        if slot_class is not None:
            merged["qubit_pairs"] = {"p": {"macros": {"cz": {
                "flux_pulse_qubit": {"__class__": slot_class, "amplitude": 0.1},
            }}}}
        return merged

    def test_reused_verbatim(self):
        chip = self._chip("newstack.pulses.SquarePulse")
        assert chip_qclass(chip, PULSE_CATALOG[self.SQ]) == (
            "newstack.pulses.SquarePulse", "reused")

    def test_reused_majority_then_lexicographic(self):
        # Mid-migration chip: two paths for the same leaf — deterministic.
        chip = self._chip("b.pulses.SquarePulse", "a.pulses.SquarePulse",
                          "b.pulses.SquarePulse")
        assert chip_qclass(chip, PULSE_CATALOG[self.SQ])[0] == "b.pulses.SquarePulse"
        tie = self._chip("b.pulses.SquarePulse", "a.pulses.SquarePulse")
        assert chip_qclass(tie, PULSE_CATALOG[self.SQ])[0] == "a.pulses.SquarePulse"

    def test_prefix_from_other_catalog_classes(self):
        chip = self._chip("newstack.pulses.DragCosinePulse",
                          "newstack.pulses.GaussianPulse")
        assert chip_qclass(chip, PULSE_CATALOG[self.SQ]) == (
            "newstack.pulses.SquarePulse", "prefix")

    def test_custom_class_never_donates_prefix(self):
        # A chip whose only classed pulse is a custom lab class must fall
        # back to the catalog path — quam_builder.custom.SquarePulse would
        # be wrong on EVERY stack.
        chip = self._chip("quam_builder.custom.WeirdPulse")
        assert chip_qclass(chip, PULSE_CATALOG[self.SQ]) == (
            PULSE_CATALOG[self.SQ].qclass, "catalog")

    def test_split_prefix_no_strict_majority(self):
        chip = self._chip("a.pulses.DragCosinePulse", "b.pulses.GaussianPulse")
        assert chip_qclass(chip, PULSE_CATALOG[self.SQ])[1] == "catalog"

    def test_gate_slot_class_counts_as_evidence(self):
        chip = self._chip(slot_class="newstack.pulses.SquarePulse")
        assert chip_qclass(chip, PULSE_CATALOG[self.SQ]) == (
            "newstack.pulses.SquarePulse", "reused")

    def test_empty_chip_falls_back_to_catalog(self):
        assert chip_qclass({}, PULSE_CATALOG[self.SQ]) == (
            PULSE_CATALOG[self.SQ].qclass, "catalog")
        assert chip_qclass(None, PULSE_CATALOG[self.SQ])[1] == "catalog"


class TestBuildTemplateQclass:
    def test_override_written_verbatim(self):
        spec = PULSE_CATALOG["SquarePulse"]
        t = build_template(spec, {"amplitude": 0.2, "length": 80},
                           qclass="newstack.pulses.SquarePulse")
        assert t["__class__"] == "newstack.pulses.SquarePulse"

    def test_default_unchanged(self):
        spec = PULSE_CATALOG["SquarePulse"]
        t = build_template(spec, {"amplitude": 0.2, "length": 80})
        assert t["__class__"] == spec.qclass
