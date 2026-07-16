"""Capability detection for a user-selected QM environment.

Runs under the *external* interpreter the user picked (like ``run_build.py``),
so at import time it uses ONLY the Python standard library — the heavy QM imports
happen inside :func:`detect`, never at module load. That keeps ``CATALOG`` (and
its ``CATALOG_IDS``) importable from ``run_build.py`` (its sibling, same env) and
from the State-Manager-side test that pins the id set, WITHOUT triggering a slow
``qm`` session init.

``CATALOG`` is the single source of truth for *what capability exists and how to
detect it*. ``core/capabilities.py`` (SM side) owns *what each id means* (label,
severity, what it produces, how to fix); the two id sets are pinned equal by a
test. ``run_build.py`` imports id constants from here so the thing that DETECTS a
capability and the thing that USES it can never drift.

Detection descriptor per id (all keys optional except one locator):
  - ``module``      : import this module; capability present if it imports.
  - ``any_module``  : list of candidate modules; present if ANY imports (and, when
                      ``attr`` is set, exposes it) — used where a symbol moved
                      between quam_builder layouts.
  - ``attr``        : the module must expose this top-level attribute.
  - ``any_attr``    : list of candidate attributes; present if ANY is exposed —
                      used where a CLASS was renamed between generations
                      (``CrossResonanceMW`` → ``CrossResonanceDriveMW``).
  - ``cls``+``method``: the module's ``cls`` must have this method (Connectivity /
                      Instruments capability sniffing).
  - ``cls``/``any_cls``+``field``: the class must carry this dataclass FIELD
                      (``__dataclass_fields__``, ``hasattr`` fallback) — schema-
                      flavor markers: the same class name carries different
                      fields on different quam-builder commits while the version
                      string never moves.
  - ``attr``+``param``: the module-level callable must accept this keyword
                      (``inspect.signature``) — e.g. ``allocate_wiring``'s
                      ``block_used_channels``, required by the shared-port CR
                      layout.

Run standalone::  python probe_capabilities.py --out <result.json>
"""

from __future__ import annotations

import argparse
import importlib
import inspect
import json
import sys
import traceback
from pathlib import Path

# Make the shared stdlib helper importable in every launch mode (mirrors
# run_build.py's defensive sys.path insert before importing _script_common).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _script_common import library_versions as _library_versions  # noqa: E402


# --- the capability catalog (pure data — NO QM imports at module load) ------
_WIRER = "qualang_tools.wirer"
_TWO_Q_FTC = ("quam_builder.architecture.superconducting.custom_gates."
              "flux_tunable_transmon_pair.two_qubit_gates")
_TWO_Q_FIX = ("quam_builder.architecture.superconducting.custom_gates."
              "fixed_transmon_pair.two_qubit_gates")
_PULSES = "quam.components.pulses"
# Every module home QM stacks have shipped pulse classes in — MUST mirror
# run_build._PULSE_HOMES (the consumer), or the report card and the build
# disagree: quam 0.6.0 / quam_builder 0.4.0 moved SNZPulse + ErfSquarePulse
# into the quam_builder architecture package and duplicated the FlatTop
# family into quam_builder.common.pulses.
_PULSE_HOMES = [
    _PULSES,
    "quam_builder.architecture.superconducting.components.pulses",
    "quam_builder.common.pulses",
]
# CR/ZZ channel component homes: the feat/add-cr-cz-macros branch renamed the
# module (cross_resonance → cross_resonance_drive) AND the classes
# (CrossResonanceMW → CrossResonanceDriveMW) at its tip — probe both spellings.
_CR_COMP_HOMES = [
    "quam_builder.architecture.superconducting.components.cross_resonance",
    "quam_builder.architecture.superconducting.components.cross_resonance_drive",
]
_ZZ_COMP = "quam_builder.architecture.superconducting.components.zz_drive"
_XYDET_HOMES = [
    "quam_builder.architecture.superconducting.components.xy_detuned_drive",
    "quam_builder.architecture.superconducting.components",
]
_QPU = "quam_builder.architecture.superconducting.qpu"
_PAIR_FF_MOD = ("quam_builder.architecture.superconducting.qubit_pair."
                "fixed_frequency_transmon_pair")

CATALOG: dict[str, dict] = {
    # -- wiring: Connectivity line methods --------------------------------
    "wire.resonator_line": {"module": _WIRER, "cls": "Connectivity", "method": "add_resonator_line"},
    "wire.qubit_drive_line": {"module": _WIRER, "cls": "Connectivity", "method": "add_qubit_drive_lines"},
    "wire.qubit_flux_line": {"module": _WIRER, "cls": "Connectivity", "method": "add_qubit_flux_lines"},
    "wire.pair_flux_line": {"module": _WIRER, "cls": "Connectivity", "method": "add_qubit_pair_flux_lines"},
    "wire.pair_cross_resonance_line": {"module": _WIRER, "cls": "Connectivity", "method": "add_qubit_pair_cross_resonance_lines"},
    "wire.pair_zz_drive_line": {"module": _WIRER, "cls": "Connectivity", "method": "add_qubit_pair_zz_drive_lines"},
    "wire.twpa_lines": {"module": _WIRER, "cls": "Connectivity", "method": "add_twpa_lines"},
    # -- instruments: Instruments FEM/hardware methods --------------------
    "instr.mw_fem": {"module": _WIRER, "cls": "Instruments", "method": "add_mw_fem"},
    "instr.lf_fem": {"module": _WIRER, "cls": "Instruments", "method": "add_lf_fem"},
    "instr.opx_plus": {"module": _WIRER, "cls": "Instruments", "method": "add_opx_plus"},
    "instr.octave": {"module": _WIRER, "cls": "Instruments", "method": "add_octave"},
    # -- build core -------------------------------------------------------
    "build.quam_wiring": {"module": "quam_builder.builder.qop_connectivity", "attr": "build_quam_wiring"},
    "build.quam": {"module": "quam_builder.builder.superconducting", "attr": "build_quam"},
    "qpu.flux_tunable": {"module": "quam_builder.architecture.superconducting.qpu", "attr": "FluxTunableQuam"},
    "qpu.fixed_frequency": {"module": "quam_builder.architecture.superconducting.qpu", "attr": "FixedFrequencyQuam"},
    # -- single-qubit gate pulses (unconditional) -------------------------
    "pulses.drag_cosine": {"any_module": [
        "quam_builder.builder.superconducting.pulses",
        "quam_builder.builder.superconducting.add_default_pulses"],
        "attr": "add_DragCosine_pulses"},
    "pulses.square": {"module": _PULSES, "attr": "SquarePulse"},
    # -- two-qubit gate macros --------------------------------------------
    "pair.cz_gate": {"module": _TWO_Q_FTC, "attr": "CZGate"},
    "pair.cz_parametric": {"module": _TWO_Q_FTC, "attr": "ParametricCZGate"},
    "pair.fixed_pair": {"module": "quam_builder.architecture.superconducting.qubit_pair.flux_tunable_transmon_pair",
                        "attr": "FluxTunableTransmonPair"},
    "pair.cr_gate": {"any_module": [_TWO_Q_FIX, _TWO_Q_FTC], "attr": "CRGate"},
    "pair.stark_cz_gate": {"any_module": [_TWO_Q_FIX, _TWO_Q_FTC], "attr": "StarkInducedCZGate"},
    # -- CR/ZZ pair channel components (build_quam realizes cr/zz wiring lines
    #    through these; a modern wirer + old builder passes the wire.* blockers
    #    yet dies inside build_quam without them) ---------------------------
    "pair.cr_channel": {"any_module": _CR_COMP_HOMES,
                        "any_attr": ["CrossResonanceMW", "CrossResonanceDriveMW"]},
    "pair.zz_channel": {"module": _ZZ_COMP, "attr": "ZZDriveMW"},
    "chan.xy_detuned": {"any_module": _XYDET_HOMES, "attr": "XYDetunedDriveMW"},
    "qpu.fixed_frequency_zz": {"module": _QPU, "attr": "FixedFrequencyZZDriveQuam"},
    # -- shared-port CR layout: allocate_wiring must accept block_used_channels
    "wire.alloc_block_reuse": {"module": _WIRER, "attr": "allocate_wiring",
                               "param": "block_used_channels"},
    # -- schema-flavor markers (INFO — never required; they name which CR
    #    generation this env WRITES/LOADS so flavor mismatches with a chip's
    #    files surface before any Quam.load) --------------------------------
    "cr.flavor_rf_pointer": {"any_module": _CR_COMP_HOMES,
                             "any_cls": ["CrossResonanceMW", "CrossResonanceDriveMW"],
                             "field": "target_qubit_RF_frequency"},
    "pair.zz_field_zz_drive": {"module": _PAIR_FF_MOD,
                               "cls": "FixedFrequencyTransmonPair",
                               "field": "zz_drive"},
    # -- CZ-variant pulse shapes (any known pulse-module home) -------------
    "pulse.cz_flattop": {"any_module": _PULSE_HOMES, "attr": "_FlatTopGaussianPulse"},
    "pulse.cz_bipolar": {"any_module": _PULSE_HOMES, "attr": "_CosineBipolarPulse"},
    "pulse.cz_snz": {"any_module": _PULSE_HOMES, "attr": "SNZPulse"},
    "pulse.cz_erf": {"any_module": _PULSE_HOMES, "attr": "ErfSquarePulse"},
    "pulse.cr_flattop": {"any_module": _PULSE_HOMES, "attr": "FlatTopGaussianPulse"},
    # -- runtime (preview/QUA only, not build) ----------------------------
    "runtime.qm_qua": {"module": "qm", "attr": "QuantumMachinesManager"},
}

CATALOG_IDS = tuple(CATALOG)


def _probe_one(desc: dict) -> tuple[bool, str]:
    """Return ``(available, detail)`` for one catalog descriptor."""
    attrs = desc.get("any_attr") or ([desc["attr"]] if desc.get("attr") else [])
    cls_names = desc.get("any_cls") or ([desc["cls"]] if desc.get("cls") else [])
    modules = desc.get("any_module") or ([desc["module"]] if desc.get("module") else [])
    field = desc.get("field")
    param = desc.get("param")
    last_err = "no module specified"
    for mod_name in modules:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 — import error = capability absent
            last_err = f"import {mod_name}: {type(exc).__name__}: {exc}"
            continue
        if cls_names and field:                        # dataclass-field flavor marker
            for cls_name in cls_names:
                cls = getattr(mod, cls_name, None)
                if cls is None:
                    last_err = f"{mod_name}.{cls_name} missing"
                    continue
                fields = getattr(cls, "__dataclass_fields__", None) or {}
                if field in fields or hasattr(cls, field):
                    return True, f"{mod_name}.{cls_name}.{field}"
                last_err = f"{cls_name}.{field} missing in {mod_name}"
            continue
        if cls_names:                                  # Connectivity/Instruments method
            for cls_name in cls_names:
                cls = getattr(mod, cls_name, None)
                if cls is None:
                    last_err = f"{mod_name}.{cls_name} missing"
                    continue
                if hasattr(cls, desc["method"]):
                    return True, f"{mod_name}.{cls_name}.{desc['method']}"
                last_err = f"{cls_name}.{desc['method']} missing in {mod_name}"
            continue
        if param and attrs:                            # callable keyword sniff
            for attr in attrs:
                fn = getattr(mod, attr, None)
                if fn is None:
                    last_err = f"{attr} missing in {mod_name}"
                    continue
                try:
                    sig = inspect.signature(fn)
                except (TypeError, ValueError):
                    last_err = f"{mod_name}.{attr} has no inspectable signature"
                    continue
                if param in sig.parameters:
                    return True, f"{mod_name}.{attr}({param}=...)"
                last_err = f"{attr}() lacks {param}= in {mod_name}"
            continue
        if attrs:                                      # top-level symbol
            for attr in attrs:
                if hasattr(mod, attr):
                    return True, f"{mod_name}.{attr}"
            last_err = f"{'/'.join(attrs)} missing in {mod_name}"
            continue
        return True, mod_name                          # bare import success
    return False, last_err


def detect() -> dict:
    """Introspect the installed stack; return the capability manifest.

    ``{"versions": {...}, "capabilities": {id: {"available": bool, "detail": str}}}``.
    Never raises — a probe of a broken env still returns a manifest (all absent).
    """
    caps: dict[str, dict] = {}
    for cid, desc in CATALOG.items():
        try:
            ok, detail = _probe_one(desc)
        except Exception as exc:  # noqa: BLE001 — defensive; a bad descriptor never kills the probe
            ok, detail = False, f"probe error: {type(exc).__name__}: {exc}"
        caps[cid] = {"available": ok, "detail": detail}
    return {"versions": _library_versions(), "capabilities": caps}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="write the manifest JSON here")
    args = ap.parse_args()
    result = {"status": "error", "mode": "capabilities",
              "versions": {}, "error": None, "traceback": None}
    try:
        result.update(detect())
        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 — surface as a structured error
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc()
    Path(args.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"status": result["status"], "result_file": args.out}))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
