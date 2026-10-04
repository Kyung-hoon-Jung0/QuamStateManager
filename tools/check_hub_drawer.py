"""S7 checks (docs/282) on disposable copies: the old per-value history must be
contained in the ledger's, one alias path must agree across three surfaces,
and the drawer's server time before vs after.

Nothing here writes into a source archive: run folders are byte-copied
(``shutil.copy2``, never a link) into ``--scratch``, and everything under the
scratch directory is removed at the end unless ``--keep``.

    python tools/check_hub_drawer.py golden --archive <root> --runs 300 \
        --scratch <dir> --report <json> [--paths 200] [--seed 282]
    python tools/check_hub_drawer.py perf-big --chip <chip dir> --runs 20 \
        --scratch <dir> --report <json>
    python tools/check_hub_drawer.py perf-synthetic --runs 10000 \
        --scratch <dir> --report <json>
"""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import random
import shutil
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quam_state_manager.core import hub, hub_build, hub_index, hub_rules, run_time  # noqa: E402
from quam_state_manager.core.pointer_resolver import is_pointer  # noqa: E402

_ABSENT = object()


def remove_scratch(target: Path, scratch: Path, *, strict: bool = True) -> bool:
    """Delete *target* (inside *scratch* only). Cached SQLite handles of the
    in-process app are closed first; a file still held is reported, never
    allowed to lose the measured report."""
    target, scratch = target.resolve(), scratch.resolve()
    if target != scratch and scratch not in target.parents:
        raise ValueError("cleanup target must be inside the scratch directory")
    if not target.exists():
        return True
    try:
        from quam_state_manager.core import chip_trends_ram
        chip_trends_ram.close_all()
    except Exception:  # noqa: BLE001
        pass
    hub_index.close_readers()
    import gc
    gc.collect()
    for _ in range(5):
        try:
            shutil.rmtree(target)
            return True
        except PermissionError:
            time.sleep(1.0)
            gc.collect()
    if strict:
        raise
    return False


def copy_runs(source: Path, dest: Path, count: int) -> list[Path]:
    """The newest *count* runs (by instant), node.json + data.json + the saved
    pair only, as real byte copies."""
    runs, _hint = hub_build.enumerate_runs(source)
    chosen = runs[-count:]
    out = []
    for run in chosen:
        folder = run.folder
        target = dest / folder.relative_to(source)
        target.mkdir(parents=True)
        for name in ("node.json", "data.json"):
            if (folder / name).is_file():
                shutil.copy2(folder / name, target / name)
        for path in hub_build.state_paths(folder):
            if path.is_file():
                copied = target / path.relative_to(folder)
                copied.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, copied)
        out.append(target)
    return out


def make_chip(newest_run: Path, chip: Path, data: Path) -> None:
    state_p, wiring_p = hub_build.state_paths(newest_run)
    chip.mkdir(parents=True)
    state = json.loads(state_p.read_text(encoding="utf-8"))
    state.setdefault("extras", {})["data_folder"] = str(data)
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    shutil.copy2(wiring_p, chip / "wiring.json")


@contextmanager
def sm_app(instance: Path, chip: Path, data: Path | None, *, backfill: bool):
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes
    hub.set_inline(True)
    app = create_app(testing=True, instance_path=str(instance))
    app.config["HUB_SYNC_ON_OPEN"] = True
    c = app.test_client()
    t0 = time.perf_counter()
    assert c.post("/load", data={"folder": str(chip)}).status_code in (200, 302)
    load_s = time.perf_counter() - t0
    if data is not None:
        c.post("/workspace/add", data={"folder": str(data)})
    t1 = time.perf_counter()
    backfill_report = None
    if backfill:
        from quam_state_manager.core.history import save_chip_decision
        with app.test_request_context():
            ctx = routes._active_ctx()
            hm = routes._history()
            rep = hm.backfill_from_workspace(ctx["path"], routes._ws(),
                                             instance_path=app.instance_path)
            # the runs are this chip's own (copied from its own folder): answer
            # the data-folder question the Param History page would ask
            for pend in rep.get("pending_decisions") or []:
                save_chip_decision(app.instance_path, pend["chip_key"], pend["data_folder"], "same")
            if rep.get("pending_decisions"):
                rep = hm.backfill_from_workspace(ctx["path"], routes._ws(),
                                                 instance_path=app.instance_path)
            backfill_report = {k: v for k, v in rep.items() if isinstance(v, (int, str))}
    backfill_s = time.perf_counter() - t1
    try:
        yield SimpleNamespace(app=app, client=c, routes=routes, load_s=load_s,
                              backfill_s=backfill_s, backfill_report=backfill_report)
    finally:
        hub_index.close_readers()


# ----------------------------------------------------------------------
# path sampling
# ----------------------------------------------------------------------

def leaves(doc, prefix=()):
    """Every scalar leaf in the drawer's dot-path grammar (lists by index)."""
    if isinstance(doc, dict):
        for k, v in doc.items():
            yield from leaves(v, prefix + (str(k),))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from leaves(v, prefix + (str(i),))
    else:
        yield ".".join(prefix), doc


def alias_paths(merged) -> list[str]:
    out = []
    for qn, q in (merged.get("qubits") or {}).items():
        if not isinstance(q, dict):
            continue
        for ch in ("xy", "z", "resonator"):
            ops = (q.get(ch) or {}).get("operations") if isinstance(q.get(ch), dict) else None
            if not isinstance(ops, dict):
                continue
            for op, val in ops.items():
                if not (isinstance(val, str) and val.startswith("#./")):
                    continue
                target = ops.get(val[3:])
                if isinstance(target, dict):
                    for field, fv in target.items():
                        if not isinstance(fv, (dict, list)):
                            out.append(f"qubits.{qn}.{ch}.operations.{op}.{field}")
    return out


def sample_paths(merged, n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    every = [(p, v) for p, v in leaves(merged) if p and not p.startswith("extras.data_folder")]
    long_el, ptr, plain = [], [], []
    for p, v in every:
        parent = p.rsplit(".", 1)[0]
        node = merged
        for seg in parent.split("."):
            node = node[int(seg)] if isinstance(node, list) else node.get(seg) if isinstance(node, dict) else None
        if isinstance(node, list) and len(node) > 16 and all(not isinstance(x, (dict, list)) for x in node):
            long_el.append(p)
        elif is_pointer(v):
            ptr.append(p)
        else:
            plain.append(p)
    aliases = alias_paths(merged)
    pick = []
    for pool, cap, cls in ((aliases, 40, "alias"), (ptr, 20, "pointer_leaf"),
                           (long_el, 20, "long_array_element")):
        for p in rng.sample(pool, min(cap, len(pool))):
            pick.append({"path": p, "class": cls})
    rest = rng.sample(plain, min(n - len(pick), len(plain)))
    pick += [{"path": p, "class": "leaf"} for p in rest]
    return pick[:n]


# ----------------------------------------------------------------------
# golden containment
# ----------------------------------------------------------------------

def _same(a, b) -> bool:
    if a is _ABSENT or b is _ABSENT:
        return a is b
    if a is None or b is None:
        return a is None and b is None
    try:
        return hub_rules.same(a, b)
    except Exception:  # noqa: BLE001
        return a == b


def _seq(points, positions):
    return [(positions[p["eid"]], None if p["removed"] else p["value"], p) for p in points]


def _at(seq, pos):
    best = None
    for item in seq:
        if item[0] <= pos:
            best = item
        else:
            break
    return best


def classify(path_rec, old_points, new_points, hop_seqs, positions, key_pos, last_pos,
             shown_eids, *, cap_rows, snap_keys=frozenset(), key_of_pos=None, examples=None):
    """Old points -> (class counts, missing examples); new points not matched
    exactly -> extra classes. *examples* collects up to 4 per class."""
    seq = _seq(new_points, positions)
    res = Counter()
    extra = Counter()
    missing = []
    matched_eids = set()
    old_pos = []
    key_of_pos = key_of_pos or {}
    examples = examples if examples is not None else {}

    def note(cls, item):
        lst = examples.setdefault(cls, [])
        if len(lst) < 4:
            lst.append(item)
    for i_old, op in enumerate(old_points):
        ts = op["ts"]
        pos = key_pos.get(ts)
        if pos is None:
            # a capture stamp (not a run key): place it by time among the events
            later = [p for k, p in key_pos.items() if k <= ts]
            pos = max(later) if later else -1
            if ts > max(key_pos, default=""):
                pos = last_pos
        old_pos.append(pos)
        v = op["value"]
        hit = _at(seq, pos)
        nv = _ABSENT if hit is None else hit[1]
        if hit is not None and (_same(v, nv) or (v is None and nv is None)):
            if hit[0] == pos:
                res["exact"] += 1
            else:
                res["earlier_change_in_ledger"] += 1
                # why the old surface dated it later
                if i_old == 0:
                    why = "earlier:oldest_old_point_is_its_window_start"
                elif key_of_pos.get(hit[0]) not in snap_keys:
                    why = "earlier:the_changing_run_has_no_param_history_snapshot"
                else:
                    why = "earlier:other"
                res[why] += 1
                note(why, {"path": path_rec["path"], "old_ts": ts, "value": repr(v)[:60],
                           "ledger_change_key": key_of_pos.get(hit[0]), "ledger_flags": hit[2]["flags"]})
            matched_eids.add(hit[2]["eid"])
            res["shown_by_new_drawer" if hit[2]["eid"] in shown_eids else "beyond_new_drawer_cap"] += 1
            continue
        if hit is None and v is None:
            res["absent_both"] += 1
            continue
        if isinstance(v, str) and is_pointer(v):
            found = False
            for hseq in hop_seqs:
                h = _at(hseq, pos)
                if h is not None and _same(h[1], v):
                    found = True
            if found:
                res["pointer_string_in_via"] += 1
                continue
        res["missing"] += 1
        missing.append({"path": path_rec["path"], "ts": ts, "old": repr(v)[:80],
                        "ledger": repr(nv)[:80] if nv is not _ABSENT else "absent",
                        "pos": pos})
    min_old = min(old_pos) if old_pos else None
    for item in seq:
        p = item[2]
        if p["eid"] in matched_eids:
            continue
        if not old_points:
            cls = "old_showed_nothing"
        elif item[0] < min_old:
            cls = "older_than_old_window"
        elif "reverts_to_earlier" in p["flags"]:
            cls = "return_to_an_earlier_state"
        elif key_of_pos.get(item[0]) not in snap_keys:
            cls = "run_without_param_history_snapshot"
        elif not isinstance(item[1], (int, float)) or isinstance(item[1], bool):
            cls = "non_numeric_or_removed"
        elif len(old_points) >= cap_rows:
            cls = "old_point_cap"
        else:
            cls = "inside_old_window"
        extra[cls] += 1
        note("extra:" + cls, {"path": path_rec["path"], "key": key_of_pos.get(item[0]),
                              "value": repr(item[1])[:60], "flags": p["flags"]})
    return res, extra, missing


def golden(args) -> dict:
    scratch = args.scratch.resolve()
    work = scratch / "golden"
    remove_scratch(work, scratch)
    data, chip, inst = work / "data", work / "chip", work / "inst"
    t0 = time.perf_counter()
    copied = copy_runs(args.archive, data, args.runs)
    copy_s = time.perf_counter() - t0
    make_chip(copied[-1], chip, data)
    report = {"runs_copied": len(copied), "copy_s": round(copy_s, 1), "seed": args.seed}
    try:
        with sm_app(inst, chip, data, backfill=True) as sm:
            r = sm.routes
            report["load_and_ledger_s"] = round(sm.load_s, 1)
            report["param_history_backfill_s"] = round(sm.backfill_s, 1)
            report["param_history_backfill"] = sm.backfill_report
            with sm.app.test_request_context():
                ctx = r._active_ctx()
                chip_dir = Path(r._hub_chip_dir(ctx["path"]))
                merged = ctx["store"].merged
                snaps = r._history().list_snapshots(ctx["path"])
                report["param_history_snapshots"] = len(snaps)
                with hub_index.snapshot(SimpleNamespace(directory=chip_dir)) as (_c, index):
                    positions = dict(index.positions)
                    run_kind = index.names["kind"].get("run")
                    key_pos = {}
                    for pos, eid in enumerate(index.eids):
                        if index.kind[pos] == run_kind:
                            key_pos[run_time.snapshot_key(index.t[pos], index.run_id[pos])] = pos
                    key_of_pos = {p: k for k, p in key_pos.items()}
                    last_pos = len(index.eids)
                    report["ledger_events"] = len(index.eids)
                snap_keys = frozenset(m.timestamp for m in snaps)
                examples = {"drawer": {}, "column": {}}
                picks = sample_paths(merged, args.paths, args.seed)
                totals = {"drawer": Counter(), "column": Counter()}
                extras = {"drawer": Counter(), "column": Counter()}
                missing = {"drawer": [], "column": []}
                per_class = Counter(p["class"] for p in picks)
                modes = Counter()
                for rec in picks:
                    dp = rec["path"]
                    ans_all = r._value_history(ctx, {"v": dp})
                    modes[ans_all["mode"]] += 1
                    if ans_all["mode"] != "ledger":
                        continue
                    new_pts = ans_all["rows"]["v"]["points"]
                    shown = r._value_history(ctx, {"v": dp}, limit=r._VH_DRAWER_LIMIT)
                    shown_eids = {p["eid"] for p in shown["rows"]["v"]["points"]}
                    hop_seqs = [_seq(h["rows"], positions) for h in ans_all["rows"]["v"]["retargets"]]
                    hist, _cur, _chart = r._legacy_field_history(ctx, dp)
                    old_d = [{"ts": p["timestamp"], "value": p["value"]} for p in reversed(hist["points"])]
                    res, ext, miss = classify(rec, old_d, new_pts, hop_seqs, positions, key_pos,
                                              last_pos, shown_eids, cap_rows=20, snap_keys=snap_keys,
                                              key_of_pos=key_of_pos, examples=examples["drawer"])
                    totals["drawer"].update(res)
                    extras["drawer"].update(ext)
                    missing["drawer"] += miss
                    view = r._legacy_column_history(ctx, {"v": dp})
                    chips = view["rows"][0]["chips"] if view["rows"] else []
                    old_c = [{"ts": ch["ts"], "value": ch["raw"]} for ch in reversed(chips)]
                    col_shown = {p["eid"] for p in new_pts[-r.CH_MAX_CHIPS:]}
                    res, ext, miss = classify(rec, old_c, new_pts, hop_seqs, positions, key_pos,
                                              last_pos, col_shown, cap_rows=r.CH_MAX_CHIPS,
                                              snap_keys=snap_keys, key_of_pos=key_of_pos,
                                              examples=examples["column"])
                    totals["column"].update(res)
                    extras["column"].update(ext)
                    missing["column"] += miss
                report.update({
                    "paths": len(picks), "path_classes": dict(per_class), "modes": dict(modes),
                    "old_points": {k: dict(v) for k, v in totals.items()},
                    "extra_ledger_points": {k: dict(v) for k, v in extras.items()},
                    "missing_examples": {k: v[:20] for k, v in missing.items()},
                    "examples": examples,
                })
                if args.alias:
                    report["alias"] = alias_agreement(sm, ctx, args.alias)
                # the alias path with the most recorded changes, for (b)
                best, best_n = None, 0
                for ap in alias_paths(merged):
                    a = r._value_history(ctx, {"v": ap})
                    n = len(a["rows"]["v"]["points"]) if a["mode"] == "ledger" else 0
                    if n > best_n:
                        best, best_n = ap, n
                if best:
                    report["alias_most_changed"] = alias_agreement(sm, ctx, best)
    finally:
        hub_index.close_readers()
        if not args.keep:
            report["cleanup_done"] = remove_scratch(work, scratch, strict=False)
    return report


def alias_agreement(sm, ctx, dot_path: str) -> dict:
    """(b): the drawer, Column History and Chip Status Trends for ONE alias
    path, before (the old code paths) and after (the shared read)."""
    r = sm.routes
    store = ctx["store"]
    qubits = list(store.qubit_names)
    pairs = list(store.qubit_pair_names)
    hm = r._history()
    from quam_state_manager.core import chip_trends_ram
    tbl = chip_trends_ram.table(hm, Path(ctx["path"]))

    def trends(alias_on: bool):
        real = r._trend_alias_series
        if not alias_on:
            r._trend_alias_series = lambda dps: {}
        try:
            got = r._trend_series_leaf(hm, Path(ctx["path"]), dot_path, qubits, pairs, tbl)
        finally:
            r._trend_alias_series = real
        q = dot_path.split(".")[1]
        for s in got:
            if s["entity"] == q:
                held = s.get("held") or {}
                return [(ts, v) for ts, v in s["points"] if ts not in held]
        return []

    hist, _c, _ch = r._legacy_field_history(ctx, dot_path)
    before_drawer = [(p["timestamp"], p["value"]) for p in reversed(hist["points"])]
    view = r._legacy_column_history(ctx, {"v": dot_path})
    before_col = [(c["ts"], c["raw"]) for c in reversed(view["rows"][0]["chips"])]
    before_trends = trends(False)
    ans = r._value_history(ctx, {"v": dot_path})
    after = [(run_time.snapshot_key(p["t_us"], p.get("run_id") or 0), p["value"])
             for p in ans["rows"]["v"]["points"]]
    col = r._value_history(ctx, {"qX": dot_path}, runs=r.CH_BYRUN_COLS)
    after_col = [(run_time.snapshot_key(p["t_us"], p.get("run_id") or 0), p["value"])
                 for p in col["rows"]["qX"]["points"]]
    after_trends = trends(True)
    return {"path": dot_path,
            "before": {"drawer": before_drawer, "column": before_col, "trends": before_trends},
            "after": {"drawer": after, "column": after_col, "trends": after_trends},
            "before_agree": before_drawer == before_col == before_trends,
            "after_agree": after == after_col == after_trends,
            "holder": ans["targets"]["v"]["holder_path"],
            "via": [h["from_path"] + " -> " + h["to_path"] for h in ans["targets"]["v"]["via"]]}


# ----------------------------------------------------------------------
# drawer server time
# ----------------------------------------------------------------------

def _pct(values, q):
    v = sorted(values)
    return round(v[max(0, math.ceil(q * len(v)) - 1)], 2)


def time_drawer(sm, paths: list[str], *, legacy: bool, passes: int) -> dict:
    r = sm.routes
    real = r._value_history
    if legacy:
        def stub(ctx, path_map, **kw):
            return {"mode": "fallback", "fallback_note": None, "targets": {}, "notes": {}}
        r._value_history = stub
    try:
        cold, warm = [], []
        kinds = Counter()
        for i in range(passes + 1):
            for p in paths:
                t = time.perf_counter()
                resp = sm.client.get("/field/history", query_string={"path": p})
                dt = (time.perf_counter() - t) * 1000
                assert resp.status_code == 200
                (cold if i == 0 else warm).append(dt)
                body = resp.data.decode("utf-8", "replace")
                kinds["ledger_rows" if 'class="vh-row' in body else
                      "ledger_empty" if "from the change ledger" in body else
                      "wait" if "data-vh-retry" in body else
                      "old_path" if "fh-table" in body or "fh-empty" in body else "other"] += 1
        return {"first_ms": round(cold[0], 2) if cold else None,
                "cold_p50": _pct(cold, .5), "cold_p95": _pct(cold, .95),
                "warm_p50": _pct(warm, .5), "warm_p95": _pct(warm, .95),
                "n_warm": len(warm), "answers": dict(kinds)}
    finally:
        r._value_history = real


def perf_big(args) -> dict:
    """A big chip (its own state) with *runs* synthetic runs that each move a
    few numeric leaves; Param History backfilled for the old path."""
    scratch = args.scratch.resolve()
    work = scratch / "perf_big"
    remove_scratch(work, scratch)
    data, chip, inst = work / "data", work / "chip", work / "inst"
    state = json.loads((args.chip / "state.json").read_text(encoding="utf-8"))
    wiring = (args.chip / "wiring.json").read_text(encoding="utf-8")
    rng = random.Random(args.seed)
    qubits = sorted(state.get("qubits") or {})
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    moved = []
    for rid in range(1, args.runs + 1):
        for _ in range(3):
            q = rng.choice(qubits)
            key = rng.choice(["T1", "f_01", "T2ramsey"])
            if isinstance(state["qubits"][q].get(key), (int, float)):
                state["qubits"][q][key] = float(state["qubits"][q][key] or 1.0) * (1 + rng.uniform(-1e-3, 1e-3))
                moved.append(f"qubits.{q}.{key}")
        instant = start + timedelta(minutes=5 * rid)
        folder = data / instant.date().isoformat() / f"#{rid}_scan_{instant:%H%M%S}"
        (folder / "quam_state").mkdir(parents=True)
        (folder / "node.json").write_text(json.dumps({
            "created_at": instant.isoformat(), "id": rid,
            "metadata": {"name": "scan", "status": "finished"}}), encoding="utf-8")
        (folder / "quam_state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text(wiring, encoding="utf-8")
    chip.mkdir(parents=True)
    state.setdefault("extras", {})["data_folder"] = str(data)
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text(wiring, encoding="utf-8")
    report = {"runs": args.runs, "state_bytes": (chip / "state.json").stat().st_size}
    try:
        with sm_app(inst, chip, data, backfill=True) as sm:
            report["load_and_ledger_s"] = round(sm.load_s, 1)
            report["param_history_backfill_s"] = round(sm.backfill_s, 1)
            with sm.app.test_request_context():
                merged = sm.routes._active_ctx()["store"].merged
            paths = sorted(set(moved))[:15] + alias_paths(merged)[:10]
            rest = [p for p, v in leaves(merged) if isinstance(v, float)]
            paths += random.Random(args.seed).sample(rest, min(25, len(rest)))
            report["paths"] = len(paths)
            report["before"] = time_drawer(sm, paths, legacy=True, passes=args.passes)
            report["after"] = time_drawer(sm, paths, legacy=False, passes=args.passes)
    finally:
        hub_index.close_readers()
        if not args.keep:
            report["cleanup_done"] = remove_scratch(work, scratch, strict=False)
    return report


def perf_synthetic(args) -> dict:
    """A small chip with a 10k-run synthetic ledger (docs/279's generator
    shape); the old path reads the same runs as a Datasets root."""
    scratch = args.scratch.resolve()
    work = scratch / "perf_syn"
    remove_scratch(work, scratch)
    data, chip, inst = work / "data", work / "chip", work / "inst"
    doc = {"qubits": {f"qA{i}": {f"field{j}": 0 for j in range(8)} for i in range(128)},
           "extras": {"chip_name": "synthetic"}}
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    t0 = time.perf_counter()
    for rid in range(args.runs):
        instant = start + timedelta(minutes=3 * rid)
        entity = f"qA{rid % 128}"
        for j in range(3):
            doc["qubits"][entity][f"field{(rid + j) % 8}"] = rid + j
        folder = data / instant.date().isoformat() / f"#{rid}_scan_{instant:%H%M%S}"
        (folder / "quam_state").mkdir(parents=True)
        (folder / "node.json").write_text(json.dumps({
            "created_at": instant.isoformat(), "id": rid,
            "metadata": {"name": "scan", "status": "finished"}}), encoding="utf-8")
        (folder / "quam_state" / "state.json").write_text(json.dumps(doc), encoding="utf-8")
        (folder / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")
    gen_s = time.perf_counter() - t0
    chip.mkdir(parents=True)
    doc["extras"]["data_folder"] = str(data)
    (chip / "state.json").write_text(json.dumps(doc), encoding="utf-8")
    (chip / "wiring.json").write_text("{}", encoding="utf-8")
    report = {"runs": args.runs, "generate_s": round(gen_s, 1)}
    try:
        with sm_app(inst, chip, data, backfill=False) as sm:
            report["load_and_ledger_s"] = round(sm.load_s, 1)
            rng = random.Random(args.seed)
            paths = [f"qubits.qA{rng.randrange(128)}.field{rng.randrange(8)}" for _ in range(50)]
            report["paths"] = len(paths)
            report["before"] = time_drawer(sm, paths, legacy=True, passes=args.passes)
            report["after"] = time_drawer(sm, paths, legacy=False, passes=args.passes)
    finally:
        hub_index.close_readers()
        if not args.keep:
            report["cleanup_done"] = remove_scratch(work, scratch, strict=False)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("golden")
    g.add_argument("--archive", type=Path, required=True)
    g.add_argument("--runs", type=int, default=300)
    g.add_argument("--paths", type=int, default=200)
    g.add_argument("--alias", default=None, help="one alias path for check (b)")
    b = sub.add_parser("perf-big")
    b.add_argument("--chip", type=Path, required=True)
    b.add_argument("--runs", type=int, default=20)
    s = sub.add_parser("perf-synthetic")
    s.add_argument("--runs", type=int, default=10000)
    for p in (g, b, s):
        p.add_argument("--scratch", type=Path, required=True)
        p.add_argument("--report", type=Path, required=True)
        p.add_argument("--seed", type=int, default=282)
        p.add_argument("--passes", type=int, default=3)
        p.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    fn = {"golden": golden, "perf-big": perf_big, "perf-synthetic": perf_synthetic}[args.cmd]
    out = fn(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k not in ("missing_examples", "alias")},
                     default=str))


if __name__ == "__main__":
    main()
