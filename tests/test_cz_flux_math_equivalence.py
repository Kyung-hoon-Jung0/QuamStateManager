"""Capture existing fixture calls before extraction, then replay them exactly.

Capture: PYTHONUTF8=1 python tests/test_cz_flux_math_equivalence.py
The capture runs the existing CZ and inspector tests with recording wrappers.
"""
import copy
import functools
import json
import math
import re
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


def assert_equivalent(actual, expected, path, hz_abs_tol=0.0):
    """Pin structure and values, allowing only floating-point roundoff.

    NumPy's transcendental functions can differ by a few ULPs across platforms.
    A relative tolerance at GHz scale would hide meaningful frequency changes.
    """
    if isinstance(expected, float):
        assert isinstance(actual, float), path
        field = path.rsplit(".", 1)[-1].split("[", 1)[0]
        frequency = path.startswith("C.fitted_shift") or field.endswith("_hz")
        tolerance = max(hz_abs_tol if frequency else 0.0, 8 * math.ulp(expected))
        assert abs(actual - expected) <= tolerance, (path, actual, expected)
    elif isinstance(expected, dict):
        assert isinstance(actual, dict) and actual.keys() == expected.keys(), path
        for key in expected:
            assert_equivalent(actual[key], expected[key], f"{path}.{key}", hz_abs_tol)
    elif isinstance(expected, list):
        assert isinstance(actual, list) and len(actual) == len(expected), path
        for index, (got, want) in enumerate(zip(actual, expected)):
            assert_equivalent(got, want, f"{path}[{index}]", hz_abs_tol)
    elif isinstance(expected, str) and expected.endswith(_OLD_MISSING):
        # the one combined reason now names what is really missing (prefix kept)
        head = expected[:-len(_OLD_MISSING)]
        tail = actual[len(head):] if isinstance(actual, str) else None
        assert isinstance(actual, str) and actual.startswith(head) and (
            tail in _NEW_MISSING or _LEAF_MISSING.fullmatch(tail)), (path, actual, expected)
    else:
        assert actual == expected, (path, actual, expected)


_OLD_MISSING = "f_01 or the operation amplitude is missing"
_NEW_MISSING = {"f_01 is missing", "the operation amplitude is missing",
                "f_01 and the operation amplitude are missing"}
# with f_01 present, an unreadable amplitude names its own leaf
# (cz_gate_plan.unreadable_amplitude) instead of "the operation amplitude"
_LEAF_MISSING = re.compile(r"[\w.\-]+\.amplitude is missing or not a number")


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
                    # D7 names each line's length and the macro's own duration in the note
                    row["duration_note"] = ("24 ns played span (control 24 ns, target 24 ns, flat length, edges "
                                            "and padding); the macro's own duration is 24 ns (the control pulse)")
                    row["two_flux_lengths"] = {"control": 24, "target": 24, "macro_duration_ns": 24}
                    for line in row["lines"]:
                        line["played_length_ns"] = 24
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
                # A CZGate whose duration is "#./inferred_duration" now reads the
                # duration the gate infers (its longest played pulse length). Only rows
                # that were "unknown: no play-time duration override" may change, and
                # only the duration, its note and the plan's duration_minus_t_p_ns.
                if (exp_row.get("duration_ns") is None
                        and str(exp_row.get("duration_note")).startswith("unknown: no play-time duration override")
                        and " ns inferred the way the gate infers it" in str(got_row.get("duration_note"))):
                    assert isinstance(got_row["duration_ns"], int) and got_row["duration_ns"] > 0
                    exp_row["duration_ns"] = got_row["duration_ns"]
                    exp_row["duration_note"] = got_row["duration_note"]
                    if isinstance(exp_plan, dict) and isinstance(got_plan, dict) and "duration_minus_t_p_ns" in exp_plan:
                        exp_plan["duration_minus_t_p_ns"] = got_plan.get("duration_minus_t_p_ns")
                # A two-flux row with no stored duration now reads its played span:
                # the longer of the two lines' played lengths. Same rows only; the
                # duration must be that maximum, as its own note states it.
                span = re.match(r"(\d+(?:\.\d+)?) ns played span \(control (\d+(?:\.\d+)?) ns, "
                                r"target (\d+(?:\.\d+)?) ns", str(got_row.get("duration_note")))
                if (exp_row.get("duration_ns") is None and exp_row.get("kind") == "two-flux"
                        and str(exp_row.get("duration_note")).startswith("unknown: no play-time duration override")
                        and span):
                    total, ctl, tgt = (float(g) for g in span.groups())
                    assert got_row["duration_ns"] == total == max(ctl, tgt), exp_row["path"]
                    exp_row["duration_ns"] = got_row["duration_ns"]
                    exp_row["duration_note"] = got_row["duration_note"]
                    if isinstance(exp_plan, dict) and isinstance(got_plan, dict) and "duration_minus_t_p_ns" in exp_plan:
                        exp_plan["duration_minus_t_p_ns"] = got_plan.get("duration_minus_t_p_ns")
                # The recheck's wording item adds only `words` to the nearest
                # resonance, recomputed here from the PINNED name and distance in
                # the frame of the row's single moving line (meaning pinned in
                # test_gate_inspector); name, delta and distance stay exact.
                # Each line now flags whether its amplitude leaf holds text (such
                # an amplitude is named, never played). Every captured fixture
                # amplitude is numeric, so the flag is pinned False here; its
                # meaning is pinned in the CZ plan tests.
                for line in exp_row["lines"]:
                    line["amplitude_text"] = False
                # A two-flux row now states each line's played length and the pair's
                # lengths (meaning pinned in test_cz_plan_d3_d7). Display data only:
                # taken from the output, but each role's length must be the one its
                # line states, so the two views cannot disagree.
                if exp_row.get("kind") == "two-flux" and "two_flux_lengths" not in exp_row \
                        and "two_flux_lengths" in got_row:
                    tfl = got_row["two_flux_lengths"]
                    exp_row["two_flux_lengths"] = tfl
                    for exp_ln, got_ln in zip(exp_row["lines"], got_row["lines"]):
                        if "played_length_ns" in got_ln and "played_length_ns" not in exp_ln:
                            if isinstance(tfl, dict) and exp_ln["role"] in tfl:
                                assert got_ln["played_length_ns"] == tfl[exp_ln["role"]], exp_row["path"]
                            exp_ln["played_length_ns"] = got_ln["played_length_ns"]
                # The two-flux length check adds its own notes (a length mismatch,
                # an underivable length, a missing or bad sampling rate; meaning pinned
                # in test_cz_plan_d3_d7 and test_cz_plan_honest_rows). Only those
                # sentences may be new: with them taken out, every older note stays
                # exact and in order.
                if exp_row.get("kind") == "two-flux" and isinstance(exp_row.get("notes"), list):
                    tfl = got_row.get("two_flux_lengths")
                    mismatch = tfl.get("mismatch") if isinstance(tfl, dict) else None
                    names = [ln["name"] for ln in exp_row["lines"]]
                    length_notes = {"two-flux length check not made: the played length of "
                                    + " and ".join(sub) + " could not be derived"
                                    for sub in (names, names[:1], names[1:]) if sub}
                    length_notes.add("two-flux lengths at the default 1 GS/s; no sampling_rate stored")
                    length_notes |= {f"two-flux length check not made: {ln['qubit_path']}"
                                     ".z.opx_output.sampling_rate is not a positive number"
                                     for ln in exp_row["lines"]}
                    if mismatch:
                        length_notes.add(mismatch)
                    got_notes = list(got_row.get("notes") or [])
                    # a lobe that overlaps no lobe of the other line is named too
                    length_notes |= {n for n in got_notes if n.startswith("two-flux lobes paired by time: ")}
                    if not set(exp_row["notes"]) & length_notes and                             [n for n in got_notes if n not in length_notes] == exp_row["notes"]:
                        exp_row["notes"] = got_notes
                # A two-flux row whose lobe times are known pairs lobes by time, not
                # by order (meaning pinned in test_cz_plan_lobe_time_pairing). Every
                # captured resonance must still be found, with its exact distance; the
                # only new entries are time-paired ones (named per line). The nearest
                # keeps its name and distance; only its lobe label may name the pair.
                got_res = got_row.get("lobe_resonances") or []
                if exp_row.get("kind") == "two-flux" and exp_row.get("lobe_resonances") != got_res:
                    pairs = [(r["name"], r["distance_hz"]) for r in got_res]
                    for r in exp_row["lobe_resonances"]:
                        assert (r["name"], r["distance_hz"]) in pairs, (exp_row["path"], r)
                    old_stages = {r["lobe"] for r in exp_row["lobe_resonances"]}
                    assert all(r["lobe"] in old_stages or ", " in r["lobe"] for r in got_res), exp_row["path"]
                    exp_row["lobe_resonances"] = got_res
                    exp_near, got_near = exp_row.get("nearest"), got_row.get("nearest")
                    if isinstance(exp_near, dict) and isinstance(got_near, dict):
                        assert (exp_near["name"], exp_near["distance_hz"]) == (
                            got_near["name"], got_near["distance_hz"]), exp_row["path"]
                        exp_near["lobe"] = got_near["lobe"]
                exp_near = exp_row.get("nearest")
                if isinstance(exp_near, dict):
                    movers = [ln["role"] for ln in exp_row["lines"] if ln.get("moves")]
                    exp_near["words"] = C.cz_flux_math.resonance_offset_words(
                        exp_near["name"], exp_near["distance_hz"], movers[0] if len(movers) == 1 else None)
                if isinstance(exp_plan, dict) and isinstance(got_plan, dict):
                    added = set(got_plan) - set(exp_plan)
                    assert added <= MEASURED_PLAN_KEYS, (record["call"], sorted(added))
                    for key in added:
                        exp_plan[key] = got_plan[key]
                # One per-gate fidelity per macro (query.pair_gate_fidelity) adds only
                # which key the value came from, its physicality and any disagreeing key;
                # the value and load id stay pinned. Their meaning is pinned in
                # test_pair_gate_fidelity. The pick's wording (`basis_text`, one
                # sentence naming why this key was read) and `no_number_text` (the
                # per-gate keys that hold no number) are display text only, pinned in
                # test_cz_gate_plan and test_nonfinite_fidelity.
                exp_fid, got_fid = exp_row.get("fidelity"), got_row.get("fidelity")
                if isinstance(exp_fid, dict) and isinstance(got_fid, dict):
                    for name, entry in exp_fid.items():
                        got = got_fid.get(name)
                        if isinstance(entry, dict) and isinstance(got, dict):
                            added = set(got) - set(entry)
                            assert added <= {"key", "physical", "conflict", "basis_text", "no_number_text"}, (
                                record["call"], sorted(added))
                            for key in added:
                                entry[key] = got[key]
        model = None
        if record["call"] == "C.fitted_shift":
            model = restore(record["args"])[0]
        elif record["call"].startswith("Conversion."):
            model = C.model_of(record["qubit"])
        # Subtracting two GHz arc frequencies propagates their rounding into
        # shifts and range limits, including at zero. Only Hz fields receive
        # this allowance; voltages and every other float keep their own ULPs.
        hz_abs_tol = 8 * math.ulp(model["f_max_hz"] + model["ec_hz"]) if model else 0.0
        assert_equivalent(actual, expected, record["call"], hz_abs_tol)


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
