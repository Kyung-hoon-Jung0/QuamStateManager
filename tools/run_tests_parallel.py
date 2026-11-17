"""Run the pytest suite as N parallel pytest processes (no pytest-xdist needed).

Test files are split into chunks; N workers pull chunks from one queue, the
slowest chunks first, so one long file never decides the wall clock alone.
Each worker has its own --basetemp (pytest's numbered temp folders are not
safe to share between processes) and writes a junit xml. Per-file times from
the last run are kept in a JSON file outside the repository and used to order
and size the chunks of the next run.

Usage (from the repository root):
    python tools/run_tests_parallel.py [-n 8] [--out DIR] [--chunk-seconds 60]
        [--timings FILE] [-- extra pytest args]

Exit code 0 only when every chunk exited 0 (or 5, "no tests collected") and
no chunk timed out. The summary line counts passed / failed / errors / skipped
from the junit files, never from pytest's text output.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _default_timings() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "qsm_test_timings.json"


def _test_files() -> list[Path]:
    # every folder below tests/, as pytest collects them (testpaths = ["tests"])
    return sorted(f for f in (REPO / "tests").rglob("test_*.py") if "__pycache__" not in f.parts)


def _key(f: Path) -> str:
    return f.relative_to(REPO).as_posix()


def _estimate(files: list[Path], timings: dict) -> dict:
    """Seconds per file: last measured time, else a size-based guess."""
    est = {}
    for f in files:
        key = _key(f)
        if key in timings:
            est[key] = float(timings[key])
        else:
            est[key] = 1.0 + f.stat().st_size / 4000.0
    return est


def _chunks(files: list[Path], est: dict, chunk_seconds: float) -> list[list[Path]]:
    """Greedy: the slowest files first; a file above the budget is its own
    chunk; small files are packed until the budget is reached."""
    order = sorted(files, key=lambda f: -est[_key(f)])
    out: list[list[Path]] = []
    cur: list[Path] = []
    cur_t = 0.0
    for f in order:
        t = est[_key(f)]
        if t >= chunk_seconds:
            out.append([f])
            continue
        if cur and cur_t + t > chunk_seconds:
            out.append(cur)
            cur, cur_t = [], 0.0
        cur.append(f)
        cur_t += t
    if cur:
        out.append(cur)
    out.sort(key=lambda c: -sum(est[_key(f)] for f in c))
    return out


def _parse_junit(path: Path) -> tuple[dict, dict]:
    counts = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    per_file: dict = {}
    failed_ids: list = []
    try:
        root = ET.parse(path).getroot()
    except Exception:
        return counts, {"_failed_ids": failed_ids, "_times": per_file}
    for tc in root.iter("testcase"):
        cls = tc.get("classname", "")
        mod = cls.split(".")
        fname = None
        for i, part in enumerate(mod):
            if part.startswith("test_"):
                fname = "/".join(mod[:i + 1]) + ".py"   # tests/sub/test_x.py
                break
        t = float(tc.get("time") or 0)
        if fname:
            per_file[fname] = per_file.get(fname, 0.0) + t
        tags = {child.tag for child in tc}
        if "failure" in tags:
            counts["failed"] += 1
            failed_ids.append(f"{cls}::{tc.get('name')}")
        elif "error" in tags:
            counts["errors"] += 1
            failed_ids.append(f"{cls}::{tc.get('name')} (error)")
        elif "skipped" in tags:
            counts["skipped"] += 1
        else:
            counts["passed"] += 1
    return counts, {"_failed_ids": failed_ids, "_times": per_file}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-n", type=int, default=int(os.environ.get("FANOUT_MAX", "8")))
    ap.add_argument("--out", default=None, help="folder for junit files and logs (default: system temp)")
    ap.add_argument("--chunk-seconds", type=float, default=60.0)
    ap.add_argument("--timings", default=None)
    ap.add_argument("--chunk-timeout", type=float, default=3600.0)
    ap.add_argument("extra", nargs="*", help="extra pytest args (after --)")
    a = ap.parse_args(argv)

    out = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="qsm_par_"))
    if REPO in out.resolve().parents or out.resolve() == REPO:
        print("refusing an output folder inside the repository", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    tpath = Path(a.timings) if a.timings else _default_timings()
    try:
        timings = json.loads(tpath.read_text(encoding="utf-8"))
    except Exception:
        timings = {}

    files = _test_files()
    est = _estimate(files, timings)
    chunks = _chunks(files, est, a.chunk_seconds)
    print(f"{len(files)} files in {len(chunks)} chunks on {a.n} workers; out={out}", flush=True)

    q: queue.Queue = queue.Queue()
    for i, c in enumerate(chunks):
        q.put((i, c))
    results: dict = {}
    lock = threading.Lock()
    t0 = time.time()

    def worker(w: int) -> None:
        base = out / f"tmp_w{w}"
        while True:
            try:
                i, c = q.get_nowait()
            except queue.Empty:
                return
            junit = out / f"chunk_{i:04d}.xml"
            log = out / f"chunk_{i:04d}.log"
            cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                   f"--basetemp={base}", f"--junitxml={junit}", *a.extra,
                   *[str(f.relative_to(REPO)) for f in c]]
            st = time.time()
            with open(log, "w", encoding="utf-8", errors="replace") as fh:
                try:
                    rc = subprocess.run(cmd, cwd=REPO, stdout=fh, stderr=subprocess.STDOUT,
                                        timeout=a.chunk_timeout).returncode
                except subprocess.TimeoutExpired:
                    rc = "timeout"
            with lock:
                results[i] = (rc, time.time() - st, c)
                done = len(results)
                if rc not in (0, 5):
                    print(f"  chunk {i} rc={rc} ({len(c)} files) log={log}", flush=True)
                if done % 20 == 0 or done == len(chunks):
                    print(f"  {done}/{len(chunks)} chunks, {time.time() - t0:.0f}s", flush=True)

    threads = [threading.Thread(target=worker, args=(w,), daemon=True) for w in range(a.n)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    total = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0}
    failed_ids: list = []
    new_times: dict = {}
    bad_chunks = []
    for i, (rc, secs, c) in sorted(results.items()):
        counts, extra = _parse_junit(out / f"chunk_{i:04d}.xml")
        for k in total:
            total[k] += counts[k]
        failed_ids += extra["_failed_ids"]
        new_times.update(extra["_times"])
        if rc not in (0, 5):
            bad_chunks.append((i, rc))
    for f in files:   # files with no junit time (collection error, timeout) keep the old estimate
        new_times.setdefault(_key(f), est[_key(f)])
    try:
        tpath.write_text(json.dumps(new_times, indent=0, sort_keys=True), encoding="utf-8")
    except Exception as e:
        print(f"could not save timings: {e}", file=sys.stderr)

    wall = time.time() - t0
    for fid in failed_ids:
        print(f"FAILED {fid}")
    print(f"{total['passed']} passed, {total['failed']} failed, {total['errors']} errors, "
          f"{total['skipped']} skipped in {wall:.0f}s ({wall / 60:.1f} min), "
          f"{len(bad_chunks)} chunk(s) not clean")
    ok = not bad_chunks and total["failed"] == 0 and total["errors"] == 0
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
