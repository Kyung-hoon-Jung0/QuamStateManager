"""Read-only S3 archive checks; all generated data stays in --scratch.

Explicit paths only. No server or live SQLite connection. --history-copy must
already be a filesystem copy, including any WAL, in the supplied scratch tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quam_state_manager.core import hub_build, hub_rules as rules
from quam_state_manager.core.hub_store import OPS, HubStore, value


def measured_build(root: Path, out: Path) -> dict:
    if (out / "ledger.sqlite").exists():
        raise ValueError("full-build output already exists; select a fresh scratch directory")
    env = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    log = out.parent / (out.name + "-build.log")
    started = time.perf_counter()
    with log.open("w", encoding="utf-8") as stream:
        proc = subprocess.Popen([sys.executable, "-m", "quam_state_manager.core.hub_build",
                                 str(root), "--out", str(out)], stdout=stream, stderr=subprocess.STDOUT,
                                env=env, cwd=Path(__file__).resolve().parents[1])
        monitored = psutil.Process(proc.pid)
        peak = 0
        next_notice = started + 45
        while proc.poll() is None:
            try:
                info = monitored.memory_info()
                peak = max(peak, info.rss, getattr(info, "peak_wset", 0))
            except psutil.NoSuchProcess:
                pass
            if time.perf_counter() > next_notice:
                print(json.dumps({"building": root.name, "elapsed_s": round(time.perf_counter() - started),
                                  "peak_rss_mb": round(peak / 2**20, 2)}), flush=True)
                next_notice += 45
            time.sleep(0.1)
    if proc.returncode:
        raise RuntimeError(log.read_text(encoding="utf-8"))
    result = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    result.update(archive=root.name, ledger_mb=round((out / "ledger.sqlite").stat().st_size / 2**20, 3),
                  peak_rss_mb=round(peak / 2**20, 2), process_seconds=round(time.perf_counter() - started, 3))
    print(json.dumps({"build": result}), flush=True)
    return result


def replay(root: Path, out: Path, samples=500, seed=270) -> dict:
    times, mismatches = [], []
    with HubStore(out) as store:
        rows = list(store.conn.execute("SELECT eid,rel_path FROM events WHERE error IS NULL ORDER BY ord"))
        locations = {r[0] for r in store.conn.execute("SELECT rel_path FROM events")}
        discovered, hint = hub_build.enumerate_runs(root)
        frozen = [r for r in discovered if r.folder.relative_to(root).as_posix() in locations]
        selected = {r["eid"] for r in random.Random(seed).sample(rows, min(samples, len(rows)))}
        checkpoints = {r[0] for r in store.conn.execute("SELECT eid FROM checkpoints")}
        selected |= checkpoints
        folders = {r["eid"]: root / r["rel_path"] for r in rows}
        for eid in sorted(selected):
            # An error checkpoint holds the unchanged ledger head, not its run's missing state.
            if eid not in folders:
                continue
            own = rules.flatten(hub_build.read_doc(folders[eid]))
            started = time.perf_counter()
            actual = store.state_at(eid)
            times.append((time.perf_counter() - started) * 1000)
            if not rules.same(rules.flatten(actual), own):
                mismatches.append({"eid": eid, "paths": [c.path for c in rules.diff(own, rules.flatten(actual))][:20]})
        # Idempotence measures a complete second scan without adding anything.
        counts = (len(rows), store.conn.execute("SELECT COUNT(*) FROM changes").fetchone()[0])
    # Idempotence is defined over the same input set. A live acquisition may
    # add folders during the 500-run replay; those are catch-up, not duplicates.
    with patch.object(hub_build, "enumerate_runs", return_value=(frozen, hint)):
        second = hub_build.build(root, out)
    assert second["added"] == 0 and second["change_rows"] == counts[1], second
    ordered = sorted(times)
    result = dict(archive=root.name, random_runs=min(samples, len(rows)), checkpoints=len(checkpoints),
                  unique_checked=len(times), mismatches=len(mismatches), examples=mismatches[:10],
                  p50_ms=round(ordered[len(ordered) // 2], 3),
                  p95_ms=round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * .95) - 1)], 3),
                  idempotent_added=second["added"], idempotent_seconds=second["seconds"])
    print(json.dumps({"replay": result}), flush=True)
    return result


def synthetic(source: Path, target: Path, count=10000) -> dict:
    """Concatenate retimed copies; hardlinks refer ONLY to scratch-owned copies.

    First copy every distinct raw payload into target/payloads, then share that
    immutable scratch file between synthetic runs. No customer inode is linked.
    """
    target.mkdir(parents=True, exist_ok=True)
    payloads = target / "payloads"
    payloads.mkdir(exist_ok=True)
    runs, _ = hub_build.enumerate_runs(source)
    started = time.perf_counter()
    for i in range(count):
        original = runs[i % len(runs)]
        cycle = i // len(runs)
        match = hub_build._RUN.fullmatch(original.folder.name)
        day = (datetime.fromisoformat(original.folder.parent.name) + timedelta(days=cycle * 90)).date().isoformat()
        folder = target / day / f"#{i + 1}_{match[2]}_{match[3]}"
        pair_dir = folder / "quam_state"
        pair_dir.mkdir(parents=True, exist_ok=True)
        node = json.loads((original.folder / "node.json").read_bytes())
        for container, key in [(node, "created_at"), (node.get("metadata", {}), "run_start"),
                               (node.get("metadata", {}), "run_end")]:
            if isinstance(container.get(key), str):
                try:
                    container[key] = (datetime.fromisoformat(container[key]) + timedelta(days=cycle * 90)).isoformat()
                except ValueError:
                    pass
        node["id"] = i + 1
        (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")
        for src in hub_build.state_paths(original.folder):
            if src.is_file():
                raw = src.read_bytes()
                # NTFS caps hardlinks to one file. Rotate scratch-owned copies
                # every 500 runs; never link or modify a customer inode.
                payload = payloads / f"{hashlib.sha1(raw).hexdigest()}-{i // 500}"
                if not payload.exists():
                    payload.write_bytes(raw)
                if not (pair_dir / src.name).exists():
                    os.link(payload, pair_dir / src.name)
    result = dict(runs=count, source_runs=len(runs), payload_files=len(list(payloads.iterdir())),
                  preparation_seconds=round(time.perf_counter() - started, 3))
    print(json.dumps({"synthetic_preparation": result}), flush=True)
    return result


def _normal(path) -> str:
    # The legacy index may contain doubled backslashes; Windows accepts both.
    return os.path.normcase(os.path.abspath(str(path)))


def coverage(history_copy: Path, ledgers: list[Path], scratch: Path) -> dict:
    from quam_state_manager.core import leaf_index

    if not history_copy.resolve().is_relative_to(scratch.resolve()):
        raise ValueError("history must be a scratch copy")
    history = sqlite3.connect(history_copy)
    history.row_factory = sqlite3.Row
    stores = [HubStore(out) for out in ledgers]
    locations = {}
    for store in stores:
        for loc in store.conn.execute("SELECT l.eid,r.path,l.rel_path FROM locations l JOIN roots r USING(root_id)"):
            locations[_normal(Path(loc["path"]) / loc["rel_path"])] = (store, loc["eid"])
    counts = Counter()
    missing = []
    excluded_snaps = []
    per_run = []
    try:
        for snap in history.execute("SELECT * FROM leaf_snaps ORDER BY id"):
            loc = locations.get(_normal(snap["folder"])) if snap["folder"] else None
            cps = list(history.execute("SELECT p.path,c.value,c.kind FROM leaf_cp c JOIN leaf_paths p ON p.id=c.path_id "
                                       "WHERE c.snap_id=?", (snap["id"],)))
            if loc is None:
                counts["outside_archives"] += len(cps)
                excluded_snaps.append(dict(snap))
                continue
            store, eid = loc
            doc = store.state_at(eid)
            flat = rules.flatten(doc)
            rows = {r["path"]: r for r in store.conn.execute("SELECT p.path,c.* FROM changes c JOIN paths p USING(pid) WHERE eid=?", (eid,))}
            earlier = store.conn.execute("SELECT eid FROM events WHERE ord<? AND error IS NULL ORDER BY ord DESC LIMIT 1",
                                         (store.event(eid)["ord"],)).fetchone()
            before = rules.flatten(store.state_at(earlier[0])) if earlier else {}
            # Reconstruct what the legacy walker sees at this run, for evidence
            # on raw-pointer and array explanations (no live-history reads).
            numbers, _, _ = leaf_index.numeric_leaves(doc, {})
            snapshot_dir = history_copy.parent / snap["ts"]
            snapshot_numbers = {}
            post_patch_evidence = []
            if (snapshot_dir / "state.json").is_file():
                snapshot_doc = rules.merged(json.loads((snapshot_dir / "state.json").read_bytes()),
                                           json.loads((snapshot_dir / "wiring.json").read_bytes()))
                snapshot_flat = rules.flatten(snapshot_doc)
                snapshot_numbers, _, _ = leaf_index.numeric_leaves(snapshot_doc, {})
                differences = rules.diff(snapshot_flat, flat)
                node = json.loads((Path(snap["folder"]) / "node.json").read_bytes())
                new_proven = hub_build._proven(node, differences, flat)
                patches = node.get("patches", [])
                old_node = {"patches": [dict(p, value=p["old"]) for p in patches
                                        if isinstance(p, dict) and "old" in p]}
                old_proven = hub_build._proven(old_node, differences, snapshot_flat)
                if differences and all(c.path in new_proven & old_proven for c in differences):
                    post_patch_evidence = [dict(path=c.path, old=value(c.old_num, c.old_txt),
                                                saved=value(c.num, c.txt)) for c in differences]
            run_counts = Counter()
            arrays = {p for p, v in flat.items() if isinstance(v, dict)}
            for cp in cps:
                path, expected, kind = cp["path"], cp["value"], cp["kind"]
                row = rows.get(path)
                reason = None
                if kind == leaf_index.KIND_NUM and row is not None and row["op"] != OPS["gone"] and rules.same(value(row["num"], row["txt"]), expected):
                    reason = "covered"
                elif kind == leaf_index.KIND_GONE and row is not None and row["op"] == OPS["gone"]:
                    reason = "covered"
                elif kind in (leaf_index.KIND_PTR, leaf_index.KIND_PTR_NUM):
                    if isinstance(flat.get(path), str) and flat[path].startswith(("#/", "#../", "#./")):
                        observed = numbers.get(path)
                        if rules.same(observed, expected):
                            reason = "raw_pointer_holders"
                elif any(path.startswith(p + ".") for p in arrays):
                    if rules.same(numbers.get(path), expected):
                        reason = "long_arrays"
                if reason is None and kind == leaf_index.KIND_NUM:
                    own = flat.get(path)
                    if expected is None and isinstance(own, float) and math.isnan(own):
                        reason = "legacy_nan"
                    elif path in flat and rules.same(own, expected) and path in before and rules.same(before[path], own):
                        # Sparse history's first baseline / catch-up rows are
                        # facts about its prior snapshot, not the prior run.
                        reason = "run_by_run_unchanged"
                if reason is None and kind == leaf_index.KIND_GONE and path not in before and path not in flat:
                    reason = "run_by_run_already_gone"
                if (reason is None and post_patch_evidence and kind in (leaf_index.KIND_NUM, leaf_index.KIND_PTR_NUM)
                        and rules.same(snapshot_numbers.get(path), expected)
                        and ((kind == leaf_index.KIND_NUM and path in new_proven & old_proven and row is not None
                              and rules.same(value(row["num"], row["txt"]), flat.get(path)))
                             or (kind == leaf_index.KIND_PTR_NUM and rules.same(flat.get(path), snapshot_flat.get(path))
                                 and not rules.same(numbers.get(path), expected)))):
                    # Binding rule: the saved run is authoritative. The legacy
                    # copy contains exactly the patch.old values, proven on
                    # both sides, whereas the archive contains patch.value.
                    reason = "saved_post_patch_state"
                if reason is None:
                    reason = "missing"
                    missing.append(dict(eid=eid, run_id=snap["run_id"], path=path, expected=expected,
                                        kind=kind, own=flat.get(path), before=before.get(path)))
                counts[reason] += 1
                run_counts[reason] += 1
            per_run.append(dict(run_id=snap["run_id"], eid=eid, counts=dict(run_counts),
                                post_patch_evidence=post_patch_evidence))
    finally:
        history.close()
        for store in stores:
            store.close()
    result = dict(total=sum(counts.values()), applicable=sum(counts.values()) - counts["outside_archives"],
                  covered=counts["covered"], explained_by_rule=sum(v for k, v in counts.items()
                    if k not in ("covered", "missing", "outside_archives")), missing=counts["missing"],
                  reasons=dict(counts), per_run=per_run, missing_examples=missing[:30],
                  outside_snapshots=excluded_snaps)
    print(json.dumps({"coverage": result}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, action="append", required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--history-copy", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--resume-checks", action="store_true")
    args = parser.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    report = json.loads(args.report.read_text(encoding="utf-8")) if args.resume_checks else dict(builds=[], replays=[])
    ledgers = []
    for index, root in enumerate(args.archive):
        out = args.scratch / f"archive-{index + 1}"
        if not any(r["archive"] == root.name for r in report["builds"]):
            report["builds"].append(measured_build(root, out))
            args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not any(r["archive"] == root.name for r in report["replays"]):
            report["replays"].append(replay(root, out))
        ledgers.append(out)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.history_copy:
        report["coverage"] = coverage(args.history_copy, ledgers, args.scratch)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    synthetic_root = args.scratch / "synthetic-10000"
    if "synthetic" not in report:
        report["synthetic"] = synthetic(args.archive[0], synthetic_root)
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    synthetic_out = args.scratch / "synthetic-ledger"
    if not any(r["archive"] == synthetic_root.name for r in report["builds"]):
        report["builds"].append(measured_build(synthetic_root, synthetic_out))
        args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if not any(r["archive"] == synthetic_root.name for r in report["replays"]):
        report["replays"].append(replay(synthetic_root, synthetic_out))
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert all(r["mismatches"] == 0 for r in report["replays"]), report
    assert report.get("coverage", {}).get("missing", 0) == 0, report


if __name__ == "__main__":
    main()
