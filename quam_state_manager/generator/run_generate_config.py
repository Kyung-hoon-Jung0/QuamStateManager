"""Standalone QM-config previewer.

Loads a ``quam_state`` folder (``state.json`` + ``wiring.json``) into a
QUAM machine object and calls ``machine.generate_config()``. The result —
the dict that ``QuantumMachinesManager.open_qm(config)`` would receive on
real hardware — is written to ``_result.json`` next to the work dir.

Like ``run_build.py``, this script runs inside a *user-selected* conda env
that has the QM stack installed (``quam``, ``quam_builder``,
``qualang_tools``). It is NEVER imported by ``quam_state_manager`` — it may
import only the QM libraries and the Python standard library.

Driven by ``quam_state_manager.core.config_generator.run_generate_config``.

Usage::

    python run_generate_config.py --state-folder /path/to/quam_state --out work_dir
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

# Same defensive sys.path insert as run_build.py — the script's directory is
# normally sys.path[0] when run as `python <path>/run_generate_config.py`,
# but PYTHONSAFEPATH / -P (3.11+) suppress that.
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)
from _script_common import library_versions as _library_versions  # noqa: E402

RESULT_FILENAME = "_result.json"


# ---------------------------------------------------------------------------
# Load + generate
# ---------------------------------------------------------------------------

def _load_machine(state_folder: Path):
    """Load a QUAM machine from a folder containing state.json + wiring.json.

    The authoritative entry point is the top-level ``__class__`` marker in
    the state file itself (e.g.
    ``quam_builder...qpu.flux_tunable_quam.FluxTunableQuam``) — importing
    THAT class works across quam_builder versions whose module layout moved
    (older releases used ``qpu.flux_tunable`` / ``qpu.fixed_frequency``,
    which remain as fallbacks for states without a usable marker).
    """
    import importlib
    import json as _json
    import os
    os.environ["QUAM_STATE_PATH"] = str(state_folder)

    errors: list[str] = []

    # 1. The state file's own __class__ marker.
    try:
        state = _json.loads(
            (Path(state_folder) / "state.json").read_text(encoding="utf-8"))
        qclass = state.get("__class__")
        if isinstance(qclass, str) and "." in qclass:
            module_name, cls_name = qclass.rsplit(".", 1)
            machine_cls = getattr(importlib.import_module(module_name), cls_name)
            return machine_cls.load()
        errors.append(f"state.json has no usable __class__ marker ({qclass!r})")
    except Exception as exc:  # noqa: BLE001 — collected and reported below
        errors.append(f"__class__ load failed with {type(exc).__name__}: {exc}")

    # 2./3. Legacy module layouts.
    for module_name, cls_name in (
        ("quam_builder.architecture.superconducting.qpu.flux_tunable",
         "FluxTunableQuam"),
        ("quam_builder.architecture.superconducting.qpu.fixed_frequency",
         "FixedFrequencyQuam"),
    ):
        try:
            machine_cls = getattr(importlib.import_module(module_name), cls_name)
            return machine_cls.load()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{cls_name}.load() failed with {type(exc).__name__}: {exc}")

    raise RuntimeError(
        f"Could not load QUAM machine from {state_folder!s}. " + "; ".join(errors)
    )


def _read_chip_class(state_folder: Path):
    """Best-effort read of the chip's top-level ``__class__`` marker.

    Returned to the parent so the UI can show what the chip was saved as
    (e.g. ``quam_config.my_quam.Quam`` for a customer's own QUAM subclass, or
    a ``quam_builder...`` path) — the single most useful clue for an
    env-mismatch load failure.
    """
    try:
        state = json.loads(
            (Path(state_folder) / "state.json").read_text(encoding="utf-8"))
        cls = state.get("__class__")
        return cls if isinstance(cls, str) else None
    except (OSError, ValueError):
        return None


def _annotate_load_error(message: str, chip_class, versions) -> str:
    """Prepend an actionable env-mismatch hint to a load/generate failure.

    ``_load_machine`` tries the chip's own ``__class__`` then two legacy
    fallbacks; on a version/branch mismatch the raw message ends in a
    ``ModuleNotFoundError`` that names a *fallback* module (e.g.
    ``quam_builder...qpu.flux_tunable``) — NOT the real cause, which is usually
    the very first error (an unknown attribute or a missing package). Reading
    that wall of red, a user reinstalls libraries instead of selecting the
    matching env. Lead with the likely cause + remedy so the fix is obvious.
    """
    needles = ("Could not load QUAM machine", "ModuleNotFoundError",
               "No module named", "is not a valid attr")
    if not any(n in message for n in needles):
        return message
    qb = (versions or {}).get("quam_builder") or "?"
    qv = (versions or {}).get("quam") or "?"
    saved = f" (saved as '{chip_class}')" if chip_class else ""
    hint = (
        f"Couldn't load this chip{saved} in the selected Generate-Config env. "
        "This is almost always a version/package mismatch — the env's "
        f"quam/quam_builder (here quam {qv}, quam_builder {qb}) differs from "
        "what the chip was saved with, or the chip's own QUAM class package "
        "isn't importable in this env. Fix: in the wizard's Environment step, "
        "select the env that matches this chip — the same one you run its "
        "calibration nodes in. Technical detail follows.\n\n"
    )
    return hint + message


def _make_config_json_safe(value):
    """Coerce a generated QM config into something json.dumps can serialise.

    The config dict contains numpy arrays (waveform samples) and a handful of
    numpy scalars sprinkled around mixer/oscillator entries. Convert them
    recursively to plain Python types so the envelope round-trips through
    ``json.dumps`` without ``TypeError``.
    """
    try:
        import numpy as np
    except ImportError:
        np = None

    if isinstance(value, dict):
        return {k: _make_config_json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_make_config_json_safe(v) for v in value]
    if np is not None:
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, (np.floating, np.integer, np.bool_)):
            return value.item()
        if isinstance(value, np.complexfloating):
            # QM configs only ever expect real samples; complex implies an
            # IQ pair we should have already split. Preserve real part.
            return float(value.real)
    return value


def run_generate_config(state_folder: Path) -> dict:
    """Load + generate. Returns the parts of the envelope this step provides."""
    machine = _load_machine(state_folder)
    config = machine.generate_config()
    return {
        "config": _make_config_json_safe(config),
        "qubits": sorted(str(q) for q in getattr(machine, "qubits", {}) or {}),
        "qubit_pairs": sorted(
            str(p) for p in getattr(machine, "qubit_pairs", {}) or {}
        ),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="QM config previewer")
    parser.add_argument(
        "--state-folder", required=True,
        help="folder containing state.json + wiring.json",
    )
    parser.add_argument(
        "--out", required=True,
        help="work directory that _result.json is written to",
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "status": "error",
        "versions": {},
        "warnings": [],
        "error": None,
        "traceback": None,
        "config": None,
        "qubits": [],
        "qubit_pairs": [],
        "chip_class": None,
    }

    try:
        result["versions"] = _library_versions()
        state_folder = Path(args.state_folder).resolve()
        result["chip_class"] = _read_chip_class(state_folder)
        if not (state_folder / "state.json").exists():
            raise FileNotFoundError(
                f"state.json not found in {state_folder}"
            )
        result.update(run_generate_config(state_folder))
        result["status"] = "ok"
    except Exception as exc:  # noqa: BLE001 - top-level guard
        result["status"] = "error"
        result["error"] = _annotate_load_error(
            f"{type(exc).__name__}: {exc}",
            result.get("chip_class"),
            result.get("versions"),
        )
        result["traceback"] = traceback.format_exc()

    result_path = out_dir / RESULT_FILENAME
    with open(result_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print(json.dumps({"status": result["status"], "result_file": str(result_path)}))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
