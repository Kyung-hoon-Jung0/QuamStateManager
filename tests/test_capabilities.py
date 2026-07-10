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
