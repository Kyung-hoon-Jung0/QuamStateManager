"""power_rabi's wide verification (docs/78 §17.6) — corpus-derived, not scaled.

§17.6 left `power_rabi` with no `verify_wide` at all. The obvious fix was the
shape the four spectroscopy families use — multiply the swept span by four —
and 230 archived runs refuse it:

* the family is TWO experiments. 122 survey runs sweep 1 pulse over the full
  prefactor window (103 of them at exactly [0.001, 1.99], step 0.005); 108
  error-amplification runs sweep 20-160 pulses over a median width of 0.3.
* pulse count and window width are anti-correlated because they are physically
  coupled: N pulses alias unless the range stays near 1/N of a Rabi period.
* so a x4 widening of the narrow window reaches only [0.6, 1.6] — short of the
  0.0024-2.366 that accepted optima actually span — while keeping the pulse
  count that makes that range fold.

The wide check is therefore a MODE SWITCH to the lab's own survey, which is
also the only measurement that can unmask a locked harmonic.
"""
from __future__ import annotations

from collections import deque

from quam_state_manager.core.autofit import families as fam_mod
from quam_state_manager.core.autofit.engine import PlanEngine
from quam_state_manager.core.autofit.plan import Step


class _Engine:
    """Just enough engine to exercise `_maybe_verify_wide` — it touches only
    the ledger and the queue."""
    _maybe_verify_wide = PlanEngine._maybe_verify_wide

    def __init__(self):
        self.events = []

    def _ledger(self, event, **payload):
        self.events.append((event, payload))


def _insert(family, node, params, discovered=("qA1",)):
    eng = _Engine()
    q: deque = deque()
    step = Step(id="s1", node=node, family=family, label="l", params=dict(params))
    eng._maybe_verify_wide(step, set(discovered), dict(params), q)
    return eng, q


class TestPowerRabiHasOneAtAll:
    def test_the_family_now_declares_a_wide_verification(self):
        fam = fam_mod.family_for("power_rabi")
        assert fam is not None and fam.verify_wide, \
            "docs/78 §17.6: power_rabi shipped without one"

    def test_it_is_a_survey_not_a_scaled_span(self):
        vw = fam_mod.family_for("power_rabi").verify_wide
        assert "survey_params" in vw and "span_param" not in vw

    def test_the_survey_settings_are_the_labs_own(self):
        """[0.001, 1.99] at step 0.005 with one pulse — 103 of 122 archived
        survey runs, verbatim. A rounder number would be an invented one."""
        s = fam_mod.family_for("power_rabi").verify_wide["survey_params"]
        assert s["max_number_pulses_per_sweep"] == 1
        assert (s["min_amp_factor"], s["max_amp_factor"]) == (0.001, 1.99)
        assert s["amp_factor_step"] == 0.005


class TestTheInsertedStep:
    NARROW = {"min_amp_factor": 0.98, "max_amp_factor": 1.02,
              "amp_factor_step": 0.0005, "max_number_pulses_per_sweep": 40,
              "num_shots": 800, "use_state_discrimination": True}

    def test_it_switches_the_mode_rather_than_widening_the_window(self):
        _, q = _insert("power_rabi", "11_power_rabi", self.NARROW)
        assert len(q) == 1
        p = q[0].params
        assert p["max_number_pulses_per_sweep"] == 1
        assert (p["min_amp_factor"], p["max_amp_factor"]) == (0.001, 1.99)

    def test_a_x4_widening_would_NOT_have_reached_the_survey_range(self):
        """The refutation, pinned: scaling the narrow window stops well short
        of where real optima are found, so the naive shape cannot come back."""
        lo, hi = self.NARROW["min_amp_factor"], self.NARROW["max_amp_factor"]
        scaled_lo, scaled_hi = 1 - (1 - lo) * 4, 1 + (hi - 1) * 4
        assert scaled_hi < 1.99 and scaled_lo > 0.001

    def test_the_step_is_pinned_so_the_survey_is_not_pathologically_dense(self):
        """Carrying the fine mode's 0.0005 across the full window would be
        ~4,000 points — the settings have to travel together."""
        _, q = _insert("power_rabi", "11_power_rabi", self.NARROW)
        assert q[0].params["amp_factor_step"] == 0.005

    def test_averaging_is_carried_not_reset(self):
        """Whatever the ladder climbed to is kept: more shots never weakens a
        confirmation, and resetting them would undo the rung that recovered
        the target in the first place."""
        _, q = _insert("power_rabi", "11_power_rabi", self.NARROW)
        assert q[0].params["num_shots"] == 800

    def test_unrelated_parameters_pass_through_untouched(self):
        _, q = _insert("power_rabi", "11_power_rabi", self.NARROW)
        assert q[0].params["use_state_discrimination"] is True

    def test_the_step_is_marked_so_it_cannot_verify_itself(self):
        _, q = _insert("power_rabi", "11_power_rabi", self.NARROW)
        v = q[0]
        assert v.verify_of == "s1" and v.inserted_by == "verify_wide"
        assert v.only_targets == ("qA1",) and v.retry_max == 0

    def test_the_insertion_is_ledgered_with_its_parameters(self):
        eng, _ = _insert("power_rabi", "11_power_rabi", self.NARROW)
        ev = [e for e in eng.events if e[0] == "verify_wide_inserted"]
        assert ev and ev[0][1]["params"]["max_number_pulses_per_sweep"] == 1


class TestTheSpectroscopyShapeIsUnchanged:
    """The four families that already had one must be byte-identical — this
    change adds a branch, it does not re-tune anything that was calibrated."""

    def test_span_is_still_scaled_by_four(self):
        _, q = _insert("qubit_spectroscopy", "08_qubit_spectroscopy",
                       {"frequency_span_in_mhz": 25.0, "num_shots": 100})
        assert q[0].params["frequency_span_in_mhz"] == 100.0

    def test_the_default_span_still_applies_when_the_run_names_none(self):
        _, q = _insert("qubit_spectroscopy", "08_qubit_spectroscopy",
                       {"num_shots": 100})
        vw = fam_mod.family_for("qubit_spectroscopy").verify_wide
        assert q[0].params["frequency_span_in_mhz"] == \
            vw["span_default"] * vw["factor"]


class TestItStillOnlyFiresWhenItShould:
    def test_nothing_discovered_inserts_nothing(self):
        _, q = _insert("power_rabi", "11_power_rabi", {}, discovered=())
        assert not q

    def test_a_verification_step_never_verifies_itself(self):
        eng = _Engine()
        q: deque = deque()
        step = Step(id="s1__verify_wide", node="11_power_rabi",
                    family="power_rabi", label="l", params={},
                    inserted_by="verify_wide")
        eng._maybe_verify_wide(step, {"qA1"}, {}, q)
        assert not q

    def test_a_family_without_one_inserts_nothing(self):
        _, q = _insert("ramsey", "10_ramsey", {"num_shots": 100})
        assert not q
