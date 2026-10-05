"""S9 checks (docs/284) on disposable byte copies: the Versions panel and State
History read the change ledger, so what they show must be what the old
snapshot path showed.

``archive``: a window of a real run archive is byte-copied (``shutil.copy2``,
never a link) into ``--scratch``; SM opens a chip made from its newest run,
builds the ledger, backfills Param History (the OLD path's own copy of every
run's saved state), sees outside edits through ``auto`` captures and makes SM
writes through the real doors. Then:

* golden -- every Param History snapshot is matched to the ledger event that
  holds its state by an independent rule (a run by its own snapshot key, a
  capture by the ledger's import verdict, anything else by content hash), and
  the LEDGER REPLAY of that event (``hub_versions.document`` =
  ``HubStore.state_at``, what Diff and Compare now read) is compared with the
  snapshot's merged document -- typed-JSON equality and the S2 ``same`` rule
  reported separately. The exact pair Stage / Restore now take
  (``hub_versions.exact_pair``) is compared with the snapshot's two files.
  Every ledger event no snapshot shows is classified, and its replay is
  compared with the run's own saved files. The Versions read itself must list
  every snapshot the ledger does not hold and none it does.
* round trip -- Stage + Apply and Restore-live of one run version through the
  OLD id (its Param History snapshot) and the NEW id (its ledger version), on
  the same live chip: the bytes written must be identical, and each Revert
  last apply must give the baseline bytes back.

``history``: a rig's Param History, chip and data folder are byte-copied; SM
imports the snapshots that are not runs as observed states; the same golden
comparison runs.

Nothing here writes into a source: every copy lives under ``--scratch`` and is
removed at the end (``--keep`` leaves it). The report holds counts and generic
labels; ``--evidence`` (a scratch file) holds paths of any mismatch.

    python tools/check_hub_versions.py archive --archive <root> --label "Archive A" \
        --runs 200 --scratch <dir> --report <json> [--evidence <json>]
    python tools/check_hub_versions.py history --chip <chip> --history <chip history dir> \
        [--data <data folder>] --label "Rig history" --scratch <dir> --report <json>
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import shutil
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_hub_drawer as base  # noqa: E402
from quam_state_manager.core import hub, hub_index, hub_rules, hub_sync, hub_versions, run_time  # noqa: E402
from quam_state_manager.core.hub_store import CHIP_UNCERTAIN, SM_KINDS  # noqa: E402

ACTOR = "operator"


# ----------------------------------------------------------------------
# comparison
# ----------------------------------------------------------------------

def canon(doc) -> str:
    """Typed JSON text: key order is immaterial, 1 and 1.0 are not."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _walk(a, b, path, out, where="a scalar leaf"):
    if isinstance(a, dict) and isinstance(b, dict):
        for k in a.keys() | b.keys():
            if k not in a or k not in b:
                out["structure"].append(f"{path}.{k}".lstrip("."))
            else:
                _walk(a[k], b[k], f"{path}.{k}", out)
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out["structure"].append(path.lstrip(".") + "[len]")
        # S2 stores a scalar list longer than 16 as one array blob
        inner = ("an element of a long scalar array (an S2 array blob)"
                 if len(b) > 16 and all(not isinstance(v, (dict, list)) for v in b)
                 else "an element of a short list (a row of the changes table)")
        for i, (x, y) in enumerate(zip(a, b)):
            _walk(x, y, f"{path}.{i}", out, inner)
    elif not hub_rules.same(a, b):
        out["value"].append(path.lstrip("."))
    elif type(a) is not type(b):
        out["type"].append((path.lstrip("."), type(a).__name__, type(b).__name__, where))


def compare(got, want) -> dict:
    """``typed`` (canonical typed JSON equal), ``same`` (the S2 rule over the
    whole document), and where they part: type-only leaves (``int`` saved,
    ``float`` replayed, ...) with where they sit, and value / structure
    differences."""
    out = {"structure": [], "value": [], "type": []}
    _walk(got, want, "", out)
    return {"typed": canon(got) == canon(want), "same": not out["structure"] and not out["value"],
            "type_only": [f"{w} saved, {g} replayed: {where}" for _p, g, w, where in out["type"]],
            "type_paths": [p for p, _g, _w, _where in out["type"]][:5],
            "differ_paths": (out["structure"] + out["value"])[:10]}


def read_pair(folder: Path):
    state = json.loads((folder / "state.json").read_text(encoding="utf-8"))
    wiring = json.loads((folder / "wiring.json").read_text(encoding="utf-8"))
    return state, wiring


def _is_run_snapshot(m) -> bool:
    return bool(getattr(m, "kind", None) == "exp" or m.trigger == "experiment"
                or m.run_id is not None or m.experiment_folder_path)


def golden(sm, label: str, evidence: dict) -> dict:
    """The golden comparison on the chip *sm* has open (see the module doc)."""
    from quam_state_manager.core.working_copy import content_hash
    r = sm.routes
    with sm.app.test_request_context():
        ctx = r._active_ctx()
        hm = r._history()
        path = ctx["path"]
        chip_dir = Path(r._hub_chip_dir(path))
        snaps = hm.list_snapshots(path)
        srcs = hm.snapshot_sources(path, snaps)
    rep: dict = {"label": label, "param_history_snapshots": len(snaps)}
    tally = Counter()
    with hub_versions._ledger(chip_dir) as store:
        events = {row["eid"]: dict(row) for row in store.conn.execute("SELECT * FROM events ORDER BY ord")}
        order = [eid for eid in events]
        good = {eid for eid, e in events.items()
                if hub_versions.has_state(e) and not e["flags"] & CHIP_UNCERTAIN}
        try:
            observed = {row[0]: (row[1], row[2]) for row in
                        store.conn.execute("SELECT ts, outcome, eid FROM observed_snapshots")}
        except Exception:  # noqa: BLE001 -- no observed import yet
            observed = {}
    rep["ledger_events"] = len(events)
    rep["ledger_events_by_kind"] = dict(Counter(e["kind"] for e in events.values()))
    rep["state_bearing_events"] = len(good)
    rep["events_of_an_uncertain_chip_identity"] = sum(
        1 for e in events.values() if e["flags"] & CHIP_UNCERTAIN)
    run_key = {}
    for eid in order:
        e = events[eid]
        if e["kind"] == "run" and eid in good:
            run_key.setdefault(run_time.snapshot_key(e["t_utc_us"], e["run_id"]), eid)
    by_chash: dict = {}
    for eid in order:
        if eid in good and events[eid]["chash"]:
            by_chash.setdefault(events[eid]["chash"], []).append(eid)

    # -- every snapshot -> the event that holds its state (independent rule)
    mapped: dict[str, int] = {}
    how = Counter()
    unmapped_why = Counter()
    snap_hash: dict[str, str | None] = {}
    snap_pair: dict = {}
    for m in snaps:
        ts = m.timestamp
        folder = hm.snapshot_dir(path, ts)
        try:
            pair = read_pair(folder)
        except (OSError, ValueError):
            unmapped_why["snapshot_unreadable"] += 1
            continue
        snap_pair[ts] = pair
        digest = snap_hash[ts] = content_hash(*pair)
        lineage = (srcs.get(ts) or {}).get("lineage")
        eid = None
        if _is_run_snapshot(m):
            eid = run_key.get(ts)
            if eid is not None:
                how["run: its own snapshot key"] += 1
            else:
                cands = [x for x in by_chash.get(digest, ())
                         if events[x]["kind"] == "run" and events[x]["run_id"] == m.run_id]
                if cands:
                    eid = cands[0]
                    how["run: same run id, same content (a collision key)"] += 1
        else:
            outcome, oeid = observed.get(ts, (None, None))
            if outcome in ("added", "inserted") and oeid in good:
                eid = oeid
                how["capture: the ledger's own observed event"] += 1
            else:
                cands = by_chash.get(digest, ())
                if cands:
                    t_us = hub_sync.snapshot_instant_us(ts) or 0
                    eid = min(cands, key=lambda x: abs(events[x]["t_utc_us"] - t_us))
                    how[f"capture ({m.trigger}): content hash, nearest event"
                        + (f" [import verdict {outcome}]" if outcome else "")] += 1
        if eid is None:
            if _is_run_snapshot(m):
                why = "a run the ledger does not hold (its folder is not in the copied data)"
            elif lineage == "parallel":
                why = "another folder's snapshot (parallel lineage, never imported)"
            else:
                why = f"a capture ({m.trigger}) whose state no event holds"
            unmapped_why[why] += 1
            continue
        mapped[ts] = eid
    rep["snapshots_mapped"] = len(mapped)
    rep["mapped_by"] = dict(how)
    rep["snapshots_not_held"] = dict(unmapped_why)

    # -- the ledger replay (Diff / Compare) and the exact pair (Stage / Restore)
    doc_res = Counter()
    pair_res = Counter()
    type_only = Counter()
    by_kind = Counter()
    ev_examples = []
    files_differ = []
    for ts, eid in list(mapped.items()):
        e = events[eid]
        if e["kind"] != "run":
            continue
        try:
            ps, pw = hub_versions.exact_pair(chip_dir, hub_versions.ref_of(e))
        except hub_versions.Unavailable:
            continue
        state, wiring = snap_pair[ts]
        if not (hub_rules.same(ps, state) and hub_rules.same(pw, wiring)):
            # the run's saved files (verified against the raw hash the ledger
            # read) are not what Param History copied: the folder was
            # rewritten since, or the snapshot came from another copy of it.
            # The ledger holds the files; the snapshot's state is its own.
            files_differ.append(eid)
            del mapped[ts]
            unmapped_why["a run snapshot whose run's saved files differ from it now"] += 1
    rep["snapshots_mapped"] = len(mapped)
    rep["snapshots_not_held"] = dict(unmapped_why)
    for ts, eid in mapped.items():
        e = events[eid]
        ref = hub_versions.ref_of(e)
        state, wiring = snap_pair[ts]
        want = hub_rules.merged(state, wiring)
        try:
            got = hub_versions.document(chip_dir, ref)
        except hub_versions.Unavailable as exc:
            doc_res["refused: " + str(exc)[:60]] += 1
            continue
        c = compare(got, want)
        verdict = "typed_equal" if c["typed"] else "same_only" if c["same"] else "MISMATCH"
        doc_res[verdict] += 1
        by_kind[(e["kind"], verdict)] += 1
        type_only.update(c["type_only"])
        if verdict == "MISMATCH" and len(ev_examples) < 20:
            ev_examples.append({"snapshot": ts, "event": ref, "kind": e["kind"], "paths": c["differ_paths"]})
        try:
            ps, pw = hub_versions.exact_pair(chip_dir, ref)
        except hub_versions.Unavailable as exc:
            pair_res["refused: " + str(exc).split(":")[0][:40]] += 1
            continue
        if canon(ps) == canon(state) and canon(pw) == canon(wiring):
            pair_res["typed_equal (both files)"] += 1
        elif hub_rules.same(ps, state) and hub_rules.same(pw, wiring):
            pair_res["same_only"] += 1
        else:
            pair_res["MISMATCH"] += 1
            if len(ev_examples) < 40:
                ev_examples.append({"snapshot": ts, "event": ref, "pair": True,
                                    "state": compare(ps, state)["differ_paths"],
                                    "wiring": compare(pw, wiring)["differ_paths"]})
    rep["replay_vs_snapshot"] = dict(doc_res)
    rep["replay_vs_snapshot_by_kind"] = {f"{k}: {v}": n for (k, v), n in sorted(by_kind.items())}
    rep["type_only_leaves"] = dict(type_only)
    rep["exact_pair_vs_snapshot"] = dict(pair_res)

    # -- events no snapshot shows: classified, and their replay vs their files
    shown = set(mapped.values())
    snap_hashes = {h for h in snap_hash.values() if h}
    unshown = Counter()
    vs_files = Counter()
    for eid in order:
        if eid not in good or eid in shown:
            continue
        e = events[eid]
        if eid in files_differ:
            why = "a run whose Param History snapshot differs from its saved files now"
        elif e["kind"] in SM_KINDS:
            why = f"an SM write ({e['kind']}) Param History kept no copy of"
        elif e["chash"] and e["chash"] in snap_hashes:
            why = f"a {e['kind']} whose state a snapshot of another moment holds (Param History keeps one copy per content)"
        elif e["kind"] == "run":
            why = "a run Param History took no snapshot of"
        else:
            why = f"an {e['kind']} event with no snapshot"
        unshown[why] += 1
        if e["kind"] not in SM_KINDS:
            try:
                ps, pw = hub_versions.exact_pair(chip_dir, hub_versions.ref_of(e))
            except hub_versions.Unavailable as exc:
                vs_files["no saved files: " + str(exc).split(":")[0][:40]] += 1
                continue
            got = hub_versions.document(chip_dir, hub_versions.ref_of(e))
            c = compare(got, hub_rules.merged(ps, pw))
            vs_files["typed_equal" if c["typed"] else "same_only" if c["same"] else "MISMATCH"] += 1
            type_only.update(c["type_only"])
            if not c["same"] and len(ev_examples) < 60:
                ev_examples.append({"event": hub_versions.ref_of(e), "vs_files": c["differ_paths"]})
    rep["events_no_snapshot_shows"] = dict(unshown)
    rep["their_replay_vs_their_saved_files"] = dict(vs_files)
    rep["type_only_leaves"] = dict(type_only)

    # -- the Versions read: every snapshot the ledger does not hold is listed,
    # none it holds is listed twice
    h = hub_versions.hashes_for(chip_dir)
    hub_versions.read(chip_dir, snaps, limit=1)
    h.wait(600)
    t0 = time.perf_counter()
    res = hub_versions.read(chip_dir, snaps, limit=len(snaps) + len(events) + 10)
    rep["versions_read_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    rep["versions_mode"] = res["mode"]
    listed = {row["ref"] for row in res.get("rows", []) if row["legacy"]}
    held = set(mapped)
    rep["versions_rows"] = {"events": res.get("events"), "older_snapshots": res.get("legacy_total"),
                            "pending": res.get("pending")}
    rep["older_listed_but_held"] = sorted(listed & held)[:20]
    rep["older_not_listed_and_not_held"] = sorted(set(snap_pair) - listed - held)[:20]
    rep["older_listed_but_held_n"] = len(listed & held)
    rep["older_not_listed_and_not_held_n"] = len(set(snap_pair) - listed - held)
    evidence[label] = ev_examples
    return rep


# ----------------------------------------------------------------------
# round trip: the old id and the new id through the same doors
# ----------------------------------------------------------------------

def _live(chip: Path) -> tuple[bytes, bytes]:
    return tuple((chip / n).read_bytes() for n in ("state.json", "wiring.json"))


def round_trip(sm, chip: Path) -> dict:
    """Stage + Apply, then Restore-live, of one run version by its OLD id and
    by its NEW id; each followed by Revert last apply."""
    from quam_state_manager.core.working_copy import content_hash
    r, c = sm.routes, sm.client
    with sm.app.test_request_context():
        ctx = r._active_ctx()
        hm = r._history()
        path = ctx["path"]
        chip_dir = Path(r._hub_chip_dir(path))
        snaps = hm.list_snapshots(path)
    live_hash = content_hash(*(json.loads(b) for b in _live(chip)))
    with hub_versions._ledger(chip_dir) as store:
        runs = [dict(x) for x in store.conn.execute(
            "SELECT * FROM events WHERE kind='run' AND error IS NULL AND (flags & 4)=0 ORDER BY ord")]
    keys = {m.timestamp: m for m in snaps if _is_run_snapshot(m)}
    pick = None
    for e in reversed(runs[: max(1, len(runs) - 2)]):
        key = run_time.snapshot_key(e["t_utc_us"], e["run_id"])
        if key in keys and e["chash"] != live_hash:
            try:
                hub_versions.exact_pair(chip_dir, hub_versions.ref_of(e))
            except hub_versions.Unavailable:
                continue
            pick = (key, hub_versions.ref_of(e), e)
            break
    if pick is None:
        return {"skipped": "no run version with both ids whose state differs from live"}
    old_id, new_id, ev = pick
    key = chip_dir.name

    def post(url, ok=(200,)):
        resp = c.post(url, headers={"X-SM-Actor": ACTOR})
        assert resp.status_code in ok, (url, resp.status_code, resp.get_data(as_text=True)[:300])
        return resp

    def journal_tail():
        lines = (chip_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        last = json.loads([x for x in lines if '"kind"' in x][-1])
        return {k: last.get(k) for k in ("kind", "actor", "src")}

    def revert():
        with sm.app.test_request_context():
            pre = r._active_ctx()["last_apply"]["pre_ts"]
        post(f"/state-history/{pre}/stage?from=tray")
        post("/state/apply-to-live")
        return _live(chip)

    baseline = _live(chip)
    out = {"version": f"run #{ev['run_id']}", "old_id": "Param History snapshot", "new_id": "ledger version"}
    written: dict = {}
    for name, ident in (("old", old_id), ("new", new_id)):
        post(f"/state-history/{ident}/stage?chip_key={key}")
        post("/state/apply-to-live")
        written[(name, "stage")] = _live(chip)
        out[f"{name}_stage_record"] = journal_tail()
        out[f"{name}_stage_revert_gives_baseline"] = revert() == baseline
        before = sum(1 for p in chip_dir.iterdir() if (p / "meta.json").is_file())
        resp = c.post(f"/state-history/{ident}/restore-live?chip_key={key}", headers={"X-SM-Actor": ACTOR})
        if resp.status_code == 409 and "wiring" in resp.get_data(as_text=True):
            out[f"{name}_restore_topology_gate"] = True
            post(f"/state-history/{ident}/restore-live?force_pending=1&force_align=1&chip_key={key}")
        else:
            assert resp.status_code == 200, (ident, resp.status_code, resp.get_data(as_text=True)[:300])
        written[(name, "restore")] = _live(chip)
        out[f"{name}_restore_record"] = journal_tail()
        out[f"{name}_restore_backed_up_first"] = (
            sum(1 for p in chip_dir.iterdir() if (p / "meta.json").is_file()) > before)
        out[f"{name}_restore_revert_gives_baseline"] = revert() == baseline
    pair = hub_versions.exact_pair(chip_dir, new_id)
    for door in ("stage", "restore"):
        a, b = written[("old", door)], written[("new", door)]
        out[f"{door}_bytes_identical"] = a == b
        out[f"{door}_bytes"] = [len(a[0]), len(a[1])]
        got = tuple(json.loads(x) for x in b)
        out[f"{door}_live_equals_the_runs_files_typed"] = (canon(got[0]) == canon(pair[0])
                                                           and canon(got[1]) == canon(pair[1]))
    return out


# ----------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------

def cmd_archive(args) -> dict:
    import check_hub_chip_status as s8
    scratch = args.scratch.resolve()
    work = scratch / "golden_s9"
    base.remove_scratch(work, scratch)
    data, chip, inst = work / "data", work / "chip", work / "inst"
    t0 = time.perf_counter()
    copied = base.copy_runs(args.archive, data, args.runs)
    base.make_chip(copied[-1], chip, data)
    rep = {"archive": args.label, "runs_copied": len(copied),
           "runs_with_a_saved_pair": sum(1 for f in copied if all(p.is_file() for p in base.hub_build.state_paths(f))),
           "copy_s": round(time.perf_counter() - t0, 1)}
    evidence: dict = {}
    try:
        with base.sm_app(inst, chip, data, backfill=True) as sm:
            rep["load_and_ledger_s"] = round(sm.load_s, 1)
            rep["param_history_backfill_s"] = round(sm.backfill_s, 1)
            if args.observed:
                real_enum = base.hub_build.enumerate_runs

                def with_state(root):
                    runs, hint = real_enum(root)
                    return [x for x in runs if base.hub_build.state_paths(x.folder)[0].is_file()], hint
                base.hub_build.enumerate_runs = with_state
                try:
                    inj = base.inject_outside_edits(sm, chip, copied, args.observed, args.seed)
                finally:
                    base.hub_build.enumerate_runs = real_enum
                rep["outside_edits"] = {k: inj[k] for k in ("made", "captured", "import")}
            if args.sm_writes:
                rep["sm_writes"] = s8.sm_writes(sm, args.sm_writes, args.seed)
            rep["golden"] = golden(sm, args.label, evidence)
            rep["round_trip"] = round_trip(sm, chip)
    finally:
        hub_index.close_readers()
        if args.evidence:
            args.evidence.write_text(json.dumps(evidence, indent=1), encoding="utf-8")
        if not args.keep:
            rep["cleanup_done"] = base.remove_scratch(work, scratch, strict=False)
    return rep


def cmd_history(args) -> dict:
    """A rig's Param History, chip and data folder, copied; SM opens the copy,
    imports the non-run snapshots as observed states, and the golden runs."""
    scratch = args.scratch.resolve()
    work = scratch / "history_s9"
    base.remove_scratch(work, scratch)
    chip, inst, data = work / "chip", work / "inst", (work / "data" if args.data else None)
    t0 = time.perf_counter()
    shutil.copytree(args.chip, chip, copy_function=shutil.copy2)
    if args.data:
        shutil.copytree(args.data, data, copy_function=shutil.copy2)
    hist = inst / "history" / args.history.name
    shutil.copytree(args.history, hist, copy_function=shutil.copy2,
                    ignore=shutil.ignore_patterns("ledger.sqlite*", "events.jsonl", hub_versions.HASHES_FILE))
    # the rig's chip-name ladder, so the copy resolves to the copied history
    for name in ("_chip_aliases.json", "_fingerprints.json"):
        if (args.history.parent / name).is_file():
            shutil.copy2(args.history.parent / name, hist.parent / name)
    # only the data folder is re-pointed (at the copy); the wiring stays as it
    # was (its fingerprint is the chip's identity), and nothing here connects
    state = json.loads((chip / "state.json").read_text(encoding="utf-8"))
    extras = state.setdefault("extras", {})
    if data is not None:
        extras["data_folder"] = str(data)
    else:
        extras.pop("data_folder", None)
    (chip / "state.json").write_text(json.dumps(state, indent=4), encoding="utf-8")
    snaps_copied = sum(1 for p in hist.iterdir() if (p / "meta.json").is_file())
    rep = {"history": args.label, "snapshots_copied": snaps_copied,
           "copy_s": round(time.perf_counter() - t0, 1)}
    evidence: dict = {}
    try:
        with base.sm_app(inst, chip, data, backfill=False) as sm:
            with sm.app.test_request_context():
                chip_dir = Path(sm.routes._hub_chip_dir(sm.routes._active_ctx()["path"]))
            if chip_dir.resolve() != hist.resolve():
                raise SystemExit(f"the copied chip resolves to another history folder ({chip_dir.name})")
            cs = hub_sync.sync_for(chip_dir)
            cs.request(full=True)
            hub_sync._kick(cs)
            rep["observed_import"] = {k: v for k, v in cs.counts.items() if k.startswith("observed:")}
            rep["load_and_ledger_s"] = round(sm.load_s, 1)
            rep["golden"] = golden(sm, args.label, evidence)
    finally:
        hub_index.close_readers()
        if args.evidence:
            args.evidence.write_text(json.dumps(evidence, indent=1), encoding="utf-8")
        if not args.keep:
            rep["cleanup_done"] = base.remove_scratch(work, scratch, strict=False)
    return rep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("archive")
    a.add_argument("--archive", type=Path, required=True)
    a.add_argument("--runs", type=int, default=200)
    a.add_argument("--observed", type=int, default=10)
    a.add_argument("--sm-writes", type=int, default=6)
    a.add_argument("--seed", type=int, default=284)
    h = sub.add_parser("history")
    h.add_argument("--chip", type=Path, required=True)
    h.add_argument("--history", type=Path, required=True)
    h.add_argument("--data", type=Path)
    for p in (a, h):
        p.add_argument("--label", required=True)
        p.add_argument("--scratch", type=Path, required=True)
        p.add_argument("--report", type=Path, required=True)
        p.add_argument("--evidence", type=Path)
        p.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    args.scratch.mkdir(parents=True, exist_ok=True)
    home = args.scratch.resolve() / "home"
    home.mkdir(exist_ok=True)
    os.environ.update(QUALIBRATE_CONFIG_FILE=str(home / "absent.toml"), HOME=str(home),
                      USERPROFILE=str(home), SM_DISABLE_ENV_WARMUP="1")
    hub.set_inline(True)
    rep = cmd_archive(args) if args.cmd == "archive" else cmd_history(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps(rep, indent=1, default=str), flush=True)


if __name__ == "__main__":
    main()
