"""S8 checks (docs/283) on disposable byte copies of a real archive.

``golden``: every point the OLD Chip Status Trends (curated + leaf tier), the
OLD Param History grid and the OLD Param History Changes showed must be
contained in what the NEW surfaces answer from the change ledger (same value,
in force at the same run); every extra point the new surfaces show is
classified. Then the metric meta's writer check: sampled cells, each judged
by brute force against the run's own ``node.json`` patches, the ledger rows
and the SM events' own entries.

Nothing here writes into a source archive: run folders are byte-copied
(``shutil.copy2``, never a link) into ``--scratch``; everything under the
scratch directory is removed at the end. The report holds counts and generic
labels only; the spot-check evidence (paths, values, patches) is written to
``--evidence`` (a scratch file the person reviewing deletes after reading).

    python tools/check_hub_chip_status.py golden --archive <root> --label "Archive A" \
        --runs 200 --scratch <dir> --report <json> --evidence <json>
"""

from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import json
import math
from pathlib import Path
import random
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_hub_drawer as base  # noqa: E402
from quam_state_manager.core import hub, hub_index, hub_query, hub_rules, run_time  # noqa: E402
from quam_state_manager.core import value_history as vh  # noqa: E402
from quam_state_manager.core.history import (  # noqa: E402
    DEFAULT_TRACKED_PROPERTIES, _DERIVED_FIDELITY_PROPS, _VALUE_PATHS)
from quam_state_manager.core.hub_store import CHIP_UNCERTAIN, SM_KINDS  # noqa: E402

_ABSENT = object()


def _same(a, b) -> bool:
    if a is _ABSENT or b is _ABSENT:
        return a is b
    if a is None or b is None:
        return a is None and b is None
    try:
        return hub_rules.same(a, b)
    except Exception:  # noqa: BLE001
        return a == b


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v) -> bool:
    return _num(v) and math.isfinite(v)


def _eid_of_key(key: str) -> int | None:
    """``YYYYMMDD_HHMMSS_e<eid>`` (hub_status.event_key) -> eid."""
    tail = str(key or "").rsplit("_", 1)[-1]
    return int(tail[1:]) if tail.startswith("e") and tail[1:].isdigit() else None


class Ledger:
    """Positions and event facts of the copied chip's ledger (one snapshot)."""

    def __init__(self, binding):
        with hub_index.snapshot(binding) as (conn, index):
            self.positions = dict(index.positions)
            self.eids = list(index.eids)
            self.n = len(index.eids)
            run_kind = index.names["kind"].get("run")
            obs_kind = index.names["kind"].get("observed")
            self.key_pos: dict[str, int] = {}
            for pos, eid in enumerate(index.eids):
                if index.kind[pos] == run_kind:
                    self.key_pos[run_time.snapshot_key(index.t[pos], index.run_id[pos])] = pos
                elif obs_kind is not None and index.kind[pos] == obs_kind:
                    self.key_pos[index.keys[eid][4].split(":", 1)[1]] = pos
            self.sorted_keys = sorted(self.key_pos)
            self.events = {r["eid"]: dict(r) for r in conn.execute("SELECT * FROM events")}
            chash = {eid: ev.get("chash") for eid, ev in self.events.items()}
            seen: dict = {}
            self.after_return: set[int] = set()
            self.returns: set[int] = set()
            for pos, eid in enumerate(index.eids):
                h = chash.get(eid)
                if h is not None and h in seen and seen[h] < pos - 1:
                    self.returns.add(eid)
                    if pos + 1 < len(index.eids):
                        self.after_return.add(index.eids[pos + 1])
                if h is not None:
                    seen[h] = pos
            self.paths = set(index.paths)

    def map_snapshots(self, hm, path, snaps) -> dict:
        """A Param History snapshot that is neither a run nor an observed
        capture (an SM write's save / backup copy) shows the state of the
        ledger event whose content equals its own: mapped by content hash,
        the nearest such event in time. ``{kind: count}`` of the mapping."""
        from datetime import datetime, timezone
        from quam_state_manager.core import hub_sync
        by_hash: dict = {}
        for pos, eid in enumerate(self.eids):
            h = self.events[eid].get("chash")
            if h:
                by_hash.setdefault(h, []).append(pos)
        self.by_content: dict[str, int] = {}
        out = Counter()
        for m in snaps:
            ts = m.timestamp
            if ts in self.key_pos:
                continue
            d = hm.snapshot_dir(path, ts)
            try:
                st = json.loads((d / "state.json").read_text(encoding="utf-8"))
                wp = d / "wiring.json"
                wr = json.loads(wp.read_text(encoding="utf-8")) if wp.exists() else {}
            except (OSError, ValueError):
                out["unreadable"] += 1
                continue
            cands = by_hash.get(hub_sync._content_hash_of(st, wr)) or []
            if not cands:
                out["state_not_in_the_ledger"] += 1
                continue
            try:
                t = datetime.strptime(ts[:15], "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
                t_us = int(t.timestamp() * 1e6)
            except ValueError:
                t_us = 0
            pos = min(cands, key=lambda q: abs(self.events[self.eids[q]]["t_utc_us"] - t_us))
            self.by_content[ts] = pos
            out["mapped_by_content"] += 1
        return dict(out)

    def pos_of(self, ts: str) -> int:
        """An OLD snapshot stamp -> the ledger position whose state it shows:
        a run's own key, a capture's own key, an SM copy's content, else the
        newest event at or before that instant."""
        if ts in self.key_pos:
            return self.key_pos[ts]
        if ts in getattr(self, "by_content", {}):
            return self.by_content[ts]
        i = bisect_right(self.sorted_keys, ts)
        return max((self.key_pos[k] for k in self.sorted_keys[:i]), default=-1) if i else -1


def fold(series: list[tuple], pos: int):
    """The value in force at *pos* from ``[(pos, value)]`` change points."""
    i = bisect_right([p for p, _v in series], pos)
    return series[i - 1][1] if i else _ABSENT


def contain(surface, dp, old_points, new_series, led: Ledger, snap_keys, totals, missing, matched):
    """Every OLD point (ts, value) must be in force in the NEW series at the
    same event. ``matched`` collects the (dp, pos) the old surface showed."""
    first = True
    prev = _ABSENT
    for ts, value in old_points:
        if value is None:
            totals[surface + ":old_shows_no_value"] += 1
            continue
        same_as_prev = prev is not _ABSENT and _same(prev, value)
        prev = value
        pos = led.pos_of(ts)
        totals[surface + ":old_points"] += 1
        got = fold(new_series, pos)
        if got is not _ABSENT and _same(got, value):
            exact = any(p == pos for p, _v in new_series)
            totals[surface + (":same_event" if exact else ":dated_earlier")] += 1
            if not exact:
                change = max(p for p, _v in new_series if p <= pos)
                ev = led.events.get(led.eids[change]) or {}
                key = (run_time.snapshot_key(ev.get("t_utc_us"), ev.get("run_id"))
                       if ev.get("kind") == "run" else None)
                if surface == "grid":
                    why = "a_snapshot_after_the_change_the_value_was_still_in_force"
                elif first:
                    why = "the_oldest_old_point_is_its_window_start"
                elif same_as_prev:
                    # the old change-point read kept BOTH edges of a value's
                    # stretch (and a held endpoint at the newest snapshot)
                    why = "the_closing_edge_of_an_unchanged_stretch"
                elif key is not None and key not in snap_keys:
                    why = "the_changing_run_has_no_param_history_snapshot"
                elif led.eids[change] in led.returns or led.eids[change] in led.after_return:
                    why = "the_change_is_a_return_or_right_after_one"
                else:
                    why = "OTHER"
                totals[surface + ":dated_earlier:" + why] += 1
            matched.add((dp, pos))
        else:
            totals[surface + ":MISSING"] += 1
            if len(missing) < 20:
                missing.append({"surface": surface, "old_ts": ts, "old": repr(value)[:60],
                                "new": "absent" if got is _ABSENT else repr(got)[:60]})
        first = False


def classify_extras(surface, dp, new_series, points_by_pos, matched, led: Ledger, snap_keys,
                    old_min_pos, extras, examples, tag: str = ""):
    for pos, value in new_series:
        if (dp, pos) in matched:
            continue
        p = points_by_pos.get(pos) or {}
        ev = led.events.get(led.eids[pos]) if 0 <= pos < led.n else {}
        key = run_time.snapshot_key(ev.get("t_utc_us"), ev.get("run_id")) if ev.get("kind") == "run" else None
        if not _num(value) or isinstance(value, bool):
            cls = "not_a_number" if value is not None else "removal"
        elif not _finite(value):
            cls = "nan_or_infinity"
        elif ev.get("kind") == "observed":
            cls = "outside_edit_seen_by_sm"
        elif ev.get("kind") in SM_KINDS:
            cls = "sm_write"
        elif old_min_pos is None:
            cls = "path_the_old_tier_never_held" + (":" + tag if tag else "")
        elif pos < old_min_pos:
            cls = "older_than_the_old_window"
        elif led.eids[pos] in led.returns:
            cls = "a_return_to_an_earlier_saved_state"
        elif led.eids[pos] in led.after_return:
            cls = "the_change_right_after_such_a_return"
        elif key is not None and key not in snap_keys:
            cls = "a_run_with_no_param_history_snapshot"
        elif p.get("held"):
            cls = "held_endpoint"
        else:
            cls = "unexplained"
        extras[surface + ":" + cls] += 1
        if cls == "unexplained" and len(examples) < 20:
            examples.append({"surface": surface, "pos": pos, "value": repr(value)[:60],
                             "kind": ev.get("kind")})


# ----------------------------------------------------------------------
# the old surfaces (unchanged code paths, run on the same copy)
# ----------------------------------------------------------------------

def old_curated(hm, path, *, trends: bool) -> dict:
    """``{(qubit, prop): [(ts, value)]}`` -- the Trends read (change points,
    downsampled to 400) or the grid read (every snapshot)."""
    rows = (hm.extract_property_history(path, list(DEFAULT_TRACKED_PROPERTIES), downsample=400,
                                        compress="changes") if trends else
            hm.extract_property_history(path, list(DEFAULT_TRACKED_PROPERTIES), downsample=None))
    out = {}
    for r in rows:
        pts = [(v["timestamp"], v["value"]) for v in r["values"]]
        if trends:      # Trends charted numbers only
            pts = [(t, v) for t, v in pts if _num(v)]
        out[r["qubit"], r["property"]] = pts
    return out


def old_changes(hm, path) -> dict:
    """``{path: [(ts, value)]}`` from the change-point index feed."""
    out: dict = {}
    for g in hm.leaf_change_groups(path, limit_snaps=10 ** 7, rows_per_snap=10 ** 7):
        for r in g["rows"]:
            out.setdefault(r["path"], []).append((g["timestamp"], r["value"]))
    for v in out.values():
        v.sort()
    return out


# ----------------------------------------------------------------------
# the new surfaces (the S8 adapters the routes call)
# ----------------------------------------------------------------------

def new_curated_series(table, led: Ledger) -> dict:
    """``{(qubit, prop): ([(pos, value)], {pos: value dict})}`` from
    ``LedgerTable.curated`` (Trends' and the grid's one read)."""
    out = {}
    for row in table.curated(tuple(DEFAULT_TRACKED_PROPERTIES)):
        ser, by = [], {}
        for v in row["values"]:
            if v.get("held"):
                continue
            pos = led.positions[v["point"]["eid"]]
            ser.append((pos, v["value"]))
            by[pos] = v
        out[row["qubit"], row["property"]] = (ser, by)
    return out


def new_leaf_series(table, led: Ledger, paths: list[str]) -> dict:
    out = {}
    got = table.leaf_series_many(paths, hold_to_newest=True)
    for dp, rows in got.items():
        ser = []
        for r in rows:
            if len(r) > 6:          # held endpoint: not an event of its own
                continue
            ser.append((led.positions[_eid_of_key(r[0])], r[1]))
        out[dp] = (ser, {})
    return out


def new_changes(binding, led: Ledger) -> dict:
    """``{holder path: [(pos, value)]}`` -- every row of every page of the
    Changes surface's read (``hub_query.timeline``, changed events only)."""
    out: dict = {}
    cursor = None
    while True:
        res = hub_query.timeline(binding, cursor=cursor, changed_only=True, limit=200)
        for ev in res["events"]:
            pos = led.positions[ev["eid"]]
            for c in ev["changes"]:
                out.setdefault(c["path"], []).append((pos, None if c["op"] == "gone" else c["new"]))
        cursor = res["cursor"]
        if cursor is None:
            break
    for v in out.values():
        v.sort(key=lambda t: t[0])
    return out


# ----------------------------------------------------------------------
# the metric meta's writer, by brute force
# ----------------------------------------------------------------------

def _patch_sets(folder: str | None, holder: str, value) -> bool:
    """Does the run's own node.json patch set *holder* to exactly *value*?"""
    if not folder:
        return False
    try:
        node = json.loads((Path(folder) / "node.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    for p in node.get("patches") or []:
        if not isinstance(p, dict) or p.get("op") not in ("add", "replace"):
            continue
        segs = [s.replace("~1", "/").replace("~0", "~") for s in str(p.get("path") or "").split("/")[1:]]
        if segs[:1] == ["quam"]:
            segs = segs[1:]
        if vh.holder_spelling(".".join(segs)) == holder and _same(p.get("value"), value):
            return True
    return False


def brute_writer(conn, led: Ledger, sm_lines: dict, roots: dict, eid: int, holders: list[str]) -> dict:
    """What the ledger and the source files prove about event *eid* for the
    cell's *holders*: ``{"kind", "proven", "uncertain", "first", "actor", "sm_entries_ok"}``."""
    ev = led.events[eid]
    rows = {}
    for h in holders:
        r = conn.execute("SELECT c.op, c.num, c.txt FROM changes c JOIN paths p USING(pid) "
                         "WHERE c.eid=? AND p.path=?", (eid, h)).fetchone()
        if r is not None:
            rows[h] = None if r[0] == 2 else (json.loads(r[2]) if r[2] is not None else r[1])
    out = {"kind": ev["kind"], "run_id": ev.get("run_id"),
           "uncertain": bool(int(ev.get("flags") or 0) & CHIP_UNCERTAIN),
           "first": ev.get("base_hash") is None, "actor": ev.get("actor"), "changed": len(rows)}
    if ev["kind"] == "run":
        folder = (roots.get(ev["root_id"]) or "").rstrip("/\\") + "/" + (ev.get("rel_path") or "")
        out["proven"] = bool(rows) and all(_patch_sets(folder, h, v) for h, v in rows.items())
        out["folder"] = folder
    elif ev["kind"] in SM_KINDS:
        sid = conn.execute("SELECT sm_id FROM sm_events WHERE eid=?", (eid,)).fetchone()
        line = sm_lines.get(sid[0]) if sid else None
        entries = {}
        for e in (sm_lines["_hub"].entries_of(line) if line else []):
            entries[vh.holder_spelling(str(e.get("path") or ""))] = e.get("new")
        out["sm_entries_ok"] = bool(rows) and all(
            h in entries and _same(entries[h], v) for h, v in rows.items())
        out["proven"] = False
    else:
        out["proven"] = False
    return out


def meta_check(sm, ctx, table, led: Ledger, seed: int, evidence: dict) -> dict:
    """100 metric-meta cells: the writer the meta names, judged against brute
    force (the run's own ``node.json`` patches, the ledger rows and the SM
    events' own entries).

    S10 C5: the old-meta side (which cells the snapshot meta named a run for,
    and 10 spot checks of those) -> gone with the snapshot meta; its last run
    was the C0 golden."""
    from quam_state_manager.core import metric_meta as mm
    from functools import partial
    new = sm.client.get("/topology/metric-meta").get_json()
    assert new["mode"] == "ledger", new.get("mode")
    store = ctx["store"]
    with store._lock:
        doc = store.merged
        qpaths = mm.qubit_paths(doc, list(store.qubit_names), resolver=partial(vh.target, container=True))
        ppaths, _loads = mm.pair_rb_paths(doc, list(store.qubit_pair_names),
                                          resolver=partial(vh.target, container=True))
    cells = []
    for group, key in ((qpaths, "q"), (ppaths, "p")):
        for metric, per in group.items():
            for entity, paths in per.items():
                e = (new.get(key) or {}).get(metric, {}).get(entity)
                if e and e.get("eid") is not None:
                    cells.append((key, metric, entity, paths, e))
    rng = random.Random(seed)
    by_prov: dict = {}
    for c in cells:
        by_prov.setdefault(c[4]["provenance"], []).append(c)
    sample = []
    for prov in sorted(by_prov):              # every class present, then the rest at random
        sample += rng.sample(by_prov[prov], min(10, len(by_prov[prov])))
    rest = [c for c in cells if c not in sample]
    sample += rng.sample(rest, min(max(0, 100 - len(sample)), len(rest)))
    sample = sample[:100]
    primary = hub.Hub.for_chip(Path(table.directory))
    sm_lines = {line["id"]: line for line in primary.events()}
    sm_lines["_hub"] = primary
    counts = Counter()
    disagree = []
    with hub_index.snapshot(table.binding) as (conn, _index):
        roots = {row[0]: row[1] for row in conn.execute("SELECT root_id, path FROM roots")}
        merged = store.merged
        for key, metric, entity, paths, e in sample:
            holders = [vh.target(merged, dp)["holder"] for dp in paths]
            bf = brute_writer(conn, led, sm_lines, roots, e["eid"], holders)
            counts["sampled"] += 1
            counts["class:" + e["provenance"]] += 1
            named = e.get("run")
            if bf["kind"] == "run":
                should = bf["proven"] and not bf["uncertain"]
                ok = (named is not None) == should and (named is None or named == bf["run_id"])
                if e["provenance"] == "first_record":
                    ok = ok and bf["first"] and named is None
            elif bf["kind"] in SM_KINDS:
                ok = named is None and bf.get("sm_entries_ok") and e["kind"] == bf["kind"] \
                    and e.get("actor") == bf["actor"]
            else:
                ok = named is None
            counts["agree" if ok else "DISAGREE"] += 1
            if not ok and len(disagree) < 20:
                disagree.append({"metric": metric, "provenance": e["provenance"], "bf": {
                    k: v for k, v in bf.items() if k != "folder"}})
    evidence["meta_disagree"] = disagree
    return {k: v for k, v in counts.items()}


# ----------------------------------------------------------------------

_METRIC_LAST = {"T1", "T2ramsey", "T2echo", "f_01", "amplitude", "averaged", "x180", "x90"}


def _metric_patch(patch) -> bool:
    """Does a node.json patch set a Chip Status metric leaf?"""
    path = str((patch or {}).get("path") or "") if isinstance(patch, dict) else ""
    if not path.startswith(("/quam/qubits/", "/quam/qubit_pairs/")):
        return False
    return (path.rsplit("/", 1)[-1] in _METRIC_LAST or "confusion_matrix" in path
            or "fidelity" in path.lower())


def copy_window(source: Path, dest: Path, count: int, how: str) -> list[Path]:
    """*count* consecutive runs as byte copies (node.json + data.json + the
    saved pair): the newest ones, or (``patched``) the window holding the
    most runs whose own patches set a Chip Status metric leaf -- so the
    writer check meets proven writers too."""
    if how == "newest":
        return base.copy_runs(source, dest, count)
    import shutil
    from quam_state_manager.core import hub_build
    runs, _hint = hub_build.enumerate_runs(source)
    hit = [int(any(_metric_patch(pt) for pt in (r.node.get("patches") or []))) for r in runs]
    best, best_n = max(0, len(runs) - count), -1
    window = sum(hit[:count])
    for start in range(0, max(1, len(runs) - count + 1)):
        if start:
            window += hit[start + count - 1] - hit[start - 1]
        if window >= best_n:
            best, best_n = start, window
    out = []
    for run in runs[best:best + count]:
        target = dest / run.folder.relative_to(source)
        target.mkdir(parents=True)
        for name in ("node.json", "data.json"):
            if (run.folder / name).is_file():
                shutil.copy2(run.folder / name, target / name)
        for p in hub_build.state_paths(run.folder):
            if p.is_file():
                c = target / p.relative_to(run.folder)
                c.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(p, c)
        out.append(target)
    return out


def golden(args) -> dict:
    scratch = args.scratch.resolve()
    work = scratch / "golden_s8"
    base.remove_scratch(work, scratch)
    data, chip, inst = work / "data", work / "chip", work / "inst"
    t0 = time.perf_counter()
    copied = copy_window(args.archive, data, args.runs, args.window)
    base.make_chip(copied[-1], chip, data)
    report = {"archive": args.label, "runs_copied": len(copied), "seed": args.seed,
              "window": args.window,
              "copy_s": round(time.perf_counter() - t0, 1)}
    evidence: dict = {}
    try:
        with base.sm_app(inst, chip, data, backfill=True) as sm:
            r = sm.routes
            report["load_and_ledger_s"] = round(sm.load_s, 1)
            report["param_history_backfill_s"] = round(sm.backfill_s, 1)
            if args.observed:
                # S7's injector, offered only runs that saved a state (an
                # archive can hold a run folder without one)
                real_enum = base.hub_build.enumerate_runs

                def with_state(root):
                    runs, hint = real_enum(root)
                    return [x for x in runs if base.hub_build.state_paths(x.folder)[0].is_file()], hint
                base.hub_build.enumerate_runs = with_state
                try:
                    inj = base.inject_outside_edits(sm, chip, copied, args.observed, args.seed)
                finally:
                    base.hub_build.enumerate_runs = real_enum
                report["outside_edits"] = {k: inj[k] for k in ("made", "captured", "import")}
            if args.sm_writes:
                report["sm_writes"] = sm_writes(sm, args.sm_writes, args.seed)
            with sm.app.test_request_context():
                ctx = r._active_ctx()
                ans, table = r._hub_status_table(ctx)
                assert ans["mode"] == "ledger" and table is not None, ans["mode"]
                hm = r._history()
                path = ctx["path"]
                led = Ledger(table.binding)
                snaps = hm.list_snapshots(path)
                snap_keys = frozenset(m.timestamp for m in snaps)
                report["sm_copy_snapshots"] = led.map_snapshots(hm, path, snaps)
                report.update(ledger_events=led.n, param_history_snapshots=len(snaps))
                totals, extras = Counter(), Counter()
                missing, examples = [], []
                # ---- curated: Trends (change points) and the grid (every snapshot)
                new_cur = new_curated_series(table, led)
                grid_rows = {(row["qubit"], row["property"]): row for row in r._hub_grid_rows(
                    table, list(DEFAULT_TRACKED_PROPERTIES), None, None, None, None)}
                for surface, trends in (("trends_curated", True), ("grid", False)):
                    old = old_curated(hm, path, trends=trends)
                    for cell, pts in old.items():
                        if surface == "grid":
                            row = grid_rows.get(cell)
                            ser = [(led.positions[v["point"]["eid"]], v["value"])
                                   for v in (row["values"] if row else []) if not v.get("held")]
                            by = {}
                        else:
                            ser, by = new_cur.get(cell, ([], {}))
                        matched: set = set()
                        dp = f"{cell[0]}:{cell[1]}"
                        contain(surface, dp, pts, ser, led, snap_keys, totals, missing, matched)
                        old_pos = [led.pos_of(t) for t, v in pts if v is not None]
                        classify_extras(surface, dp, ser, by, matched, led, snap_keys,
                                        min(old_pos) if old_pos else None, extras, examples,
                                        tag=cell[1])
                    for cell in set(new_cur) - set(old):
                        extras[surface + ":cell_the_old_index_never_held"] += len(new_cur[cell][0])
                # ---- Trends leaf tier: 200 sampled paths of the current state
                sampled = base.sample_paths(ctx["store"].merged, args.paths, args.seed)
                picks = [p["path"] for p in sampled]
                pick_class = {p["path"]: p["class"] for p in sampled}
                report["leaf_path_classes"] = dict(Counter(pick_class.values()))
                report["leaf_paths"] = len(picks)
                old_leaf = hm.leaf_field_series_many(path, picks, hold_to_newest=True)
                new_leaf = new_leaf_series(table, led, picks)
                for dp in picks:
                    pts = [(row[0], row[1]) for row in old_leaf.get(dp) or [] if _num(row[1]) or isinstance(row[1], bool)]
                    ser, by = new_leaf.get(dp, ([], {}))
                    matched = set()
                    contain("trends_leaf", dp, pts, ser, led, snap_keys, totals, missing, matched)
                    old_pos = [led.pos_of(t) for t, _v in pts]
                    classify_extras("trends_leaf", dp, ser, by, matched, led, snap_keys,
                                    min(old_pos) if old_pos else None, extras, examples,
                                    tag=pick_class.get(dp, ""))
                # ---- Changes: every row the old feed showed, for the sampled
                # paths' holders and every curated leaf; and a count over ALL paths
                old_ch = old_changes(hm, path)
                new_ch = new_changes(table.binding, led)
                merged = ctx["store"].merged
                wanted = {vh.target(merged, dp)["holder"] for dp in picks}
                wanted |= {vh.holder_spelling(".".join(("qubits", q) + rel))
                           for q in ctx["store"].qubit_names for rel in _VALUE_PATHS.values()}
                with hub_index.snapshot(table.binding) as (conn, index):
                    rows_cache = vh._Rows(conn, index)
                    for dp, pts in old_ch.items():
                        holder = vh.holder_spelling(dp)
                        full = holder in wanted
                        surface = "changes" if full else "changes_all_paths"
                        # a pointer leaf's number lives at its target: the
                        # holder the path named at that event (S7's rule)
                        per_pos = {}
                        for ts, value in pts:
                            pos = led.pos_of(ts)
                            h = holder if holder in led.paths and not _is_ptr_at(rows_cache, holder, pos) \
                                else vh.holder_at(rows_cache, dp, pos)[0]
                            per_pos.setdefault(h, []).append((ts, value))
                        for h, hpts in per_pos.items():
                            matched = set()
                            series = new_ch.get(h)
                            if series is None and h and h.rpartition(".")[0] in led.paths:
                                # an element of a long array: the Changes page
                                # shows the array's row; the element is read
                                # out of it (S7's per-event element rule)
                                totals[surface + ":long_array_element_read_from_its_array_row"] += len(hpts)
                                series = [(led.positions[row[0]["eid"]],
                                           None if row[3] == "gone" else row[2])
                                          for row in rows_cache.rows(h)]
                            elif h != holder:
                                totals[surface + ":pointer_leaf_shown_at_the_holder_it_named"] += len(hpts)
                            contain(surface, h, hpts, series or [], led, snap_keys,
                                    totals, missing, matched)
                            if full and h == holder:
                                old_pos = [led.pos_of(t) for t, _v in hpts]
                                classify_extras("changes", h, new_ch.get(h, []), {}, matched, led,
                                                snap_keys, min(old_pos) if old_pos else None,
                                                extras, examples)
                report.update(points=dict(sorted(totals.items())), extras=dict(sorted(extras.items())),
                              missing_examples=missing, unexplained_examples=examples)
                # ---- the metric meta's writer
                report["meta_writer"] = meta_check(sm, ctx, table, led, args.seed, evidence)
    finally:
        hub_index.close_readers()
        report["cleanup_done"] = base.remove_scratch(work, scratch, strict=False)
    if args.evidence:
        args.evidence.write_text(json.dumps(evidence, indent=1, default=str), encoding="utf-8")
    return report


def _is_ptr_at(rows, holder: str, pos: int) -> bool:
    v = rows.fold(holder, pos)
    return isinstance(v, str) and v.startswith(("#/", "#./", "#../"))


def sm_writes(sm, count: int, seed: int) -> dict:
    """*count* SM writes through the real doors (edit + apply-to-live as a
    named operator; every third one undone), so the meta check meets SM
    writers too."""
    r = sm.routes
    rng = random.Random(seed + 7)
    with sm.app.test_request_context():
        store = r._active_ctx()["store"]
        qubits = list(store.qubit_names)
    made = undone = 0
    for i in range(count):
        q = rng.choice(qubits)
        prop = rng.choice(["T1", "T2ramsey", "f_01"])
        dp = f"qubits.{q}.{prop}"
        with sm.app.test_request_context():
            cur = r._active_ctx()["store"].merged["qubits"][q].get(prop)
        if not _finite(cur):
            continue
        val = repr(float(cur) * (1.0 + 0.003 * (i + 1)))
        if sm.client.post("/field/edit", data={"dot_path": dp, "value": val}).status_code != 200:
            continue
        if sm.client.post("/state/apply-to-live", headers={"X-SM-Actor": "operator"}).status_code == 200:
            made += 1
            if i % 3 == 2 and sm.client.post("/undo", headers={"X-SM-Actor": "operator"}).status_code == 200:
                undone += 1
    return {"applied": made, "undone": undone}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("golden")
    g.add_argument("--archive", type=Path, required=True)
    g.add_argument("--label", default="Archive A")
    g.add_argument("--scratch", type=Path, required=True)
    g.add_argument("--report", type=Path, required=True)
    g.add_argument("--evidence", type=Path)
    g.add_argument("--runs", type=int, default=200)
    g.add_argument("--paths", type=int, default=200)
    g.add_argument("--seed", type=int, default=283)
    g.add_argument("--observed", type=int, default=20)
    g.add_argument("--sm-writes", type=int, default=6)
    g.add_argument("--window", choices=("newest", "patched"), default="patched")
    args = ap.parse_args()
    if not 1 <= args.runs <= 300:
        ap.error("--runs must be between 1 and 300 (a few hundred at most)")
    rep = golden(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("archive", "points", "extras", "meta_writer", "cleanup_done")
                      if k in rep}, indent=1, default=str))


if __name__ == "__main__":
    main()
