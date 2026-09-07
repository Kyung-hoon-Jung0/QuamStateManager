"""docs/175 -- the Populate step's TWPA section (customer report: the wizard
created the TWPA line, but the Populate step had no TWPA fields at all, so a
built ``twpas.<id>`` carried only builder defaults).

Three seams, each pinned here without the QM stack:
  * ``run_build._apply_twpa`` / ``_twpa_of`` seed a built TWPA from
    ``populate["twpa"][<id>]``, hasattr-gated per field -- the TWPA dataclass
    differs by quam_builder generation (0.4.0: pump_frequency / pump_amplitude
    / settling_time / isolation_* / *_attenuation; an older lab fork carried
    max_avg_gain / spectroscopy instead), so a field the class lacks is
    skipped, never invented;
  * ``regen_spec._extract_populate`` reads the seeds BACK so a re-generate
    re-opens the section PREFILLED (the customer's chip keeps pump_frequency
    null and the tone on the pump channel's RF_frequency -- the fallback);
  * ``regen_populate.protect_paths`` protects an in-wizard seed from tier-1
    carry, which would otherwise silently revert it to the source chip.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace as NS

from quam_state_manager.core import regen_populate, regen_spec

_ROOT = Path(__file__).resolve().parents[1]


def _rb():
    # run_build is a standalone subprocess script; load it by path the way
    # test_script_emitter's verbatim check does.
    p = _ROOT / "quam_state_manager" / "generator" / "run_build.py"
    spec = importlib.util.spec_from_file_location("rb_twpa_populate", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_twpa(**override):
    """A quam_builder-0.4.0-shaped TWPA: pump + pump_ share ONE port."""
    port = NS(upconverter_frequency=None, band=None, full_scale_power_dbm=None)
    pump = NS(RF_frequency=None, opx_output=port, opx_input=None,
              operations={"pump": NS(length=16)})
    pump_ = NS(RF_frequency=None, opx_output=port, opx_input=None,
               operations={"pump": NS(length=2000)})
    tw = NS(pump=pump, pump_=pump_, pump_frequency=None, pump_amplitude=1,
            settling_time=1000, isolation_frequency=None,
            isolation_amplitude=1, pumpline_attenuation=None,
            signalline_attenuation=None)
    for k, v in override.items():
        setattr(tw, k, v)
    return tw


FULL = {
    "pump_frequency": 8.3e9, "LO_frequency": 8.0e9, "band": "3",   # <select> string
    "full_scale_power_dbm": -5, "pump_amplitude": 0.8,
    "pump_length": 20000, "settling_time": 1500,
    "isolation_frequency": 7.9e9, "isolation_amplitude": 0.5,
    "pumpline_attenuation": 20, "signalline_attenuation": 10,
}


class TestApplyTwpa:
    def test_every_field_lands(self):
        rb = _rb()
        tw = _fake_twpa()
        rb._apply_twpa(tw, dict(FULL))
        assert tw.pump_frequency == 8.3e9
        # mirrors _apply_qubit (f_01 + xy.RF_frequency): both pump channels
        assert tw.pump.RF_frequency == 8.3e9 and tw.pump_.RF_frequency == 8.3e9
        port = tw.pump.opx_output
        assert port.upconverter_frequency == 8.0e9
        assert port.band == 3                      # coerced from the "3" string
        assert port.full_scale_power_dbm == -5
        assert tw.pump.operations["pump"].length == 20000
        assert tw.pump_.operations["pump"].length == 20000
        assert tw.pump_amplitude == 0.8 and tw.settling_time == 1500
        assert tw.isolation_frequency == 7.9e9 and tw.isolation_amplitude == 0.5
        assert tw.pumpline_attenuation == 20 and tw.signalline_attenuation == 10

    def test_shared_port_written_once_not_split(self):
        rb = _rb()
        tw = _fake_twpa()
        rb._apply_twpa(tw, {"LO_frequency": 8.0e9})
        assert tw.pump.opx_output is tw.pump_.opx_output
        assert tw.pump.opx_output.upconverter_frequency == 8.0e9

    def test_hasattr_gate_never_invents_a_field(self):
        """An older-fork TWPA class has no settling_time / isolation_* /
        *_attenuation -- the seed must skip them, not create attributes."""
        rb = _rb()
        port = NS(upconverter_frequency=None, band=None, full_scale_power_dbm=None)
        old = NS(pump=NS(RF_frequency=None, opx_output=port, opx_input=None,
                         operations={}),
                 pump_=None, pump_frequency=None, pump_amplitude=1)
        rb._apply_twpa(old, dict(FULL))
        for absent in ("settling_time", "isolation_frequency",
                       "isolation_amplitude", "pumpline_attenuation",
                       "signalline_attenuation"):
            assert not hasattr(old, absent), absent
        assert old.pump_frequency == 8.3e9 and old.pump.RF_frequency == 8.3e9
        assert old.pump_amplitude == 0.8

    def test_pump_frequency_reaches_channels_even_without_the_class_field(self):
        rb = _rb()
        tw = _fake_twpa()
        del tw.pump_frequency
        rb._apply_twpa(tw, {"pump_frequency": 8.1e9})
        assert not hasattr(tw, "pump_frequency")
        assert tw.pump.RF_frequency == 8.1e9 and tw.pump_.RF_frequency == 8.1e9

    def test_bad_band_is_ignored_not_raised(self):
        rb = _rb()
        tw = _fake_twpa()
        rb._apply_twpa(tw, {"band": "x"})          # no LO -> band-only path
        assert tw.pump.opx_output.band is None


class TestTwpaOf:
    def test_resolves_every_spelling(self):
        rb = _rb()
        t1, ta = _fake_twpa(), _fake_twpa()
        m = NS(twpas={"twpa1": t1, "twpaA": ta})
        assert rb._twpa_of(m, "twpa1") is t1
        assert rb._twpa_of(m, "1") is t1
        assert rb._twpa_of(m, "A") is ta
        assert rb._twpa_of(m, "twpaA") is ta
        assert rb._twpa_of(m, "zz") is None

    def test_machine_without_twpas_is_none(self):
        rb = _rb()
        assert rb._twpa_of(NS(qubits={}), "twpa1") is None

    def test_apply_populate_loop_is_degrade_only(self):
        """A pre-TWPA builder leaves machine.twpas empty; a populate naming an
        unbuilt id must be a no-op, never a crash."""
        rb = _rb()
        rb.apply_populate(NS(qubits={}, twpas={}),
                          {"twpa": {"twpa9": {"pump_frequency": 1.0}}},
                          handle_pairs=False)
        rb.apply_populate(NS(qubits={}),
                          {"twpa": {"twpa1": {"pump_frequency": 1.0}}},
                          handle_pairs=False)

    def test_apply_populate_reaches_the_twpa(self):
        rb = _rb()
        tw = _fake_twpa()
        rb.apply_populate(NS(qubits={}, twpas={"twpa1": tw}),
                          {"twpa": {"1": {"settling_time": 777}}},
                          handle_pairs=False)
        assert tw.settling_time == 777


# --- the customer's chip shape: pump_frequency null, tone on pump.RF ---------
def _customer_state_wiring():
    state = {
        "qubits": {}, "qubit_pairs": {},
        # ports ride in the STATE dict: protect_paths gates every path on
        # _exists(new_state, ...) and the merged tree carries ports (the same
        # shape test_regen_populate._mini_chip uses) -- a wiring-only ports
        # dict would make every port-side protect silently empty.
        "ports": {"mw_outputs": {"con1": {"3": {"7": {
            "upconverter_frequency": 8.0e9, "band": 3,
            "full_scale_power_dbm": -5}}}}},
        "twpas": {"twpa1": {
            "pump": {"RF_frequency": 8.3e9,
                     "opx_output": "#/wiring/twpas/twpa1/p/opx_output",
                     "operations": {"pump": {"length": 20000, "amplitude": 1}}},
            "pump_": {"RF_frequency": 8.3e9,
                      "opx_output": "#/wiring/twpas/twpa1/p/opx_output",
                      "operations": {"pump": {"length": 2000, "amplitude": 0.5}}},
            "pump_frequency": None, "pump_amplitude": 1, "settling_time": 1000,
            "isolation_frequency": None, "isolation_amplitude": 1,
            "pumpline_attenuation": None, "signalline_attenuation": None,
        }},
    }
    wiring = {
        "wiring": {"twpas": {"twpa1": {
            "p": {"opx_output": "#/ports/mw_outputs/con1/3/7"}}}},
        "ports": {"mw_outputs": {"con1": {"3": {"7": {
            "upconverter_frequency": 8.0e9, "band": 3,
            "full_scale_power_dbm": -5}}}}},
    }
    return state, wiring


class TestRegenReadBack:
    def test_extract_populate_prefills_from_the_customer_shape(self):
        state, wiring = _customer_state_wiring()
        root = {**state, **wiring}
        out = regen_spec._extract_populate(state, root)
        assert out["twpa"]["twpa1"] == {
            "pump_frequency": 8.3e9,          # channel fallback (field is null)
            "LO_frequency": 8.0e9, "full_scale_power_dbm": -5, "band": 3,
            "pump_length": 20000, "pump_amplitude": 1, "settling_time": 1000,
            "isolation_amplitude": 1,         # a real numeric seed on the chip
        }

    def test_no_twpas_no_bucket(self):
        state = {"qubits": {}, "qubit_pairs": {}}
        assert "twpa" not in regen_spec._extract_populate(state, dict(state))


class TestProtectPaths:
    def test_twpa_seeds_are_protected_from_tier1_carry(self):
        state, wiring = _customer_state_wiring()
        changed = [("twpa", "twpa1", "pump_frequency"),
                   ("twpa", "twpa1", "settling_time"),
                   ("twpa", "twpa1", "pump_length"),
                   ("twpa", "twpa1", "LO_frequency")]
        spec_pop = {"twpa": {"twpa1": {"pump_frequency": 8.4e9,
                                       "settling_time": 1500,
                                       "pump_length": 30000,
                                       "LO_frequency": 8.1e9}}}
        protect, _conflicts = regen_populate.protect_paths(
            changed, spec_pop, state, wiring, state, wiring)
        assert "twpas.twpa1.pump_frequency" in protect
        assert "twpas.twpa1.pump.RF_frequency" in protect
        assert "twpas.twpa1.pump_.RF_frequency" in protect
        assert "twpas.twpa1.settling_time" in protect
        assert "twpas.twpa1.pump.operations.pump.length" in protect
        # the LO lands on the pump PORT (resolved through the two-hop pointer)
        assert any(p.endswith(".upconverter_frequency") and "con1" in p
                   for p in protect), sorted(protect)

    def test_unknown_twpa_protects_nothing_and_does_not_raise(self):
        state, wiring = _customer_state_wiring()
        protect, _ = regen_populate.protect_paths(
            [("twpa", "twpa9", "settling_time")],
            {"twpa": {"twpa9": {"settling_time": 1}}},
            state, wiring, state, wiring)
        assert not any("twpa9" in p for p in protect)
