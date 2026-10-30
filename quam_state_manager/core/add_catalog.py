"""The gate and pulse type tables of the three add paths, in one place.

Read by the pair page's Add gate (``routes.pair_gate_form`` / ``pair_add_gate``),
the Pulses page's create form (its gate list and slot classes) and the
Generate wizard's additions (``additions``: the builder's gate names per pair).
Each UI keeps its own filter over these tables; the tables themselves live
only here. Pure data plus pure readers: no Flask, no store.
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# a pair's 2Q gate names (the builder's convention, read by the wizard)
# ---------------------------------------------------------------------------

# The alias a "default gate" addition writes: ``macros.cz = "#./<gate>"`` --
# the shape the builder itself writes on a tunable-coupler chip
# (``run_build._seed_coupler_cz``), and the name SM's CZ plan reads first
# (``cz_gate_plan``: ``operation == "cz"``, else the pair's first gate).
DEFAULT_GATE_NAME = "cz"
# What the official CZ nodes play when their ``operation`` parameter is left
# alone (their ``parameters.py`` default). [measured: the CZ node parameters]
NODE_DEFAULT_OPERATION = "cz_unipolar"
# The CZ variants the builder seeds per pair (``run_build._CZ_VARIANTS``,
# which stays a stdlib-only copy for the seeder; a test pins the two equal).
CZ_VARIANTS = ("unipolar", "flattop", "bipolar", "SNZ", "flattop_erf")

# ---------------------------------------------------------------------------
# the pair page's gate types and their flux-slot pulse classes
# ---------------------------------------------------------------------------

# ``arch`` gates each type to the pair's architecture (docs/54): flux types
# need flux evidence (z lines / coupler / CZ-shaped macros) and must never be
# written onto a FixedFrequencyTransmonPair; CR/Stark types need the matching
# pair drive channel. Enforced in routes.pair_gate_form (UI) AND routes.pair_add_gate (409).
GATE_TYPES: dict[str, dict[str, Any]] = {
    "cz_unipolar": {
        "label": "CZ Unipolar (square pulse)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("length", "Flux length (ns)", 100, "int"),
            ("coupler_amplitude", "Coupler amplitude", 0.0, "float"),
            ("coupler_length", "Coupler length (ns)", 100, "int"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cz_flattop": {
        "label": "CZ Flat-top (Gaussian envelope)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("flat_length", "Flat length (ns)", 200, "int"),
            ("smoothing_length", "Smoothing length (ns)", 20, "int"),
            ("coupler_amplitude", "Coupler amplitude", 0.0, "float"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cz_parametric": {
        "label": "CZ Parametric (AC-flux modulated)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("length", "Flux length (ns)", 100, "int"),
            ("modulation_frequency", "Modulation freq (Hz)", 250_000_000.0, "float"),
            ("coupler_amplitude", "Coupler amplitude", 0.0, "float"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cr_gate": {
        "label": "CR gate (cross-resonance macro)",
        "arch": "cr",
        "fields": [
            ("qc_correction_phase", "Correction phase (control)", 0.0, "float"),
            ("qt_correction_phase", "Correction phase (target)", 0.0, "float"),
        ],
    },
    "stark_cz": {
        "label": "Stark-induced CZ (ZZ drive macro)",
        "arch": "zz",
        "fields": [
            ("qc_correction_phase", "Correction phase (control)", 0.0, "float"),
            ("qt_correction_phase", "Correction phase (target)", 0.0, "float"),
        ],
    },
}


# r15 (docs/71 §3): the three remaining generator CZ variants become add-gate
# types — offered ONLY when the selected env's roster verifies the pulse
# classes they need (mirror of capabilities._CZ_VARIANT_CAPS; no roster ⇒
# exactly the legacy list, a regression fence). Shapes mirror
# run_build._cz_variant_pulses: SNZ / flattop_erf carry the whole gate on the
# qubit z line (coupler slot None); bipolar's coupler mirrors the qubit
# shape's fields by self-reference (single source of truth, the builder's
# link_attrs semantics).
ENV_GATE_TYPES: dict[str, dict[str, Any]] = {
    "cz_bipolar": {
        "label": "CZ Bipolar (cosine, net-zero)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("flat_length", "Flat length (ns)", 200, "int"),
            ("smoothing_length", "Smoothing length (ns)", 20, "int"),
            ("post_zero_padding_length", "Post-zero padding (ns)", 20, "int"),
            ("coupler_amplitude", "Coupler amplitude", 0.0, "float"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cz_snz": {
        "label": "CZ SNZ (sudden net-zero, qubit line only)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("flat_length", "Flat length (ns)", 200, "int"),
            ("t_phi_eff", "t_phi_eff (ns)", 0.0, "float"),
            ("padding", "Padding (ns)", 20, "int"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
    "cz_flattop_erf": {
        "label": "CZ Flat-top Erf (qubit line only)",
        "arch": "flux",
        "fields": [
            ("amplitude", "Flux amplitude", 0.05, "float"),
            ("flat_length", "Flat length (ns)", 200, "int"),
            ("risetime_samples", "Risetime (samples)", 16, "int"),
            ("post_zero_padding_length", "Post-zero padding (ns)", 20, "int"),
            ("phase_shift_control", "Phase shift (control)", 0.0, "float"),
            ("phase_shift_target", "Phase shift (target)", 0.0, "float"),
        ],
    },
}

# Roster leaves each new variant needs — per slot, ANY listed alternative
# satisfies it (modern quam_builder ships CosineBipolarPulse, legacy quam
# ships _CosineBipolarPulse; both are valid bipolar carriers).
ENV_GATE_LEAVES: dict[str, tuple[tuple[str, ...], ...]] = {
    "cz_bipolar": (("CosineBipolarPulse", "_CosineBipolarPulse"),
                   ("SmoothedFlatTopGaussianPulse", "_FlatTopGaussianPulse")),
    "cz_snz": (("SNZPulse",),),
    "cz_flattop_erf": (("ErfSquarePulse",),),
}

# Flux-slot pulse-class candidates per gate type (evidence/roster resolved by
# routes._slot_qclass; None entry ⇒ that gate has no coupler slot).
SLOT_LEAVES: dict[str, dict[str, tuple[str, ...] | None]] = {
    "cz_unipolar": {"qubit": ("SquarePulse",), "coupler": ("SquarePulse",)},
    "cz_flattop": {"qubit": ("SmoothedFlatTopGaussianPulse",
                             "_FlatTopGaussianPulse"),
                   "coupler": ("SmoothedFlatTopGaussianPulse",
                               "_FlatTopGaussianPulse")},
    "cz_bipolar": {"qubit": ("CosineBipolarPulse", "_CosineBipolarPulse"),
                   "coupler": ("SmoothedFlatTopGaussianPulse",
                               "_FlatTopGaussianPulse")},
    "cz_snz": {"qubit": ("SNZPulse",), "coupler": None},
    "cz_flattop_erf": {"qubit": ("ErfSquarePulse",), "coupler": None},
}


def all_gate_types() -> dict[str, dict[str, Any]]:
    """Every gate type, legacy first then the env variants (in that order)."""
    return {**GATE_TYPES, **ENV_GATE_TYPES}


def env_gate_types(roster: dict | None) -> dict[str, dict[str, Any]]:
    """The env variants whose pulse classes *roster* verifies (docs/71 §3):
    each needed slot has ANY of its listed leaves. No roster -> {} (the
    legacy gate list stays byte-identical)."""
    if not roster:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for gid, gdef in ENV_GATE_TYPES.items():
        needs = ENV_GATE_LEAVES[gid]
        if all(any(leaf in roster for leaf in alts) for alts in needs):
            out[gid] = gdef
    return out


def slot_leaves(gate_type: str) -> dict[str, tuple[str, ...] | None]:
    """``{slot: leaf candidates}`` of *gate_type* ({} for a type without
    flux slots); a ``None`` candidate list = the gate has no such slot."""
    return SLOT_LEAVES.get(gate_type) or {}
