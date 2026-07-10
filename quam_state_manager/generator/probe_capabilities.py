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
  - ``cls``+``method``: the module's ``cls`` must have this method (Connectivity /
                      Instruments capability sniffing).

Run standalone::  python probe_capabilities.py --out <result.json>
"""

from __future__ import annotations

import argparse
import importlib
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
    # -- CZ-variant pulse shapes (quam.components.pulses) ------------------
    "pulse.cz_flattop": {"module": _PULSES, "attr": "_FlatTopGaussianPulse"},
    "pulse.cz_bipolar": {"module": _PULSES, "attr": "_CosineBipolarPulse"},
    "pulse.cz_snz": {"module": _PULSES, "attr": "SNZPulse"},
    "pulse.cz_erf": {"module": _PULSES, "attr": "ErfSquarePulse"},
    "pulse.cr_flattop": {"module": _PULSES, "attr": "FlatTopGaussianPulse"},
    # -- runtime (preview/QUA only, not build) ----------------------------
    "runtime.qm_qua": {"module": "qm", "attr": "QuantumMachinesManager"},
}

CATALOG_IDS = tuple(CATALOG)


def _probe_one(desc: dict) -> tuple[bool, str]:
    """Return ``(available, detail)`` for one catalog descriptor."""
    attr = desc.get("attr")
    modules = desc.get("any_module") or ([desc["module"]] if desc.get("module") else [])
    last_err = "no module specified"
    for mod_name in modules:
        try:
            mod = importlib.import_module(mod_name)
        except Exception as exc:  # noqa: BLE001 — import error = capability absent
            last_err = f"import {mod_name}: {type(exc).__name__}: {exc}"
            continue
        cls_name = desc.get("cls")
        if cls_name:                                   # Connectivity/Instruments method
            cls = getattr(mod, cls_name, None)
            if cls is None:
                last_err = f"{mod_name}.{cls_name} missing"
                continue
            if hasattr(cls, desc["method"]):
                return True, f"{mod_name}.{cls_name}.{desc['method']}"
            last_err = f"{cls_name}.{desc['method']} missing in {mod_name}"
            continue
        if attr:                                       # top-level symbol
            if hasattr(mod, attr):
                return True, f"{mod_name}.{attr}"
            last_err = f"{attr} missing in {mod_name}"
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
