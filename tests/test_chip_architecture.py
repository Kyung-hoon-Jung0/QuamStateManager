"""Generate wizard chip-architecture declaration + the fixed-Q+tunable-coupler block.

quam_builder's CZGate plays on the qubit z line, so a coupler-only CZ for
fixed-frequency qubits cannot be represented. The wizard declares the chip
architecture up front (3 valid combos, the impossible one is not offered) and
validate_spec rejects a hand-crafted coupler-without-flux spec.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from quam_state_manager.core.config_generator import validate_spec

REPO = Path(__file__).resolve().parent.parent
GEN_JS = REPO / "quam_state_manager" / "web" / "static" / "generate.js"
GEN_HTML = REPO / "quam_state_manager" / "web" / "templates" / "_generate.html"


def _spec(lines):
    return {
        "network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
        "instruments": {"controllers": [
            {"con": 1, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 5, "fem": "lf"}]}],
            "opx_plus": [], "octaves": []},
        "qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "twpas": [],
        "lines": lines, "populate": {},
    }


# ── Backend guard: coupler without qubit flux is rejected ──────────────────
def test_validate_rejects_coupler_without_qubit_flux():
    # fixed-frequency qubits (no flux line) + a tunable coupler line
    spec = _spec([
        {"element": "q1", "line": "resonator", "channel": None},
        {"element": "q1", "line": "drive", "channel": None},
        {"element": "q1-q2", "line": "coupler", "channel": {"kind": "lf_fem", "con": 1}},
    ])
    errs = validate_spec(spec)
    assert any("coupler lines need qubit flux" in e for e in errs), errs


def test_validate_accepts_coupler_with_qubit_flux():
    # flux-tunable qubits + tunable coupler — the valid CZ combo
    spec = _spec([
        {"element": "q1", "line": "resonator", "channel": None},
        {"element": "q1", "line": "drive", "channel": None},
        {"element": "q1", "line": "flux", "channel": {"kind": "lf_fem", "con": 1}},
        {"element": "q1-q2", "line": "coupler", "channel": {"kind": "lf_fem", "con": 1}},
    ])
    assert validate_spec(spec) == []


def test_validate_accepts_fixed_frequency_cr():
    # fixed-frequency qubits + cross-resonance (no coupler, no flux) — valid
    spec = _spec([
        {"element": "q1", "line": "resonator", "channel": None},
        {"element": "q1", "line": "drive", "channel": None},
        {"element": "q1-q2", "line": "cross_resonance", "channel": None},
    ])
    assert validate_spec(spec) == []


def test_shipped_sample_specs_still_validate():
    for name in ("sample_spec_3q.json", "sample_spec_multichassis.json"):
        spec = json.loads((REPO / "docs" / "examples" / name).read_text())
        assert validate_spec(spec) == [], name


# ── Frontend: the explicit selector offers only the 3 valid architectures ──
def test_generate_html_has_chip_arch_selector():
    html = GEN_HTML.read_text(encoding="utf-8")
    assert 'id="gen-chip-arch"' in html
    assert "flux_tunable_coupler" in html
    assert "flux_tunable_fixed_coupler" in html
    assert "fixed_frequency" in html


def test_generate_js_blocks_invalid_combo():
    js = GEN_JS.read_text(encoding="utf-8")
    # the CHIP_ARCH map has exactly the 3 valid architectures
    assert "flux_tunable_coupler:" in js
    assert "fixed_frequency:" in js
    # the defensive guard against fixed-Q + tunable-coupler CZ
    assert "!state.qubitFlux && state.pairGate === \"cz_tunable\"" in js
