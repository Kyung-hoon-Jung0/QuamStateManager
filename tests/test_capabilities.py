"""Unit tests for the env-capability model (core/capabilities.py).

Pure functions over hand-built specs + fake manifests — no conda env needed.
Plus the drift pin: the SM-side REGISTRY id set must equal the in-env detection
CATALOG id set (mirrors the CZ-variant allowlist sync test).
"""
from __future__ import annotations

from quam_state_manager.core import capabilities as cap
from quam_state_manager.generator.probe_capabilities import CATALOG_IDS


def _manifest(available_ids, all_ids=None):
    all_ids = all_ids if all_ids is not None else CATALOG_IDS
    return {"versions": {"quam_builder": "0.2.0"},
            "capabilities": {cid: {"available": cid in available_ids, "detail": ""}
                             for cid in all_ids}}


# --- drift pin --------------------------------------------------------------

def test_registry_matches_detection_catalog():
    assert set(cap.REGISTRY) == set(CATALOG_IDS), \
        set(cap.REGISTRY) ^ set(CATALOG_IDS)


# --- required_capabilities --------------------------------------------------

_FIXED_CZ_SPEC = {
    "instruments": {"controllers": [{"con": 1, "fems": [
        {"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}]},
    "qubit_pairs": [["q1", "q2"]],
    "pair_gate": "cz_fixed",
    "lines": [{"element": "q1", "line": "resonator"},
              {"element": "q1", "line": "drive"},
              {"element": "q1", "line": "flux"}],
    "populate": {"pairs": {"q1-q2": {"cz_variant": "SNZ"}}},
}


def test_required_core_and_context():
    req = cap.required_capabilities(_FIXED_CZ_SPEC)
    # core always
    assert {"build.quam_wiring", "build.quam", "pulses.drag_cosine",
            "pulses.square", "wire.resonator_line", "wire.qubit_drive_line"} <= req
    assert "wire.qubit_flux_line" in req            # flux line present
    assert "qpu.flux_tunable" in req                # flux → flux-tunable QPU
    assert "instr.mw_fem" in req and "instr.lf_fem" in req
    assert "pair.cz_gate" in req
    assert "pair.fixed_pair" in req                 # cz_fixed → pair object needed
    assert "pulse.cz_snz" in req                    # SNZ variant requested
    # NOT required — no CR, no TWPA, no octave, no bipolar
    assert "pair.cr_gate" not in req
    assert "wire.twpa_lines" not in req
    assert "instr.octave" not in req
    assert "pulse.cz_bipolar" not in req


def test_twpa_and_cr_and_octave_required_when_present():
    spec = {
        "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
                        "octaves": [{"index": 1}]},
        "qubit_pairs": [["q1", "q2"]],
        "pair_gate": "cr",
        "twpas": ["twpaA"],
        "lines": [{"element": "twpaA", "line": "twpa_pump"},
                  {"element": "q1-q2", "line": "cross_resonance"}],
    }
    req = cap.required_capabilities(spec)
    assert "wire.twpa_lines" in req
    assert "wire.pair_cross_resonance_line" in req
    assert "pair.cr_gate" in req and "pulse.cr_flattop" in req
    assert "instr.octave" in req
    assert "qpu.fixed_frequency" in req             # no flux lines → fixed
    assert "pair.cz_gate" not in req


def test_bipolar_needs_two_pulse_shapes():
    spec = {"qubit_pairs": [["q1", "q2"]], "pair_gate": "cz_tunable",
            "lines": [{"element": "q1", "line": "flux"},
                      {"element": "q1-q2", "line": "coupler"}],
            "populate": {"pairs": {"q1-q2": {"cz_variant": "bipolar"}}}}
    req = cap.required_capabilities(spec)
    assert {"pulse.cz_bipolar", "pulse.cz_flattop"} <= req
    assert "wire.pair_flux_line" in req             # coupler line


# --- assess: three buckets --------------------------------------------------

def test_assess_all_present_is_buildable():
    req = cap.required_capabilities(_FIXED_CZ_SPEC)
    rep = cap.assess(_FIXED_CZ_SPEC, _manifest(req))
    assert rep["buildable"] is True
    assert rep["blockers"] == [] and rep["warnings"] == []
    assert {r["id"] for r in rep["ok"]} == req


def test_assess_missing_twpa_is_degrade_not_blocker():
    spec = {"twpas": ["twpaA"], "qubit_pairs": [],
            "lines": [{"element": "twpaA", "line": "twpa_pump"},
                      {"element": "q1", "line": "resonator"},
                      {"element": "q1", "line": "drive"}]}
    req = cap.required_capabilities(spec)
    have = req - {"wire.twpa_lines"}                # env lacks add_twpa_lines
    rep = cap.assess(spec, _manifest(have))
    assert rep["buildable"] is True                 # degrade doesn't block
    assert [w["id"] for w in rep["warnings"]] == ["wire.twpa_lines"]
    w = rep["warnings"][0]
    assert w["severity"] == cap.DEGRADE and w["produces"] and w["fix"]
    assert rep["blockers"] == []


def test_assess_missing_fixed_pair_is_blocker():
    have = cap.required_capabilities(_FIXED_CZ_SPEC) - {"pair.fixed_pair"}
    rep = cap.assess(_FIXED_CZ_SPEC, _manifest(have))
    assert rep["buildable"] is False
    assert [b["id"] for b in rep["blockers"]] == ["pair.fixed_pair"]


def test_assess_missing_cr_gate_is_degrade():
    spec = {"qubit_pairs": [["q1", "q2"]], "pair_gate": "cr",
            "lines": [{"element": "q1", "line": "resonator"},
                      {"element": "q1", "line": "drive"},
                      {"element": "q1-q2", "line": "cross_resonance"}]}
    have = cap.required_capabilities(spec) - {"pair.cr_gate"}
    rep = cap.assess(spec, _manifest(have))
    assert rep["buildable"] is True
    assert "pair.cr_gate" in [w["id"] for w in rep["warnings"]]


def test_not_requested_missing_is_never_a_warning():
    # env lacks TWPA + octave, but the spec asks for neither → silent.
    spec = {"qubit_pairs": [], "pair_gate": "",
            "lines": [{"element": "q1", "line": "resonator"},
                      {"element": "q1", "line": "drive"}]}
    have = set(CATALOG_IDS) - {"wire.twpa_lines", "instr.octave"}
    rep = cap.assess(spec, _manifest(have))
    ids = {r["id"] for r in rep["blockers"] + rep["warnings"]}
    assert "wire.twpa_lines" not in ids and "instr.octave" not in ids
    assert rep["buildable"] is True
    # inventory still lists them as available:False, requested:False
    inv = {r["id"]: r for r in rep["inventory"]}
    assert inv["wire.twpa_lines"]["available"] is False
    assert inv["wire.twpa_lines"]["requested"] is False


def test_assess_no_manifest_is_unknown_not_all_missing():
    rep = cap.assess(_FIXED_CZ_SPEC, None)
    assert rep["manifest_ok"] is False
    assert rep["buildable"] is False                # can't confirm → not buildable
    # but we don't fabricate blockers when we simply couldn't probe
    assert rep["blockers"] == [] and rep["warnings"] == []


# --- CR/ZZ pair-channel + shared-port requirements (docs/54) -----------------

_CR_SHARED_SPEC = {
    "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}]},
    "qubit_pairs": [["q0", "q1"], ["q1", "q0"]],
    "pair_gate": "cr",
    "cr_port_mode": "shared_xy",
    "lines": [{"element": "q0", "line": "resonator"},
              {"element": "q0", "line": "drive"},
              {"element": "q0-q1", "line": "cross_resonance"},
              {"element": "q0-q1", "line": "zz_drive"}],
}


def test_cr_lines_require_channel_components():
    req = cap.required_capabilities(_CR_SHARED_SPEC)
    # a modern wirer + old builder passes the wire blocker yet dies in
    # build_quam — the channel-component ids close that hole
    assert "pair.cr_channel" in req
    assert "pair.zz_channel" in req
    assert "pair.stark_cz_gate" in req
    assert "qpu.fixed_frequency_zz" in req
    assert "chan.xy_detuned" in req
    assert "wire.alloc_block_reuse" in req          # shared_xy mode
    # flavor markers are inventory-only, never required
    assert "cr.flavor_rf_pointer" not in req
    assert "pair.zz_field_zz_drive" not in req


def test_dedicated_mode_does_not_require_block_reuse():
    spec = dict(_CR_SHARED_SPEC, cr_port_mode="dedicated")
    assert "wire.alloc_block_reuse" not in cap.required_capabilities(spec)


def test_missing_cr_channel_is_blocker_zz_extras_degrade():
    have = set(CATALOG_IDS) - {"pair.cr_channel", "qpu.fixed_frequency_zz",
                               "chan.xy_detuned", "pair.stark_cz_gate"}
    rep = cap.assess(_CR_SHARED_SPEC, _manifest(have))
    blocker_ids = {r["id"] for r in rep["blockers"]}
    warning_ids = {r["id"] for r in rep["warnings"]}
    assert "pair.cr_channel" in blocker_ids          # build_quam would crash
    assert {"qpu.fixed_frequency_zz", "chan.xy_detuned",
            "pair.stark_cz_gate"} <= warning_ids     # ZZ degrades gracefully
    assert rep["buildable"] is False


def test_missing_zz_wire_is_blocker_now():
    # build_connectivity calls add_qubit_pair_zz_drive_lines unconditionally
    # when a zz line is present — a miss is an AttributeError crash.
    have = set(CATALOG_IDS) - {"wire.pair_zz_drive_line"}
    rep = cap.assess(_CR_SHARED_SPEC, _manifest(have))
    assert "wire.pair_zz_drive_line" in {r["id"] for r in rep["blockers"]}


# --- flavor_findings: chip ↔ env schema-generation mismatch ------------------

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).parent))
from cr_fixtures import make_cz_reference, make_flavor_a, make_flavor_b  # noqa: E402


def _env_manifest(*, rf_pointer: bool, zz_quam: bool, cr_channel: bool = True):
    have = set(CATALOG_IDS)
    if not rf_pointer:
        have -= {"cr.flavor_rf_pointer"}
    if not zz_quam:
        have -= {"qpu.fixed_frequency_zz"}
    if not cr_channel:
        have -= {"pair.cr_channel"}
    return _manifest(have)


def test_rf_chip_with_lo_if_env_is_blocker():
    state, _ = make_flavor_b()
    findings = cap.flavor_findings(state, _env_manifest(rf_pointer=False, zz_quam=False))
    assert any(f["level"] == "blocker" and "target_qubit_RF_frequency" in f["message"]
               for f in findings)


def test_rf_chip_with_matching_env_is_clean():
    state, _ = make_flavor_b()
    assert cap.flavor_findings(state, _env_manifest(rf_pointer=True, zz_quam=True)) == []


def test_lo_if_chip_with_rf_env_warns():
    state, _ = make_flavor_a()
    findings = cap.flavor_findings(state, _env_manifest(rf_pointer=True, zz_quam=True))
    assert any(f["level"] == "warning" for f in findings)


def test_zz_transmon_chip_needs_zz_quam():
    state, _ = make_flavor_b()
    for q in state["qubits"].values():
        q["__class__"] = (q["__class__"]
                          .replace("FixedFrequencyTransmon",
                                   "FixedFrequencyZZDriveTransmon"))
    findings = cap.flavor_findings(state, _env_manifest(rf_pointer=True, zz_quam=False))
    assert any("FixedFrequencyZZDriveTransmon" in f["message"]
               and f["level"] == "blocker" for f in findings)


def test_cz_chip_and_no_manifest_are_silent():
    state, _ = make_cz_reference()
    assert cap.flavor_findings(state, _env_manifest(rf_pointer=False, zz_quam=False)) == []
    state_b, _ = make_flavor_b()
    assert cap.flavor_findings(state_b, None) == []
    assert cap.flavor_findings(state_b, {}) == []
