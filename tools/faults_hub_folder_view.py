"""S10 C1.5 fault injection (pipeline step 5b): break one input per channel the
folder view watches, and read what each surface SAYS (its note line), then a
VERDICT line per fault.

    python tools/faults_hub_folder_view.py [--only NAME ...]

Every fault runs on generic synthetic folders in a fresh temporary directory
(nothing outside it is written). Exit status 1 when any verdict is FAIL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from quam_state_manager.core import hub, hub_index, hub_lanes, value_history as vh  # noqa: E402
from quam_state_manager.web import routes as routes_mod  # noqa: E402
import tests.test_hub_folder_view as t  # noqa: E402

FAULTS: dict = {}


def fault(fn):
    FAULTS[fn.__name__] = fn
    return fn


def notes_of(html: str) -> list[str]:
    out = []
    for m in re.finditer(r'<p class="vh-note[^"]*" data-note="([^"]+)">(.*?)</p>', html, re.S):
        out.append(f"{m.group(1)}: {re.sub(r'<[^>]+>', '', m.group(2)).strip()}")
    for m in re.finditer(r"<p class=\"vh-note vh-fallback\">(.*?)</p>", html, re.S):
        out.append("fallback: " + re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip())
    return out


def drawer_values(html: str) -> list[str]:
    return [r["value"] for r in t.rows(html)]


def lab_notes(ans: dict) -> list[str]:
    return [f"{n['code']}: {n['text']}" for n in vh.folder_notes(ans["ledger"].get("left_out"))]


def verdict(name: str, ok: bool, why: str) -> bool:
    print(f"VERDICT {name}: {'PASS' if ok else 'FAIL'} -- {why}", flush=True)
    return ok


# ----------------------------------------------------------------------

@fault
def sm_live_null(tmp: Path) -> bool:
    """An SM write whose journal line named no folder (sm_events.live NULL):
    after this folder's own history began it is hidden and counted as of a
    folder that is not recorded, never shown as this folder's."""
    lab = t.Lab(tmp)
    lab.folder("labA", t.chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    lab.folder("labB", t.chip_state())
    lab.write("labB", "qubits.qA1.T1", 9e-5)
    t._blank_live(lab)
    ans = lab.read(lab.view("labA"), "qubits.qA1.T1")
    pts = [p["value"] for p in ans["rows"]["k"]["points"]]
    for line in lab_notes(ans):
        print("  drawer note:", line)
    return verdict("sm_live_null", 9e-5 not in pts and ans["ledger"]["left_out"]["unknown"] == 1,
                   f"A's points {pts}; unknown counted {ans['ledger']['left_out']['unknown']}")


@fault
def snapshot_meta_pruned(tmp: Path) -> bool:
    """An observed state imported before folders were recorded whose snapshot
    meta is gone: no folder can be shown, so it is classified by the cut --
    never claimed by the folder on screen."""
    lab = t.Lab(tmp)
    lab.folder("labA", t.chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    snap = tmp / "snap"
    snap.mkdir()
    st = t.chip_state(t1=6e-5)
    (snap / "state.json").write_text(json.dumps(st), encoding="utf-8")
    (snap / "wiring.json").write_text(json.dumps(t.WIRING), encoding="utf-8")
    from quam_state_manager.core import history as hmod, hub_sync
    from quam_state_manager.core.hub_store import HubStore
    ts = hmod.source_stamp(t.now_us())
    with HubStore(lab.chip) as store:
        hub_sync.attach_observed(store, {"ts": ts, "t_us": t.now_us(), "trigger": "auto", "dir": str(snap),
                                         "live": None})
    hub_index.close_readers(lab.chip)
    ans = lab.read(lab.view("labA", snapshots={}), "qubits.qA1.T1")
    pts = [p["value"] for p in ans["rows"]["k"]["points"]]
    for line in lab_notes(ans):
        print("  drawer note:", line)
    return verdict("snapshot_meta_pruned", 6e-5 not in pts and ans["ledger"]["left_out"]["unknown"] == 1,
                   f"A's points {pts}; unknown {ans['ledger']['left_out']['unknown']}")


@fault
def takelive_backup_meta_gone(tmp: Path) -> bool:
    """A take-live backup snapshot whose working-copy meta is gone stands for
    no folder (Param History cannot say whose it is): an observed state of it
    is not this folder's."""
    from quam_state_manager.core import history as hmod
    hm = hmod.HistoryManager(str(tmp / "inst"))
    backup = tmp / "inst" / "working_state" / "chip-0000.takelive_backup" / "chip" / "quam_state"
    owner = hm.owner_of(str(backup))
    kind, _folder = hmod.source_kind(owner, hmod._source_key(str(tmp / "labA")))
    print(f"  owner of the backup: {owner}; kind: {kind}")
    lab = t.Lab(tmp / "lab")
    lab.folder("labA", t.chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    t._blank_live(lab)                       # the write's folder: what the backup stood for, now unknown
    view = lab.view("labA", cut="19700101_000000_0000")
    ans = lab.read(view, "qubits.qA1.T1")
    for line in lab_notes(ans):
        print("  drawer note:", line)
    return verdict("takelive_backup_meta_gone", owner is None and kind == "unknown"
                   and ans["rows"]["k"]["points"] == [],
                   f"owner {owner}, kind {kind}, points {ans['rows']['k']['points']}")


@fault
def corrupt_blob_at_a_seam(tmp: Path) -> bool:
    """A blob a seam's state needs is corrupt: the surface says the history
    could not be read -- never the ledger's global rows in its place."""
    env = t._two(tmp, b_data=True)
    with env["app"].app_context():
        chip = routes_mod._hub_chip_dir(routes_mod._active_ctx()["path"])
    hub_index.close_readers(chip)
    con = sqlite3.connect(chip / "ledger.sqlite")
    try:
        eid = con.execute("SELECT MAX(eid) FROM events WHERE kind='run'").fetchone()[0]
        shape = con.execute("SELECT shape_hash FROM events WHERE eid=?", (eid,)).fetchone()[0]
        con.execute("UPDATE blobs SET gz=? WHERE hash=?", (b"\x1f\x8b broken", shape))
        con.commit()
    finally:
        con.close()
    hub_lanes.clear_caches()
    hub_index.close_readers(chip)
    html = t._drawer(env, "qubits.qA1.T1")
    for line in notes_of(html):
        print("  drawer note:", line)
    vals = drawer_values(html)
    ok = "9e-05" not in html and "from the change ledger" not in html and "could not be read" in html
    return verdict("corrupt_blob_at_a_seam", ok, f"drawer rows {vals}")


@fault
def run_rediffed_after_the_view_was_cached(tmp: Path) -> bool:
    """A run folder rewritten after A's view was built: the next read
    rebuilds the view (the ledger moved), never serves the kept one."""
    lab = t.Lab(tmp)
    lab.folder("labA", t.chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    lab.folder("labB", t.chip_state())
    lab.write("labB", "qubits.qA1.T1", 9e-5)
    run = lab.run(t.chip_state(t1=3e-5))
    lab.sync("labA")
    before = [p["value"] for p in lab.points("labA", "qubits.qA1.T1")]
    st = t.chip_state(t1=4e-5)
    (run / "quam_state" / "state.json").write_text(json.dumps(st), encoding="utf-8")
    os.utime(run / "quam_state" / "state.json", (os.path.getmtime(run / "quam_state" / "state.json") + 5,) * 2)
    from quam_state_manager.core import hub_sync
    cs = hub_sync.sync_for(lab.chip)
    cs.folder = lab.key("labA")
    for rs in cs.roots.values():
        cs.sweep.extend((rs, rel) for rel in rs.known)
    hub_sync.catch_up(lab.chip, [str(lab.data)])
    after = [p["value"] for p in lab.points("labA", "qubits.qA1.T1")]
    print(f"  A's T1 before {before}, after the rewrite {after}")
    return verdict("run_rediffed_after_the_view_was_cached", before[-1] == 3e-5 and after[-1] == 4e-5
                   and 9e-5 not in after, "the rewritten run's value is read through a rebuilt view")


@fault
def two_processes_writing_a_and_b(tmp: Path) -> bool:
    """B's window is another process writing into the same ledger while A's
    view is kept: A's next read leaves B's write out and counts it."""
    lab = t.Lab(tmp)
    lab.folder("labA", t.chip_state())
    lab.write("labA", "qubits.qA1.T1", 2e-5)
    lab.folder("labB", t.chip_state())
    first = lab.read(lab.view("labA"), "qubits.qA1.T1")
    script = textwrap.dedent(f"""
        import sys, json
        sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        from quam_state_manager.core import hub, safe_io, working_copy
        hub.set_inline(True)
        live = Path({str(lab.lives['labB'])!r})
        wc = working_copy.load({str(lab.inst)!r}, live) or working_copy.create({str(lab.inst)!r}, live)
        s, w = safe_io.read_state_wiring(wc.working_folder)
        old = s["qubits"]["qA1"]["T1"]
        s["qubits"]["qA1"]["T1"] = 9e-5
        safe_io.write_state_wiring(wc.working_folder, s, w)
        p = hub.Pending(Path({str(lab.chip)!r}), "sm_apply", "human:other", "apply",
                        entries=[{{"path": "qubits.qA1.T1", "old": old, "new": 9e-5}}])
        working_copy.apply_to_live(wc, record=p, force=True)
        print("landed", p.landed)
    """)
    proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                          env=dict(os.environ, PYTHONUTF8="1"))
    print("  other process:", (proc.stdout + proc.stderr).strip().splitlines()[-1:])
    hub.project(hub.Hub.for_chip(lab.chip))
    ans = lab.read(lab.view("labA"), "qubits.qA1.T1")
    pts = [p["value"] for p in ans["rows"]["k"]["points"]]
    for line in lab_notes(ans):
        print("  drawer note:", line)
    return verdict("two_processes_writing_a_and_b", "landed True" in proc.stdout and 9e-5 not in pts
                   and ans["ledger"]["left_out"]["parallel"] == 1 and first["ledger"].get("left_out") is None,
                   f"A's points {pts}")


@fault
def link_undone_by_a_different_decision(tmp: Path) -> bool:
    """C2's unlink records ``different`` for the root: the folder's view
    leaves that root's runs out at its next read."""
    env = t._two(tmp, b_data=True)
    from quam_state_manager.core.history import save_chip_decision
    with env["app"].app_context():
        chip = routes_mod._hub_chip_dir(routes_mod._active_ctx()["path"])
        key = routes_mod._hub_root_key(str(env["data"]))
        save_chip_decision(env["app"].instance_path, chip.name, f"root:{key}", "different")
    html = t._drawer(env, "qubits.qA1.T1")
    for line in notes_of(html):
        print("  drawer note:", line)
    runs = [r for r in t.rows(html) if r["prov"].startswith("run") or r["prov"] == "first_record"]
    return verdict("link_undone_by_a_different_decision", not runs and 'data-note="unlinked_roots"' in html,
                   f"run rows left {len(runs)}")


@fault
def param_history_unreadable(tmp: Path) -> bool:
    """Param History cannot be read: no cut can be shown, so every other
    folder's row counts as parallel (left out, counted), never this folder's."""
    env = t._two(tmp, b_data=True)
    t._load(env["client"], env["b"])
    from quam_state_manager.core import history as hmod
    real = hmod.HistoryManager.snapshot_sources

    def boom(self, *a, **k):
        raise OSError("history unreadable (fault)")
    hmod.HistoryManager.snapshot_sources = boom
    try:
        routes_mod._HUB_FOLDER_VIEWS.clear()          # the kept view was derived before the fault
        html = t._drawer(env, "qubits.qA1.f_01")
    finally:
        hmod.HistoryManager.snapshot_sources = real
    for line in notes_of(html):
        print("  drawer note:", line)
    return verdict("param_history_unreadable", ">from labA/quam_state<" not in html
                   and 'data-note="other_folders"' in html,
                   "A's write (earlier when the cut is known) is left out and counted")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    hub.set_inline(True)
    ok = True
    for name, fn in FAULTS.items():
        if args.only and name not in args.only:
            continue
        print(f"== {name}: {fn.__doc__.strip().splitlines()[0]}", flush=True)
        tmp = Path(tempfile.mkdtemp(prefix="s10c15_fault_"))
        hub_lanes.clear_caches()
        try:
            ok = fn(tmp) and ok
        except Exception as exc:  # noqa: BLE001 -- a crashed fault is a FAIL, said
            ok = verdict(name, False, f"raised {type(exc).__name__}: {exc}") and ok
        finally:
            hub_index.close_readers()
            shutil.rmtree(tmp, ignore_errors=True)
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
