"""Capture existing fixture calls before extraction, then replay them exactly.

Capture: PYTHONUTF8=1 python tests/test_cz_flux_math_equivalence.py
The capture runs the existing CZ and inspector tests with recording wrappers.
"""
import copy
import functools
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from quam_state_manager.core import cz_gate_plan as C, gate_inspector as G

GOLDEN = ROOT / "tests/golden/cz_flux_math_before.json"
MEASURED_PLAN_KEYS = {"t_lim_ns", "t_p_ns", "duration_minus_t_p_ns", "sources",
                      "stored_values", "stored_reasons", "reason_details"}


def plain(value):
    if isinstance(value, np.ndarray):
        return plain(value.tolist())
    if isinstance(value, np.generic):
        return plain(value.item())
    if isinstance(value, C.Conversion):
        return {"conversion_qubit": plain(value.qubit)}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [plain(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return {"nonfinite": str(value)}
    return value


def restore(value):
    if isinstance(value, dict):
        if set(value) == {"conversion_qubit"}:
            return C.Conversion(restore(value["conversion_qubit"]))
        if set(value) == {"nonfinite"}:
            return float(value["nonfinite"])
        return {k: restore(v) for k, v in value.items()}
    if isinstance(value, list):
        return [restore(v) for v in value]
    return value


def invoke(record):
    name = record["call"]
    args, kwargs = restore(record["args"]), restore(record["kwargs"])
    if name.startswith("Conversion."):
        fn = getattr(C.Conversion(record["qubit"]), name.split(".")[1])
    else:
        module, method = name.split(".")
        fn = getattr(C if module == "C" else G, method)
    return plain(fn(*args, **kwargs))


def test_pre_extraction_fixture_outputs_are_identical():
    for record in json.loads(GOLDEN.read_text(encoding="utf-8")):
        expected = copy.deepcopy(record["output"])
        if record["call"] == "C.operations":
            # S2.5 now exposes the final holder for pointer-valued amplitudes.
            # Keep all captured numerical outputs and every other field pinned.
            doc = restore(record["args"])[0]
            for row in expected[0]:
                if row["path"] == "qubit_pairs.q1-2.macros.cz_snz":
                    # W6-1 resolves the SNZ runtime length: two 10 ns lobes,
                    # two central samples and two edge samples. Only this
                    # formerly unknown duration changes; flux math stays pinned.
                    row["duration_ns"] = 24
                    row["duration_note"] = "24 ns from the played SNZ waveform (flat length, edges and padding)"
                def holder(path):
                    return (C.read(doc, path).get("holder_path") or path) if path else path
                # S3 adds unknown-only coupler metadata and replaces the old omission
                # note. All pre-existing numerical outputs remain pinned exactly.
                has_coupler = bool(row.get("coupler") or C.current(doc, "qubit_pairs." + row["pair"] + ".coupler"))
                if has_coupler:
                    row.update(coupler_model=None, shifted_resonances=[])
                    if row.get("coupler_note") in row["notes"]:
                        row["notes"].remove(row["coupler_note"])
                    row["notes"].append(C.COUPLER_UNKNOWN)
                    for line in row["lines"]:
                        line.update(coupler_shift_hz=None, shifted_frequency_hz=None,
                                    coupler_frequency_hz=None, coupler_method=C.COUPLER_UNKNOWN)
                row["amplitudes"] = [holder(p) for p in row["amplitudes"]]
                for line in row["lines"]:
                    line["amplitude_path"] = holder(line["amplitude_path"])
        actual = invoke(record)
        if record["call"] == "C.operations":
            # The measured-timing display adds only these plan keys; their values
            # are pinned in test_cz_gate_plan_measurements. Every older key stays exact.
            for exp_row, got_row in zip(expected[0], actual[0]):
                exp_plan, got_plan = exp_row.get("plan"), got_row.get("plan")
                if isinstance(exp_plan, dict) and isinstance(got_plan, dict):
                    added = set(got_plan) - set(exp_plan)
                    assert added <= MEASURED_PLAN_KEYS, (record["call"], sorted(added))
                    for key in added:
                        exp_plan[key] = got_plan[key]
        assert actual == expected, record["call"]


def capture():
    import pytest
    records = {}
    patches = []

    def wrap(owner, method, prefix):
        original = getattr(owner, method)
        @functools.wraps(original)
        def recording(*args, **kwargs):
            inputs = args[1:] if prefix == "Conversion" else args
            record = {"call": prefix + "." + method,
                      "args": plain(copy.deepcopy(inputs)),
                      "kwargs": plain(copy.deepcopy(kwargs))}
            if prefix == "Conversion":
                record["qubit"] = copy.deepcopy(args[0].qubit)
            output = original(*args, **kwargs)
            record["output"] = plain(output)
            records[json.dumps(record, sort_keys=True)] = record
            return output
        patches.append((owner, method, original))
        setattr(owner, method, recording)

    for method in ("operations", "fitted_frequency", "fitted_shift", "resonances"):
        wrap(C, method, "C")
    for method in ("point", "curve", "_range"):
        wrap(C.Conversion, method, "Conversion")
    wrap(G, "compute_detuning_sweep", "G")
    files = sorted(p for p in (ROOT / "tests").glob("test_cz_*.py")
                   if not p.name.startswith("test_cz_flux_math"))
    files.append(ROOT / "tests/test_gate_inspector.py")
    try:
        result = pytest.main([*(str(p) for p in files), "-q", "-p", "no:cacheprovider"])
    finally:
        for owner, method, original in reversed(patches):
            setattr(owner, method, original)
    GOLDEN.write_text(json.dumps(list(records.values()), ensure_ascii=False,
                                 separators=(",", ":")), encoding="utf-8")
    print(f"Captured {len(records)} calls; fixture suite exit code {result}")


if __name__ == "__main__":
    capture()
