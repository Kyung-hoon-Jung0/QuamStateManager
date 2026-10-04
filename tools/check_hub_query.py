"""Measure the read side on disposable synthetic and copied archives."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
import shutil
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quam_state_manager.core import hub_build, hub_index, hub_query, hub_rules
from quam_state_manager.core.hub_store import HubStore, value


def remove_scratch(target, scratch):
    target, scratch = target.resolve(), scratch.resolve()
    if scratch not in target.parents:
        raise ValueError("cleanup target must be inside the scratch directory")
    if target.exists():
        shutil.rmtree(target)


def synthetic(root, runs):
    doc = {"qubits": {f"qA{i}": {f"field{j}": 0 for j in range(8)} for i in range(128)}}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for rid in range(runs):
        instant = start + timedelta(minutes=3 * rid)
        entity = f"qA{rid % 128}"
        for j in range(3):
            doc["qubits"][entity][f"field{(rid + j) % 8}"] = rid + j
        experiment = ("scan", "rabi", "spectroscopy", "sweep")[rid % 4]
        folder = root / instant.date().isoformat() / f"#{rid}_{experiment}_{instant:%H%M%S}"
        (folder / "quam_state").mkdir(parents=True)
        node = {"created_at": instant.isoformat(), "metadata": {"name": experiment, "status": "finished"},
                "data": {"parameters": {"model": {"qubits": [entity],
                         "qubit_pairs": [f"{entity}-qA{(rid + 1) % 128}"], "cz_macro_name": "cz_SNZ"}}}}
        (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")
        (folder / "quam_state" / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")


def copy_archive(source, destination, count):
    runs, _ = hub_build.enumerate_runs(source)
    # Copy only run metadata and saved states, excluding large measurement files.
    chosen = runs[:count]
    for run in chosen:
        folder = run.folder
        target = destination / folder.relative_to(source)
        target.mkdir(parents=True)
        if (folder / "node.json").is_file():
            shutil.copy2(folder / "node.json", target / "node.json")
        state, wiring = hub_build.state_paths(folder)
        for path in (state, wiring):
            if path.is_file():
                copied = target / path.relative_to(folder)
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, copied)
    return len(chosen)


def p95(values):
    return sorted(values)[math.ceil(.95 * len(values)) - 1]


def measure(directory, zone, iterations):
    with HubStore(directory) as store:
        bound = hub_query.context(store, zone=zone)
        hub_index.close_readers(directory)
        begin = time.perf_counter()
        with hub_index.snapshot(bound) as (_, index):
            elapsed = time.perf_counter() - begin
            memory = hub_index.footprint(index)
            paths = list(index.paths)
            days = list(index.postings["day"])
            names = list(index.postings["experiment"])
            entities = list(index.postings["entity"])
        randomizer = random.Random(279)
        search_samples, day_samples, series_samples, series_lengths = [], [], [], []
        for i in range(iterations):
            text = (names[i % len(names)] if names else "scan")
            if entities and i % 2:
                text += " " + randomizer.choice(entities)
            begin = time.perf_counter()
            hub_query.search(bound, text)
            search_samples.append((time.perf_counter() - begin) * 1000)
            day = days[i % len(days)]
            begin = time.perf_counter()
            hub_query.timeline(bound, day_from=day, day_to=day)
            day_samples.append((time.perf_counter() - begin) * 1000)
            path = randomizer.choice(paths)
            begin = time.perf_counter()
            rows = hub_query.series(bound, path)
            series_samples.append((time.perf_counter() - begin) * 1000)
            series_lengths.append(len(rows))
        result = {"events": store.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0],
                  "changes": store.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0],
                  "paths": len(paths), "index_build_s": round(elapsed, 4),
                  "index_mib": round(memory / 2**20, 3), "iterations": iterations,
                  "search_p95_ms": round(p95(search_samples), 3),
                  "timeline_day_p95_ms": round(p95(day_samples), 3),
                  "series_p95_ms": round(p95(series_samples), 3),
                  "series_rows_p95": p95(series_lengths)}
    hub_index.close_readers(directory)
    return result


def equivalence(directory):
    with HubStore(directory) as store:
        paths = [row[0] for row in store.conn.execute("SELECT path FROM paths ORDER BY path")]
        selected = random.Random(279).sample(paths, 200)
        expected = {path: [] for path in selected}
        previous = {}
        successful = 0
        for event in store.conn.execute("SELECT * FROM events ORDER BY ord"):
            if event["error"]:
                continue
            successful += 1
            flat = hub_rules.flatten(store.state_at(event["eid"]))
            # Recompute from consecutive replayed states, never from changes.
            for change in hub_rules.diff(previous, flat):
                if change.path in expected:
                    expected[change.path].append((event["eid"], value(change.old_num, change.old_txt),
                                                  value(change.num, change.txt), change.op))
            previous = flat
        mismatch = 0
        checked_rows = 0
        for path in selected:
            actual = [(event["eid"], old, new, op)
                      for event, old, new, op, proven in hub_query.series(store, path)]
            wanted = expected[path]
            checked_rows += len(wanted)
            if len(actual) != len(wanted) or any(
                a[0] != b[0] or a[3] != b[3] or not hub_rules.same(a[1], b[1])
                or not hub_rules.same(a[2], b[2]) for a, b in zip(actual, wanted)):
                mismatch += 1
    hub_index.close_readers(directory)
    return {"paths": len(selected), "events_replayed": successful, "rows_compared": checked_rows,
            "mismatches": mismatch, "seed": 279}


def cli_build(root, out, scratch):
    begin = time.perf_counter()
    with (scratch / "builder.log").open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-m", "quam_state_manager.core.hub_build", str(root),
                        "--out", str(out)], stdout=log, stderr=log, check=True)
    return round(time.perf_counter() - begin, 3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=10000)
    parser.add_argument("--copy-runs", type=int, default=500)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--zone", default="UTC")
    args = parser.parse_args()
    if not 1 <= args.copy_runs <= 500:
        parser.error("copy-runs must be between 1 and 500")
    scratch = args.scratch.resolve()
    workspace = Path(__file__).resolve().parents[1]
    if workspace not in scratch.parents:
        parser.error("scratch must be inside this worktree")
    scratch.mkdir(parents=True, exist_ok=True)
    targets = [scratch / name for name in ("synthetic", "synthetic-ledger", "copy", "copy-ledger")]
    if any(target.exists() for target in targets):
        parser.error("benchmark directories must be fresh")
    result = {}
    try:
        print(f"Preparing {args.runs} synthetic runs", flush=True)
        synthetic(targets[0], args.runs)
        print("Building synthetic ledger with the offline CLI", flush=True)
        synthetic_build = cli_build(targets[0], targets[1], scratch)
        result["synthetic"] = measure(targets[1], args.zone, args.iterations)
        result["synthetic"]["ledger_build_s"] = synthetic_build
        print(json.dumps({"synthetic": result["synthetic"]}), flush=True)
        copied = copy_archive(args.archive.resolve(), targets[2], args.copy_runs)
        print("Building copied archive ledger with the offline CLI", flush=True)
        real_build = cli_build(targets[2], targets[3], scratch)
        result["copy"] = measure(targets[3], args.zone, args.iterations)
        result["copy"].update({"folders_copied": copied, "ledger_build_s": real_build})
        print(json.dumps({"copy": result["copy"]}), flush=True)
        result["equivalence"] = equivalence(targets[3])
        print(json.dumps({"equivalence": result["equivalence"]}), flush=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if result["equivalence"]["mismatches"]:
            raise AssertionError("series equivalence check failed")
    finally:
        hub_index.close_readers()
        for target in targets:
            remove_scratch(target, scratch)
    print("Disposable archives and ledgers deleted", flush=True)


if __name__ == "__main__":
    main()
