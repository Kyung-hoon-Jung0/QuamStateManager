"""S10 C1.5 single-anchor mutation check; source bytes are restored after each run.

python tools/mutate_hub_folder_view.py --report OUTPUT.json [--only NAME ...]
A mutation is killed only by an assertion failure, never a tool/collection error.
Each mutation runs the pins it targets: a ``-k`` expression over
tests/test_hub_folder_view.py, or a list of pytest node ids.
"""

# S10 C7: old -> new, remove callerless snapshot hooks and retain ledger behavior.

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TEST = "tests/test_hub_folder_view.py"
INDEX = "quam_state_manager/core/hub_index.py"
LANES = "quam_state_manager/core/hub_lanes.py"
HISTORY = "quam_state_manager/core/history.py"
QUERY = "quam_state_manager/core/hub_query.py"
VH = "quam_state_manager/core/value_history.py"
SYNC = "quam_state_manager/core/hub_sync.py"
ROUTES = "quam_state_manager/web/routes.py"
#: (name, file, anchor, replacement, the pins it must turn red: a -k expression
#: over TEST, or a list of pytest node ids)
MUTATIONS = [
    ("live_dropped_from_the_index", INDEX,
     "    index.live.append(lives.setdefault(lk, len(lives) + 1) if lk else 0)",
     "    index.live.append(0)",
     "p1 or shared_data_folder_seen_from_a or cached_answer"),
    ("classify_by_time_only", LANES,
     "        kind, folder, lineage = classify_source(owner, view.key, source_stamp(f[\"t\"]), view.cut)",
     "        kind, folder, lineage = classify_source(owner and (view.key, owner[1]), view.key,\n"
     "                                                source_stamp(f[\"t\"]), view.cut)",
     "p1 or p2 or shared_data_folder_seen_from_a or no_data_folder_seen_from_a"),
    ("cut_at_the_newest_own_snapshot", HISTORY,
     "        if kind == SOURCE_THIS and (first is None or at < first):",
     "        if kind == SOURCE_THIS and (first is None or at > first):",
     "shared_data_folder_seen_from_a or shared_data_folder_seen_from_b"),
    ("global_rows_at_seams", QUERY,
     "    if lane is not None:\n        # S10 C1.5: a seam's row is its difference within the lane",
     "    if False:\n        # S10 C1.5: a seam's row is its difference within the lane",
     "p1 or p2 or p3"),
    ("break_seams_skipped", LANES,
     "                elif (f[\"kind\"] in SM_KINDS and f[\"base_chash\"] and lane_prev is not None",
     "                elif (False and f[\"kind\"] in SM_KINDS and f[\"base_chash\"] and lane_prev is not None",
     "p3 or single_folder_break"),
    ("unknown_treated_as_own", HISTORY,
     "    if owner is None:\n        return SOURCE_UNKNOWN, None",
     "    if owner is None:\n        return SOURCE_THIS, None",
     "unknown_folder or one_classifier"),
    ("unlinked_runs_shown", LANES,
     "            return view.key is not None and view.key in got",
     "            return True",
     "unlinked_root or no_data_folder_b"),
    ("slot_without_the_view_key", VH,
     "                slots[key] = (chip, target_sig(tgt), limit, scope) + (\n"
     "                    (folder.ident(),) if folder is not None else ())",
     "                slots[key] = (chip, target_sig(tgt), limit, scope)",
     "cached_answer"),
    ("fast_path_on_a_mixed_chip", LANES,
     "    if not foreign and not seam_pred:\n        return index",
     "    if True:\n        return index",
     "p1 or p2 or shared_data_folder_seen_from_a or unlinked_root"),
    ("cross_lane_dedupe", SYNC,
     "    lpred = pred if same_lane(store, pred, key) else _lane_good(store, lo, key, before=True)",
     "    lpred = pred",
     "observed_dedupe"),
    ("observed_hard_coded_own", ROUTES,
     "            source = ev.get(\"_source\") or {\"kind\": \"this\", \"folder\": None, \"label\": None, \"lineage\": \"own\"}",
     "            source = {\"kind\": \"this\", \"folder\": None, \"label\": None, \"lineage\": \"own\"}",
     "observed_state_its_own"),
    ("lane_flags_not_recomputed", LANES,
     "        want = flags & ~(REVERTS_TO_EARLIER | OVERLAPS_SM_WRITE)\n",
     "        want = flags\n",
     "lane_flags"),
    ("reuse_ignores_a_new_seams_rows", VH,
     "                    touched |= lane.new_rows(high)",
     "                    pass",
     "kept_answer_is_dropped"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()
    results = []
    env = dict(os.environ, PYTHONUTF8="1", NODE_PATH="D:/work/statemanager/node_modules")
    env.pop("PYTEST_ADDOPTS", None)
    for name, rel, anchor, replacement, select in MUTATIONS:
        if args.only and name not in args.only:
            continue
        path = ROOT / rel
        original = path.read_bytes()
        source = original.decode("utf-8")
        pairs = list(zip(anchor, replacement)) if isinstance(anchor, list) else [(anchor, replacement)]
        mutated = source
        for a, r in pairs:
            if "\r\n" in source:
                # a Windows checkout (core.autocrlf): the anchors are written with "\n"
                a, r = a.replace("\n", "\r\n"), r.replace("\n", "\r\n")
            assert mutated.count(a) == 1, (name, mutated.count(a))
            mutated = mutated.replace(a, r)
        targets = list(select) if isinstance(select, list) else [TEST, "-k", select]
        try:
            path.write_bytes(mutated.encode("utf-8"))
            for _attempt in range(3):
                # the interpreter running this tool runs the pin (start it from the test env)
                proc = subprocess.run([sys.executable, "-m", "pytest", *targets,
                                       "-q", "-p", "no:cacheprovider", "--timeout=900", "--timeout-method=thread"],
                                      cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
                output = proc.stdout + proc.stderr
                if "__conda_tmp" not in output:
                    break
            killed = proc.returncode == 1 and "AssertionError" in output and "ERROR collecting" not in output
            failed = [ln for ln in output.splitlines() if ln.startswith(("FAILED", "ERROR"))][:8]
            results.append({"mutation": name, "file": rel, "pins": targets, "killed_by_assertion": killed,
                            "exit_code": proc.returncode, "failed": failed,
                            "summary": (output.strip().splitlines() or [""])[-1]})
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
            print(f"{name}: {'RED (assertion)' if killed else 'NOT KILLED'} -- {results[-1]['summary']}",
                  flush=True)
        finally:
            path.write_bytes(original)
    if not all(r["killed_by_assertion"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
