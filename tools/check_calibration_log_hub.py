"""S6 check (docs/281): the Calibration log's card sets, old against new.

Part A -- a copied run archive. The base revision renders every copied day
with a Param History built from those very runs (each run ingested as the
app's near-real-time path does), which is where its run cards read "what the
run wrote". This revision renders the same days from a ledger built over the
same copy. Every run card is compared field by field and row by row, and
every difference is put in a named class.

Part B (optional) -- a copied SM instance: chip, data folder and instance
(journal, undo journal, Param History snapshots, agent run records). Both
revisions render every day either has; write cards, agent runs and run cards
are compared.

All locations come from an untracked JSON configuration:

    {"scratch": "...", "base": "<a detached worktree of the base revision>",
     "archive": "<a run archive>", "days": 3, "runs_per_day": 30,
     "zone": "<IANA zone of the project>",
     "rig": {"chip": "...", "data": "...", "instance": "..."}}

Copies are ordinary independent files (never links); no source is written.

    python tools/check_calibration_log_hub.py <config.json>
"""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

FIELDS = ("node", "targets", "outcome", "status", "gate", "author", "because", "journal", "figures", "note")


# ----------------------------------------------------------------- copying

def _copy_archive(source: Path, archive: Path, days: int, per_day: int) -> dict:
    picked = sorted(p for p in source.iterdir() if p.is_dir() and len(p.name) == 10 and p.name[4] == "-")[:days]
    copied = size = 0
    for day in picked:
        folders = sorted((p for p in day.iterdir() if p.is_dir() and p.name.startswith("#")),
                         key=lambda p: int(p.name.split("_")[0][1:]))[:per_day]
        for folder in folders:
            target = archive / day.name / folder.name
            if not target.exists():
                shutil.copytree(folder, target, copy_function=shutil.copy2)
            copied += 1
            size += sum(p.stat().st_size for p in target.rglob("*") if p.is_file())
    return {"days": [d.name for d in picked], "runs": copied, "bytes": size}


def _chip_copy(archive: Path, chip: Path) -> None:
    """The chip = the earliest copied run's saved state, its data folder
    pointed at the copy (an ordinary file copy, then one key set)."""
    if chip.exists():
        return
    first = min(archive.glob("*/#*/quam_state"), key=lambda p: (p.parent.parent.name, int(p.parent.name.split("_")[0][1:])))
    shutil.copytree(first, chip)
    state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
    state.setdefault("extras", {})["data_folder"] = str(archive)
    (chip / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")


# --------------------------------------------------------------- old side

def _old_archive(cfg: dict, scratch: Path) -> None:
    """Runs in the BASE worktree: Param History from the copied runs, then
    every copied day through the base revision's build_day."""
    from quam_state_manager.core import story
    from quam_state_manager.core.dataset import DatasetStore
    from quam_state_manager.core.history import HistoryManager
    archive, chip, inst = scratch / "archive", scratch / "chip", scratch / "old-instance"
    ds = DatasetStore(archive)
    ds.list_runs()
    hm = HistoryManager(inst)
    runs = sorted(ds.runs.values(), key=lambda r: (story.run_epoch(r) or 0, r.run_id))
    started = time.perf_counter()
    for run in runs:
        folder = Path(run.folder_path)
        hm.ingest_run(chip, SimpleNamespace(
            quam_state_path=folder / "quam_state", run_id=run.run_id, experiment_name=run.experiment_name,
            folder_path=folder, date_str=folder.parent.name,
            timestamp=getattr(run, "created_at", None) or "", run_end=getattr(run, "run_end", None)))
    snapshots = sorted({s.run_id for s in hm.list_snapshots(chip) if s.run_id is not None})
    days = sorted({p.name for p in archive.iterdir() if p.is_dir()})
    out = {"snapshots": snapshots, "ingest_s": round(time.perf_counter() - started, 1),
           "days": {day: story.build_day(inst, "chipX", day, ds=ds, hm=hm, active_path=chip) for day in days}}
    (scratch / "old.json").write_text(json.dumps(out, default=str), encoding="utf-8")


def _render_rig(cfg: dict, scratch: Path, label: str) -> None:
    """Runs in either worktree: the copied instance's chip, every day either
    revision might show, through the page's own ``_build``."""
    from quam_state_manager.core import hub_sync
    from quam_state_manager.core.dataset import DatasetStore
    from quam_state_manager.web import journal_routes, routes
    from quam_state_manager.web.app import create_app
    rig = scratch / f"rig-{label}"
    app = create_app(testing=True, instance_path=str(rig / "inst"))
    app.config["HUB_SYNC_ON_OPEN"] = True
    ds = DatasetStore(rig / "data")
    client = app.test_client()
    with patch.object(routes, "_dataset_store", return_value=ds):
        assert client.post("/load", data={"folder": str(rig / "chip")}).status_code in (200, 302)
        with app.app_context():
            ctx = routes._active_ctx()
            chip_dir = ctx.get("hub_chip_dir")
        if chip_dir:
            deadline = time.monotonic() + 600
            while hub_sync.status(chip_dir)["state"] == "building":
                if time.monotonic() > deadline:
                    raise RuntimeError("the copied ledger did not finish building")
                time.sleep(0.2)
        days = _rig_days(rig, cfg["zone"])
        out = {}
        for day in sorted(days):
            with app.test_request_context(f"/journal/day?day={day}"):
                data = journal_routes._build(day)
            out[day] = {"history": data.get("history"), "cards": data["cards"], "unrecorded": data.get("unrecorded")}
    (scratch / f"rig-{label}.json").write_text(json.dumps(out, default=str), encoding="utf-8")
    if label == "new" and chip_dir:
        from quam_state_manager.core import hub_rules
        from quam_state_manager.core.hub_store import HubStore
        with HubStore(chip_dir) as store:
            eids = [r[0] for r in store.conn.execute("SELECT eid FROM events WHERE kind='run'")]
            flats = {eid: hub_rules.flatten(store.state_at(eid)) for eid in eids}
        (scratch / "rig-new-flats.json").write_text(json.dumps(flats, default=str), encoding="utf-8")


# ---------------------------------------------------------------- compare

def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _same(a, b):
    if _num(a) and _num(b):
        return a == b or (isinstance(a, float) and isinstance(b, float) and math.isnan(a) and math.isnan(b))
    return a == b


def _pointer_prefix(flat: dict, path: str) -> bool:
    """Does *path* walk through a pointer string in this state?"""
    parts = path.split(".")
    for i in range(1, len(parts) + 1):
        v = flat.get(".".join(parts[:i]))
        if isinstance(v, str) and v.startswith(("#/", "#../", "#./")):
            return True
    return False


def _array_parent(new_rows: dict, path: str) -> bool:
    parts = path.split(".")
    return any(isinstance(new_rows.get(".".join(parts[:i])), dict) and "_array" in new_rows[".".join(parts[:i])]
               for i in range(1, len(parts)))


def _classify_rows(old_rows, new_rows, flat_new, flat_prev, flat_snap_prev=None):
    """Per-path classes for one run whose two row lists both exist.

    *flat_snap_prev*: when the run just before this one has no Param History
    snapshot (it repeated an earlier state and was deduplicated), the state
    of the last run that does -- the predecessor the snapshot feed used."""
    old = {r["path"]: r for r in old_rows}
    new = {r["path"]: r for r in new_rows}
    classes = Counter()
    unexplained = []
    for path in old.keys() | new.keys():
        o, n = old.get(path), new.get(path)
        if o and n:
            if _same(o["new"], n["new"]) and (o.get("is_first") or _same(o["old"], n["old"])):
                classes["equal"] += 1
            elif (flat_snap_prev is not None and _same(o["new"], n["new"])
                  and _same(o["old"], flat_snap_prev.get(path))):
                classes["snapshot_feed_compared_against_an_earlier_run"] += 1
            else:
                classes["value_differs"] += 1
                unexplained.append({"path": path, "old": o, "new": n})
        elif n:
            value = n["new"] if n["op"] != "gone" else n["old"]
            if not _num(value):
                classes["ledger_only_non_numeric"] += 1      # the snapshot feed indexes numbers only
            elif n["op"] in ("add", "gone"):
                classes["ledger_only_key_added_or_removed"] += 1
            elif len(old_rows) >= 200:
                classes["snapshot_feed_capped_at_200"] += 1
            elif flat_snap_prev is not None and _same(flat_snap_prev.get(path), n["new"]):
                classes["snapshot_feed_compared_against_an_earlier_run"] += 1
            else:
                classes["unexplained_ledger_only"] += 1
                unexplained.append({"path": path, "new": n})
        else:
            if _array_parent(new, path):
                classes["snapshot_feed_array_element"] += 1   # the ledger keeps a long array as one value
            elif _pointer_prefix(flat_new, path) or _pointer_prefix(flat_prev, path):
                classes["snapshot_feed_followed_pointer"] += 1  # the ledger records the holder
            else:
                classes["unexplained_snapshot_only"] += 1
                unexplained.append({"path": path, "old": o})
    return classes, unexplained


def _part_a(cfg: dict, scratch: Path, base: Path) -> dict:
    from quam_state_manager.core import hub_build, hub_index, hub_rules
    from quam_state_manager.core.dataset import DatasetStore
    from quam_state_manager.core.hub_store import HubStore
    from quam_state_manager.web import journal_routes, routes
    from quam_state_manager.web.app import create_app
    archive = scratch / "archive"
    copy = _copy_archive(Path(cfg["archive"]), archive, int(cfg.get("days", 3)), int(cfg.get("runs_per_day", 30)))
    _chip_copy(archive, scratch / "chip")
    if not (scratch / "old.json").exists():
        subprocess.run([sys.executable, str(Path(__file__).resolve()), str(Path(cfg["_config"]).resolve()), "--old"],
                       cwd=base, check=True, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
                                                      QUALIBRATE_CONFIG_FILE=str(scratch / "no-qualibrate-config.toml")))
    old = json.loads((scratch / "old.json").read_text(encoding="utf-8"))
    if not (scratch / "ledger" / "ledger.sqlite").exists():
        hub_build.build(archive, scratch / "ledger")
    ds = DatasetStore(archive)
    app = create_app(testing=True, instance_path=str(scratch / f"new-instance-{time.time_ns()}"))
    table, cards_seen, row_classes, unexplained = Counter(), Counter(), Counter(), []
    perf = {}
    with HubStore(scratch / "ledger") as store:
        context = hub_index.context(store, zone=cfg["zone"])
        with patch.object(journal_routes, "_ledger_context", return_value=context), \
             patch.object(journal_routes, "_chip_name", return_value="chipX"), \
             patch.object(journal_routes, "_agent_chip_key", return_value="chipX"), \
             patch.object(routes, "_dataset_store", return_value=ds):
            client = app.test_client()
            busiest = max(old["days"], key=lambda d: len(old["days"][d]["cards"]))
            render = lambda: client.get(f"/journal/day?day={busiest}").get_data(as_text=True)
            t0 = time.perf_counter(); html = render(); cold = (time.perf_counter() - t0) * 1000
            warm, uncached = _warm(app, render)
            perf = {"day": busiest, "runs": len(old["days"][busiest]["cards"]), "cold_ms": round(cold, 1),
                    "warm_ms": warm, "warm_fragment_cache_cleared_ms": uncached, "html_bytes": len(html.encode())}
            for day, before in old["days"].items():
                with app.test_request_context():
                    after = journal_routes._build(day, filters={"author": "", "q": ""})
                a = {c["run_id"]: c for c in before["cards"] if c["kind"] == "run"}
                b = {c["run_id"]: c for c in after["cards"] if c["kind"] == "run"}
                for kind in ("write", "agent_run"):
                    cards_seen[f"old_{kind}"] += sum(c["kind"] == kind for c in before["cards"])
                    cards_seen[f"new_{kind}"] += sum(c["kind"] == kind for c in after["cards"])
                for rid in set(a) ^ set(b):
                    table["unexplained_run_set"] += 1
                    unexplained.append({"day": day, "run_only_in": "old" if rid in a else "new", "run_id": rid})
                for rid in sorted(set(a) & set(b)):
                    cards_seen["runs_compared"] += 1
                    o, n = a[rid], b[rid]
                    for key in FIELDS:
                        if o.get(key) != n.get(key):
                            table["unexplained_field"] += 1
                            unexplained.append({"day": day, "run_id": rid, "field": key,
                                                "old": o.get(key), "new": n.get(key)})
                    eid = n.get("eid")
                    if n.get("first_state_n"):
                        table["first_state_in_the_history"] += 1
                    elif not o["writes"] and not n["writes"]:
                        table["no_change_either_side"] += 1
                    elif not o["writes"]:
                        table["gains_exact_rows_no_snapshot" if rid not in old["snapshots"]
                              else "gains_exact_rows_snapshot_feed_empty"] += 1
                    elif not n["writes"]:
                        table["unexplained_rows_lost"] += 1
                        unexplained.append({"day": day, "run_id": rid, "rows_lost": o["writes"][:5]})
                    else:
                        pos = store.conn.execute("SELECT ord FROM events WHERE eid=?", (eid,)).fetchone()[0]
                        prev = store.conn.execute("SELECT eid FROM events WHERE ord<? ORDER BY ord DESC LIMIT 1",
                                                  (pos,)).fetchone()
                        flat_new = hub_rules.flatten(store.state_at(eid))
                        flat_prev = hub_rules.flatten(store.state_at(prev[0])) if prev else {}
                        flat_snap_prev = None
                        prev_run = store.conn.execute("SELECT run_id FROM events WHERE eid=?", (prev[0],)).fetchone() if prev else None
                        if prev_run is not None and prev_run[0] not in old["snapshots"]:
                            snap_prev = next((r[0] for r in store.conn.execute(
                                "SELECT eid, run_id FROM events WHERE ord<? ORDER BY ord DESC", (pos,))
                                if r[1] in old["snapshots"]), None)
                            flat_snap_prev = hub_rules.flatten(store.state_at(snap_prev)) if snap_prev else {}
                        classes, odd = _classify_rows(o["writes"], n["writes"], flat_new, flat_prev, flat_snap_prev)
                        row_classes.update(classes)
                        if odd:
                            table["unexplained_rows"] += 1
                            unexplained.extend(dict(x, day=day, run_id=rid) for x in odd[:10])
                        elif any(k != "equal" for k in classes):
                            table["rows_differ_only_by_expected_row_classes"] += 1
                        else:
                            table["rows_identical"] += 1
    hub_index.close_readers()
    return {"copy": copy, "snapshots": len(old["snapshots"]), "old_ingest_s": old.get("ingest_s"),
            "cards": dict(cards_seen), "run_classes": dict(table), "row_classes": dict(row_classes),
            "unexplained": unexplained[:50], "performance": perf}


def _rig_days(rig: Path, zone: str) -> list[str]:
    """Every day either revision could show: data-folder days, journal days,
    agent-record days (each with its neighbours) and today, in *zone*."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(zone)
    days = {p.name for p in (rig / "data").iterdir() if p.is_dir() and len(p.name) == 10}
    days |= {p.stem for p in (rig / "inst" / "journal").rglob("*.md") if len(p.stem) == 10}
    for path in (rig / "inst" / "agent_runs").rglob("*.json*"):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            ts = rec.get("ended") or rec.get("ts") if isinstance(rec, dict) else None
            if ts:
                days.add(datetime.fromtimestamp(float(ts), tz).strftime("%Y-%m-%d"))
    days.add(datetime.now(tz).strftime("%Y-%m-%d"))
    wide = set()
    for day in days:
        d = datetime.strptime(day, "%Y-%m-%d")
        wide |= {(d + timedelta(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)}
    return sorted(wide)


def _rig_copy(cfg: dict, scratch: Path, label: str) -> None:
    """One independent copy of the rig per revision (each writes its own
    working copy and ledger into its instance)."""
    rig = scratch / f"rig-{label}"
    if rig.exists():
        return
    source = cfg["rig"]
    for name in ("chip", "data", "instance"):
        shutil.copytree(source[name], rig / ("inst" if name == "instance" else name), copy_function=shutil.copy2)
    state = json.loads((rig / "chip" / "state.json").read_text(encoding="utf-8"))
    state.setdefault("extras", {})["data_folder"] = str(rig / "data")
    (rig / "chip" / "state.json").write_text(json.dumps(state, indent=1), encoding="utf-8")
    # The records are keyed by the live folder's path; the copy is another path.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from quam_state_manager.core import working_copy
    old_key, new_key = working_copy.key_for(Path(source["chip"])), working_copy.key_for(rig / "chip")
    for path in list((rig / "inst" / "agent_runs").rglob("*.json*")):
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace(f'"{old_key}"', f'"{new_key}"'), encoding="utf-8")
    ws = rig / "inst" / "working_state"
    for path in list(ws.glob(f"{old_key}*")):
        path.rename(ws / path.name.replace(old_key, new_key))
    # The copy predates the project time zone (docs/263): name the zone the
    # rig's runs were made in, as the landing page asks a person to.
    from quam_state_manager.core import project_time
    project_time.set_zone(rig / "inst", "rig", cfg["zone"])


def _part_b(cfg: dict, scratch: Path, base: Path) -> dict:
    here = Path(__file__).resolve().parents[1]
    for label, tree in (("old", base), ("new", here)):
        _rig_copy(cfg, scratch, label)
        if not (scratch / f"rig-{label}.json").exists():
            subprocess.run([sys.executable, str(Path(__file__).resolve()), str(Path(cfg["_config"]).resolve()),
                            "--rig", label], cwd=tree, check=True,
                           env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1",
                                    QUALIBRATE_CONFIG_FILE=str(scratch / "no-qualibrate-config.toml")))
    old = json.loads((scratch / "rig-old.json").read_text(encoding="utf-8"))
    new = json.loads((scratch / "rig-new.json").read_text(encoding="utf-8"))
    table, detail = Counter(), []
    runs_after = sorted((c for d in new.values() for c in d["cards"] if c["kind"] == "run"), key=lambda r: r["ts"])
    flats = json.loads((scratch / "rig-new-flats.json").read_text(encoding="utf-8"))
    flats = {int(k): v for k, v in flats.items()}
    recorded = {}
    for line in (scratch / "rig-new" / "inst" / "agent_runs" / "index.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("run_id") is not None:
            recorded[int(rec["run_id"])] = rec.get("actor")
    for day in sorted(old):
        o, n = old[day]["cards"], new[day]["cards"]
        ro = {c["run_id"]: c for c in o if c["kind"] == "run"}
        rn = {c["run_id"]: c for c in n if c["kind"] == "run"}
        for rid in set(ro) ^ set(rn):
            table["unexplained_run_set"] += 1
            detail.append({"day": day, "run_only_in": "old" if rid in ro else "new", "run_id": rid})
        for rid in set(ro) & set(rn):
            for key in FIELDS:
                if (key == "author" and ro[rid].get(key) == "unknown"
                        and rn[rid].get(key) == recorded.get(rid)):
                    # SM ran it: the record is keyed by the working-copy key,
                    # which the base looked up under the display name.
                    table["sm_agent_run_now_attributed"] += 1
                elif ro[rid].get(key) != rn[rid].get(key):
                    table["unexplained_field"] += 1
                    detail.append({"day": day, "run_id": rid, "field": key, "old": ro[rid].get(key),
                                   "new": rn[rid].get(key)})
            if rn[rid].get("first_state_n"):
                table["run_first_state_in_the_history"] += 1
            elif not ro[rid]["writes"] and rn[rid]["writes"]:
                table["run_gains_exact_rows"] += 1
            elif ro[rid]["writes"] and rn[rid]["writes"]:
                table["run_rows_on_both_sides"] += 1
            elif not rn[rid]["writes"] and not ro[rid]["writes"]:
                table["run_no_change_either_side"] += 1
            else:
                table["unexplained_rows_lost"] += 1
        units = [c for c in o if c["kind"] == "write"]
        writes = [c for c in n if c["kind"] == "write"]
        for unit in units:
            # The pre-ledger undo journal recorded working-copy saves; whether
            # one reached the chip shows only in a later run's saved state.
            nxt = next((r for r in runs_after if r["ts"] > unit["ts"]), None)
            state = flats.get(nxt["eid"]) if nxt else None
            held = [e for e in unit["entries"] if state is not None and _same(state.get(e["path"]), e["new"])]
            detail.append({"day": day, "unit": unit["id"], "src": unit.get("src"), "entries": len(unit["entries"]),
                           "next_run": nxt and nxt["run_id"],
                           "next_run_state_holds_its_values": None if state is None else len(held)})
        noted = (new[day].get("unrecorded") or {}).get("n", 0)
        if units or noted:
            if noted == len(units):
                table["pre_ledger_undo_edit_counted_in_the_day_note_not_a_write_card"] += noted
            else:
                table["unexplained_undo_edit_count"] += abs(noted - len(units))
                detail.append({"day": day, "undo_units": len(units), "noted": noted})
        table["sm_write_cards_from_the_ledger"] += len(writes)
        table["undone_or_partly_undone_marked"] += sum(bool(c.get("undone") or c.get("partly_undone")) for c in writes)
        table["agent_run_without_folder_now_a_card"] += sum(c["kind"] == "agent_run" for c in n)
        table["unexplained_old_agent_cards"] += sum(c["kind"] == "agent_run" for c in o)
    return {"days": sorted(old), "classes": dict(table), "detail": detail[:60],
            "history": {d: new[d]["history"] for d in new}}


def _warm(app, render, n=5):
    """Five warm requests as served (the rendered fragment may be reused),
    then five with the fragment cache emptied first (build + render)."""
    warm, uncached = [], []
    for _ in range(n):
        t0 = time.perf_counter(); render(); warm.append(round((time.perf_counter() - t0) * 1000, 1))
    for _ in range(n):
        app.config.get("journal_day_html", {}).clear()
        t0 = time.perf_counter(); render(); uncached.append(round((time.perf_counter() - t0) * 1000, 1))
    return warm, uncached


def _synthetic(scratch: Path, app_instance: Path) -> dict:
    from quam_state_manager.core import hub_index, hub_rules
    from quam_state_manager.core.hub_store import HubStore
    from quam_state_manager.web import journal_routes, routes
    from quam_state_manager.web.app import create_app
    synthetic = scratch / "synthetic"
    if synthetic.exists():
        shutil.rmtree(synthetic)
    with HubStore(synthetic) as store:
        root = store.register_root(scratch / "synthetic-archive", "+00:00")
        previous = {}
        for i in range(10_000):
            doc = {"qubits": {"qA1": {"value": i, "f": 5.0e9 + i}}}
            flat = hub_rules.flatten(doc)
            instant = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i // 500, seconds=i % 500)
            fact = {"kind": "run", "ord": i + 1, "root_id": root, "rel_path": f"run-{i}",
                    "run_id": i + 1, "experiment": "scan", "t_utc_us": int(instant.timestamp() * 1e6),
                    "targets": '["qA1"]', "state_hash": str(i), "flags": 0}
            store.append(fact, hub_rules.diff(previous, flat), doc, flat)
            previous = flat
        context = hub_index.context(store, zone="UTC")
        app = create_app(testing=True, instance_path=str(app_instance))
        with patch.object(journal_routes, "_ledger_context", return_value=context), \
             patch.object(journal_routes, "_chip_name", return_value="chipX"), \
             patch.object(journal_routes, "_agent_chip_key", return_value="chipX"), \
             patch.object(routes, "_dataset_store", return_value=None):
            client = app.test_client()
            render = lambda: client.get("/journal/day?day=2026-01-10").get_data(as_text=True)
            t0 = time.perf_counter(); html = render(); cold = (time.perf_counter() - t0) * 1000
            warm, uncached = _warm(app, render)
    hub_index.close_readers()
    return {"runs": 10_000, "day_runs": 500, "cold_ms": round(cold, 1),
            "warm_ms": warm, "warm_fragment_cache_cleared_ms": uncached, "html_bytes": len(html.encode())}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--old", action="store_true")
    parser.add_argument("--rig")
    args = parser.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8-sig"))
    cfg["_config"] = str(args.config)
    scratch = Path(cfg["scratch"]).resolve()
    base = Path(cfg["base"]).resolve()
    # Never read the developer's own QUAlibrate configuration.
    os.environ["QUALIBRATE_CONFIG_FILE"] = str(scratch / "no-qualibrate-config.toml")
    tree = base if (args.old or args.rig == "old") else Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(tree))
    if args.old:
        return _old_archive(cfg, scratch)
    if args.rig:
        return _render_rig(cfg, scratch, args.rig)
    scratch.mkdir(parents=True, exist_ok=True)
    out = {"archive": _part_a(cfg, scratch, base)}
    if cfg.get("rig"):
        out["rig"] = _part_b(cfg, scratch, base)
    out["synthetic"] = _synthetic(scratch, scratch / f"synthetic-instance-{time.time_ns()}")
    (scratch / "results.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()
