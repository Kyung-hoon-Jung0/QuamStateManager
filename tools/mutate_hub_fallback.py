"""C0 single-anchor mutation check; source bytes are restored after each run.

python tools/mutate_hub_fallback.py --report OUTPUT.json
A mutation is killed only by an assertion failure, never a tool/collection error.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "quam_state_manager/web/routes.py"
TEST = "tests/test_hub_fallback_tripwire.py"
MUTATIONS = [
    ("drawer_call_removed", '    _hub_fallback_reached("drawer", ans["reason"])', "    pass"),
    # S10 C5: "trends_call_removed" retired -- the Trends snapshot arm and its tripwire call are deleted
    # S10 C6: versions_call_removed / state_history_call_removed retired -- both call
    # sites were deleted with the snapshot branches they guarded.
    ("counts_removed", '        _HUB_FALLBACK_REACHED[key] = _HUB_FALLBACK_REACHED.get(key, 0) + 1', "        pass"),
    ("tripwire_disabled", '    if os.environ.get("HUB_FALLBACK_TRIPWIRE") == "1" and current_app.testing:', "    if False:"),
    ("testing_gate_removed", '    if os.environ.get("HUB_FALLBACK_TRIPWIRE") == "1" and current_app.testing:',
     '    if os.environ.get("HUB_FALLBACK_TRIPWIRE") == "1":'),
    ("warning_dedup_removed", '        first = warning_key not in _HUB_FALLBACK_WARNED', "        first = True"),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    original = SOURCE.read_bytes()
    source = original.decode("utf-8")
    results = []
    env = dict(os.environ, PYTHONUTF8="1", NODE_PATH="D:/work/statemanager/node_modules")
    env.pop("HUB_FALLBACK_TRIPWIRE", None)
    env.pop("PYTEST_ADDOPTS", None)
    try:
        for name, anchor, replacement in MUTATIONS:
            assert source.count(anchor) == 1, (name, source.count(anchor))
            try:
                SOURCE.write_bytes(source.replace(anchor, replacement).encode("utf-8"))
                for attempt in range(3):
                    # the interpreter running this tool runs the pin (start it from the test env)
                    proc = subprocess.run([sys.executable, "-m", "pytest", TEST,
                                           "-q", "-p", "no:cacheprovider", "--timeout=900", "--timeout-method=thread"],
                                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8")
                    output = proc.stdout + proc.stderr
                    if "__conda_tmp" not in output:
                        break
                killed = proc.returncode == 1 and "AssertionError" in output and "ERROR collecting" not in output
                results.append({"mutation": name, "killed_by_assertion": killed, "exit_code": proc.returncode})
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
                print(f"{name}: {'RED (assertion)' if killed else 'NOT KILLED'}", flush=True)
            finally:
                SOURCE.write_bytes(original)
    finally:
        SOURCE.write_bytes(original)
    if not all(r["killed_by_assertion"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
