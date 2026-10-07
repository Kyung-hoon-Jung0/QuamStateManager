"""docs/301 F36: Review knows which CZ variants the build will skip.

A CZ pair with no explicit ``cz_variant`` (the default, "all") makes run_build
seed every variant and SKIP each one whose pulse class this env lacks, with
the build warning "CZ variant 'flattop_erf' needs a pulse class missing from
this quam_builder install -- skipped". Review said "This environment can build
everything this chip needs." because ``assess`` only looked at EXPLICIT
variants. The missing shape now reaches "Will be skipped / downgraded" before
the build, worded as the skip it is (never a blocker: the other variants
still seed).
"""
from __future__ import annotations

from quam_state_manager.core import capabilities as cap
from quam_state_manager.generator.probe_capabilities import CATALOG_IDS

_SHAPES = {"pulse.cz_flattop", "pulse.cz_bipolar", "pulse.cz_snz", "pulse.cz_erf"}


def _manifest(available_ids):
    return {"versions": {"quam_builder": "0.4.0"},
            "capabilities": {cid: {"available": cid in available_ids, "detail": ""}
                             for cid in CATALOG_IDS}}


def _cz_chip(populate_pairs=None, gate="cz_tunable"):
    return {
        "instruments": {"controllers": [{"con": 1, "fems": [
            {"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}]},
        "qubits": ["q1", "q2", "q3"],
        "qubit_pairs": [["q1", "q2"], ["q2", "q3"]],
        "pair_gate": gate,
        "lines": [{"element": "q1", "line": "resonator"},
                  {"element": "q1", "line": "drive"},
                  {"element": "q1", "line": "flux"},
                  {"element": "q1-q2", "line": "coupler"}],
        "populate": {"pairs": populate_pairs or {}},
    }


def _all_but(spec, missing):
    return _manifest((cap.required_capabilities(spec) | _SHAPES) - set(missing))


def test_a_default_cz_chip_names_the_variant_it_will_skip():
    spec = _cz_chip()
    rep = cap.assess(spec, _all_but(spec, {"pulse.cz_erf"}))
    assert rep["buildable"] is True and rep["blockers"] == [], rep["blockers"]
    rows = [w for w in rep["warnings"] if w["id"] == "pulse.cz_erf"]
    assert len(rows) == 1, rep["warnings"]
    text = rows[0]["produces"]
    assert "'flattop_erf'" in text and "skipped" in text, text
    assert "falls back to unipolar" not in text, text
    inv = {r["id"]: r for r in rep["inventory"]}
    assert inv["pulse.cz_erf"]["requested"] is True


def test_blank_and_all_are_the_same_default():
    for pops in ({"q1-q2": {"cz_variant": ""}, "q2-q3": {"cz_variant": "all"}},
                 {"q1-q2": {"cz_amplitude": 0.2}}):
        spec = _cz_chip(pops)
        rep = cap.assess(spec, _all_but(spec, {"pulse.cz_erf"}))
        assert any(w["id"] == "pulse.cz_erf" for w in rep["warnings"]), pops


def test_a_shape_two_variants_need_names_both():
    spec = _cz_chip()
    rep = cap.assess(spec, _all_but(spec, {"pulse.cz_flattop"}))
    row = next(w for w in rep["warnings"] if w["id"] == "pulse.cz_flattop")
    assert "'flattop'" in row["produces"] and "'bipolar'" in row["produces"], row


def test_every_shape_present_says_nothing():
    spec = _cz_chip()
    rep = cap.assess(spec, _all_but(spec, set()))
    assert rep["warnings"] == [] and rep["buildable"] is True, rep["warnings"]


def test_explicit_variants_on_every_pair_attempt_nothing_else():
    """Only the chosen variant is built (with its unipolar fall-back), so a
    missing erf class is no concern of this chip."""
    spec = _cz_chip({"q1-q2": {"cz_variant": "SNZ"}, "q2-q3": {"cz_variant": "SNZ"}})
    rep = cap.assess(spec, _all_but(spec, {"pulse.cz_erf"}))
    assert not any(w["id"] == "pulse.cz_erf" for w in rep["warnings"]), rep["warnings"]


def test_no_cz_pairs_attempt_no_cz_shape():
    for spec in (_cz_chip(gate="cr"), dict(_cz_chip(), qubit_pairs=[])):
        rep = cap.assess(spec, _all_but(spec, _SHAPES))
        assert not ({w["id"] for w in rep["warnings"]} & _SHAPES), rep["warnings"]
