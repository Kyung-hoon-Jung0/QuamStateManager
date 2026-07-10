"""Tests for the resonator IF-floor check (Tier-1, RF−LO derived) and the Tier-2
generate_config() value checks (mixer correction, integration-weight value/
duration, time-tagging thresholds) in core/diagnostics.

The Tier-2 checks must emit ZERO findings on a real generated config — guarded by
a regression against the on-disk snapshot when present.
"""

from __future__ import annotations

import json
import os

import pytest

from quam_state_manager.core import diagnostics
from quam_state_manager.core.loader import QuamStore

MWFEM_OUT = "quam.components.ports.mw_outputs.MWFEMAnalogOutputPort"


# ---------------------------------------------------------------------------
# B1 — resonator IF floor (IF = RF_frequency − resolved upconverter_frequency)
# ---------------------------------------------------------------------------

def _if_store(rf):
    state = {
        "qubits": {"q1": {"id": "q1", "resonator": {
            "RF_frequency": rf,
            "opx_output": "#/wiring/qubits/q1/rr/opx_output",
            "operations": {},
        }}},
        "qubit_pairs": {},
        "ports": {"mw_outputs": {"con1": {"1": {"1": {
            "__class__": MWFEM_OUT, "band": 2, "upconverter_frequency": 7.46e9}}}}},
    }
    wiring = {"wiring": {"qubits": {"q1": {
        "rr": {"opx_output": "#/ports/mw_outputs/con1/1/1"}}}}, "network": {}}
    return QuamStore.from_dicts(state, wiring)


def _floor(store):
    return [f for f in diagnostics.lint_state(store)
            if f.category == "value_spec_if_floor"]


class TestResonatorIfFloor:
    def test_if_below_5mhz_is_warning(self):
        f = _floor(_if_store(7.461e9))   # |IF| = 1 MHz
        assert len(f) == 1
        assert f[0].severity == "warning"   # badge-only nudge, not a crash → never banners
        # jumps to RF_frequency (always present, the field the user edits); the
        # intermediate_frequency address is only the human-readable location label.
        assert f[0].jump_path == "qubits.q1.resonator.RF_frequency"
        assert f[0].location == "qubits.q1.resonator.intermediate_frequency"

    def test_healthy_if_silent(self):
        assert _floor(_if_store(7.0e9)) == []      # |IF| = 460 MHz

    def test_exactly_5mhz_is_flagged(self):
        # opx1000_fems.md L151: IFs that are `<= |5| MHz` cannot be measured, so an
        # exactly-5 MHz IF is within the floor (inclusive), NOT fine.
        f = _floor(_if_store(7.46e9 + 5e6))
        assert len(f) == 1 and f[0].severity == "warning"
        # just above the floor is clean
        assert _floor(_if_store(7.46e9 + 5.001e6)) == []

    def test_zero_if_is_exempt(self):
        # |IF| == 0 is a deliberate zero-IF / baseband readout (supported) — not flagged
        assert _floor(_if_store(7.46e9)) == []

    def test_sub_khz_if_is_zero_if_dust_exempt(self):
        # sub-kHz |IF| is float dust around a deliberate zero-IF readout, not a
        # demod-floor case — must NOT warn (RF = LO + 1e-4 / + 500 Hz).
        assert _floor(_if_store(7.46e9 + 0.1)) == []
        assert _floor(_if_store(7.46e9 + 500)) == []

    def test_one_mhz_still_warns(self):
        f = _floor(_if_store(7.46e9 + 1e6))   # 1 MHz, clearly above the dust floor
        assert len(f) == 1 and f[0].severity == "warning"

    def test_message_value_never_collides_with_5mhz_floor(self):
        # a near-floor IF must not render as "|5 MHz| is below the 5 MHz floor"
        f = _floor(_if_store(7.46e9 + 4.999e6))
        assert len(f) == 1
        assert "4.999 MHz" in f[0].message     # full precision, no round-to-5
        assert "|5 MHz|" not in f[0].message
        # sub-MHz renders in kHz
        g = _floor(_if_store(7.46e9 + 200e3))
        assert "200 kHz" in g[0].message

    def test_pointer_rf_skipped(self):
        assert _floor(_if_store("#/x/y")) == []

    def test_none_rf_skipped(self):
        assert _floor(_if_store(None)) == []


# ---------------------------------------------------------------------------
# Tier-2 — config value bounds
# ---------------------------------------------------------------------------

def _cfg(config):
    return diagnostics.lint_config(config)


def _cat(config, category):
    return [f for f in _cfg(config) if f.category == category]


class TestMixerCorrection:
    def test_out_of_range_is_error(self):
        cfg = {"mixers": {"m1": [{"intermediate_frequency": 1e8, "lo_frequency": 5e9,
                                  "correction": [1.0, 0.0, 0.0, 2.5]}]}}
        f = _cat(cfg, "config_mixer_correction")
        assert len(f) == 1 and f[0].severity == "error"

    def test_in_range_clean(self):
        cfg = {"mixers": {"m1": [{"correction": [1.0, 0.0, 0.0, 1.0]}]}}
        assert _cat(cfg, "config_mixer_correction") == []

    def test_asymmetric_upper_edge(self):
        # 2 - 2^-16 is the max (just inside); 2.0 exactly is out
        assert _cat({"mixers": {"m": [{"correction": [2.0 - 2 ** -16, -2.0, 0, 0]}]}},
                    "config_mixer_correction") == []
        assert len(_cat({"mixers": {"m": [{"correction": [2.0, 0, 0, 0]}]}},
                        "config_mixer_correction")) == 1


class TestIntegrationWeights:
    def test_value_and_duration(self):
        cfg = {"integration_weights": {"w1": {
            "cosine": [[3000.0, 800], [0.5, 7]], "sine": [[0.0, 800]]}}}
        cats = [f.category for f in _cfg(cfg)]
        assert "config_iw_value" in cats      # 3000 > 2048
        assert "config_iw_duration" in cats   # 7 not a multiple of 4

    def test_clean_weights_silent(self):
        cfg = {"integration_weights": {"w1": {
            "cosine": [[0.9, 800], [-0.9, 4]], "sine": [[0.0, 1760]]}}}
        assert [f for f in _cfg(cfg) if f.category.startswith("config_iw")] == []

    def test_flat_scalar_form_value_only(self):
        # bare-float form has no per-sample duration → only the value bound applies
        cfg = {"integration_weights": {"w1": {"cosine": [0.9, 5000.0], "sine": [0.0]}}}
        cats = [f.category for f in _cfg(cfg)]
        assert "config_iw_value" in cats        # 5000 > 2048
        assert "config_iw_duration" not in cats


class TestTimeTagging:
    def test_threshold_asymmetric(self):
        # +2048 is out of range; -2048 is the valid minimum
        cfg = {"elements": {"e1": {"timeTaggingParameters": {
            "signalThreshold": 2048, "derivativeThreshold": -2048}}}}
        assert len(_cat(cfg, "config_timetag_threshold")) == 1

    def test_clean_thresholds_silent(self):
        cfg = {"elements": {"e1": {"outputPulseParameters": {  # deprecated alias
            "signalThreshold": 300, "derivativeThreshold": -100}}}}
        assert _cat(cfg, "config_timetag_threshold") == []


# ---------------------------------------------------------------------------
# Regression: zero Tier-2 findings on a real generated config
# ---------------------------------------------------------------------------

_REAL_CFG = "/mnt/d/work/state-manager/.tmp_cfg/_result.json"
_TIER2 = {"config_mixer_correction", "config_iw_value",
          "config_iw_duration", "config_timetag_threshold"}


@pytest.mark.skipif(not os.path.exists(_REAL_CFG),
                    reason="no real generate_config() snapshot on disk")
def test_real_config_no_tier2_false_positives():
    data = json.load(open(_REAL_CFG))
    config = data.get("config", data)
    bad = [(f.category, f.location) for f in diagnostics.lint_config(config)
           if f.category in _TIER2]
    assert bad == [], f"Tier-2 false positives on real config: {bad}"


# ---------------------------------------------------------------------------
# MW-FEM downconverter LO spacing (≥10 MHz between the two inputs on a FEM)
# ---------------------------------------------------------------------------

MWFEM_IN = "quam.components.ports.mw_inputs.MWFEMAnalogInputPort"


def _dc_state(*freqs):
    inputs = {str(i + 1): {"__class__": MWFEM_IN, "band": 2,
                           "downconverter_frequency": f} for i, f in enumerate(freqs)}
    return {"qubits": {}, "qubit_pairs": {},
            "ports": {"mw_inputs": {"con1": {"1": inputs}}}}


def _spacing(state):
    store = QuamStore.from_dicts(state, {"wiring": {}})
    return [f for f in diagnostics.lint_state(store)
            if f.category == "connectivity_downconverter"]


class TestDownconverterSpacing:
    def test_close_los_flag_both_ports(self):
        f = _spacing(_dc_state(7.46e9, 7.462e9))   # 2 MHz apart
        assert len(f) == 2 and all(x.severity == "warning" for x in f)

    def test_shared_lo_zero_gap_flags(self):
        assert len(_spacing(_dc_state(7.46e9, 7.46e9))) == 2   # 0 Hz — not recommended

    def test_well_separated_silent(self):
        assert _spacing(_dc_state(7.46e9, 7.48e9)) == []       # 20 MHz apart

    def test_single_input_silent(self):
        assert _spacing(_dc_state(7.46e9)) == []

    def test_pointer_downconverter_resolves_and_flags(self):
        state = {"qubits": {}, "qubit_pairs": {}, "ports": {
            "mw_outputs": {"con1": {"1": {
                "1": {"upconverter_frequency": 7.46e9},
                "8": {"upconverter_frequency": 7.461e9}}}},   # 1 MHz apart
            "mw_inputs": {"con1": {"1": {
                "1": {"__class__": MWFEM_IN, "band": 2,
                      "downconverter_frequency": "#/ports/mw_outputs/con1/1/1/upconverter_frequency"},
                "2": {"__class__": MWFEM_IN, "band": 2,
                      "downconverter_frequency": "#/ports/mw_outputs/con1/1/8/upconverter_frequency"}}}}}}
        assert len(_spacing(state)) == 2


# ---------------------------------------------------------------------------
# MW-FEM output LO band-edge advisory (overlapping band would be more central)
# ---------------------------------------------------------------------------

# Port "9" is NOT in any COUPLED_PORT_PAIRS entry, so it exercises the pure
# (uncoupled) band-edge path without the coupled-mate feasibility gate.
def _be_state(band, freq, port="9"):
    return {"qubits": {}, "qubit_pairs": {}, "ports": {"mw_outputs": {"con1": {"1": {
        str(port): {"__class__": MWFEM_OUT, "band": band,
                    "upconverter_frequency": freq}}}}}}


def _be_coupled_state(out_port, out_freq, in_port, in_freq, band=2):
    """An MW-FEM output + its coupled input mate, for the feasibility-gate tests."""
    return {"qubits": {}, "qubit_pairs": {}, "ports": {
        "mw_outputs": {"con1": {"1": {str(out_port): {
            "__class__": MWFEM_OUT, "band": band, "upconverter_frequency": out_freq}}}},
        "mw_inputs": {"con1": {"1": {str(in_port): {
            "__class__": MWFEM_IN, "band": band, "downconverter_frequency": in_freq}}}}}}


def _band_edge(state):
    store = QuamStore.from_dicts(state, {"wiring": {}})
    return [f for f in diagnostics.lint_state(store)
            if f.category == "connectivity_band_edge"]


class TestBandEdge:
    def test_near_band2_edge_overlaps_band3_warns(self):
        f = _band_edge(_be_state(2, 7.46e9))   # 40 MHz from band-2's 7.5 GHz edge
        assert len(f) == 1 and f[0].severity == "warning"
        assert f[0].advisory is True           # optional recommendation, not a defect
        assert f[0].jump_path == "ports.mw_outputs.con1.1.9.upconverter_frequency"
        assert "band 3" in f[0].message        # the more-central overlapping band
        assert "coupled" not in f[0].message   # uncoupled port → no mate note

    def test_central_lo_silent(self):
        assert _band_edge(_be_state(2, 6.0e9)) == []   # 1.5 GHz from both edges

    def test_near_edge_no_overlapping_band_silent(self):
        # 30 MHz from band-1's lower edge, but no other band covers 80 MHz → silent
        assert _band_edge(_be_state(1, 80e6)) == []

    def test_out_of_band_lo_not_double_reported(self):
        # an LO outside its declared band is the connectivity_freq warning, not this
        assert _band_edge(_be_state(2, 8.0e9)) == []

    def test_pointer_lo_skipped(self):
        assert _band_edge(_be_state(2, "#/x/y")) == []

    # --- coupled-mate feasibility gate ------------------------------------
    def test_coupled_pair_feasible_warns_and_notes_mate(self):
        # out8 (band 2, 7.46 GHz) couples to in2; the mate LO 7.46 GHz also fits
        # band 3 → the pair can move together → recommend + note the mate.
        f = _band_edge(_be_coupled_state(8, 7.46e9, 2, 7.46e9))
        assert len(f) == 1 and f[0].advisory is True
        assert "band 3" in f[0].message
        assert "coupled" in f[0].message and "in2" in f[0].message

    def test_coupled_pair_infeasible_alt_suppressed(self):
        # mate in2's LO 5.0 GHz does NOT fit band 3 → the pair can't follow the
        # move → recommendation dropped entirely (no self-contradicting advice).
        assert _band_edge(_be_coupled_state(8, 7.46e9, 2, 5.0e9)) == []

    def test_coupled_pair_unresolvable_mate_suppressed(self):
        # coupled port but the mate isn't present in state → conservatively skip.
        assert _band_edge(_be_state(2, 7.46e9, port="8")) == []

    def test_labalike_pointer_linked_mate_feasible(self):
        # real LabA shape: in1's downconverter is a POINTER to out1's upconverter
        # (shared LO). It resolves to 7.46 GHz, which fits band 3 → kept.
        state = _be_coupled_state(
            1, 7.46e9, 1,
            "#/ports/mw_outputs/con1/1/1/upconverter_frequency")
        f = _band_edge(state)
        assert len(f) == 1 and f[0].advisory is True
        assert "in1" in f[0].message


# ---------------------------------------------------------------------------
# MW-FEM carrier reachability — RF_frequency vs the FEM's physical reach and the
# computed IF (RF − port LO) vs the ±500 MHz Nyquist ceiling; f_01 drive range.
# ---------------------------------------------------------------------------

def _carrier_store(*, rf=None, f01="__omit__", band=2, lo=5.0e9,
                   chan="xy", port_class=MWFEM_OUT):
    """One qubit driving (xy) or reading (resonator) through a single MW-FEM
    output port at LO ``lo`` (band ``band``). ``rf`` is the channel carrier;
    ``f01`` is set only when given (default omitted)."""
    role = "rr" if chan == "resonator" else "xy"
    comp = {"RF_frequency": rf, "opx_output": f"#/wiring/qubits/q1/{role}/opx_output",
            "operations": {}}
    q = {"id": "q1", chan: comp}
    if f01 != "__omit__":
        q["f_01"] = f01
    state = {
        "qubits": {"q1": q},
        "qubit_pairs": {},
        "ports": {"mw_outputs": {"con1": {"1": {"1": {
            "__class__": port_class, "band": band, "upconverter_frequency": lo}}}}},
    }
    wiring = {"wiring": {"qubits": {"q1": {
        role: {"opx_output": "#/ports/mw_outputs/con1/1/1"}}}}, "network": {}}
    return QuamStore.from_dicts(state, wiring)


def _scat(store, category):
    return [f for f in diagnostics.lint_state(store) if f.category == category]


class TestMwCarrierIfCeiling:
    def test_if_over_nyquist_is_error(self):
        # RF 700 MHz above LO → |IF| = 700 MHz > 500 MHz Nyquist → compiler reject.
        f = _scat(_carrier_store(rf=5.7e9, lo=5.0e9), "connectivity_if_ceiling")
        assert len(f) == 1 and f[0].severity == "error"
        assert f[0].jump_path == "qubits.q1.xy.RF_frequency"
        assert f[0].port_key is not None       # rings the offending port too

    def test_if_under_nyquist_silent(self):
        # 480 MHz IF is legal (real chips reach ~485 MHz) → no ceiling, no sub-band.
        assert _scat(_carrier_store(rf=5.48e9, lo=5.0e9), "connectivity_if_ceiling") == []

    def test_exactly_500mhz_silent(self):
        assert _scat(_carrier_store(rf=5.5e9, lo=5.0e9), "connectivity_if_ceiling") == []

    def test_readout_channel_if_ceiling(self):
        f = _scat(_carrier_store(rf=8.2e9, lo=7.46e9, chan="resonator"),
                 "connectivity_if_ceiling")
        assert len(f) == 1 and "readout" in f[0].message

    def test_pointer_rf_skipped(self):
        assert _scat(_carrier_store(rf="#./inferred_RF_frequency"),
                    "connectivity_if_ceiling") == []

    def test_non_mwfem_port_skipped(self):
        # an LF-FEM / class-less port is not an MW-FEM output → no carrier checks.
        assert _scat(_carrier_store(rf=5.7e9, lo=5.0e9, port_class="x.LFFEMAnalogOutputPort"),
                    "connectivity_if_ceiling") == []


class TestMwCarrierRange:
    def test_drive_rf_above_ceiling_warns(self):
        # RF 10.7 GHz with a band-3 LO at 10.4 GHz → |IF| = 300 MHz (under Nyquist)
        # so the ceiling does NOT fire, but 10.7 GHz is past the 10.5 GHz output
        # reach → absolute-range warning.
        f = _scat(_carrier_store(rf=10.7e9, lo=10.4e9, band=3),
                 "connectivity_carrier_range")
        assert len(f) == 1 and f[0].severity == "warning"
        assert "produce" in f[0].message

    def test_readout_rf_below_input_floor_warns(self):
        # readout RF 1.9 GHz < the 2 GHz MW input floor; LO 1.95 GHz (band 1) keeps
        # |IF| = 50 MHz so the ceiling stays silent → only the range warning.
        f = _scat(_carrier_store(rf=1.9e9, lo=1.95e9, band=1, chan="resonator"),
                 "connectivity_carrier_range")
        assert len(f) == 1 and "receive" in f[0].message

    def test_range_suppressed_when_ceiling_fires(self):
        # RF 11.2 GHz with a band-2 LO 5.0 GHz → |IF| huge → ceiling fires; the
        # redundant absolute-range warning for the same channel is suppressed.
        store = _carrier_store(rf=11.2e9, lo=5.0e9)
        assert len(_scat(store, "connectivity_if_ceiling")) == 1
        assert _scat(store, "connectivity_carrier_range") == []

    def test_in_range_silent(self):
        assert _scat(_carrier_store(rf=5.05e9, lo=5.0e9), "connectivity_carrier_range") == []


class TestF01DriveRange:
    def test_f01_above_reach_warns(self):
        f = _scat(_carrier_store(rf=5.05e9, lo=5.0e9, f01=11.2e9), "value_spec_f01_range")
        assert len(f) == 1 and f[0].severity == "warning"
        assert f[0].jump_path == "qubits.q1.f_01"

    def test_f01_in_range_silent(self):
        assert _scat(_carrier_store(rf=5.05e9, lo=5.0e9, f01=5.05e9), "value_spec_f01_range") == []

    def test_f01_pointer_skipped(self):
        assert _scat(_carrier_store(rf=5.05e9, f01="#/x"), "value_spec_f01_range") == []

    def test_f01_non_mwfem_skipped(self):
        assert _scat(_carrier_store(rf=5.05e9, f01=11.2e9,
                                   port_class="x.LFFEMAnalogOutputPort"),
                    "value_spec_f01_range") == []


# ---------------------------------------------------------------------------
# LF-FEM analog-output usable-bandwidth (direct 750 / amplified 330 / 1 GSa/s 500)
# ---------------------------------------------------------------------------

LFFEM_OUT = "quam.components.ports.analog_outputs.LFFEMAnalogOutputPort"


def _lf_store(ifq=None, mode="direct", rate=1e9):
    state = {
        "qubits": {"q1": {"id": "q1", "z": {
            "intermediate_frequency": ifq,
            "opx_output": "#/wiring/qubits/q1/z/opx_output", "operations": {}}}},
        "qubit_pairs": {},
        "ports": {"analog_outputs": {"con1": {"5": {"1": {
            "__class__": LFFEM_OUT, "output_mode": mode, "sampling_rate": rate}}}}},
    }
    wiring = {"wiring": {"qubits": {"q1": {
        "z": {"opx_output": "#/ports/analog_outputs/con1/5/1"}}}}, "network": {}}
    return QuamStore.from_dicts(state, wiring)


class TestLfOutputBandwidth:
    def test_direct_1gsps_500mhz_cap(self):
        # direct mode bandwidth is 750 MHz but a 1 GSa/s port caps the element at 500.
        assert len(_scat(_lf_store(600e6, "direct", 1e9), "connectivity_lf_output_bw")) == 1
        assert _scat(_lf_store(480e6, "direct", 1e9), "connectivity_lf_output_bw") == []

    def test_direct_2gsps_750mhz(self):
        # at 2 GSa/s the full direct 750 MHz bandwidth is usable.
        assert _scat(_lf_store(600e6, "direct", 2e9), "connectivity_lf_output_bw") == []
        f = _scat(_lf_store(800e6, "direct", 2e9), "connectivity_lf_output_bw")
        assert len(f) == 1 and "750 MHz" in f[0].message

    def test_amplified_330mhz(self):
        f = _scat(_lf_store(400e6, "amplified", 2e9), "connectivity_lf_output_bw")
        assert len(f) == 1 and "330 MHz" in f[0].message
        assert _scat(_lf_store(300e6, "amplified", 2e9), "connectivity_lf_output_bw") == []

    def test_unknown_mode_default_rate_uses_500(self):
        # output_mode + sampling_rate omitted → rate defaults to 1 GSa/s → 500 cap.
        assert len(_scat(_lf_store(600e6, None, None), "connectivity_lf_output_bw")) == 1
        assert _scat(_lf_store(480e6, None, None), "connectivity_lf_output_bw") == []

    def test_unknown_mode_2gsps_unbounded_skipped(self):
        # 2 GSa/s + unknown mode → no determinable bound → never a false positive.
        assert _scat(_lf_store(900e6, None, 2e9), "connectivity_lf_output_bw") == []

    def test_none_if_skipped(self):
        assert _scat(_lf_store(None, "direct", 1e9), "connectivity_lf_output_bw") == []

    def test_mw_port_not_lf_checked(self):
        # an MW-FEM output is not an LF-FEM port → the LF bandwidth check skips it.
        st = _carrier_store(rf=5.05e9, lo=5.0e9)
        st.merged["qubits"]["q1"]["xy"]["intermediate_frequency"] = 900e6
        assert _scat(st, "connectivity_lf_output_bw") == []


# ---------------------------------------------------------------------------
# Octave enum future-proofing (spec_constraints) — absent on the MW-FEM fleet.
# ---------------------------------------------------------------------------

from quam_state_manager.core import spec_constraints  # noqa: E402


def _oct_findings(octaves):
    return spec_constraints.spec_findings({"octaves": octaves})


class TestOctaveEnums:
    def test_bad_output_mode_flagged(self):
        f = [x for x in _oct_findings({"o1": {"RF_outputs": {"1": {"output_mode": "bogus"}}}})
             if x["category"] == "value_spec_octave_output_mode"]
        assert len(f) == 1

    def test_bad_lo_source_flagged(self):
        f = [x for x in _oct_findings({"o1": {"RF_outputs": {"1": {"LO_source": "nope"}}}})
             if x["category"] == "value_spec_octave_lo_source"]
        assert len(f) == 1

    def test_bad_if_mode_flagged(self):
        f = [x for x in _oct_findings({"o1": {"RF_inputs": {"1": {"IF_mode_I": "weird"}}}})
             if x["category"] == "value_spec_octave_if_mode"]
        assert len(f) == 1

    def test_valid_enums_silent(self):
        cfg = {"o1": {"RF_outputs": {"1": {"output_mode": "triggered", "LO_source": "internal",
                                           "input_attenuators": "ON"}},
                      "RF_inputs": {"1": {"LO_source": "external", "IF_mode_I": "direct",
                                          "IF_mode_Q": "off"}}}}
        oct_cats = [x for x in _oct_findings(cfg) if x["category"].startswith("value_spec_octave")]
        assert oct_cats == []

    def test_absent_enums_skipped(self):
        # a bare octave (no enum fields) must not fire — these are pointer/None-skipped.
        assert _oct_findings({"o1": {"RF_outputs": {"1": {"gain": 5}}}}) == [] or all(
            not x["category"].endswith(("output_mode", "lo_source", "if_mode"))
            for x in _oct_findings({"o1": {"RF_outputs": {"1": {"gain": 5}}}}))


# ---------------------------------------------------------------------------
# "What is checked?" catalogue (static, chip-independent)
# ---------------------------------------------------------------------------

class TestCheckCatalog:
    def test_domains_are_known(self):
        keys = {d["key"] for d in diagnostics.check_catalog()}
        valid = {k for k, _ in diagnostics.DIAG_DOMAINS}
        assert keys <= valid and keys, keys

    def test_labels_match_diag_domains(self):
        labels = dict(diagnostics.DIAG_DOMAINS)
        for d in diagnostics.check_catalog():
            assert d["label"] == labels[d["key"]]

    def test_severities_are_renderable_tiers(self):
        sev = {c["severity"] for d in diagnostics.check_catalog() for c in d["checks"]}
        assert sev <= {"error", "warning", "recommendation", "info"}, sev

    def test_every_check_has_title_and_desc(self):
        for d in diagnostics.check_catalog():
            assert d["checks"]
            for c in d["checks"]:
                assert c["title"] and c["desc"]

    def test_new_hardware_checks_are_documented(self):
        # the freq-range additions must appear in the catalogue (keep docs in sync).
        text = " ".join(c["title"] + " " + c["desc"]
                         for d in diagnostics.check_catalog() for c in d["checks"])
        assert "10.5 GHz" in text          # MW carrier reach
        assert "±500 MHz" in text          # IF Nyquist ceiling
        assert "LF-FEM" in text            # LF output bandwidth
