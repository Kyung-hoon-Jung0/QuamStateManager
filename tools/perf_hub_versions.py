"""S9 server time (docs/284): the Versions panel, State History, Diff vs now,
Compare (3 versions) and Stage, before (the Param History snapshot path) vs
after (the change ledger), cold and warm, through the Flask test client
(server time, no network). One process, the same chip for both sides.

"Before" is the old code path exactly: ``routes._versions_read`` is stubbed
to answer "no ledger" (no notes) at zero cost, so the panel and State History
run their unchanged old bodies, and Diff / Compare / Stage are given Param
History snapshot ids (the old ids). "After" is the ledger, given ledger
version ids of the SAME runs. "Cold" is each side's first request after every
RAM cache of both paths was dropped (the persisted snapshot-hash cache stays,
as it does across a restart); the first open with no hash cache at all is
measured separately. "Warm" is the median of the passes that follow, the two
sides alternating pass by pass.

    python tools/perf_hub_versions.py --runs 10000 --qubits 32 --scratch <dir> --report <json>
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_hub_drawer as base  # noqa: E402
from quam_state_manager.core import hub_index, hub_versions, run_time  # noqa: E402

HX = {"HX-Request": "true"}


def _drop_caches(sm, hm):
    hub_index.close_readers()
    with hub_versions._CACHE_LOCK:
        hub_versions._DOCS.clear()
        hub_versions._PAIRS.clear()
        hub_versions._RUN_HASHES.clear()
    with hub_versions._HASHERS_LOCK:
        hub_versions._HASHERS.clear()
    sm.routes._VERSION_QUICK.clear()
    sm.routes._DIFF_MEMO.clear() if hasattr(sm.routes, "_DIFF_MEMO") else None
    with hm._lock:
        hm._snapshot_list_cache.clear()
        hm._store_cache.clear()


def _req(sm, method, url):
    t0 = time.perf_counter()
    r = sm.client.open(url, method=method, headers={**HX, "X-SM-Actor": "operator"})
    ms = (time.perf_counter() - t0) * 1000.0
    assert r.status_code in (200, 302), (url, r.status_code, r.get_data(as_text=True)[:300])
    body = r.get_data(as_text=True)
    return ms, len(r.data), body


def measure(sm, ids: dict, passes: int) -> dict:
    routes = sm.routes
    with sm.app.test_request_context():
        hm = routes._history()
    real = routes._versions_read
    stub = lambda ctx, snaps, **kw: {"mode": "fallback", "reason": "no_ledger", "rows": [],  # noqa: E731
                                     "notes": [], "chip_key": "", "total": 0}

    def side(name):
        routes._versions_read = stub if name == "before" else real

    def surfaces(name):
        i = ids[name]
        key = ids["chip_key"]
        return {
            "versions_panel": ("GET", "/state/versions"),
            "state_history": ("GET", "/state-history"),
            "diff_vs_now": ("GET", f"/state/versions/{i['diff']}/diff"),
            "compare_3": ("GET", "/diff/versions?" + "&".join(f"ts={t}" for t in i["compare"])
                          + f"&chip_key={key}"),
            "stage": ("POST", f"/state-history/{i['stage']}/stage?force=1&chip_key={key}"),
        }
    out = {"before": {}, "after": {}}
    try:
        for name in surfaces("before"):
            for s in ("before", "after"):
                side(s)
                _drop_caches(sm, hm)
                method, url = surfaces(s)[name]
                ms, size, body = _req(sm, method, url)
                out[s][name] = {"cold_ms": round(ms, 1), "bytes": size,
                                "ledger_rows": 'data-source="ledger"' in body or "sh-event" in body}
            for s in ("before", "after"):
                side(s)
                for _ in range(2):
                    _req(sm, *surfaces(s)[name])
            warm = {"before": [], "after": []}
            for k in range(passes):
                for s in (("before", "after") if k % 2 == 0 else ("after", "before")):
                    side(s)
                    warm[s].append(_req(sm, *surfaces(s)[name])[0])
            for s in ("before", "after"):
                w = sorted(warm[s])
                out[s][name].update(warm_ms=round(statistics.median(w), 1),
                                    warm_p90_ms=round(w[int(0.9 * (len(w) - 1))], 1))
            print(name, {s: out[s][name] for s in out}, flush=True)
        return out
    finally:
        routes._versions_read = real


def first_open(sm) -> dict:
    """The first open with no snapshot-hash cache at all (a new install): the
    panel answers at once with the older snapshots marked as still being
    matched; the matching runs on a worker. Time until nothing is pending."""
    routes = sm.routes
    with sm.app.test_request_context():
        hm = routes._history()
        chip_dir = Path(routes._hub_chip_dir(routes._active_ctx()["path"]))
    _drop_caches(sm, hm)
    (chip_dir / hub_versions.HASHES_FILE).unlink(missing_ok=True)
    t0 = time.perf_counter()
    ms, _size, body = _req(sm, "GET", "/state/versions")
    first = {"first_request_ms": round(ms, 1), "says_matching": "still being matched" in body}
    hub_versions.hashes_for(chip_dir).wait(3600)
    first["matching_done_s"] = round(time.perf_counter() - t0, 1)
    ms, _size, body = _req(sm, "GET", "/state/versions")
    first["next_request_ms"] = round(ms, 1)
    first["next_says_matching"] = "still being matched" in body
    return first


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=10000)
    ap.add_argument("--qubits", type=int, default=32)
    ap.add_argument("--passes", type=int, default=7)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    scratch = args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    home = scratch / "home"
    home.mkdir(exist_ok=True)
    os.environ.update(QUALIBRATE_CONFIG_FILE=str(home / "absent.toml"), HOME=str(home),
                      USERPROFILE=str(home), SM_DISABLE_ENV_WARMUP="1")
    work = scratch / "perf_s9"
    base.remove_scratch(work, scratch)
    data, chip, inst = work / "data", work / "chip", work / "inst"
    doc = {"qubits": {f"q{i}": {"id": f"q{i}", "T1": 1e-5, "T2ramsey": 1.5e-5, "T2echo": 2e-5,
                                "f_01": 5e9 + i * 1e7, "n_avg": 1000} for i in range(args.qubits)},
           "qubit_pairs": {}, "extras": {"chip_name": "device"}}
    wiring = json.dumps({"network": {"host": "127.0.0.1", "port": 1, "cluster_name": "cluster"}})
    names = list(doc["qubits"])
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t0 = time.perf_counter()
    instants = {}
    for rid in range(1, args.runs + 1):
        q = names[rid % len(names)]
        doc["qubits"][q]["T1"] = 1e-5 + rid * 1e-9          # one value moves per run
        instant = start + timedelta(minutes=3 * rid)
        instants[rid] = instant
        folder = data / instant.date().isoformat() / f"#{rid}_scan_{instant:%H%M%S}"
        (folder / "quam_state").mkdir(parents=True)
        (folder / "node.json").write_text(json.dumps({
            "id": rid, "created_at": instant.isoformat(),
            "metadata": {"name": "scan", "status": "finished"}}), encoding="utf-8")
        (folder / "quam_state" / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text(wiring, encoding="utf-8")
    report = {"runs": args.runs, "qubits": len(names), "generation_s": round(time.perf_counter() - t0, 1)}
    chip.mkdir(parents=True)
    doc.setdefault("extras", {})["data_folder"] = str(data)
    (chip / "state.json").write_text(json.dumps(doc), encoding="utf-8")
    (chip / "wiring.json").write_text(wiring, encoding="utf-8")
    report["state_kb"] = round((chip / "state.json").stat().st_size / 1e3, 1)
    try:
        with base.sm_app(inst, chip, data, backfill=True) as sm:
            report.update(load_and_ledger_s=round(sm.load_s, 1),
                          param_history_backfill_s=round(sm.backfill_s, 1))
            r = sm.routes
            with sm.app.test_request_context():
                ctx = r._active_ctx()
                hm = r._history()
                chip_dir = Path(r._hub_chip_dir(ctx["path"]))
                snaps = hm.list_snapshots(ctx["path"])
            report["param_history_snapshots"] = len(snaps)
            with hub_versions._ledger(chip_dir) as store:
                events = {row["run_id"]: dict(row) for row in store.conn.execute(
                    "SELECT * FROM events WHERE kind='run'")}
            report["ledger_events"] = len(events)
            keys = {m.timestamp for m in snaps}
            picks = [args.runs - 5, args.runs - 50, args.runs - 500]
            old = [run_time.snapshot_key(events[p]["t_utc_us"], p) for p in picks]
            assert all(k in keys for k in old), "the old ids must be Param History snapshots"
            new = [hub_versions.ref_of(events[p]) for p in picks]
            ids = {"chip_key": chip_dir.name,
                   "before": {"diff": old[0], "compare": old, "stage": old[1]},
                   "after": {"diff": new[0], "compare": new, "stage": new[1]}}
            # the hash cache filled once, as on any chip opened before
            hub_versions.read(chip_dir, snaps, limit=1)
            hub_versions.hashes_for(chip_dir).wait(3600)
            report.update(measure(sm, ids, args.passes))
            report["after_first_open_no_hash_cache"] = first_open(sm)
    finally:
        hub_index.close_readers()
        if not args.keep:
            report["cleanup_done"] = base.remove_scratch(work, scratch, strict=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1), flush=True)


if __name__ == "__main__":
    main()
