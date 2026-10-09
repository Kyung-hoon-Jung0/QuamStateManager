"""S8 server time (docs/283): Chip Status Trends, the metric meta, the Param
History grid and Changes from the change ledger, cold and warm, through the
Flask test client (server time, no network).

"Cold" is the first request after every RAM cache and read index was dropped
(a ledger answer that says "preparing" is asked again, and the waiting
counts); "warm" is the median of the passes that follow.

S10 C5: the "before" side (the old snapshot paths, reached by stubbing the
mode to ``fallback``) -> gone with those paths; the report keeps its "after"
key so earlier reports still compare.

    python tools/perf_hub_chip_status.py --chip <chip dir> --runs 20 --scratch <dir> --report <json>
    python tools/perf_hub_chip_status.py --runs 10000 --qubits 32 --scratch <dir> --report <json>
"""

from __future__ import annotations

# S10 C7: old -> new, remove callerless snapshot hooks and retain ledger behavior.

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_hub_drawer as base  # noqa: E402
from quam_state_manager.core import hub_index  # noqa: E402
from quam_state_manager.web import hub_status  # noqa: E402

SURFACES = {
    "chip_status_page": ("/topology?view=trends",),
    "chip_status_trends": ("/topology/trends",),
    "metric_meta": ("/topology/metric-meta",),
    "param_history_grid": ("/param-history?since=all",),
    "param_history_changes": ("/param-history/changes",),
}
_WAITING = ("being built", "Preparing the change history")


def _get(sm, url):
    """One answer, asking again while the ledger says it is preparing."""
    asks = 0
    while True:
        r = sm.client.get(url)
        assert r.status_code == 200, (url, r.status_code)
        body = r.data.decode("utf-8", "replace")
        if not any(w in body for w in _WAITING) or asks >= 200:
            return len(r.data), asks
        asks += 1
        time.sleep(0.05)


def _drop_caches(sm):
    # S10 C7: old -> new, only the ledger reader pool needs cleanup.
    hub_index.close_readers()
    hub_status._CACHE.clear()


def _once(sm, urls) -> tuple[float, int, int]:
    t0 = time.perf_counter()
    size = waits = 0
    for url in urls:
        n, asks = _get(sm, url)
        size += n
        waits += asks
    return (time.perf_counter() - t0) * 1000.0, size, waits


def measure(sm, passes: int) -> dict:
    """``{"after": {surface: ...}}`` (the ledger). Cold: the first request
    after every cache was dropped. Warm: two warm-up requests, then the median
    and p90 of *passes* requests."""
    out = {"after": {}}
    for name, urls in SURFACES.items():
        _drop_caches(sm)
        ms, size, waits = _once(sm, urls)
        out["after"][name] = {"cold_ms": round(ms, 1), "bytes": size, "preparing_reasks": waits}
        for _ in range(2):
            _once(sm, urls)
        w = sorted(_once(sm, urls)[0] for _ in range(passes))
        out["after"][name].update(warm_ms=round(statistics.median(w), 1),
                                  warm_p90_ms=round(w[int(0.9 * (len(w) - 1))], 1))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--chip", type=Path, help="a chip folder to copy (its state is the runs' base)")
    ap.add_argument("--runs", type=int, default=10000)
    ap.add_argument("--qubits", type=int, default=32)
    ap.add_argument("--passes", type=int, default=7)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    scratch = args.scratch.resolve()
    work = scratch / "perf_s8"
    base.remove_scratch(work, scratch)
    data, chip, inst = work / "data", work / "chip", work / "inst"
    if args.chip:
        doc = json.loads((args.chip / "state.json").read_text(encoding="utf-8"))
        wiring = (args.chip / "wiring.json").read_text(encoding="utf-8")
    else:
        doc = {"qubits": {f"q{i}": {"id": f"q{i}", "T1": 1e-5, "T2ramsey": 1.5e-5, "T2echo": 2e-5,
                                    "f_01": 5e9 + i * 1e7} for i in range(args.qubits)},
               "qubit_pairs": {}, "extras": {"chip_name": "device"}}
        wiring = json.dumps({"network": {"host": "127.0.0.1", "port": 1, "cluster_name": "cluster"}})
    names = list(doc["qubits"])
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t0 = time.perf_counter()
    for rid in range(1, args.runs + 1):
        q = names[rid % len(names)]
        doc["qubits"][q]["T1"] = 1e-5 + rid * 1e-9          # one value moves per run
        instant = start + timedelta(minutes=3 * rid)
        folder = data / instant.date().isoformat() / f"#{rid}_scan_{instant:%H%M%S}"
        (folder / "quam_state").mkdir(parents=True)
        (folder / "node.json").write_text(json.dumps({
            "id": rid, "created_at": instant.isoformat(),
            "metadata": {"name": "scan", "status": "finished"}}), encoding="utf-8")
        (folder / "quam_state" / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text(wiring, encoding="utf-8")
    report = {"runs": args.runs, "qubits": len(names),
              "generation_s": round(time.perf_counter() - t0, 1)}
    chip.mkdir(parents=True)
    doc.setdefault("extras", {})["data_folder"] = str(data)
    (chip / "state.json").write_text(json.dumps(doc), encoding="utf-8")
    (chip / "wiring.json").write_text(wiring, encoding="utf-8")
    report["state_mb"] = round((chip / "state.json").stat().st_size / 1e6, 2)
    try:
        with base.sm_app(inst, chip, data, backfill=True) as sm:
            report.update(load_and_ledger_s=round(sm.load_s, 1),
                          param_history_backfill_s=round(sm.backfill_s, 1))
            with sm.app.test_request_context():
                from types import SimpleNamespace
                ctx = sm.routes._active_ctx()
                with hub_index.snapshot(SimpleNamespace(
                        directory=sm.routes._hub_chip_dir(ctx["path"]))) as (_c, index):
                    report["ledger_events"] = len(index.eids)
                    report["ledger_paths"] = len(index.paths)
            report.update(measure(sm, args.passes))
    finally:
        report["cleanup_done"] = base.remove_scratch(work, scratch, strict=False)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report, indent=1), flush=True)


if __name__ == "__main__":
    main()
