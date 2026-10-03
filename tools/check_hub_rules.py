"""Read-only archive comparison of S2 against SM's existing Differ.

Pass archive paths explicitly; no environment-specific locations are built in.
Enumeration is frozen before sampling. Incomplete/changing files are skipped,
with a count, and newly arriving runs wait until the next invocation.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quam_state_manager.core import hub_rules as hub
from quam_state_manager.core.differ import Differ
from quam_state_manager.core.loader import QuamStore

REASONS = ("int_float", "long_arrays", "bool_string", "nan_constant", "exact_float")


@dataclass(frozen=True)
class Run:
    folder: Path
    state: Path
    wiring: Path
    run_id: int
    experiment: str
    order: tuple


def read_pair(run: Run) -> tuple[dict, dict]:
    """Reject a pair changing during our read; never open for writing."""
    paths = (run.state, run.wiring)
    for _ in range(3):
        before = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
        raw = [p.read_bytes() for p in paths]
        after = [(p.stat().st_size, p.stat().st_mtime_ns) for p in paths]
        if before == after:
            docs = tuple(json.loads(data) for data in raw)
            if not all(isinstance(doc, dict) for doc in docs):
                raise ValueError("state and wiring roots must be objects")
            return docs
    raise ValueError("state pair changed during read")


def runs_in(root: Path) -> tuple[list[Run], int]:
    """Inspect dated run folders and their state pairs, without recursive writes."""
    runs, skipped = [], 0
    for day in sorted(root.iterdir()):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name) or not day.is_dir():
            continue
        for folder in list(day.iterdir()):
            match = re.fullmatch(r"#(\d+)_(.+)_(\d{6})", folder.name)
            if match is None or not folder.is_dir():
                continue
            try:
                # Standard layout first, then alternate state subfolders.
                candidates = [folder / "quam_state" / "state.json", folder / "state.json"]
                candidates.extend(sorted(folder.glob("*/state.json")))
                state = next(p for p in candidates if p.is_file() and p.with_name("wiring.json").is_file())
                node = json.loads((folder / "node.json").read_bytes())
                instant = datetime.fromisoformat(node["created_at"].replace("Z", "+00:00"))
                if instant.tzinfo is None:
                    raise ValueError("archive check requires offset-aware created_at")
                rid = int(match[1])
                order = (instant.astimezone(timezone.utc), rid, folder.name)
                runs.append(Run(folder, state, state.with_name("wiring.json"), rid, match[2], order))
            except (OSError, ValueError, KeyError, TypeError, StopIteration):
                skipped += 1
    return sorted(runs, key=lambda run: run.order), skipped


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _marker(value):
    return isinstance(value, dict) and set(value) == {"_array", "_hash"}


def _row_value(row, old):
    num, txt = (row.old_num, row.old_txt) if old else (row.num, row.txt)
    return json.loads(txt) if txt is not None else num


def compare(prev: Run, next_run: Run) -> dict:
    state_a, wiring_a = read_pair(prev)
    state_b, wiring_b = read_pair(next_run)
    a, b = hub.flatten(hub.merged(state_a, wiring_a)), hub.flatten(hub.merged(state_b, wiring_b))
    rows = hub.diff(a, b)
    # Tuple inputs in Differ use a shallow merge. These stores use the actual
    # loader merge, without loading folders or writing any cache/index.
    legacy = Differ().diff(QuamStore.from_dicts(state_a, wiring_a),
                           QuamStore.from_dicts(state_b, wiring_b), ignore_keys=set())
    old = {r.dot_path: r for r in legacy}
    new = {r.path: r for r in rows}
    arrays = {p for flat in (a, b) for p, v in flat.items() if _marker(v)}
    reasons, matched, unexplained = Counter(), 0, []
    old_only, new_only = Counter(), Counter()
    for path in sorted(old.keys() | new.keys()):
        left, right = old.get(path), new.get(path)
        if left is not None and right is not None:
            op = {"added": "add", "removed": "gone", "modified": "set"}[left.change_type]
            if (op == right.op or (op == "set" and right.op == "retarget")) and (
                hub.same(left.old_value, _row_value(right, True))
                and hub.same(left.new_value, _row_value(right, False))
            ):
                matched += 1
                continue
        reason = None
        if any(path == p or path.startswith(p + ".") for p in arrays):
            # Compression covers old element rows and the new holder row,
            # including transitions across the 16/17 representation boundary.
            reason = "long_arrays"
        elif left is not None and right is None and left.change_type == "modified":
            x, y = left.old_value, left.new_value
            if _number(x) and _number(y) and type(x) is not type(y) and hub.same(x, y):
                reason = "int_float"
            elif isinstance(x, float) and isinstance(y, float) and math.isnan(x) and math.isnan(y):
                reason = "nan_constant"
        elif left is None and right is not None and right.op == "set":
            x, y = a.get(path), b.get(path)
            if isinstance(x, float) and isinstance(y, float) and not hub.same(x, y):
                # Confirm the legacy tolerance actually hides this exact change.
                from quam_state_manager.core.differ import _values_equal
                if _values_equal(x, y, 1e-12):
                    reason = "exact_float"
        if reason is None:
            unexplained.append(path)
        else:
            reasons[reason] += 1
            if left is not None and right is None:
                old_only[reason] += 1
            elif left is None and right is not None:
                new_only[reason] += 1

    # Independently pin every emitted row's values and op against the saved
    # flat maps. Long-array classification cannot hide an incorrect row.
    for row in rows:
        present_a, present_b = row.path in a, row.path in b
        if not (hub.same(_row_value(row, True), a.get(row.path))
                and hub.same(_row_value(row, False), b.get(row.path))):
            unexplained.append(row.path + " (payload)")
        expected_op = "add" if not present_a else "gone" if not present_b else (
            "retarget" if all(isinstance(v, str) and v.startswith(("#/", "#../", "#./"))
                              for v in (a[row.path], b[row.path])) else "set")
        if row.op != expected_op:
            unexplained.append(row.path + " (op)")
    expected_paths = {p for p in a.keys() | b.keys()
                      if p not in a or p not in b or not hub.same(a[p], b[p])}
    unexplained.extend(p + " (missing/extra row)" for p in expected_paths ^ new.keys())
    def waveform_or_spectator(path):
        return (path.endswith(".waveform_I") or ".waveform_I." in path
                or ".spectator_qubits." in path or ".spectator_qubits_control." in path)

    return {"old_rows": len(legacy), "hub_rows": len(rows), "matched": matched,
            "reasons": reasons, "old_only": old_only, "new_only": new_only,
            "unexplained": unexplained,
            "waveform_spectator_old_rows": sum(waveform_or_spectator(p) for p in old),
            "waveform_spectator_hub_rows": sum(waveform_or_spectator(p) for p in new)}


def check_archive(root: Path, pairs: int, seed: int, focus: tuple | None) -> bool:
    runs, scan_skipped = runs_in(root)
    indices = list(range(len(runs) - 1))
    random.Random(seed).shuffle(indices)
    totals, reasons, old_only, new_only = Counter(), Counter(), Counter(), Counter()
    unexplained = []
    for index in indices:
        if totals["pairs"] == pairs:
            break
        try:
            result = compare(runs[index], runs[index + 1])
        except (OSError, ValueError):
            totals["read_skipped"] += 1
            continue
        totals["pairs"] += 1
        for key in ("old_rows", "hub_rows", "matched"):
            totals[key] += result[key]
        for target, key in ((reasons, "reasons"), (old_only, "old_only"), (new_only, "new_only")):
            target.update(result[key])
        unexplained.extend(result["unexplained"])
    report = {"archive": root.name, "eligible_runs": len(runs), "scan_skipped": scan_skipped,
              "seed": seed, "read_skipped": totals["read_skipped"], **dict(totals),
              "differences_by_reason": {r: reasons[r] for r in REASONS},
              "old_only_by_reason": {r: old_only[r] for r in REASONS},
              "new_only_by_reason": {r: new_only[r] for r in REASONS},
              "unexplained": len(unexplained)}
    print(json.dumps(report, sort_keys=True), flush=True)
    if unexplained:
        print(json.dumps({"unexplained_examples": unexplained[:10]}), flush=True)
    ok = totals["pairs"] == pairs and not unexplained
    if focus:
        start, end, experiment = focus
        focus_count = 0
        selected = [r for r in runs if start <= r.run_id <= end and r.experiment == experiment]
        adjacent = {(prev.folder, nxt.folder) for prev, nxt in zip(runs, runs[1:])}
        for prev, nxt in zip(selected, selected[1:]):
            if prev.run_id < nxt.run_id:
                result = compare(prev, nxt)
                focus_count += 1
                print(json.dumps({"focus": [prev.run_id, nxt.run_id],
                                  "archive_adjacent": (prev.folder, nxt.folder) in adjacent,
                                  "old_rows": result["old_rows"],
                                  "hub_rows": result["hub_rows"],
                                  "waveform_spectator_old_rows": result["waveform_spectator_old_rows"],
                                  "waveform_spectator_hub_rows": result["waveform_spectator_hub_rows"],
                                  "unexplained": len(result["unexplained"])}), flush=True)
                ok = ok and not result["unexplained"]
        print(json.dumps({"focus_pairs": focus_count}), flush=True)
        ok = ok and focus_count > 0
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, action="append", required=True)
    parser.add_argument("--pairs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=269)
    parser.add_argument("--focus-archive", type=int, help="1-based index of --archive")
    parser.add_argument("--focus-start", type=int, default=898)
    parser.add_argument("--focus-end", type=int, default=906)
    parser.add_argument("--focus-experiment", default="38_two_qubit_xeb")
    args = parser.parse_args()
    logging.getLogger("quam_state_manager.core.loader").setLevel(logging.ERROR)
    ok = True
    for index, root in enumerate(args.archive, 1):
        focus = (args.focus_start, args.focus_end, args.focus_experiment) if index == args.focus_archive else None
        ok = check_archive(root, args.pairs, args.seed, focus) and ok
    nan_rows = hub.diff(hub.flatten({"value": float("nan")}), hub.flatten({"value": float("nan")}))
    print(json.dumps({"nan_constant_rows": len(nan_rows)}), flush=True)
    return 0 if ok and not nan_rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
