"""Curated parameter/column specs shared by the web surfaces (pure data).

Extracted from ``web/routes.py`` (docs/49 Compare-hub redesign, amendment A8)
so the compare machinery (``core/compare.py``, P1) can consume the curated
qubit/pair property maps and bulk-edit column spec without importing the
~7,000-line Flask route module. ``routes.py`` re-imports every name below, so
existing consumers — including tests that import from ``routes`` — keep
working unchanged.

This module must stay import-light and side-effect-free: specs only, no
Flask, no core-store imports.

NOTE (docs/49 A8): ``_FREQ_TWIN_RULES`` — the f_01 ↔ RF_frequency twin-field
edit rules — deliberately stays in ``routes.py`` (it is coupled to the edit
routes). The Compare-hub divergence badge needs the same RULE DATA from core
(core may never import routes), so the suffix table is mirrored below as
``FREQ_TWIN_RULES``; a lock-step test pins the two lists equal so they cannot
drift apart. When the hub UI lands in routes, routes should re-import this
copy and drop its own literal.
"""

from __future__ import annotations

from typing import Any

# Mapping: flat_key -> (section_name, dot_path_template)
# dot_path_template uses {name} for the qubit name.
_PAIR_PROPERTY_MAP: list[tuple[str, str, str | None]] = [
    ("Identity", "id", None),
    ("Identity", "qubit_control", "qubit_pairs.{name}.qubit_control"),
    ("Identity", "qubit_target", "qubit_pairs.{name}.qubit_target"),
    ("Identity", "moving_qubit", "qubit_pairs.{name}.moving_qubit"),
    ("General", "detuning", "qubit_pairs.{name}.detuning"),
    ("General", "mutual_flux_bias", "qubit_pairs.{name}.mutual_flux_bias"),
    ("General", "confusion", "qubit_pairs.{name}.confusion"),
    ("Coupler", "coupler_decouple_offset", "qubit_pairs.{name}.coupler.decouple_offset"),
    ("Coupler", "coupler_interaction_offset", "qubit_pairs.{name}.coupler.interaction_offset"),
    ("Coupler", "coupler_delay_ns", "qubit_pairs.{name}.coupler.opx_output.delay"),
    # Cross-resonance drive channel (CR 2Q gate). LO/IF/target-RF are
    # resolved/runtime read-outs (display-only); upconverter is editable.
    ("Cross Resonance", "cr_lo_frequency", None),
    ("Cross Resonance", "cr_intermediate_frequency", None),
    ("Cross Resonance", "cr_target_qubit_rf", None),
    ("Cross Resonance", "cr_upconverter", "qubit_pairs.{name}.cross_resonance.upconverter"),
    ("Cross Resonance", "cr_operations", None),
]

# CZ gate keys are dynamic (cz_flattop_*, cz_unipolar_*) — built at runtime by _build_pair_sections()

_QUBIT_PROPERTY_MAP: list[tuple[str, str, str | None]] = [
    ("Identity", "id", None),
    ("Identity", "grid_location", "qubits.{name}.grid_location"),
    ("Frequencies", "f_01", "qubits.{name}.f_01"),
    ("Frequencies", "f_12", "qubits.{name}.f_12"),
    ("Frequencies", "anharmonicity", "qubits.{name}.anharmonicity"),
    ("Frequencies", "chi", "qubits.{name}.chi"),
    ("Frequencies", "xy_RF_frequency", "qubits.{name}.xy.RF_frequency"),
    ("Frequencies", "xy_intermediate_frequency", "qubits.{name}.xy.intermediate_frequency"),
    ("Frequencies", "readout_frequency", "qubits.{name}.resonator.f_01"),
    ("Frequencies", "readout_RF_frequency", "qubits.{name}.resonator.RF_frequency"),
    ("Coherence", "T1", "qubits.{name}.T1"),
    ("Coherence", "T2ramsey", "qubits.{name}.T2ramsey"),
    ("Coherence", "T2echo", "qubits.{name}.T2echo"),
    ("XY Drive", "x180_amplitude", "qubits.{name}.xy.operations.x180_DragCosine.amplitude"),
    ("XY Drive", "x180_length", "qubits.{name}.xy.operations.x180_DragCosine.length"),
    ("XY Drive", "x180_alpha", "qubits.{name}.xy.operations.x180_DragCosine.alpha"),
    ("XY Drive", "x90_amplitude", "qubits.{name}.xy.operations.x90_DragCosine.amplitude"),
    ("XY Drive", "saturation_amplitude", "qubits.{name}.xy.operations.saturation.amplitude"),
    ("Readout", "readout_amplitude", "qubits.{name}.resonator.operations.readout.amplitude"),
    ("Readout", "readout_length", "qubits.{name}.resonator.operations.readout.length"),
    ("Readout", "readout_threshold", "qubits.{name}.resonator.operations.readout.threshold"),
    ("Readout", "readout_iw_angle", "qubits.{name}.resonator.operations.readout.integration_weights_angle"),
    ("Readout", "confusion_matrix", "qubits.{name}.resonator.confusion_matrix"),
    ("Readout", "time_of_flight", "qubits.{name}.resonator.time_of_flight"),
    ("Flux", "z_joint_offset", "qubits.{name}.z.joint_offset"),
    ("Flux", "z_independent_offset", "qubits.{name}.z.independent_offset"),
    ("Flux", "z_flux_point", "qubits.{name}.z.flux_point"),
    ("Flux", "z_delay_ns", "qubits.{name}.z.opx_output.delay"),
    ("Flux", "freq_vs_flux_01_quad_term", "qubits.{name}.freq_vs_flux_01_quad_term"),
    ("Flux", "phi0_current", "qubits.{name}.phi0_current"),
    ("Flux", "phi0_voltage", "qubits.{name}.phi0_voltage"),
    ("Gate Fidelity", "gate_fidelity_avg", "qubits.{name}.gate_fidelity.averaged"),
    ("Gate Fidelity", "gate_fidelity_x180", "qubits.{name}.gate_fidelity.x180"),
    ("Gate Fidelity", "gate_fidelity_x90", "qubits.{name}.gate_fidelity.x90"),
]

# Bulk-edit panel columns — the high-churn fields a researcher retunes across many
# qubits at once. Each column = (section, key, label, alias_template, unit).
# The alias_template is the *state* dot-path; every cell is resolved through QUAM
# pointers (resolve_field_target) so qubit fields AND port fields (the
# state→wiring→ports.* double-pointer chain) resolve by one code path.
# Drive-amplitude columns use the operation ALIAS (.operations.x180.amplitude,
# a "#./" pointer) not a hardcoded _DragCosine suffix, so non-DragCosine chips work.
# `unit` is the stored physical unit shown in the header (mandatory, user req).
# default_on=False columns (ports) start hidden — opt in via the Property panel.
_BULK_COLUMNS_SPEC: list[dict[str, Any]] = [
    # ── Qubit fields (shown by default) ───────────────────────────────────────
    {"section": "Frequencies", "key": "f_01", "label": "Qubit f₀₁", "tmpl": "qubits.{name}.f_01", "unit": "Hz"},
    {"section": "Frequencies", "key": "readout_frequency", "label": "Readout freq", "tmpl": "qubits.{name}.resonator.f_01", "unit": "Hz"},
    {"section": "Frequencies", "key": "readout_RF_frequency", "label": "Readout RF", "tmpl": "qubits.{name}.resonator.RF_frequency", "unit": "Hz"},
    {"section": "Frequencies", "key": "xy_RF_frequency", "label": "XY RF", "tmpl": "qubits.{name}.xy.RF_frequency", "unit": "Hz"},
    {"section": "Frequencies", "key": "xy_intermediate_frequency", "label": "XY IF", "tmpl": "qubits.{name}.xy.intermediate_frequency", "unit": "Hz"},
    {"section": "Frequencies", "key": "anharmonicity", "label": "Anharmonicity", "tmpl": "qubits.{name}.anharmonicity", "unit": "Hz"},
    {"section": "Frequencies", "key": "f_12", "label": "Qubit f₁₂", "tmpl": "qubits.{name}.f_12", "unit": "Hz", "default_on": False},
    {"section": "Frequencies", "key": "chi", "label": "Chi (χ)", "tmpl": "qubits.{name}.chi", "unit": "Hz", "default_on": False},
    {"section": "XY Drive", "key": "x180_amplitude", "label": "x180 amp", "tmpl": "qubits.{name}.xy.operations.x180.amplitude", "unit": ""},
    {"section": "XY Drive", "key": "x90_amplitude", "label": "x90 amp", "tmpl": "qubits.{name}.xy.operations.x90.amplitude", "unit": ""},
    {"section": "XY Drive", "key": "saturation_amplitude", "label": "Sat amp", "tmpl": "qubits.{name}.xy.operations.saturation.amplitude", "unit": ""},
    {"section": "Readout", "key": "readout_amplitude", "label": "RO amp", "tmpl": "qubits.{name}.resonator.operations.readout.amplitude", "unit": ""},
    {"section": "Readout", "key": "readout_length", "label": "RO length", "tmpl": "qubits.{name}.resonator.operations.readout.length", "unit": "ns"},
    {"section": "Readout", "key": "readout_threshold", "label": "RO threshold", "tmpl": "qubits.{name}.resonator.operations.readout.threshold", "unit": ""},
    {"section": "Readout", "key": "readout_iw_angle", "label": "RO IW angle", "tmpl": "qubits.{name}.resonator.operations.readout.integration_weights_angle", "unit": "rad"},
    {"section": "Readout", "key": "time_of_flight", "label": "Time of flight", "tmpl": "qubits.{name}.resonator.time_of_flight", "unit": "ns"},
    {"section": "Readout", "key": "depletion_time", "label": "Depletion time", "tmpl": "qubits.{name}.resonator.depletion_time", "unit": "ns", "default_on": False},
    {"section": "Flux", "key": "z_joint_offset", "label": "Flux offset", "tmpl": "qubits.{name}.z.joint_offset", "unit": "V"},
    {"section": "Flux", "key": "z_min_offset", "label": "Z min offset", "tmpl": "qubits.{name}.z.min_offset", "unit": "V", "default_on": False},
    {"section": "Flux", "key": "z_settle_time", "label": "Z settle", "tmpl": "qubits.{name}.z.settle_time", "unit": "ns", "default_on": False},
    {"section": "Flux", "key": "z_flux_point", "label": "Flux point", "tmpl": "qubits.{name}.z.flux_point", "unit": "", "default_on": False},
    {"section": "Flux", "key": "phi0_voltage", "label": "Φ₀ voltage", "tmpl": "qubits.{name}.phi0_voltage", "unit": "V", "default_on": False},
    {"section": "Flux", "key": "phi0_current", "label": "Φ₀ current", "tmpl": "qubits.{name}.phi0_current", "unit": "", "default_on": False},
    # ── Coherence (opt-in) ────────────────────────────────────────────────────
    {"section": "Coherence", "key": "T1", "label": "T1", "tmpl": "qubits.{name}.T1", "unit": "s", "default_on": False},
    {"section": "Coherence", "key": "T2ramsey", "label": "T2 Ramsey", "tmpl": "qubits.{name}.T2ramsey", "unit": "s", "default_on": False},
    {"section": "Coherence", "key": "T2echo", "label": "T2 echo", "tmpl": "qubits.{name}.T2echo", "unit": "s", "default_on": False},
    # ── Gate fidelity + identity (opt-in) ─────────────────────────────────────
    {"section": "Gate Fidelity", "key": "gate_fidelity_avg", "label": "Gate fid (avg)", "tmpl": "qubits.{name}.gate_fidelity.averaged", "unit": "", "default_on": False},
    {"section": "Identity", "key": "grid_location", "label": "Grid loc", "tmpl": "qubits.{name}.grid_location", "unit": "", "default_on": False},
    # ── Port fields (hidden by default — opt in) ──────────────────────────────
    {"section": "XY Port", "key": "xy_delay", "label": "XY delay", "tmpl": "qubits.{name}.xy.opx_output.delay", "unit": "ns", "default_on": False},
    {"section": "XY Port", "key": "xy_power", "label": "XY power", "tmpl": "qubits.{name}.xy.opx_output.full_scale_power_dbm", "unit": "dBm", "default_on": False},
    {"section": "XY Port", "key": "xy_upconv", "label": "XY LO (upconv)", "tmpl": "qubits.{name}.xy.opx_output.upconverter_frequency", "unit": "Hz", "default_on": False},
    {"section": "XY Port", "key": "xy_samp", "label": "XY samp rate", "tmpl": "qubits.{name}.xy.opx_output.sampling_rate", "unit": "Hz", "default_on": False},
    {"section": "XY Port", "key": "xy_band", "label": "XY band", "tmpl": "qubits.{name}.xy.opx_output.band", "unit": "", "default_on": False},
    {"section": "Z Port", "key": "z_delay", "label": "Z delay", "tmpl": "qubits.{name}.z.opx_output.delay", "unit": "ns", "default_on": False},
    {"section": "Z Port", "key": "z_offset", "label": "Z offset", "tmpl": "qubits.{name}.z.opx_output.offset", "unit": "V", "default_on": False},
    {"section": "Z Port", "key": "z_output_mode", "label": "Z output mode", "tmpl": "qubits.{name}.z.opx_output.output_mode", "unit": "", "default_on": False},
    {"section": "Z Port", "key": "z_samp", "label": "Z samp rate", "tmpl": "qubits.{name}.z.opx_output.sampling_rate", "unit": "Hz", "default_on": False},
    {"section": "Z Port", "key": "z_upsamp", "label": "Z upsamp mode", "tmpl": "qubits.{name}.z.opx_output.upsampling_mode", "unit": "", "default_on": False},
    {"section": "RO Out Port", "key": "ro_out_delay", "label": "RO-out delay", "tmpl": "qubits.{name}.resonator.opx_output.delay", "unit": "ns", "default_on": False},
    {"section": "RO Out Port", "key": "ro_out_power", "label": "RO-out power", "tmpl": "qubits.{name}.resonator.opx_output.full_scale_power_dbm", "unit": "dBm", "default_on": False},
    {"section": "RO Out Port", "key": "ro_out_upconv", "label": "RO-out LO (upconv)", "tmpl": "qubits.{name}.resonator.opx_output.upconverter_frequency", "unit": "Hz", "default_on": False},
    {"section": "RO Out Port", "key": "ro_out_band", "label": "RO-out band", "tmpl": "qubits.{name}.resonator.opx_output.band", "unit": "", "default_on": False},
    {"section": "RO In Port", "key": "ro_in_downconv", "label": "RO-in LO (downconv)", "tmpl": "qubits.{name}.resonator.opx_input.downconverter_frequency", "unit": "Hz", "default_on": False},
    {"section": "RO In Port", "key": "ro_in_samp", "label": "RO-in samp rate", "tmpl": "qubits.{name}.resonator.opx_input.sampling_rate", "unit": "Hz", "default_on": False},
    {"section": "RO In Port", "key": "ro_in_band", "label": "RO-in band", "tmpl": "qubits.{name}.resonator.opx_input.band", "unit": "", "default_on": False},
    {"section": "RO In Port", "key": "ro_in_gain", "label": "RO-in gain", "tmpl": "qubits.{name}.resonator.opx_input.gain_db", "unit": "dB", "default_on": False},
]

# f_01 ↔ RF_frequency twin-path suffix rules (see the module docstring NOTE:
# this is the core-side mirror of routes._FREQ_TWIN_RULES, pinned lock-step by
# tests). RF_frequency is the carrier the hardware actually plays; f_01 is
# bookkeeping the calibration keeps equal to it. Order matters: the
# ``.resonator.*`` rules must precede the bare ``.f_01`` / ``.xy.*`` rules.
FREQ_TWIN_RULES: list[tuple[str, str]] = [
    (".resonator.f_01", ".resonator.RF_frequency"),
    (".resonator.RF_frequency", ".resonator.f_01"),
    (".xy.RF_frequency", ".f_01"),
    (".f_01", ".xy.RF_frequency"),
]

# Divergence badge threshold (docs/49 summary set): real chips DO diverge
# intentionally (qB5 readout −1.23 MHz), so this only FLAGS, never errors.
FREQ_TWIN_DIVERGENCE_HZ = 1e3

# Curated per-qubit property subset for the N-way compare diff (/compare,
# /chip-compare) — the "did anything I care about move?" list.
_COMPARE_PROPS = [
    "f_01", "readout_frequency", "T1", "T2ramsey", "T2echo",
    "anharmonicity", "readout_amplitude", "readout_threshold",
    "gate_fidelity_avg", "x180_amplitude", "z_joint_offset",
]

# ── Derived views over _QUBIT_PROPERTY_MAP ─────────────────────────────────

_ALL_QUBIT_PROPS = [key for _, key, _ in _QUBIT_PROPERTY_MAP if key != "id"]

_TABLE_PROP_GROUPS: list[dict] = []
_seen_groups: set[str] = set()
for _section, _key, _ in _QUBIT_PROPERTY_MAP:
    if _key == "id":
        continue
    if _section not in _seen_groups:
        _TABLE_PROP_GROUPS.append({"name": _section, "props": []})
        _seen_groups.add(_section)
    _TABLE_PROP_GROUPS[-1]["props"].append(_key)

_ALL_TABLE_PROPS = [p for g in _TABLE_PROP_GROUPS for p in g["props"]]
