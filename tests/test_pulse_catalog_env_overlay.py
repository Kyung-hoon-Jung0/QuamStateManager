"""Env catalog overlay (points 1-3): pulse_catalog.apply_env_overlay et al.

The overlay layers the selected environment's OWN pulse roster (the
``probe_state_schema._dump_pulse_roster`` shape, fixtures:
``tests/golden/state_schema_modern.json`` / ``state_schema_fork.json``)
additively over the static catalog:

- ``resolve_qclass`` gains ``how == "env"`` for roster-verified module homes,
- ``unmodeled_fields`` treats env-declared fields as known,
- ``chip_qclass`` accepts an env-verified home as a known home,
- ``waveform_synth`` marks unknown-but-env-recognized classes
  (``schema_known``) with a friendlier error.

IMPORTANT fixture fact (pinned below): the static catalog was aligned to the
same modern env the golden rosters were dumped from (docs/53), so with the
PRISTINE fixtures every roster home/field is already covered by the catalog —
the overlay must be a provable NO-OP there. The env-only paths are therefore
exercised through minimally-extended deep copies of the real modern roster
(simulating the next generation's home/field churn), never fully synthetic
rosters.

Hard regression fence: with NO overlay installed (the default), every output
is byte-identical to the pre-overlay catalog.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from quam_state_manager.core import pulse_catalog as pc
from quam_state_manager.core import waveform_synth as ws
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.pulse_catalog import (
    PULSE_CATALOG,
    apply_env_overlay,
    chip_qclass,
    env_overlay_active,
    resolve_qclass,
    unmodeled_fields,
)

GOLDEN = Path(__file__).parent / "golden"

_QC = "quam.components.pulses."
_ARCH = "quam_builder.architecture.superconducting.components.pulses"
# A module home NO generation of the catalog knows — simulates the next
# QM-stack churn (the overlay's whole reason to exist).
_NEXT = "quam_builder.next_gen.components.pulses"


@pytest.fixture(autouse=True)
def _no_overlay_leakage():
    """Module-global overlay must never leak between tests (or test files)."""
    apply_env_overlay(None)
    yield
    apply_env_overlay(None)


@pytest.fixture(scope="module")
def modern_roster() -> dict:
    data = json.loads((GOLDEN / "state_schema_modern.json").read_text(
        encoding="utf-8"))
    return data["pulse_roster"]


@pytest.fixture(scope="module")
def fork_roster() -> dict:
    data = json.loads((GOLDEN / "state_schema_fork.json").read_text(
        encoding="utf-8"))
    return data["pulse_roster"]


def _with_extra_home(roster: dict, leaf: str, home: str) -> dict:
    r = copy.deepcopy(roster)
    r[leaf]["homes"].append(home)
    return r


def _chip(*classes: str) -> dict:
    ops = {
        f"op{i}": {"__class__": c, "amplitude": 0.1, "length": 40}
        for i, c in enumerate(classes)
    }
    return {"qubits": {"qA1": {"xy": {"operations": ops}}}}


# ---------------------------------------------------------------------------
# (f) apply / clear round-trip
# ---------------------------------------------------------------------------

class TestApplyClear:
    def test_round_trip(self, modern_roster):
        assert env_overlay_active() is None
        apply_env_overlay(modern_roster)
        assert env_overlay_active() is modern_roster  # ref swap, no copy
        apply_env_overlay(None)
        assert env_overlay_active() is None

    def test_empty_roster_clears(self, modern_roster):
        apply_env_overlay(modern_roster)
        apply_env_overlay({})  # a broken/quam-less env probe ⇒ no overlay
        assert env_overlay_active() is None


# ---------------------------------------------------------------------------
# (a) resolve_qclass: the env step
# ---------------------------------------------------------------------------

class TestResolveEnv:
    def test_arch_home_snz_stays_exact_under_real_roster(self, modern_roster):
        # The catalog already registers the quam_builder arch home
        # (_EXTRA_HOMES) — the env step sits AFTER the exact-path step and
        # must never shadow it.
        qclass = _ARCH + ".SNZPulse"
        assert resolve_qclass(qclass) == (PULSE_CATALOG["SNZPulse"], "exact")
        apply_env_overlay(modern_roster)
        assert resolve_qclass(qclass) == (PULSE_CATALOG["SNZPulse"], "exact")

    def test_future_home_snz_leaf_becomes_env(self, modern_roster):
        # Next-generation churn: SNZPulse moves to a home the static catalog
        # has never heard of, but the selected env's roster verifies it.
        qclass = _NEXT + ".SNZPulse"
        spec, how = resolve_qclass(qclass)
        assert spec is PULSE_CATALOG["SNZPulse"] and how == "leaf"
        apply_env_overlay(_with_extra_home(modern_roster, "SNZPulse", _NEXT))
        spec, how = resolve_qclass(qclass)
        assert spec is PULSE_CATALOG["SNZPulse"] and how == "env"

    def test_env_requires_home_match_not_just_leaf(self, modern_roster):
        # The roster knows SNZPulse — but only at its own homes. A foreign
        # home must still resolve as the name-only "leaf" caution.
        apply_env_overlay(modern_roster)
        spec, how = resolve_qclass("some.fork.pulses.SNZPulse")
        assert spec is PULSE_CATALOG["SNZPulse"] and how == "leaf"

    def test_env_requires_catalog_spec(self, modern_roster):
        # The modern roster genuinely knows CosineBipolarPulse (no
        # underscore) at the arch home, but the catalog has no spec for that
        # leaf — the env step must not invent one.
        qclass = _ARCH + ".CosineBipolarPulse"
        assert modern_roster["CosineBipolarPulse"]["homes"] == [_ARCH]
        apply_env_overlay(modern_roster)
        assert resolve_qclass(qclass) == (None, None)

    def test_alias_and_bare_key_paths_untouched(self, modern_roster):
        apply_env_overlay(modern_roster)
        # DragPulse leaf has no catalog spec ⇒ env step skips ⇒ alias step.
        assert resolve_qclass(_QC + "DragPulse") == (
            PULSE_CATALOG["DragGaussianPulse"], "alias")
        # bare key has no module home ⇒ env step skips ⇒ bare-key "exact".
        assert resolve_qclass("SNZPulse") == (PULSE_CATALOG["SNZPulse"], "exact")

    def test_fork_roster_quam_home_is_exact_anyway(self, fork_roster):
        # The fork moved everything back into quam.components.pulses — the
        # catalog's transcription source, so exact with or without overlay.
        assert fork_roster["SNZPulse"]["homes"] == ["quam.components.pulses"]
        apply_env_overlay(fork_roster)
        assert resolve_qclass(_QC + "SNZPulse") == (
            PULSE_CATALOG["SNZPulse"], "exact")


# ---------------------------------------------------------------------------
# (b) unmodeled_fields: env-declared fields are known
# ---------------------------------------------------------------------------

class TestUnmodeledEnv:
    BODY = {
        "__class__": _ARCH + ".SNZPulse", "amplitude": 0.1, "flat_length": 20,
        "t_phi_eff": 2.0, "length": "#./inferred_length",
    }

    def test_renamed_field_suppressed_by_roster(self, modern_roster):
        # Simulate the next generation renaming SNZ "padding" →
        # "zero_padding": the body carries a field the static catalog does
        # not model, but the env's own class declares it.
        body = dict(self.BODY, zero_padding=4)
        spec = PULSE_CATALOG["SNZPulse"]
        assert unmodeled_fields(spec, body) == ["zero_padding"]
        roster = copy.deepcopy(modern_roster)
        roster["SNZPulse"]["fields"]["zero_padding"] = {"has_default": True}
        apply_env_overlay(roster)
        assert unmodeled_fields(spec, body) == []
        # ...but a field NEITHER side knows is still flagged.
        assert unmodeled_fields(spec, dict(body, mystery_knob=1)) == [
            "mystery_knob"]

    def test_pristine_roster_is_a_noop(self, modern_roster):
        # docs/53 alignment: every real roster field is already modeled by
        # the catalog, so the pristine overlay changes nothing.
        spec = PULSE_CATALOG["SNZPulse"]
        body = dict(self.BODY, brand_new_knob=3.0)
        before = unmodeled_fields(spec, body)
        apply_env_overlay(modern_roster)
        assert unmodeled_fields(spec, body) == before == ["brand_new_knob"]

    def test_fields_none_guard(self):
        # roster "fields" may be null (probe field-dump failure) — no crash,
        # no suppression.
        spec = PULSE_CATALOG["SquarePulse"]
        body = {"__class__": _QC + "SquarePulse", "amplitude": 0.1,
                "length": 40, "stray": 1}
        apply_env_overlay({"SquarePulse": {"homes": [_QC[:-1]], "fields": None}})
        assert unmodeled_fields(spec, body) == ["stray"]


# ---------------------------------------------------------------------------
# (c) chip_qclass: env-verified home counts as known
# ---------------------------------------------------------------------------

class TestChipQclassEnv:
    def test_env_verified_prefix_accepted(self, modern_roster):
        # Chip already migrated to the next-gen home; creating an SNZPulse
        # should follow the chip's prefix once the env verifies that home.
        chip = _chip(_NEXT + ".GaussianPulse", _NEXT + ".DragCosinePulse")
        spec = PULSE_CATALOG["SNZPulse"]
        assert chip_qclass(chip, spec) == (spec.qclass, "catalog")
        apply_env_overlay(_with_extra_home(modern_roster, "SNZPulse", _NEXT))
        assert chip_qclass(chip, spec) == (_NEXT + ".SNZPulse", "prefix")

    def test_roster_home_elsewhere_still_catalog(self, modern_roster):
        # The pristine roster knows SNZPulse — at the arch home only. It must
        # NOT bless the chip's unrelated dominant prefix.
        chip = _chip(_NEXT + ".GaussianPulse", _NEXT + ".DragCosinePulse")
        spec = PULSE_CATALOG["SNZPulse"]
        apply_env_overlay(modern_roster)
        assert chip_qclass(chip, spec) == (spec.qclass, "catalog")

    def test_reused_verbatim_unaffected(self, modern_roster):
        apply_env_overlay(modern_roster)
        chip = _chip("newstack.pulses.SquarePulse")
        assert chip_qclass(chip, PULSE_CATALOG["SquarePulse"]) == (
            "newstack.pulses.SquarePulse", "reused")


# ---------------------------------------------------------------------------
# (d) waveform_synth: unknown class the env recognizes → schema_known
# ---------------------------------------------------------------------------

class TestSynthSchemaKnown:
    def test_fabricated_leaf_marks_schema_known(self):
        qclass = "otherlab.custom.pulses.FooPulse"
        p = ws.synthesize(qclass, {})
        assert not p["ok"] and "schema_known" not in p
        apply_env_overlay({"FooPulse": {"homes": ["otherlab.custom.pulses"],
                                        "fields": None}})
        p = ws.synthesize(qclass, {})
        assert not p["ok"] and p["schema_known"] is True
        assert "recognized by the selected environment" in p["error"]
        assert "preview unavailable" in p["error"]

    def test_real_roster_cosine_bipolar_no_underscore(self, modern_roster):
        # Genuine real-fixture case: the modern env ships CosineBipolarPulse
        # (no underscore) which the catalog has no spec for at all.
        qclass = _ARCH + ".CosineBipolarPulse"
        p = ws.synthesize(qclass, {})
        assert not p["ok"] and "schema_known" not in p
        apply_env_overlay(modern_roster)
        p = ws.synthesize(qclass, {})
        assert p["schema_known"] is True
        assert "'CosineBipolarPulse'" in p["error"]
        assert "recognized by the selected environment" in p["error"]

    def test_synth_for_operation_unknown_class(self, tmp_path, modern_roster):
        state = {"qubits": {"qA1": {"xy": {"operations": {
            "mystery": {"length": 10, "amplitude": 0.1,
                        "__class__": _ARCH + ".CosineBipolarPulse"},
        }}}}}
        wiring = {"wiring": {}, "network": {"host": "10.1.1.1",
                                            "cluster_name": "test"}}
        (tmp_path / "state.json").write_text(json.dumps(state),
                                             encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring),
                                              encoding="utf-8")
        store = QuamStore(tmp_path)
        path = "qubits.qA1.xy.operations.mystery"
        p = ws.synth_for_operation(store, path)
        assert not p["ok"] and "schema_known" not in p
        assert "unrecognized pulse class" in p["error"]
        apply_env_overlay(modern_roster)
        p = ws.synth_for_operation(store, path)
        assert not p["ok"] and p["schema_known"] is True
        assert "recognized by the selected environment" in p["error"]
        assert p["resolved_params"]["amplitude"] == 0.1  # raw fields kept

    def test_env_resolved_class_synthesizes_normally(self, modern_roster):
        # A roster-verified home is fully-known: synth runs the catalog math
        # and reports class_match="env" (not the leaf caution).
        apply_env_overlay(_with_extra_home(modern_roster, "SNZPulse", _NEXT))
        p = ws.synthesize(_NEXT + ".SNZPulse",
                          {"amplitude": 0.05, "flat_length": 20,
                           "t_phi_eff": 2.0, "padding": 0,
                           "length": "#./inferred_length"})
        assert p["ok"], p["error"]
        assert p["class_match"] == "env" and p["spec_key"] == "SNZPulse"


# ---------------------------------------------------------------------------
# (e) HARD REGRESSION FENCE — overlay=None is byte-identical; the pristine
# modern roster is a provable no-op (catalog already aligned to that env)
# ---------------------------------------------------------------------------

# (input, expected spec key or None, expected how) — pre-change semantics,
# verbatim from tests/test_pulse_catalog.py::TestResolveQclass.
_RESOLVE_BATTERY = [
    (_QC + "SNZPulse", "SNZPulse", "exact"),
    (_ARCH + ".SNZPulse", "SNZPulse", "exact"),
    ("quam_builder.common.pulses.GaussianPulse", "GaussianPulse", "exact"),
    ("SNZPulse", "SNZPulse", "exact"),
    (_QC + "DragPulse", "DragGaussianPulse", "alias"),
    (_QC + "ConstantReadoutPulse", "SquareReadoutPulse", "alias"),
    ("newstack.pulses.SNZPulse", "SNZPulse", "leaf"),
    ("x.y.DragPulse", "DragGaussianPulse", "leaf"),
    ("quam_builder.custom.WeirdPulse", None, None),
    ("", None, None),
    (None, None, None),
    (42, None, None),
]

_SNZ_BODY = {"__class__": _QC + "SNZPulse", "amplitude": 0.1,
             "flat_length": 20, "t_phi_eff": 2.0, "padding": 0,
             "length": "#./inferred_length", "id": None,
             "digital_marker": None, "axis_angle": None}


def _fence_outputs():
    resolved = [resolve_qclass(q) for q, _k, _h in _RESOLVE_BATTERY]
    unmodeled = [
        unmodeled_fields(PULSE_CATALOG["SNZPulse"], _SNZ_BODY),
        unmodeled_fields(PULSE_CATALOG["SNZPulse"],
                         dict(_SNZ_BODY, brand_new_knob=3.0)),
        unmodeled_fields(None, {"x": 1}),
    ]
    chips = [
        chip_qclass(_chip("newstack.pulses.SquarePulse"),
                    PULSE_CATALOG["SquarePulse"]),
        chip_qclass(_chip("quam_builder.common.pulses.FlatTopGaussianPulse",
                          "quam_builder.common.pulses.FlatTopCosinePulse"),
                    PULSE_CATALOG["GaussianPulse"]),
        chip_qclass(_chip("newstack.pulses.DragCosinePulse",
                          "newstack.pulses.GaussianPulse"),
                    PULSE_CATALOG["SquarePulse"]),
        chip_qclass({}, PULSE_CATALOG["SquarePulse"]),
    ]
    synth = [
        ws.synthesize("quam_builder.custom.WeirdPulse", {}),
        ws.synthesize("SNZPulse", dict(_SNZ_BODY)),
    ]
    return resolved, unmodeled, chips, synth


class TestNoOverlayByteIdentical:
    def test_default_matches_pre_change_semantics(self, monkeypatch):
        monkeypatch.setattr(pc, "_ENV_OVERLAY", None)
        resolved, unmodeled, chips, synth = _fence_outputs()
        for (qc, key, how), (spec, got_how) in zip(_RESOLVE_BATTERY, resolved):
            want = PULSE_CATALOG[key] if key else None
            assert spec is want and got_how == how, qc
        assert unmodeled == [[], ["brand_new_knob"], []]
        assert chips == [
            ("newstack.pulses.SquarePulse", "reused"),
            ("quam_builder.common.pulses.GaussianPulse", "prefix"),
            (PULSE_CATALOG["SquarePulse"].qclass, "catalog"),
            (PULSE_CATALOG["SquarePulse"].qclass, "catalog"),
        ]
        unknown, snz = synth
        assert unknown["error"] == (
            "no synthesizer for pulse class 'quam_builder.custom.WeirdPulse'")
        assert "schema_known" not in unknown
        assert snz["ok"] and snz["class_match"] == "exact"

    def test_pristine_modern_roster_is_byte_identical(self, modern_roster):
        # The catalog was aligned to this very env (docs/53): every roster
        # home/field is already covered, so installing the REAL roster must
        # not move a single output — except the unknown-class synth payload,
        # which legitimately gains the schema_known grace note for leaves
        # the env ships (WeirdPulse is not one of them).
        apply_env_overlay(None)
        base = _fence_outputs()
        apply_env_overlay(modern_roster)
        assert _fence_outputs() == base

    def test_clearing_restores_baseline(self, modern_roster):
        apply_env_overlay(None)
        base = _fence_outputs()
        apply_env_overlay(_with_extra_home(modern_roster, "SNZPulse", _NEXT))
        assert resolve_qclass(_NEXT + ".SNZPulse")[1] == "env"  # overlay live
        apply_env_overlay(None)
        assert _fence_outputs() == base
