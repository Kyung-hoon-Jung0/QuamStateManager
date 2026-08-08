"""Runner+agent P4 (docs/78 D-3/D-4/D-5): what the agent may change.

The classification axis is "can a wrong choice LIE to us?", not "is it a
number?" — the whole safety story rests on the judge seeing the consequence.
"""
from __future__ import annotations

import inspect

import pytest

from quam_state_manager.core.autofit import action_space as A


class TestClassification:
    @pytest.mark.parametrize("key", ["num_shots", "frequency_span_in_mhz",
                                     "min_amp_factor", "num_flux_points",
                                     "operation_amplitude_factor"])
    def test_self_revealing_knobs_are_class_a(self, key):
        """`num_shots = 3` is a number and is perfectly safe — wrong ⇒ visibly
        noisy, cost one run."""
        assert A.classify(key) == "A"

    @pytest.mark.parametrize("key", ["reset_type", "use_state_discrimination",
                                     "multiplexed"])
    def test_deceptive_knobs_are_class_b(self, key):
        """`use_state_discrimination = True` without calibrated IQ blobs is a
        boolean and is dangerous — clean-looking populations that are garbage."""
        assert A.classify(key) == "B"

    def test_wiring_facts_are_frozen(self):
        for k in ("line_attenuation_in_db", "input_line_impedance_in_ohm"):
            assert A.classify(k) == "frozen"

    def test_load_data_id_is_reserved(self):
        """docs/78 §17.6: D-3 claimed this was already blocked. It was not, and
        it is the dangerous one — a node given it replays archived data instead
        of measuring, so a plan could report success with the fridge idle."""
        assert A.classify("load_data_id") == "reserved"

    def test_an_unclassified_key_is_not_silently_allowed(self):
        """A parameter nobody classified is one nobody thought about, and the
        deceptive ones are exactly the ones that look harmless."""
        assert A.classify("some_new_node_param") == "unknown"


class TestSanitize:
    def test_forbidden_keys_are_dropped_with_a_reason(self):
        clean, dropped = A.sanitize({
            "num_shots": 200, "load_data_id": 4711,
            "line_attenuation_in_db": 3, "simulate": True})
        assert clean == {"num_shots": 200}
        by = {d["key"]: d for d in dropped}
        assert set(by) == {"load_data_id", "line_attenuation_in_db", "simulate"}
        assert all(d["reason"] for d in dropped), "a silent drop is not a drop"
        assert "replay archived data" in by["load_data_id"]["reason"]

    def test_the_targets_key_is_reserved_per_node(self):
        clean, dropped = A.sanitize({"qubits": ["qA1"], "num_shots": 10},
                                    targets_name="qubits")
        assert clean == {"num_shots": 10}
        assert dropped[0]["key"] == "qubits"

    def test_class_b_survives_sanitize_for_the_precondition_check(self):
        """Sanitize removes what the agent may NEVER set; class B is proposed
        and then precondition-checked, so it must reach that check."""
        clean, _ = A.sanitize({"reset_type": "active"})
        assert clean == {"reset_type": "active"}


class TestAgentWritePath:
    def test_the_real_backend_sanitizes_before_queueing(self):
        """The agent's overrides go through scheduler.add_item, whose own
        reserved set does NOT include load_data_id. This is the choke point."""
        from quam_state_manager.core.autofit import realbackend
        src = inspect.getsource(realbackend.RealBackend.run_step)
        assert "action_space.sanitize" in src
        assert "param_overrides\": safe_params" in src or \
               '"param_overrides": safe_params' in src


class TestBounds:
    def test_bounds_never_come_from_schema_defaults(self):
        """D-5 caveat: observed values leave defaults far behind — num_shots=3
        against a default of 100, flux ±2.5 V against ±0.5 V."""
        b = A.bounds_for("qubit_spectroscopy")
        assert b["num_shots"]["min"] <= 3
        assert b["num_shots"]["source"] == "hardware"

    def test_the_corpus_widens_a_soft_floor_but_not_a_physical_ceiling(self):
        """A bound that would reject what this lab has actually run is a false
        constraint — the same rule the P2 bands are built on. But hardware
        reach is physics: the corpus cannot widen it."""
        ranges = {"fam": {"num_shots": {"min": 1, "max": 5_000_000},
                          "frequency_span_in_mhz": {"min": 0.05,
                                                    "max": 99_999}}}
        b = A.bounds_for("fam", ranges)
        # real use is accommodated — and then some, because an observed range
        # is a SAMPLE, not a limit (docs/78 §22.1: a zero-slack envelope was
        # vacuous on its own data and rejected 2-22% of held-out usage)
        assert b["num_shots"]["max"] >= 5_000_000
        assert b["frequency_span_in_mhz"]["max"] < 99_999  # ceiling held
        assert b["frequency_span_in_mhz"]["min"] <= 0.05


class TestTheEnvelopeIsASampleNotALimit:
    """docs/78 §22.1 — three shape defects, each measured."""

    def test_an_observed_range_is_widened_before_it_binds(self):
        b = A.bounds_for("fam", {"fam": {"num_shots": {"min": 100,
                                                       "max": 200}}})
        assert b["num_shots"]["max"] > 200 and b["num_shots"]["min"] < 100

    def test_a_sweep_edge_is_bounded_only_on_its_dangerous_side(self):
        """`min_power_dbm = -40` was rejected for being ABOVE the observed
        -50 — but starting a sweep higher is strictly safer."""
        b = A.bounds_for("fam", {"fam": {"min_power_dbm": {"min": -50,
                                                           "max": -45}}})
        assert b["min_power_dbm"]["max"] is None
        b2 = A.bounds_for("fam", {"fam": {"max_power_dbm": {"min": -10,
                                                            "max": 0}}})
        assert b2["max_power_dbm"]["min"] is None

    def test_a_coarser_step_than_ever_observed_is_not_refused(self):
        b = A.bounds_for("fam", {"fam": {"frequency_step_in_mhz":
                                         {"min": 0.25, "max": 0.5}}})
        ok, bad = A.validate_proposal(
            {"frequency_step_in_mhz": 0.05},
            {"properties": {"frequency_step_in_mhz":
                            {k: v for k, v in
                             (("minimum", b["frequency_step_in_mhz"]["min"]),
                              ("maximum", b["frequency_step_in_mhz"]["max"]))
                             if v is not None}}})
        assert ok and not bad

    def test_a_knob_nobody_varied_does_not_enforce_its_own_default(self):
        """One observed value that IS the schema default is not evidence of a
        limit — it is evidence nobody touched the knob."""
        ranges = {"fam": {"num_freq_points": {"min": 101, "max": 101}}}
        b = A.bounds_for("fam", ranges,
                         schema_defaults={"fam": {"num_freq_points": 101}})
        assert "num_freq_points" not in b

    def test_a_single_value_that_is_NOT_the_default_still_bounds_loosely(self):
        ranges = {"fam": {"num_freq_points": {"min": 101, "max": 101}}}
        b = A.bounds_for("fam", ranges,
                         schema_defaults={"fam": {"num_freq_points": 51}})
        assert b["num_freq_points"]["max"] > 101   # widened, not pinned


class TestReducedSchema:
    NODE_SCHEMA = {"properties": {
        "num_shots": {"type": "integer", "description": "averages"},
        "frequency_span_in_mhz": {"type": "number"},
        "reset_type": {"type": "string",
                       "description": "Must be implemented as a method of "
                                      "Quam.qubit"},
        "load_data_id": {"type": "integer"},
        "line_attenuation_in_db": {"type": "number"},
        "qubits": {"type": "array"},
    }}

    def test_only_class_a_survives_and_carries_bounds(self):
        s = A.reduced_schema(self.NODE_SCHEMA, "qubit_spectroscopy",
                             targets_name="qubits")
        assert set(s["properties"]) == {"num_shots", "frequency_span_in_mhz"}
        assert s["additionalProperties"] is False
        assert s["properties"]["num_shots"]["minimum"] >= 1
        assert "maximum" in s["properties"]["frequency_span_in_mhz"]

    def test_a_missing_node_schema_opens_nothing(self):
        """No recorded schema ⇒ no fields opened. The failure direction is
        'the agent may change nothing', never 'anything goes'."""
        assert A.reduced_schema(None, "qubit_spectroscopy")["properties"] == {}


class TestValidateProposal:
    def setup_method(self):
        self.schema = A.reduced_schema(TestReducedSchema.NODE_SCHEMA,
                                       "qubit_spectroscopy",
                                       targets_name="qubits")

    def test_an_out_of_bound_value_is_rejected_not_clamped(self):
        """Clamping would hand the loop a number nobody chose and hide that the
        agent asked for something impossible."""
        ok, bad = A.validate_proposal({"num_shots": 0}, self.schema)
        assert ok == {} and bad and "below" in bad[0]

    def test_a_field_outside_the_allowed_set_is_rejected(self):
        ok, bad = A.validate_proposal({"load_data_id": 12}, self.schema)
        assert ok == {} and "not in the allowed set" in bad[0]

    def test_a_good_proposal_passes_through(self):
        ok, bad = A.validate_proposal({"num_shots": 400}, self.schema)
        assert ok == {"num_shots": 400} and bad == []


class TestClassBPreconditions:
    def test_state_discrimination_needs_calibrated_blobs(self):
        allowed, why = A.check_class_b("use_state_discrimination", True)
        assert not allowed and "garbage" in why
        allowed, _ = A.check_class_b("use_state_discrimination", True,
                                     chip_facts={"iq_blobs_calibrated": True})
        assert allowed

    def test_an_unverifiable_precondition_refuses(self):
        """The recorded schema states this in PROSE ('Must be implemented as a
        method of Quam.qubit'). Prose is the specification; enforcement is
        code, and an unverifiable precondition is not a satisfied one."""
        allowed, why = A.check_class_b("reset_type", "active")
        assert not allowed and "cannot verify" in why
        allowed, _ = A.check_class_b(
            "reset_type", "active", chip_facts={"reset_methods": ["active"]})
        assert allowed

    def test_turning_something_off_never_needs_a_precondition(self):
        for key in ("use_state_discrimination", "multiplexed"):
            allowed, _ = A.check_class_b(key, False)
            assert allowed

    def test_an_unknown_class_b_key_is_refused(self):
        allowed, why = A.check_class_b("some_future_flag", True)
        assert not allowed and "refusing" in why

    def test_apply_reports_every_refusal(self):
        ok, refused = A.apply_class_b({"use_state_discrimination": True,
                                       "multiplexed": False})
        assert ok == {"multiplexed": False}
        assert [r["key"] for r in refused] == ["use_state_discrimination"]
