"""Fold the main comparison, the injected hard cases and the pin mutations into wp3/report.json."""
import json
import os
import sys

W = sys.argv[1]
main = json.load(open(os.path.join(W, "report.json"), encoding="utf-8"))
if "main" in main:
    main = main["main"]
inj = json.load(open(os.path.join(W, "inject", "report.json"), encoding="utf-8"))
cases = json.load(open(os.path.join(W, "inject", "cases.json"), encoding="utf-8"))
mut = [
    {"mutation": "M1 utc_stamp in local time (+9 h)", "pin": "test_a_point_sits_at_the_runs_utc_second_not_its_local_clock", "red": True, "by_assertion": True},
    {"mutation": "M2 hub_rules.same: int != float (one layer)", "pin": "test_the_same_value_resaved_and_an_int_float_resave_are_no_change", "red": False,
     "note": "not reachable alone: the ledger stores numbers as REAL and effective_rows re-applies same(); M2b breaks both layers"},
    {"mutation": "M2b same() int != float AND effective_rows skips no row", "pin": "test_the_same_value_resaved_and_an_int_float_resave_are_no_change", "red": True, "by_assertion": True},
    {"mutation": "M3 provenance(): every run row run_proven", "pin": "test_a_run_is_named_as_writer_only_with_its_own_patch", "red": True, "by_assertion": True},
    {"mutation": "M4 trend_index payload drops non-finite/null values", "pin": "test_datasets_trends_is_each_complete_runs_fit_result_in_run_id_order", "red": True, "by_assertion": True},
    {"mutation": "M5 curated series draws NaN as a value, not a gap", "pin": "test_nan_is_a_gap_and_nan_resaved_is_no_change", "red": True, "by_assertion": True},
]
findings = [
    {"id": "TR-01", "severity_guess": "P1", "surface": "Chip Status > Trends (+ drawer, report)",
     "one_line": "a chain of contradicted changes drops the change every later recorded state agrees with; the line holds an older value"},
    {"id": "TR-02", "severity_guess": "P1 (P0 if a wrong number counts as P0)", "surface": "Chip Status > Trends (+ drawer)",
     "one_line": "a composite value (derived readout fidelity, pointer-alias retarget) mixes kept and witness-filtered parts into a number its run never saved"},
    {"id": "TR-03", "severity_guess": "P2", "surface": "Chip Status > Trends + drawer",
     "one_line": "a data folder copied to a new place: every point and drawer row loses its run link (uid/saved_uid/Data button)"},
    {"id": "TR-04", "severity_guess": "P2", "surface": "printable report > Trends",
     "one_line": "caption still says 'at most 400 per line'"},
]
out = {"main": main, "inject": {"tally": inj["tally"], "by_kind": inj["by_kind"], "lane": inj["lane"], "cases": cases},
       "mutations": mut, "findings": findings}
json.dump(out, open(os.path.join(W, "report.json"), "w", encoding="utf-8"), indent=1)
json.dump(mut, open(os.path.join(W, "mutations.json"), "w", encoding="utf-8"), indent=1)
print(json.dumps({k: v for k, v in main["tally"].items()}, indent=0))
