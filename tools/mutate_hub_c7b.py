"""Mutation checks for callerless machinery and package/tool deletion pins."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

# S10 C7: import context -> explicit sibling path, support module-based loading.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from mutate_hub_drawer import run_one

# S10 C7: old -> new, assert retired machinery and tool references cannot return.
FAMILY = "quam_state_manager/core/chip_trends_ram.py"
TOOL = "tools/perf_hub_chip_status.py"
ANCHOR = 'SURFACES = {'
C7 = "tests/test_s10_c7_snapshot_machinery_gone.py::test_callerless_snapshot_machinery_stays_absent"

MUTATIONS = [
    ("family_append_method_restored", FAMILY,
     '    def _signature(self) -> tuple:',
     '    def der' + 'ive(self):\n        return self\n\n    def _signature(self) -> tuple:', [C7]),
    ("family_copy_method_restored", FAMILY,
     '    @property\n    def sort_key(self):',
     '    def co' + 'py(self):\n        return self\n\n    @property\n    def sort_key(self):', [C7]),
    ("family_append_filter_restored", FAMILY,
     '        self.roots = tuple(roots)',
     '        self.te' + 'rm = None\n        self.roots = tuple(roots)', [C7]),
    ("family_append_index_restored", FAMILY,
     '        self.roots = tuple(roots)',
     '        self._in' + 'dex = {}\n        self.roots = tuple(roots)', [C7]),
    ("snapshot_token_reference_restored", TOOL, ANCHOR,
     '# hist_' + 'token\n' + ANCHOR, [C7]),
    ("snapshot_pool_reference_restored", TOOL, ANCHOR,
     '# param_history_' + 'ram\n' + ANCHOR, [C7]),
    ("c4_tool_reference_restored", TOOL, ANCHOR,
     '# _runs_' + 'field_series\n' + ANCHOR,
     ["tests/test_hub_drawer.py::test_removed_value_readers_are_absent"]),
    ("c5_tool_reference_restored", TOOL, ANCHOR,
     '# _PH_' + 'CHANGES_MEMO\n' + ANCHOR,
     ["tests/test_s10_chip_status_deleted.py::test_the_chip_status_and_param_history_snapshot_paths_stay_deleted"]),
    ("c6_tool_reference_restored", TOOL, ANCHOR,
     '# data-' + 'changes\n' + ANCHOR,
     ["tests/test_s10_c6_snapshot_branches_gone.py::test_the_deleted_snapshot_branch_names_are_absent"]),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    results = []
    for mutation in MUTATIONS:
        result = run_one(*mutation, python=sys.executable)
        results.append(result)
        print(f"{result['name']}: {'RED' if result['red'] else 'SURVIVED'}", flush=True)
    report = {"mutations": len(results), "red": sum(r["red"] for r in results), "results": results}
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert report["red"] == report["mutations"], "a mutation survived or failed without an assertion"


if __name__ == "__main__":
    main()
