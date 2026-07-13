"""Dump ground-truth waveforms from the real quam library (golden generator).

Standalone script in the ``run_generate_config.py`` mold: it is executed by a
user-selected QM-stack interpreter (the ``LabC`` conda env) and is NEVER
imported by the State Manager process. For every case in
``tests/waveform_matrix.py`` it instantiates the real quam pulse class, calls
``calculate_waveform()``, and writes the results + derived properties to
``<out>/waveform_golden.json``. The committed golden file pins the in-process
numpy synthesizer (``core/waveform_synth.py``) bit-for-bit.

Usage (from WSL, paths in Windows form for the Windows interpreter):

    <qm-env>/python \
        quam_state_manager/generator/run_waveform_golden.py \
        --out 'D:\\work\\state-manager\\tests\\golden'
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
import warnings
from pathlib import Path


def _json_safe_waveform(waveform):
    """Encode a quam ``calculate_waveform()`` result uniformly."""
    import numbers

    import numpy as np

    if isinstance(waveform, numbers.Number) and not isinstance(waveform, bool):
        if isinstance(waveform, complex):
            return {"kind": "constant", "re": float(waveform.real),
                    "im": float(waveform.imag)}
        return {"kind": "constant", "re": float(waveform), "im": None}

    arr = np.asarray(waveform)
    if np.iscomplexobj(arr):
        return {"kind": "array", "re": [float(v) for v in arr.real],
                "im": [float(v) for v in arr.imag]}
    return {"kind": "array", "re": [float(v) for v in arr.astype(float)],
            "im": None}


def _materialize_length(pulse) -> int | None:
    """Concrete sample length as quam itself resolves it.

    quam resolves ``"#./inferred_length"``-style references on attribute
    access even for detached components, so reading ``pulse.length`` is
    sufficient; fall back to the runtime property for robustness.
    """
    try:
        length = pulse.length
        if isinstance(length, (int, float)) and not isinstance(length, bool):
            return int(length)
    except Exception:
        pass
    for prop in ("inferred_length", "inferred_total_length"):
        if hasattr(type(pulse), prop):
            try:
                return int(getattr(pulse, prop))
            except Exception:
                continue
    return None


# Module homes QM stacks have shipped pulse classes in, tried in order —
# quam 0.6.0 / quam_builder 0.4.0 moved SNZPulse, ErfSquarePulse and the
# GaussianFiltered* classes out of quam.components.pulses (mirrors
# run_build._PULSE_HOMES).
_PULSE_HOMES = (
    "quam.components.pulses",
    "quam_builder.architecture.superconducting.components.pulses",
    "quam_builder.common.pulses",
)


def _resolve_class(key: str):
    import importlib

    for mod_name in _PULSE_HOMES:
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        cls = getattr(mod, key, None)
        if cls is not None:
            return cls
    raise AttributeError(f"{key} not found in any of {_PULSE_HOMES}")


def _adapt_params(cls, params: dict) -> dict:
    """Rename kwargs to the installed class's field names.

    quam_builder 0.4.0 renamed the GaussianFiltered* classes'
    ``post_zero_padding_length`` to ``padding_length`` (same math — verified
    bit-identical), and quam 0.6.0 dropped ``sigma`` from
    ``_FlatTopGaussianPulse`` (it was already ignored by qualang_tools).
    """
    import dataclasses

    try:
        fields = {f.name for f in dataclasses.fields(cls)}
    except TypeError:
        return params
    out = dict(params)
    if ("post_zero_padding_length" in out and "post_zero_padding_length" not in fields
            and "padding_length" in fields):
        out["padding_length"] = out.pop("post_zero_padding_length")
    if "sigma" in out and "sigma" not in fields:
        out.pop("sigma")
    return out


def run(out_dir: Path) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
    from waveform_matrix import CASES  # noqa: E402

    import quam

    try:
        import qualang_tools
        qualang_tools_version = getattr(qualang_tools, "__version__", "?")
    except Exception:
        qualang_tools_version = None
    import scipy

    result = {
        "versions": {
            "python": sys.version.split()[0],
            "quam": getattr(quam, "__version__", "?"),
            "qualang_tools": qualang_tools_version,
            "scipy": scipy.__version__,
        },
        "cases": {},
    }

    failures = []
    for case in CASES:
        case_id, key, params = case["id"], case["key"], dict(case["params"])
        entry: dict = {"key": key}
        try:
            cls = _resolve_class(key)
            params = _adapt_params(cls, params)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                pulse = cls(**params)
                length = _materialize_length(pulse)
                entry["length"] = length
                # SNZ derived properties (pin the t_phi / B decomposition)
                if key == "SNZPulse":
                    entry["derived"] = {
                        "t_phi": int(pulse.t_phi),
                        "b_over_a_ratio": float(pulse.b_over_a_ratio),
                    }
                waveform = pulse.calculate_waveform()
            entry["waveform"] = _json_safe_waveform(waveform)
            entry["raised"] = False
            if case["raises"]:
                failures.append(f"{case_id}: expected an exception, got a waveform")
        except Exception as exc:  # noqa: BLE001 — recorded, compared by the test
            entry["raised"] = True
            entry["error_type"] = type(exc).__name__
            entry["error"] = str(exc)
            if not case["raises"]:
                failures.append(
                    f"{case_id}: unexpected {type(exc).__name__}: {exc}\n"
                    + traceback.format_exc()
                )
        result["cases"][case_id] = entry

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "waveform_golden.json"
    out_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"wrote {out_path} ({len(result['cases'])} cases)")

    if failures:
        print("FAILURES:", file=sys.stderr)
        for failure in failures:
            print(" -", failure, file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True,
                        help="output directory for waveform_golden.json")
    args = parser.parse_args()
    return run(Path(args.out))


if __name__ == "__main__":
    sys.exit(main())
