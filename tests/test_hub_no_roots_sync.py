"""S10 C1 -- a chip with no data folder gets its ledger and its observed states.

Before C1 a chip with no registered data folder was never kicked: its sync
status read ``ready`` with nothing looked at, no ledger was created until SM
wrote to the chip, and the states SM itself had seen (its Param History
snapshots) never reached the ledger -- every value surface drew the old
snapshot path instead.

Pins, on generic synthetic chips in ``tmp_path`` (each mutation-checked):

* the sync status says ``building`` until the first slice for the chip ran;
* a chip with no data folder, opened in a TESTING app that does NOT sync data
  folders on open, gets its ledger and this folder's own non-run snapshots as
  ``observed`` events: the value drawer and the Versions panel show them as
  "seen by SM";
* a run's snapshot and a parallel folder's snapshot are never imported, and a
  chip whose history holds another folder of the same identity keeps its
  per-folder snapshot path (one ledger per identity cannot tell the folders
  apart): another folder's value is never shown as this folder's;
* two windows doing the first slice at once create one ledger (one identity)
  and import each snapshot once; the WAL switch waits for another window;
* the drawer, Versions, State History and Trends reach no snapshot fallback
  for such a chip;
* a chip with no data folder whose ledger cannot be opened is ``degraded``
  (it shows what it has), never ``building`` forever.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_sync
from quam_state_manager.core import hub_store as hub_store_mod
from quam_state_manager.web import routes as routes_mod
from tests.test_hub_drawer import WIRING, _inline, chip_dir, chip_state, drawer, make_app, rows  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent

_bump = [0]


def _write(folder: Path, state: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    # a strictly new mtime, so the capture's mtime gate never skips a write
    _bump[0] += 1
    t = time.time() + 50 + _bump[0]
    for name in ("state.json", "wiring.json"):
        os.utime(folder / name, (t, t))


def _snap(hm, folder: Path, state: dict, trigger: str, **kw):
    _write(folder, state)
    meta = hm.check_and_snapshot(str(folder), trigger, force=True, **kw)
    assert meta is not None
    time.sleep(0.002)          # distinct, ordered stamps
    return meta


def _open_folderless(tmp_path, monkeypatch, *, parallel: bool) -> dict:
    """A live chip with NO data folder whose Param History holds: its own
    first state (T1 1e-5), an outside edit SM saw (2e-5), a run's save
    (9e-5, never imported), optionally a parallel folder's save of the same
    chip identity (5e-5, never imported) and its newest outside edit (3e-5)."""
    monkeypatch.delenv("HUB_FALLBACK_TRIPWIRE", raising=False)
    monkeypatch.setattr(routes_mod, "_HUB_FALLBACK_REACHED", {})
    monkeypatch.setattr(routes_mod, "_HUB_FALLBACK_WARNED", set())
    live, other = tmp_path / "chips" / "live", tmp_path / "chips" / "copy"
    app = make_app(tmp_path, sync=False)       # a TESTING app: data folders are not synced on open
    with app.app_context():
        hm = routes_mod._history()
        _snap(hm, live, chip_state(t1=1.0e-5), "manual")
        _snap(hm, live, chip_state(t1=2.0e-5), "auto")
        run_folder = tmp_path / "runs" / "2026-01-01" / "#7_scan_120000"
        _snap(hm, live, chip_state(t1=9.0e-5), "experiment", kind="exp", run_id=7,
              experiment_name="scan", experiment_folder_path=str(run_folder))
        if parallel:
            _snap(hm, other, chip_state(t1=5.0e-5), "save")
        _snap(hm, live, chip_state(t1=3.0e-5), "auto")
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    return {"app": app, "client": client, "live": live, "other": other, "tmp": tmp_path}


@pytest.fixture
def folderless(tmp_path, monkeypatch):
    return _open_folderless(tmp_path, monkeypatch, parallel=False)


@pytest.fixture
def two_folders(tmp_path, monkeypatch):
    return _open_folderless(tmp_path, monkeypatch, parallel=True)


def _ledger_rows(env, sql: str, args=()) -> list:
    path = chip_dir(env) / "ledger.sqlite"
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


# ======================================================================
# 1. status: building until the first slice ran
# ======================================================================

def test_status_is_building_until_the_first_slice_ran(tmp_path):
    chip = tmp_path / "hist" / "chip"
    cs = hub_sync.open_chip(chip, [], kick=False, observed=lambda: [])
    st = hub_sync.status(chip)
    assert st["state"] == "building", "nothing was looked at yet: never 'ready'"
    assert not (chip / "ledger.sqlite").exists()
    hub_sync.kick(cs)                             # inline: the first slice runs now
    st = hub_sync.status(chip)
    assert st["state"] == "ready" and st["note"] == "no data folder is registered to this chip"
    assert (chip / "ledger.sqlite").is_file(), "the first slice creates the ledger"


def test_a_chip_with_nothing_to_read_is_not_kicked_and_stays_ready(tmp_path):
    """No data folder and no observed source: there is no slice to wait for."""
    chip = tmp_path / "hist" / "bare"
    hub_sync.open_chip(chip, [])
    assert hub_sync.status(chip)["state"] == "ready"
    assert not (chip / "ledger.sqlite").exists()


def test_a_folderless_chip_whose_ledger_cannot_open_is_degraded_not_building(tmp_path, monkeypatch):
    chip = tmp_path / "hist" / "broken"

    def refuse(*_a, **_k):
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(hub_store_mod, "HubStore", refuse)
    cs = hub_sync.open_chip(chip, [], observed=lambda: [])
    st = hub_sync.status(chip)
    assert st["state"] == "degraded", "a ledger that cannot open is not 'building' forever"
    assert "could not be opened" in st["note"] and "unable to open" in st["ledger_error"]
    monkeypatch.undo()
    hub_sync.kick(cs)                             # the retry succeeds
    st = hub_sync.status(chip)
    assert st["state"] == "ready" and "ledger_error" not in st


def test_a_chip_with_a_data_folder_whose_ledger_cannot_open_is_degraded_not_building(tmp_path, monkeypatch):
    """S10 C6 browser check: a corrupt ledger.sqlite on a chip WITH a data
    folder left every list "being built" forever -- the folderless rule now
    holds for every chip: a slice that cannot open the ledger is degraded."""
    chip = tmp_path / "hist" / "broken_rooted"
    root = tmp_path / "data"
    root.mkdir()

    def refuse(*_a, **_k):
        raise sqlite3.DatabaseError("file is not a database")
    monkeypatch.setattr(hub_store_mod, "HubStore", refuse)
    cs = hub_sync.open_chip(chip, [(str(root), "extras")], observed=lambda: [])
    hub_sync.kick(cs)
    st = hub_sync.status(chip)
    assert st["state"] == "degraded", "a ledger that cannot open is not 'building' forever"
    assert "could not be opened or written" in st["note"] and "not a database" in st["ledger_error"]
    monkeypatch.undo()
    hub_sync.kick(cs)                             # the retry succeeds
    st = hub_sync.status(chip)
    assert st["state"] in ("ready", "building") and "ledger_error" not in st


def test_periodic_relists_the_observed_states_of_a_folderless_chip(tmp_path):
    chip = tmp_path / "hist" / "periodic"
    calls = []
    cs = hub_sync.open_chip(chip, [], observed=lambda: calls.append(1) or [])
    assert len(calls) == 1 and hub_sync.status(chip)["state"] == "ready"
    assert hub_sync.periodic(now=time.monotonic() + hub_sync.LISTING_EVERY_S + 1) >= 1
    assert len(calls) == 2, "a folderless chip's snapshots are listed again every LISTING_EVERY_S"
    assert cs.active


# ======================================================================
# 2. the observed import, through the real routes
# ======================================================================

def test_a_folderless_chip_gets_its_ledger_and_its_observed_states(folderless):
    st = folderless["client"].get("/hub/status").get_json()
    assert st["state"] == "ready" and st["roots"] == [], st
    assert (chip_dir(folderless) / "ledger.sqlite").is_file(), "opening the chip created its ledger"
    got = rows(drawer(folderless, "qubits.qA1.T1"))
    assert [r["value"] for r in got] == ["3e-05", "2e-05", "1e-05"], got
    assert all(r["label"].startswith("seen by SM (") for r in got), [r["label"] for r in got]
    assert [r["label"] for r in got][:2] == ["seen by SM (auto snapshot)"] * 2
    assert all("writer unknown" in r["body"] and not r["data"] for r in got), \
        "a state SM only saw names no run"


def test_versions_show_the_observed_states_as_seen_by_sm(folderless):
    html = folderless["client"].get("/state/versions", headers={"HX-Request": "true"}).data.decode()
    seen = re.findall(r'<span class="sv-event sv-event-seen"[^>]*>([^<]*)</span>', html)
    assert seen == ["seen by SM (auto snapshot)", "seen by SM (auto snapshot)",
                    "seen by SM (manual snapshot)"], seen


def _imported(env) -> list:
    events = _ledger_rows(env, "SELECT kind, t_src FROM events ORDER BY ord")
    assert {k for k, _ in events} <= {"observed"}, events
    with env["app"].app_context():
        snaps = {m.timestamp: m for m in routes_mod._history().list_snapshots(str(env["live"]))}
    return [snaps[ts] for _, ts in events]


def test_no_run_snapshot_is_imported(folderless):
    values = [r["value"] for r in rows(drawer(folderless, "qubits.qA1.T1"))]
    assert "9e-05" not in values, "a run's save is never an observed state"
    imported = _imported(folderless)
    assert len(imported) == 3, [(m.trigger, m.run_id) for m in imported]
    assert all(m.trigger != "experiment" and m.run_id is None for m in imported), \
        [(m.trigger, m.run_id) for m in imported]


def test_no_parallel_folder_snapshot_is_imported_and_the_folder_keeps_its_own_path(two_folders):
    imported = _imported(two_folders)
    assert len(imported) == 3, [m.source_path for m in imported]
    assert all(Path(m.source_path or "").name == "live" for m in imported), \
        [m.source_path for m in imported]
    # S10 C1.5: other_folders snapshot fallback -> the folder view: the one ledger per chip
    # identity is read per folder, so this folder reads it and never shows the other's value
    html = drawer(two_folders, "qubits.qA1.T1")
    assert "5e-05" not in html and "5.0e-05" not in html
    assert "from the change ledger" in html
    reached = two_folders["client"].get("/hub/status").get_json()["fallback_reached"]
    assert "drawer" not in reached, reached
    panel = two_folders["client"].get("/state/versions").get_data(as_text=True)
    assert 'data-source="ledger"' in panel


def test_the_tripwire_stays_silent_on_the_drawer_versions_state_history_and_trends(folderless, monkeypatch):
    monkeypatch.setenv("HUB_FALLBACK_TRIPWIRE", "1")
    client = folderless["client"]
    for url in ("/field/history?path=qubits.qA1.T1", "/state/versions", "/state-history?body=1",
                "/topology/trends?metrics=T1"):
        try:
            r = client.get(url, headers={"HX-Request": "true"})
        except RuntimeError as exc:           # the tripwire: a fallback was drawn
            raise AssertionError(f"{url}: {exc}") from exc
        assert r.status_code == 200, (url, r.data[:300])
    reached = client.get("/hub/status").get_json()["fallback_reached"]
    assert not {k: v for k, v in reached.items()
                if k in ("drawer", "versions", "state_history", "trends")}, reached


def test_a_snapshot_taken_after_the_open_is_imported_at_the_next_look(folderless):
    live = folderless["live"]
    with folderless["app"].app_context():
        _snap(routes_mod._history(), live, chip_state(t1=4.0e-5), "auto")
    cs = hub_sync.sync_for(chip_dir(folderless))
    assert hub_sync.periodic(now=time.monotonic() + hub_sync.LISTING_EVERY_S + 1) >= 1
    assert cs.status()["state"] == "ready"
    assert [r["value"] for r in rows(drawer(folderless, "qubits.qA1.T1"))][:2] == ["4e-05", "3e-05"]


# ======================================================================
# 3. two windows creating one ledger
# ======================================================================

_WINDOW = (
    "import json, sys, time\n"
    "from quam_state_manager.core import hub_sync\n"
    "from quam_state_manager.core.hub_store import HubStore\n"
    "chip, snaps, t = sys.argv[1], json.loads(sys.argv[2]), float(sys.argv[3])\n"
    "cs = hub_sync.ChipSync(chip)\n"
    "cs.observed_source = lambda: snaps\n"
    "cs.request(full=True)\n"
    "while time.time() < t: pass\n"
    "with HubStore(chip, check_same_thread=False) as st:\n"
    "    while cs.run_slice(st, None): pass\n"
    "print(json.dumps({'ledger_id': cs.ledger_id, 'state': cs.status()['state'],\n"
    "                  'errors': list(cs.errors)}))\n"
)


def _snapshot_dirs(tmp_path: Path, n: int) -> list[dict]:
    out = []
    for i in range(n):
        ts = f"20260101_1200{i:02d}_000000"
        folder = tmp_path / "snaps" / ts
        _write(folder, chip_state(t1=(i + 1) * 1.0e-5))
        out.append({"ts": ts, "t_us": hub_sync.snapshot_instant_us(ts), "trigger": "auto",
                    "dir": str(folder)})
    return out


def test_two_windows_doing_the_first_slice_together_make_one_ledger(tmp_path):
    """Two (here four) SM windows open one folderless chip at the same
    instant: each creates the ledger and imports the chip's snapshots. One
    identity, every snapshot once, no window fails. Rounds: the race is
    timing-dependent."""
    snaps = _snapshot_dirs(tmp_path, 4)
    env = dict(os.environ, PYTHONUTF8="1", PYTHONPATH=str(ROOT))
    for round_ in range(4):
        chip = tmp_path / f"chip{round_}"
        start = time.time() + 2.0
        procs = [subprocess.Popen([sys.executable, "-c", _WINDOW, str(chip), json.dumps(snaps), str(start)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                 for _ in range(4)]
        outs = [p.communicate(timeout=120) for p in procs]
        failed = [err for (_, err), p in zip(outs, procs) if p.returncode]
        assert not failed, (round_, failed)
        res = [json.loads(out.strip().splitlines()[-1]) for out, _ in outs]
        assert len({r["ledger_id"] for r in res}) == 1, (round_, "one ledger, one identity", res)
        assert all(r["state"] == "ready" and not r["errors"] for r in res), (round_, res)
        con = sqlite3.connect(str(chip / "ledger.sqlite"))
        try:
            kinds = [k for (k,) in con.execute("SELECT kind FROM events ORDER BY ord")]
            looked = con.execute("SELECT COUNT(*), COUNT(DISTINCT ts) FROM observed_snapshots").fetchone()
        finally:
            con.close()
        assert kinds == ["observed"] * 4, (round_, "each snapshot imported once", kinds)
        assert looked == (4, 4), (round_, looked)


def test_flush_returns_only_once_the_ledger_handles_are_released(tmp_path):
    """Every chip SM syncs now has a Hub (folderless chips included), and the
    projector releases each Hub's ledger handle when a burst ends: flush()
    must mean "done and closed". It used to signal idle first, so with many
    Hubs a caller of flush() still found the handle open."""
    from tests.test_hub_sync import doc, run
    root, chip = tmp_path / "data", tmp_path / "chip"
    for i in range(1, 5):
        run(root, i, doc(float(i)))
    extra = [hub.Hub.for_chip(tmp_path / "others" / f"c{i}") for i in range(3000)]
    hub.set_inline(False)
    try:
        cs = hub_sync.open_chip(chip, [(str(root), "declared")])
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and (cs.has_work() or not hub.flush(1)):
            time.sleep(0.02)
        assert hub.flush(60)
        still_open = "_ledger_store" in hub.Hub.for_chip(chip).__dict__
    finally:
        hub.set_inline(True)
        hub.flush(60)
        for h in extra:
            hub.Hub._cache.pop(os.path.normcase(str(h.dir)), None)
    assert not still_open, "flush() returned while the burst's ledger handle was still open"


def test_a_second_window_keeps_the_identity_the_first_one_is_writing(tmp_path):
    """A ledger's identity is written once: a window that finds none while
    another window is writing it waits for that window and keeps its identity
    (each used to write its own and the last one won, so the two windows bound
    their RAM bookkeeping to two different identities of one file)."""
    chip = tmp_path / "chip"
    with hub_store_mod.HubStore(chip):
        pass
    con = sqlite3.connect(str(chip / "ledger.sqlite"), isolation_level=None)
    con.execute("DELETE FROM meta WHERE k='ledger_id'")     # the schema is there, the identity not yet
    con.close()
    writing = threading.Event()

    def other_window():
        c = sqlite3.connect(str(chip / "ledger.sqlite"), isolation_level=None)
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT INTO meta VALUES('ledger_id', ?)", ("a" * 16,))
        writing.set()
        time.sleep(0.4)
        c.execute("COMMIT")
        c.close()
    t = threading.Thread(target=other_window)
    t.start()
    try:
        assert writing.wait(5)
        with hub_store_mod.HubStore(chip) as st:
            got = st.meta("ledger_id")
    finally:
        t.join(10)
    assert got == "a" * 16, "the identity another window was writing is kept, not replaced"


def test_the_wal_switch_waits_for_another_window_creating_the_ledger(tmp_path):
    """Another window holds the brand-new ledger file's write lock while it
    creates it: the WAL switch fails at once with 'database is locked' (the
    busy timeout does not cover it), so the open must wait, not fail."""
    chip = tmp_path / "chip"
    chip.mkdir()
    ready, release = threading.Event(), threading.Event()

    def other_window():
        con = sqlite3.connect(str(chip / "ledger.sqlite"), isolation_level=None)
        con.execute("BEGIN IMMEDIATE")
        ready.set()
        release.wait(5)
        time.sleep(0.3)
        con.execute("COMMIT")
        con.close()
    t = threading.Thread(target=other_window)
    t.start()
    try:
        assert ready.wait(5)
        release.set()
        cs = hub_sync.ChipSync(chip)
        cs.observed_source = lambda: []
        cs.request(full=True)
        try:
            with hub_store_mod.HubStore(chip) as st:
                while cs.run_slice(st, None):
                    pass
                mode = st.conn.execute("PRAGMA journal_mode").fetchone()[0]
            failed = None
        except sqlite3.OperationalError as exc:
            failed, mode = str(exc), None
        assert failed is None, f"the open did not wait for the other window: {failed}"
        assert mode == "wal" and cs.status()["state"] == "ready" and cs.ledger_id
    finally:
        release.set()
        t.join(10)
