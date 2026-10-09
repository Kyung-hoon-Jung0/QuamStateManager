"""S10 C1 single-anchor mutation check; source bytes are restored after each run.

python tools/mutate_hub_no_roots.py --report OUTPUT.json [--only NAME ...]
A mutation is killed only by an assertion failure, never a tool/collection error.
Each mutation runs the pins it targets: a ``-k`` expression over
tests/test_hub_no_roots_sync.py, or a list of pytest node ids.
"""

# S10 C7: old -> new, remove callerless snapshot hooks and retain ledger behavior.

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
TEST = "tests/test_hub_no_roots_sync.py"
SYNC = "quam_state_manager/core/hub_sync.py"
HUB = "quam_state_manager/core/hub.py"
STORE = "quam_state_manager/core/hub_store.py"
ROUTES = "quam_state_manager/web/routes.py"
#: (name, file, anchor, replacement, the pins it must turn red: a -k expression
#: over TEST, or a list of pytest node ids)
MUTATIONS = [
    ("ready_before_the_first_slice", SYNC,
     "        if not self.syncable:\n            # nothing to read",
     "        if not roots:\n            # nothing to read",
     "building_until_the_first_slice"),
    ("open_does_not_kick_a_folderless_chip", SYNC,
     "    if kick and cs.syncable:\n        _kick(cs)",
     "    if kick and cs.roots:\n        _kick(cs)",
     "periodic_relists or cannot_open"),
    ("kick_requires_a_data_folder", SYNC,
     "    if cs.syncable:\n        _kick(cs)\n\n\ndef slice_failed",
     "    if cs.roots:\n        _kick(cs)\n\n\ndef slice_failed",
     "building_until_the_first_slice or gets_its_ledger or versions_show or tripwire"),
    ("periodic_skips_a_folderless_chip", SYNC,
     "        if not cs.syncable or not cs.active:",
     "        if not cs.roots or not cs.active:",
     "periodic_relists or taken_after_the_open"),
    ("testing_gate_on_the_folderless_kick", ROUTES,
     "        kick = (not app.config.get(\"TESTING\") or bool(app.config.get(\"HUB_SYNC_ON_OPEN\"))\n"
     "                or not roots)",
     "        kick = (not app.config.get(\"TESTING\") or bool(app.config.get(\"HUB_SYNC_ON_OPEN\")))",
     "gets_its_ledger or versions_show or tripwire"),
    ("run_snapshots_imported", ROUTES,
     "            if (getattr(m, \"kind\", None) == \"exp\" or m.trigger == \"experiment\"\n"
     "                    or m.run_id is not None or m.experiment_folder_path):\n                continue",
     "            if False:\n                continue",
     "no_run_snapshot"),
    ("parallel_snapshots_imported", ROUTES,
     "            if src.get(\"lineage\", LINEAGE_PARALLEL) == LINEAGE_PARALLEL:\n"
     "                continue",
     "            if False:\n                continue",
     "no_parallel_folder_snapshot"),
    # S10 C1.5 deleted C1's other_folders fallback (routes._hub_other_folders and
    # its two gates): every chip reads through its folder view, mutation-checked
    # by tools/mutate_hub_folder_view.py.
    ("failed_slice_not_recorded", HUB,
     "            self.errors.append(f\"{hub.dir}: sync: {type(exc).__name__}: {exc}\")\n"
     "            hub_sync.slice_failed(hub.dir, exc)\n\n    def _sync_one",
     "            self.errors.append(f\"{hub.dir}: sync: {type(exc).__name__}: {exc}\")\n\n    def _sync_one",
     "cannot_open"),
    ("failed_slice_never_cleared", SYNC,
     "        more = self.has_work()\n        self.slice_error = None\n",
     "        more = self.has_work()\n",
     "cannot_open"),
    ("degraded_state_removed", SYNC,
     "        elif not roots and self.slice_error is not None:",
     "        elif False:",
     "cannot_open"),
    ("idle_signalled_before_the_handles_are_released", HUB,
     # the order before the fix: idle first, the handles released after
     ["            finally:\n                try:\n                    if self._q.empty():",
      "                finally:\n                    with self._lock:\n                        self._pending -= 1\n"
      "                        if self._pending <= 0:\n                            self._pending = 0\n"
      "                            self._idle.set()\n"],
     ["            finally:\n                with self._lock:\n                    self._pending -= 1\n"
      "                    if self._pending <= 0:\n                        self._pending = 0\n"
      "                        self._idle.set()\n                try:\n                    if self._q.empty():",
      "                finally:\n                    pass\n"],
     "flush_returns_only_once"),
    ("wal_switch_not_retried", STORE,
     "        _enable_wal(self.conn)\n",
     "        self.conn.execute(\"PRAGMA journal_mode=WAL\")\n",
     "wal_switch or two_windows"),
    ("schema_step_not_under_one_lock", STORE,
     "        self.conn.execute(\"BEGIN IMMEDIATE\")\n        try:\n            if not self._schema_current():",
     "        try:\n            if not self._schema_current():",
     "two_windows"),
    ("identity_not_rechecked_under_the_lock", STORE,
     "            self.conn.execute(\"BEGIN IMMEDIATE\")\n            try:\n                for key, val in expected.items():",
     "            try:\n                for key, val in expected.items():",
     "two_windows or keeps_the_identity"),
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
