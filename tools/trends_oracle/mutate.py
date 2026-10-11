"""Mutation check of the hard-case pins: apply one mutation to product code, run the pin, restore
byte-for-byte (line endings kept).

SM_TEST_ENV=<conda env with the QM stack> python mutate.py <worktree> [name ...] -> mutations.json
"""
import json
import os
import subprocess
import sys

WT = sys.argv[1]
ONLY = set(sys.argv[2:])
MUT = [
    ("M1_utc_stamp_local", "quam_state_manager/core/timefmt.py",
     'return (_EPOCH + timedelta(microseconds=int(utc_us))).strftime("%Y%m%d_%H%M%S")',
     'return (_EPOCH + timedelta(microseconds=int(utc_us) + 9 * 3600 * 10**6)).strftime("%Y%m%d_%H%M%S")',
     "test_a_point_sits_at_the_runs_utc_second_not_its_local_clock"),
    ("M2_same_int_float_differ", "quam_state_manager/core/hub_rules.py",
     "        return a == b\n    if type(a) is not type(b):",
     "        return a == b and type(a) is type(b)\n    if type(a) is not type(b):",
     "test_the_same_value_resaved_and_an_int_float_resave_are_no_change"),
    ("M3_proven_always", "quam_state_manager/core/value_history.py",
     "        if proven:\n            return \"run_proven\"",
     "        if True:\n            return \"run_proven\"",
     "test_a_run_is_named_as_writer_only_with_its_own_patch"),
    ("M4_nonfinite_dropped", "quam_state_manager/core/trend_index.py",
     '"v": [None if x is _NONFINITE else x for x in vals]',
     '"v": [x for x in vals if x is not _NONFINITE and x is not None]',
     "test_datasets_trends_is_each_complete_runs_fit_result_in_run_id_order"),
    ("M5_nan_drawn_as_value", "quam_state_manager/web/routes.py",
     'pts = ([(p["timestamp"], p["value"] if _vh_numeric(p["value"]) is not None else None)',
     'pts = ([(p["timestamp"], p["value"] if True else None)',
     "test_nan_is_a_gap_and_nan_resaved_is_no_change"),
    ("M2b_int_float_two_layers", "quam_state_manager/core/hub_rules.py|quam_state_manager/core/value_history.py",
     "        return a == b\n    if type(a) is not type(b):|            if _same_or_absent(running, new):",
     "        return a == b and type(a) is type(b)\n    if type(a) is not type(b):|            if False:",
     "test_the_same_value_resaved_and_an_int_float_resave_are_no_change"),
]
env = dict(os.environ, PYTHONUTF8="1")
out = []
for name, rel, old, new, test in MUT:
    if ONLY and name not in ONLY:
        continue
    saved, bad = [], None
    for rel1, old1, new1 in zip(rel.split("|"), old.split("|"), new.split("|")):
        p = os.path.join(WT, rel1)
        with open(p, "rb") as fh:
            raw = fh.read()
        src = raw.decode("utf-8")
        if "\r\n" in src:
            old1, new1 = old1.replace("\n", "\r\n"), new1.replace("\n", "\r\n")
        if src.count(old1) != 1:
            bad = f"anchor found {src.count(old1)} times in {rel1}"
            break
        saved.append((p, raw))
        with open(p, "wb") as fh:
            fh.write(src.replace(old1, new1).encode("utf-8"))
    if bad:
        for p, raw in saved:
            with open(p, "wb") as fh:
                fh.write(raw)
        out.append({"mutation": name, "error": bad})
        print(out[-1], flush=True)
        continue
    try:
        r = subprocess.run(["conda", "run", "-n", os.environ["SM_TEST_ENV"], "python", "-m", "pytest",
                            f"tests/test_trends_oracle_cases.py::{test}", "-q", "-p", "no:cacheprovider",
                            "--timeout=600", "--timeout-method=thread"],
                           cwd=WT, env=env, capture_output=True, text=True, shell=True)
        tail = [l for l in r.stdout.splitlines() if l.strip()][-3:]
        out.append({"mutation": name, "test": test, "red": r.returncode != 0,
                    "by_assertion": "AssertionError" in r.stdout, "tail": tail})
    finally:
        for p, raw in saved:
            with open(p, "wb") as fh:
                fh.write(raw)
    print(out[-1], flush=True)
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mutations.json"), "w"), indent=1)
