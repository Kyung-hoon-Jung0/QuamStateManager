"""S10 walk round 3 (N4 / N5 / N8) single-anchor mutations; source bytes are restored after each run.

python tools/mutate_s10_walk_round3.py --report OUTPUT.json [--only NAME ...]
A mutation is killed only by an assertion failure, never a tool/collection error.
Each mutation runs the pins it targets: a ``-k`` expression over
tests/test_s10_walk_round3.py, or a list of pytest node ids.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TEST = "tests/test_s10_walk_round3.py"
SYNC = "quam_state_manager/core/hub_sync.py"
LANES = "quam_state_manager/core/hub_lanes.py"
VH = "quam_state_manager/core/value_history.py"
ROUTES = "quam_state_manager/web/routes.py"
TEMPLATES = "quam_state_manager/web/templates/"
#: (name, file, anchor, replacement, the pins it must turn red: a -k expression
#: over TEST, or a list of pytest node ids)
MUTATIONS = [
    # -- N4: an archived chip's grid and drawer show its newest recorded values
    ("latest_values_never_filled", ROUTES,
     "            latest_values = _hub_latest_values(hub_table, props)\n",
     "            latest_values = {}\n",
     "newest_recorded_value"),
    ("latest_cell_shows_a_dash", TEMPLATES + "_param_history.html",
     "{% elif latest and latest.value is not none %}",
     "{% elif False %}",
     "newest_recorded_value"),
    ("drawer_names_no_latest", ROUTES,
     "    latest = values[-1] if values and not is_loaded else None\n",
     "    latest = None\n",
     "newest_recorded_value"),
    ("open_chips_drift_box_drawn", TEMPLATES + "_param_history.html",
     "  {% if is_loaded_chip %}\n  <details class=\"ph-drift-card\" open>",
     "  {% if True %}\n  <details class=\"ph-drift-card\" open>",
     "none_of_the_open_chips_widgets"),
    ("open_chips_pill_drawn", TEMPLATES + "_param_history.html",
     "  {% if active_chips and is_loaded_chip %}",
     "  {% if active_chips %}",
     "none_of_the_open_chips_widgets"),
    ("open_chips_import_drawn", TEMPLATES + "_param_history.html",
     "      {% if is_loaded_chip %}{# S10 walk (N4): an import into the OPEN chip #}",
     "      {% if True %}{# S10 walk (N4): an import into the OPEN chip #}",
     "none_of_the_open_chips_widgets"),
    # -- N5: the folder the runs are under now; one count; a label on its own row
    ("the_folder_left_is_named", LANES,
     "            rids.sort(key=lambda r, e=eid: ((e, r) in gone, -(r or 0)))\n",
     "            pass\n",
     "moved_data_folder"),
    ("declared_spelling_unused", LANES,
     "        if got and got[0] and root_key(",
     "        if False and got and got[0] and root_key(",
     "moved_data_folder"),
    ("declared_spelling_never_kept", SYNC,
     "                store.set_meta(f\"{ROOT_SPELLING}{rs.root_id}\", spelled)\n",
     "                pass\n",
     "moved_data_folder"),
    ("an_unreadable_run_counted_in", LANES,
     "                unlinked_apart[\"unreadable\"] += 1\n                continue\n",
     "                pass\n",
     "moved_data_folder"),
    ("the_run_apart_unsaid", VH,
     "        if apart:\n            joined = ",
     "        if False:\n            joined = ",
     "moved_data_folder"),
    ("a_backup_label_rides_another_row", ROUTES,
     "        if getattr(m, \"kind\", None) == \"backup\":\n",
     "        if False:\n",
     "backup_label"),
    # -- N8: the step it is on
    ("steps_never_numbered", SYNC,
     "            return (f\": step {k} of {steps}, \" if steps > 1 else \": \") + what + cnt\n",
     "            return \": \" + what + cnt\n",
     "count_going_down or number_the_steps"),
    ("the_snapshot_step_never_entered", SYNC,
     "                self.phase = \"observing\"     # S10 walk (N8): its own step, counted in snapshots\n",
     "                pass\n",
     "count_going_down"),
    ("the_snapshot_step_not_counted_as_a_step", SYNC,
     "            \"observes\": self.observed_source is not None,\n",
     "            \"observes\": False,\n",
     "count_going_down"),
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
        a, r = anchor, replacement
        if "\r\n" in source:
            # a Windows checkout (core.autocrlf): the anchors are written with "\n"
            a, r = a.replace("\n", "\r\n"), r.replace("\n", "\r\n")
        assert source.count(a) == 1, (name, source.count(a))
        mutated = source.replace(a, r)
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
