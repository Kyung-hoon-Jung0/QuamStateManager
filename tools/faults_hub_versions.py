"""S9 fault injection (docs/284): what the Versions panel, State History and
the version doors SAY and DO when the ledger cannot hand a version over.

Every fault goes through the real routes (Flask test client) on a synthetic
chip in a scratch folder. The real mechanism is used wherever one exists: a
blob deleted from the ledger, a run folder deleted and swept, a run saved by
another chip identity, a ledger deleted and rebuilt with an older run added
(its ids shift), an SM write projected without its bytes (the restart path:
``Hub.record`` + ``landed()`` with no post pair), a ledger file overwritten
with junk, another read holding the chip's read connection (preparing). Only
"building" is injected at the status seam (``hub_sync.status``), as S7/S8 did.

For each fault the report holds, per surface: the panel row (listed or not,
its words, which buttons it offers), the panel's notes, the State History
row, the Diff and Compare answers, and the Stage / Restore-live doors' status
and words -- with the live files, the snapshot count and the SM journal read
before and after, so a refusal is proven to have written nothing.

    python tools/faults_hub_versions.py --scratch <dir> --report <json>
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quam_state_manager.core import hub, hub_index, hub_sync, hub_versions  # noqa: E402
from quam_state_manager.core.hub_store import HubStore  # noqa: E402
from tests.test_hub_versions import WIRING, chip_state, run  # noqa: E402


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


class Env:
    """Three runs in a declared data folder (#1 genesis, #2 T1, #3 f_01), the
    live chip holding #3, an app whose ledger is kept current inline."""

    def __init__(self, root: Path, *, sm_writes: int = 0):
        from quam_state_manager.web.app import create_app
        from quam_state_manager.web import routes
        self.root = root
        self.data, self.live = root / "data", root / "chips" / "live"
        self.states = [chip_state(), chip_state(t1=3.0e-5), chip_state(t1=3.0e-5, f01=5.1e9)]
        self.folders = [run(self.data, i + 1, s) for i, s in enumerate(self.states)]
        self.live.mkdir(parents=True)
        current = json.loads(json.dumps(self.states[2]))
        current["extras"]["data_folder"] = str(self.data)
        (self.live / "state.json").write_text(json.dumps(current), encoding="utf-8")
        (self.live / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
        self.app = create_app(testing=True, instance_path=str(root / "inst"))
        self.app.config["HUB_SYNC_ON_OPEN"] = True
        self.client = self.app.test_client()
        self.routes = routes
        assert self.client.post("/load", data={"folder": str(self.live)}).status_code in (200, 302)
        with self.app.app_context():
            self.chip_dir = Path(routes._hub_chip_dir(routes._active_ctx()["path"]))
        for k in range(sm_writes):
            self.edit_apply(f"{4 + k}e-05")

    def edit_apply(self, value: str) -> None:
        c = self.client
        assert c.post("/field/edit", data={"dot_path": "qubits.qA1.T1", "value": value}).status_code == 200
        r = c.post("/state/apply-to-live", headers={"X-SM-Actor": "operator"})
        assert r.status_code == 200, r.get_data(as_text=True)[:300]

    def events(self) -> list[dict]:
        with HubStore(self.chip_dir) as store:
            return [dict(r) for r in store.conn.execute("SELECT * FROM events ORDER BY ord")]

    def ref(self, *, run_id=None, kind=None, nth=0) -> str:
        evs = [e for e in self.events()
               if (run_id is None or e["run_id"] == run_id) and (kind is None or e["kind"] == kind)]
        return hub_versions.ref_of(evs[nth])

    def sweep(self) -> None:
        cs = hub_sync.sync_for(self.chip_dir)
        cs.request(full=True)
        hub_sync._kick(cs)

    def footprint(self) -> tuple:
        """What a refused door must not touch: the live bytes, the snapshot
        folders, the SM journal."""
        live = tuple((self.live / n).read_bytes() for n in ("state.json", "wiring.json"))
        snaps = sorted(p.name for p in self.chip_dir.iterdir() if (p / "meta.json").is_file())
        journal = self.chip_dir / "events.jsonl"
        return live, tuple(snaps), journal.read_bytes() if journal.is_file() else b""

    def working(self) -> bytes:
        ctx = self.app.config["contexts"][self.app.config["active_context"]]
        return json.dumps(ctx["store"].merged, sort_keys=True, default=str).encode("utf-8")


def _panel_row(html: str, ref: str) -> dict:
    m = re.search(r'<li class="state-version-row[^"]*">((?:(?!</li>).)*?value="%s"(?:(?!<li ).)*?)</li>'
                  % re.escape(ref), html, re.S)
    if not m:
        return {"listed": False}
    row = m.group(1)
    diff = re.search(r'<button[^>]*class="btn-xs outline sv-diff"[^>]*>', row)
    return {"listed": True, "words": _text(row)[:400],
            "diff_offered": bool(diff) and "disabled" not in diff.group(0),
            "stage_offered": "sv-stage" in row, "restore_offered": "sv-restore" in row}


def _history_row(html: str, ref: str) -> dict:
    m = re.search(r'<div class="sh-entry[^"]*" data-ts="%s">(.*?)<div class="sh-entry-actions">(.*?)</div>\s*</div>'
                  % re.escape(ref), html, re.S)
    if not m:
        return {"listed": False}
    return {"listed": True, "words": _text(m.group(1))[:400],
            "diff_offered": "View changes vs current" in m.group(2),
            "stage_offered": "/stage?" in m.group(2), "restore_offered": "/restore-live?" in m.group(2)}


# S10 C7: surface note classes -> shared note class, extract the current verdict text.
def _notes(html: str) -> list[str]:
    return [_text(t) for t in re.findall(r'<p class="vh-note\b[^"]*"[^>]*>(.*?)</p>', html, re.S)]


def probe(e: Env, ref: str, other: str, *, doors: bool = True) -> dict:
    c = e.client
    out: dict = {}
    panel = c.get("/state/versions").get_data(as_text=True)
    src = re.search(r'data-source="(\w+)"', panel)
    out["panel"] = {"source": src.group(1) if src else None, "notes": _notes(panel),
                    "row": _panel_row(panel, ref)}
    page = c.get("/state-history").get_data(as_text=True)
    out["state_history"] = {"notes": _notes(page), "row": _history_row(page, ref)}
    r = c.get(f"/state/versions/{ref}/diff")
    body = r.get_data(as_text=True)
    out["diff"] = {"status": r.status_code,
                   "words": _text(body)[:240] if ("Diff failed" in body or r.status_code != 200)
                   else f"{body.count('review-row diff-row-')} differing values shown"}
    r = c.get(f"/diff/versions?ts={other}&ts={ref}&chip_key={e.chip_dir.name}",
              headers={"HX-Request": "true"})
    body = r.get_data(as_text=True)
    out["compare"] = {"status": r.status_code,
                      "words": (f"table, {body.count('<tr') - 1} rows" if "vc-table" in body
                                else _text(m.group(1)) if (m := re.search(r'<p class="vc-error">(.*?)</p>', body, re.S))
                                else _text(body)[:240])}
    if not doors:
        return out
    for action in ("restore-live", "stage"):
        before, wc = e.footprint(), e.working()
        r = c.post(f"/state-history/{ref}/{action}?chip_key={e.chip_dir.name}",
                   headers={"X-SM-Actor": "operator"})
        after = e.footprint()
        out[action] = {"status": r.status_code, "words": _text(r.get_data(as_text=True))[:300],
                       "live_unchanged": before[0] == after[0],
                       "no_new_snapshot": before[1] == after[1],
                       "journal_unchanged": before[2] == after[2],
                       "working_unchanged": wc == e.working()}
    return out


def scenarios(scratch: Path) -> list[dict]:
    out = []
    n = [0]

    def fresh(**kw) -> Env:
        n[0] += 1
        root = scratch / f"f{n[0]}"
        if root.exists():
            shutil.rmtree(root)
        return Env(root, **kw)

    def case(name, how, expected, result):
        out.append({"fault": name, "injected_by": how, "expected": expected, **result})
        print("##", name, flush=True)

    # 1a. a missing blob: the shape blob a run's merged document is rebuilt from
    e = fresh()
    ref, other = e.ref(run_id=2), e.ref(run_id=1)
    with HubStore(e.chip_dir) as store:
        sh = store.conn.execute("SELECT shape_hash FROM events WHERE run_id=2").fetchone()[0]
        shared = store.conn.execute("SELECT COUNT(*) FROM events WHERE shape_hash=?", (sh,)).fetchone()[0]
        store.conn.execute("DELETE FROM blobs WHERE hash=?", (sh,))
        store.conn.commit()
    case("missing blob (a run's shape)",
         f"the shape blob of run #2 deleted from the ledger (shared by {shared} events)",
         "Diff/Compare refuse with 'Missing ledger blob'; Stage/Restore stay exact from the run's own "
         "saved files (verified against the raw hash)", probe(e, ref, other))

    # 1b. a missing blob: the pair an SM write kept (its anchor)
    e = fresh(sm_writes=2)
    sm_refs = [hub_versions.ref_of(ev) for ev in e.events() if ev["kind"] == "sm_apply"]
    with HubStore(e.chip_dir) as store:
        first = store.conn.execute("SELECT e.eid, a.hash FROM events e JOIN sm_anchors a USING(eid) "
                                   "WHERE e.kind='sm_apply' ORDER BY e.ord LIMIT 1").fetchone()
        store.conn.execute("DELETE FROM blobs WHERE hash=?", (first[1],))
        store.conn.commit()
    res = probe(e, sm_refs[0], e.ref(run_id=1))
    res["the_write_replayed_from_it"] = probe(e, sm_refs[1], e.ref(run_id=1), doors=False)
    case("missing blob (an SM write's kept pair)",
         "the anchor blob of the first of two SM applies deleted (the second replays from it)",
         "Diff and Stage/Restore refuse with 'Missing ledger blob'; nothing written", res)

    # 2. SOURCE_GONE: the run folder deleted, the sync's sweep flags it
    e = fresh()
    ref = e.ref(run_id=1)
    shutil.rmtree(e.folders[0])
    e.sweep()
    flags = {ev["run_id"]: ev["flags"] for ev in e.events() if ev["kind"] == "run"}
    case("SOURCE_GONE", f"run #1's folder deleted, full sweep (flags now {flags[1]})",
         "Diff/Compare read the ledger's merged document; Stage/Restore refuse 'SOURCE_GONE' before "
         "any confirm, backup or write", probe(e, ref, e.ref(run_id=3)))

    # 3. an uncertain chip: a run saved by another chip identity
    e = fresh()
    run(e.data, 4, chip_state(t1=9.0e-5, f01=5.1e9, name="another-device"))
    with e.app.app_context():
        hub_sync.on_roots_moved([str(e.data)])
    ev4 = [ev for ev in e.events() if ev["run_id"] == 4][0]
    ref = hub_versions.ref_of(ev4)
    case("an uncertain chip", f"run #4 saved under another chip name (flags {ev4['flags']})",
         "not listed; a note counts it; a direct Diff/Stage/Restore of its id refuses 'chip identity is "
         "uncertain'", probe(e, ref, e.ref(run_id=1)))

    # 4. a stale event id after a ledger rebuild
    e = fresh()
    old_ref = e.ref(run_id=2)
    hub_index.close_readers()
    for h in list(hub.Hub._cache.values()):
        h.release()
    for name in ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm"):
        p = e.chip_dir / name
        if p.exists():
            p.unlink()
    run(e.data, 0, chip_state(t1=0.5e-5))          # an older run: every id after it shifts
    e.sweep()
    new_ref = e.ref(run_id=2)
    case("a stale event id after a ledger rebuild",
         f"ledger deleted, an older run #0 added, ledger rebuilt by the sync; run #2 was {old_ref}, now {new_ref}",
         "the old id refuses 'from an earlier build of the change history; reopen the list'; the panel "
         "lists the new ids", {**probe(e, old_ref, e.ref(run_id=1)),
                               "new_id_listed": _panel_row(e.client.get("/state/versions").get_data(as_text=True),
                                                           new_ref)["listed"]})

    # 5. a DERIVED event: an SM write projected without its bytes
    e = fresh()
    from quam_state_manager.core.working_copy import content_hash
    base = content_hash(json.loads((e.live / "state.json").read_bytes()),
                        json.loads((e.live / "wiring.json").read_bytes()))
    rec = hub.Hub.for_chip(e.chip_dir).record(
        "sm_apply", "human:operator", [{"path": "qubits.qA1.T1", "old": 3.0e-5, "new": 7.0e-5}],
        base, None, "apply", post_hash="unknown")
    rec.landed()
    ev = [x for x in e.events() if x["kind"] == "sm_apply"][-1]
    case("a DERIVED event", f"an SM write recorded and landed with no post bytes (flags {ev['flags']})",
         "Diff and Stage/Restore refuse 'DERIVED'; nothing written", probe(e, hub_versions.ref_of(ev), e.ref(run_id=1)))

    # 6. an unreadable ledger
    e = fresh()
    ref, other = e.ref(run_id=2), e.ref(run_id=1)
    hub_index.close_readers()
    for h in list(hub.Hub._cache.values()):
        h.release()
    for name in ("ledger.sqlite-wal", "ledger.sqlite-shm"):
        p = e.chip_dir / name
        if p.exists():
            p.unlink()
    (e.chip_dir / "ledger.sqlite").write_bytes(b"not a database " * 512)
    # S10 C7: snapshot path -> shared ledger renderer, describe the current mode table.
    case("an unreadable ledger", "ledger.sqlite overwritten with junk bytes",
         "both surfaces list legacy snapshots with a 'could not be read' note; a ledger id "
         "refuses at every door", probe(e, ref, other))

    # 7a. building (status seam)
    e = fresh()
    ref, other = e.ref(run_id=2), e.ref(run_id=1)
    real = hub_sync.status
    hub_sync.status = lambda _d: {"state": "building", "done": 1, "total": 3}
    try:
        res = probe(e, ref, other)
    finally:
        hub_sync.status = real
    case("building", "hub_sync.status = building 1 of 3",
         "legacy snapshots in the ledger renderer with 'being built (1 of 3 runs)'; no ledger event; every door says to "
         "try again when complete", res)

    # 7b. preparing: another read holds the chip's read connection
    e = fresh()
    ref, other = e.ref(run_id=2), e.ref(run_id=1)
    e.client.get("/state/versions")                       # the chip's reader exists
    slot = hub_index._slot(e.chip_dir)
    reader = hub_index._READERS.get(slot)
    held = threading.Event()
    release = threading.Event()

    def hold():
        with reader.lock:
            held.set()
            release.wait(60)
    t = threading.Thread(target=hold, daemon=True)
    t.start()
    held.wait(10)
    try:
        res = probe(e, ref, other, doors=False)
    finally:
        release.set()
        t.join(10)
    res["after_release"] = probe(e, ref, other, doors=False)["panel"]["source"]
    case("preparing", "another read holds the chip's read connection (hub_index reader lock)",
         "legacy snapshots in the ledger renderer with 'Preparing the change history'; the panel answers from the "
         "ledger once the other read is done", res)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scratch", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    os.environ.setdefault("QUALIBRATE_CONFIG_FILE", str(args.scratch / "absent.toml"))
    hub.set_inline(True)
    scratch = args.scratch.resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        rows = scenarios(scratch)
    finally:
        hub_index.close_readers()
        for h in list(hub.Hub._cache.values()):
            h.release()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(json.dumps(rows, indent=1)[:20000])


if __name__ == "__main__":
    main()
