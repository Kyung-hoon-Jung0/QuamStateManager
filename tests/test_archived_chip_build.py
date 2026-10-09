"""S10 walk: an archived chip with no change ledger gets its history back.

Before S10 Param History drew an archived chip's history from its snapshots.
After S10 every surface reads the change ledger, and a chip that never had one
(last opened before ledgers existed, or its live folder is gone) showed only
"could not be read (no_ledger)" with a Try again that could never succeed.

Pins, on generic synthetic chips in ``tmp_path`` (each mutation-checked):

* the journey: viewing such a chip builds its ledger from its own Param
  History snapshots -- a snapshot that is not a run becomes an ``observed``
  event ("seen by SM", writer unknown) -- and the grid, its sparkline, the
  cell drawer and Changes show those values with that provenance; no live or
  data folder is written;
* its run captures are its run history: a run whose folder is still on disk
  is read from its data folder (a true run event: its own patch proves the
  value), a run whose folder is gone is imported from its capture ("saved in
  #N", writer not proven, run folder deleted), and only the run folders the
  captures name are read (another chip's run beside them never is); the
  archived grid then holds, for every parameter, the very value points the
  pre-S10 Param History grid held for the same snapshots
  (``HistoryManager.extract_property_history``, that grid's code path);
* the build runs off the request thread: the first answer is "being built",
  counted in runs and then in snapshots, and asks again by itself; a build
  cut short (the process ended) is resumed, never read as complete; a build
  of an older kind (no runs) is resumed with them;
* snapshots that cannot be read end ``unavailable`` in plain words, with no
  Try again and no rebuild loop;
* "Open this chip" stays offered wherever a folder on disk opens as the chip
  (grid and cell drawer alike), including when its captures are SM's own
  capture of the live folder after adopting a run (``auto``, kind ``exp``,
  S10 final review) -- never counted as captures of runs;
* the archived-chip picker names a chip by its display name, the history
  folder second;
* a grid read that fails says "could not be read", a code bug is a 500, and
  only a wait says wait (S10 final review);
* a ``no_ledger`` end state offers no Try again.
"""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_sync
from quam_state_manager.core.scanner import ExperimentEntry
from quam_state_manager.web import routes
from tests.test_hub_drawer import WIRING, _inline, chip_state, make_app  # noqa: F401

HX = {"HX-Request": "true"}
_bump = [0]


def _write(folder: Path, state: dict, wiring: dict | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring or WIRING), encoding="utf-8")
    _bump[0] += 1                       # a strictly new mtime: the capture never skips
    t = time.time() + 50 + _bump[0]
    for name in ("state.json", "wiring.json"):
        os.utime(folder / name, (t, t))


def _snap(hm, folder: Path, state: dict, trigger: str, **kw):
    _write(folder, state)
    meta = hm.check_and_snapshot(str(folder), trigger, force=True, **kw)
    assert meta is not None
    time.sleep(0.002)                   # distinct, ordered stamps
    return meta


def _open_other(app, tmp_path):
    other = tmp_path / "chips" / "open"
    _write(other, chip_state(name="other"), {"network": {"host": "127.0.0.2", "cluster_name": "C2"}})
    client = app.test_client()
    assert client.post("/load", data={"folder": str(other)}).status_code in (200, 302)
    return client


def _archived(tmp_path, *, keep_live: bool = False, corrupt: bool = False,
              runs_only: bool = False, name: str = "device") -> dict:
    """An instance whose Param History captured chip *name* before it had a
    ledger (the chip is never opened here), then another chip opened. Its
    captures: its first state (manual, T1 1e-5), an outside edit SM saw (auto,
    2e-5), a run's saved copy whose run folder is gone (experiment #7, 9e-5)
    and SM's own capture of the live folder after adopting a run (auto, kind
    exp, 3e-5). ``runs_only``: only run captures (#7, #8). ``corrupt``: no
    snapshot's files can be read. ``keep_live``: the chip's live folder stays."""
    app = make_app(tmp_path, sync=False)
    live = tmp_path / "chips" / "archived"
    run_dir = tmp_path / "runs" / "2026-01-01" / "#7_scan_120000"
    with app.app_context():
        hm = routes._history()
        if not runs_only:
            _snap(hm, live, chip_state(t1=1.0e-5, name=name), "manual", kind="manual")
            _snap(hm, live, chip_state(t1=2.0e-5, name=name), "auto")
        _snap(hm, live, chip_state(t1=9.0e-5, name=name), "experiment", kind="exp", run_id=7,
              experiment_name="scan", experiment_folder_path=str(run_dir))
        if runs_only:
            _snap(hm, live, chip_state(t1=8.0e-5, name=name), "experiment", kind="exp", run_id=8,
                  experiment_name="scan", experiment_folder_path=str(run_dir.parent / "#8_scan_120100"))
        else:
            _snap(hm, live, chip_state(t1=3.0e-5, name=name), "auto", kind="exp")
        directory = Path(hm.resolve_chip_dir(str(live))[0])
        hm._join_deferred_index()
        stamps = [m.timestamp for m in hm.list_snapshots(str(live))]
    client = _open_other(app, tmp_path)
    if corrupt:
        for ts in stamps:
            (directory / ts / "state.json").write_text("{not json", encoding="utf-8")
    if not keep_live:
        shutil.rmtree(live)
    assert not (directory / "ledger.sqlite").exists()
    return {"app": app, "client": client, "live": live, "dir": directory, "key": directory.name}


def _run_folder(data: Path, rid: int, minute: int, t1: float, *, name: str = "device"):
    """A run folder of the standard layout whose own patch sets qA1.T1, and
    the scanner entry Param History ingests it from (its own instant)."""
    day, stamp = "2026-01-01", f"2026-01-01T12:{minute:02d}:00+00:00"
    folder = data / day / f"#{rid}_scan_12{minute:02d}00"
    state = chip_state(t1=t1, name=name)
    (folder / "quam_state").mkdir(parents=True)
    (folder / "quam_state" / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "quam_state" / "wiring.json").write_text(json.dumps(WIRING), encoding="utf-8")
    node = {"created_at": stamp, "id": rid, "metadata": {"name": "scan", "status": "finished"},
            "data": {"parameters": {"model": {"qubits": ["qA1"]}}},
            "patches": [{"op": "replace", "path": "/quam/qubits/qA1/T1", "value": t1}]}
    (folder / "node.json").write_text(json.dumps(node), encoding="utf-8")
    entry = ExperimentEntry(folder_path=folder, quam_state_path=folder / "quam_state", run_id=rid,
                            experiment_name="scan", timestamp=stamp, status="finished",
                            qubits=["qA1"], qubit_pairs=[], outcomes={}, parent_ids=[],
                            date_str=day, is_standalone=False)
    return folder, entry, state


GONE = (4, 5, 9, 10, 14)


def _with_runs(tmp_path, *, rewrite: int | None = None) -> dict:
    """A 30-snapshot archived chip with runs: #1-#12 ingested by Param History
    from their run folders (T1 k us, each run's own patch sets it); #13 and
    #14 first captured by SM in the live folder, then stamped by their run's
    ingest (docs/200); 16 states SM saw (T1 101-116 us). The folders of #4,
    #5, #9, #10 and #14 are gone since. Beside them in the same data folder
    lies a run of ANOTHER chip (#99), earlier than all of them. Every
    snapshot changes qA1.T1; qA2.T1 never changes. ``rewrite``: that run
    saved again after Param History captured it (T1 + 0.5 us, its patch too):
    the capture held a save the run replaced."""
    app = make_app(tmp_path, sync=False)
    live, data = tmp_path / "chips" / "archived", tmp_path / "data"
    folders = {99: _run_folder(data, 99, 0, 7.7e-3, name="another")[0]}
    with app.app_context():
        hm = routes._history()
        _write(live, chip_state(t1=0.5e-6))
        for k in range(1, 13):
            folders[k], entry, _state = _run_folder(data, k, k, k * 1e-6)
            assert hm.ingest_run(str(live), entry)["ingested"] == 1
            if k == rewrite:                            # the run's final save, after the capture
                again = chip_state(t1=(k + 0.5) * 1e-6)
                (folders[k] / "quam_state" / "state.json").write_text(json.dumps(again), encoding="utf-8")
                node = json.loads((folders[k] / "node.json").read_text(encoding="utf-8"))
                node["patches"][0]["value"] = (k + 0.5) * 1e-6
                (folders[k] / "node.json").write_text(json.dumps(node), encoding="utf-8")
        for k in (13, 14):
            folders[k], entry, state = _run_folder(data, k, k, k * 1e-6)
            _write(live, state)                         # the run wrote the live folder
            assert hm.check_and_snapshot(str(live), "auto", kind="exp", force=True) is not None
            assert hm.ingest_run(str(live), entry)["enriched"] == 1   # SM's capture, stamped
        for i in range(16):
            _snap(hm, live, chip_state(t1=(101 + i) * 1e-6), ("manual", "auto", "save")[i % 3])
        directory = Path(hm.resolve_chip_dir(str(live))[0])
        hm._join_deferred_index()
        assert len(hm.list_snapshots(str(live))) == 30
    for k in GONE:
        shutil.rmtree(folders[k])
    client = _open_other(app, tmp_path)
    shutil.rmtree(live)
    return {"app": app, "client": client, "live": live, "dir": directory, "key": directory.name,
            "data": data}


def _grid(env, **q) -> str:
    r = env["client"].get("/param-history", query_string={"chip_key": env["key"], "since": "all",
                                                           "props": "T1", **q}, headers=HX)
    assert r.status_code == 200, r.data[:400]
    return r.get_data(as_text=True)


def _drawer(env, qubit: str = "qA1", prop: str = "T1") -> tuple[str, dict]:
    r = env["client"].get("/param-history/expand", query_string={
        "chip_key": env["key"], "qubit": qubit, "prop": prop}, headers=HX)
    assert r.status_code == 200, r.data[:400]
    body = r.get_data(as_text=True)
    m = re.search(r'<script id="phd-data" type="application/json">(.*?)</script>', body, re.S)
    return body, (json.loads(m.group(1)) if m else {})


def _t(body: str) -> str:
    """The words a reader sees: no tags or scripts, entities decoded, one space."""
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", body))).strip()


def _cell(grid: str, qubit: str, prop: str) -> str:
    m = re.search(r'<td class="history-cell"\s+data-qubit="%s" data-prop="%s".*?</td>' % (qubit, prop),
                  grid, re.S)
    assert m, "the grid has no %s %s cell" % (qubit, prop)
    return m.group(0)


def _ledger(env, sql: str) -> list:
    con = sqlite3.connect((env["dir"] / "ledger.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _tree_sig(folder: Path) -> dict:
    return {str(p.relative_to(folder)): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in sorted(folder.rglob("*"))}


def _pre_s10(env, qubit: str, prop: str = "T1") -> list:
    """The values the pre-S10 Param History grid drew for one cell, oldest
    first: its code path (``extract_property_history`` over the snapshot
    index, unchanged since f0a94b93) on the same snapshots."""
    with env["app"].app_context():
        hm = routes._history()
        rows = hm.extract_property_history(routes._path_for_chip_key(env["key"]), [prop],
                                           qubit_filter=[qubit])
    (row,) = [r for r in rows if r["qubit"] == qubit]
    return [v["value"] for v in row["values"]]


def _collapsed(values: list) -> list:
    out: list = []
    for v in values:
        if not out or out[-1] != v:
            out.append(v)
    return out


# ======================================================================
# 1. the journey: no ledger -> built from the snapshots -> values shown
# ======================================================================

def test_an_archived_chip_with_no_ledger_shows_its_snapshots_values(tmp_path):
    env = _archived(tmp_path)
    grid = _grid(env)
    assert 'data-vh-mode="unavailable"' not in grid and "could not be read" not in grid
    assert (env["dir"] / "ledger.sqlite").is_file()
    # the grid: three states SM saw and the run whose folder is gone, from its capture
    assert "4 recorded changes shown (4 events in the change ledger)" in _t(grid)
    spark = _cell(grid, "qA1", "T1")
    dots = re.findall(r'<circle class="hs-pt (hs-pt-\w+)" cx="[\d.]+" cy="[\d.]+" r="1.4"/>', spark)
    assert len(dots) == 4, spark
    # the cell drawer: each value with its provenance
    _body, row = _drawer(env)
    pts = [(v["value"], v["provenance"], v["label"], v["sub"]) for v in row["values"]]
    assert pts == [(1.0e-5, "observed", "seen by SM (manual snapshot)", "writer unknown"),
                   (2.0e-5, "observed", "seen by SM (auto snapshot)", "writer unknown"),
                   (9.0e-5, "run_saved", "saved in #7 scan", "writer not proven"),
                   (3.0e-5, "observed", "seen by SM (auto snapshot)", "writer unknown")], pts
    assert row["values"][2]["flags"] == ["run folder deleted"]
    assert all(v["run"] is None and v["uid"] is None for v in row["values"])
    # Changes: the archived chip's own events, its chip kept on every link
    page = env["client"].get("/param-history/changes", query_string={"chip_key": env["key"]},
                             headers=HX).get_data(as_text=True)
    assert "seen by SM (auto snapshot)" in page and "qubits.qA1.T1" in page
    assert "run #7 scan" in page and "writer not proven" in page
    assert f"chip_key={env['key']}" in page and "/field/history?path=" not in page
    found = env["client"].get("/param-history/param-search",
                              query_string={"q": "qA1.T1", "chip_key": env["key"]}).get_json()
    assert [(r["path"], r["changes"]) for r in found["results"]
            if r["path"] == "qubits.qA1.T1"] == [("qubits.qA1.T1", 4)], found
    # what it was built from is said; no folder opens as the chip, so no Open
    assert 'data-note="archive_built"' in grid
    assert ("History built from 1 run capture whose folder is gone and 3 states SM saw."
            in _t(grid)), _t(grid)
    assert "Open this chip" not in grid and "run capture of this chip" not in _t(grid)
    # the build is finished and recorded as one from snapshots
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == [("done:runs",)]
    kinds = _ledger(env, "SELECT kind, src, COUNT(*) FROM events GROUP BY kind, src ORDER BY kind")
    assert kinds == [("observed", "param_history:auto", 2), ("observed", "param_history:manual", 1),
                     ("run", "param_history:run_capture", 1)], kinds


def test_the_build_reads_and_never_writes_the_live_folder(tmp_path):
    env = _archived(tmp_path, keep_live=True)
    before = _tree_sig(env["live"])
    grid = _grid(env)
    assert "4 recorded changes shown" in _t(grid)
    assert _tree_sig(env["live"]) == before
    # the live folder still opens as this chip: Open stays offered beside the values
    assert f'name="folder" value="{env["live"]}">Open this chip</button>' in grid
    assert "Open this chip to keep it current." in grid
    # opened live, the folder keeps the ledger: no longer one built from snapshots alone
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == [("done:runs",)]
    assert env["client"].post("/load", data={"folder": str(env["live"])}).status_code in (200, 302)
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == []
    # and the run imported from its capture stays part of that folder's timeline
    # (as the capture was in Param History), never "a data folder not linked"
    page = env["client"].get("/field/history", query_string={"path": "qubits.qA1.T1"}).get_data(as_text=True)
    assert "saved in #7 scan" in page and "not part of this folder" not in page, _t(page)[:600]


def test_a_snapshot_whose_files_are_gone_is_counted_as_unreadable(tmp_path):
    env = _archived(tmp_path)
    with env["app"].app_context():
        hm = routes._history()
        own = [m for m in hm.list_snapshots(routes._path_for_chip_key(env["key"]))
               if not routes._is_run_snapshot(m)]
    (env["dir"] / own[-1].timestamp / "state.json").unlink()
    grid = _grid(env)
    note = html.unescape(re.search(r'data-note="archive_built"[^>]*>([^<]*)<', grid).group(1))
    assert "1 run capture whose folder is gone and 2 states SM saw." in note, note
    assert "1 snapshot could not be read and is left out." in note, note
    assert "3 recorded changes shown" in _t(grid)


def test_an_index_still_being_prepared_is_a_wait_never_a_grid_with_no_qubit(tmp_path, monkeypatch):
    from quam_state_manager.core import hub_index, ramcache
    env = _archived(tmp_path)
    assert "4 recorded changes shown" in _t(_grid(env))
    real = hub_index.snapshot
    calls = []

    def first_warming(store):
        calls.append(1)
        if len(calls) == 1:            # the qubit names' read: the index is being prepared
            raise ramcache.Warming("hub_read_lock", "slot", 0)
        return real(store)
    monkeypatch.setattr(hub_index, "snapshot", first_warming)
    grid = _grid(env)
    assert 'data-vh-mode="preparing"' in grid and "history-cell" not in grid


def test_the_archived_heading_and_picker_name_the_chip_by_its_display_name(tmp_path):
    env = _archived(tmp_path, name="Test chip 7")
    assert env["key"] != "Test chip 7"            # the history folder is a sanitized key
    page = env["client"].get("/param-history", query_string={"since": "all"},
                             headers=HX).get_data(as_text=True)
    item = re.search(r'<div class="archive-item">.*?</div>', page, re.S).group(0)
    assert '<strong class="archive-display">Test chip 7</strong>' in item
    assert f'<code class="archive-key" title="History folder">{env["key"]}</code>' in item
    grid = _grid(env)
    assert "Viewing history for chip <strong>Test chip 7</strong>" in grid


# ======================================================================
# 2. the run captures are the run history: real runs first, then captures
# ======================================================================

def test_the_archived_grid_holds_every_value_point_the_pre_s10_grid_held(tmp_path):
    env = _with_runs(tmp_path)
    data_before = _tree_sig(env["data"])
    grid = _grid(env)
    assert "30 recorded changes shown (30 events in the change ledger)" in _t(grid), _t(grid)[-500:]
    # a parameter that changes at every snapshot: as many points, the same values, in order
    pre = _pre_s10(env, "qA1")
    _body, row = _drawer(env, "qA1")
    assert len(pre) == 30
    assert [v["value"] for v in row["values"]] == pre
    # one that never changes: the same values (the ledger draws each change once)
    _body, row2 = _drawer(env, "qA2")
    assert _collapsed([v["value"] for v in row2["values"]]) == _collapsed(_pre_s10(env, "qA2"))
    # each point says what it was: a run read from its folder (its own patch set the
    # value), a run from its capture (writer not proven, folder deleted), a state SM saw
    by_value = {round(v["value"] * 1e6): v for v in row["values"]}
    for k in range(1, 15):
        v = by_value[k]
        if k in GONE:
            assert (v["provenance"], v["label"], v["sub"]) == (
                "run_saved", f"saved in #{k} scan", "writer not proven"), v
            assert v["flags"] == ["run folder deleted"], v
        else:
            assert (v["provenance"], v["label"], v["sub"]) == (
                "run_proven", f"#{k} scan", "its own patch set it"), v
    assert all(by_value[k]["provenance"] == "observed" for k in range(101, 117))
    # only the run folders the captures name were read: another chip's run never is
    assert _ledger(env, "SELECT COUNT(*) FROM events WHERE run_id=99") == [(0,)]
    assert 7700 not in by_value
    # the note says what the history was built from
    assert ("History built from 9 runs in their data folders, 5 run captures whose folders "
            "are gone, and 16 states SM saw." in _t(grid)), _t(grid)
    # read only: the data folder is untouched; the ledger keeps its invariants
    assert _tree_sig(env["data"]) == data_before
    with hub.Hub.for_chip(env["dir"]).ledger() as store:
        assert hub_sync.verify(store)["problems"] == []


def _is_subsequence(short: list, long: list) -> bool:
    it = iter(long)
    return all(any(x == y for y in it) for x in short)


def test_a_run_saved_again_after_its_capture_keeps_both_saves(tmp_path):
    """Param History captured run #6's saved state, then the run saved again
    (its final save) before its folder was read here: the history keeps the
    captured value (what SM copied from the run folder) and the run's final
    one after it -- nothing the pre-S10 grid drew is missing."""
    env = _with_runs(tmp_path, rewrite=6)
    _grid(env)
    pre = _pre_s10(env, "qA1")
    _body, row = _drawer(env, "qA1")
    got = [v["value"] for v in row["values"]]
    assert len(pre) == 30 and len(got) == 31
    assert _is_subsequence(pre, got), (pre, got)
    at = got.index(6.0e-6)
    assert row["values"][at]["label"] == "seen by SM (experiment snapshot)", row["values"][at]
    assert (got[at + 1], row["values"][at + 1]["label"]) == (6.5e-6, "#6 scan")


def test_runs_build_in_the_background_and_resume_after_a_restart(tmp_path, monkeypatch):
    env = _with_runs(tmp_path)
    queued = _queued_only(monkeypatch)
    first = _grid(env)
    assert queued and 'data-vh-mode="building"' in first
    assert _one_slice(env) is True
    second = _grid(env)
    assert 'data-vh-mode="building"' in second
    # S10 walk (perf): "(n of 9 runs)" -> the phase it is in, counted from the first slice
    assert re.search(r"being built(: looking through run folders)? \(\d+ of 9( runs)?\)", _t(second)), _t(second)
    for _ in range(4):
        _one_slice(env)
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == [("running",)]
    # the process ends mid-build: its RAM goes, the ledger file stays
    hub.Hub.for_chip(env["dir"]).release()
    hub_sync._SYNCS.pop(hub_sync._norm(env["dir"]))
    monkeypatch.undo()                                 # the next process (inline here)
    grid = _grid(env)
    assert "30 recorded changes shown (30 events in the change ledger)" in _t(grid), _t(grid)[-500:]
    _body, row = _drawer(env, "qA1")
    assert [v["value"] for v in row["values"]] == _pre_s10(env, "qA1")


def test_a_build_of_an_older_kind_is_resumed_with_its_runs(tmp_path, monkeypatch):
    """A ledger an earlier build made from the states SM saw alone (no runs,
    its marker "done") is not read as complete: the next view adds the runs."""
    env = _with_runs(tmp_path)
    real = routes._hub_archive_sources

    def states_only(hm, target_path, chip_dir):
        snaps, _setup = real(hm, target_path, chip_dir)
        return (lambda: [s for s in snaps() if not s.get("run") and s["trigger"] != "experiment"]), None
    monkeypatch.setattr(routes, "_hub_archive_sources", states_only)
    assert "16 recorded changes shown" in _t(_grid(env))
    monkeypatch.undo()
    with hub.Hub.for_chip(env["dir"]).ledger() as store:
        with hub_sync.txn(store):
            store.set_meta(hub_sync.ARCHIVE_BUILD, "done")      # the older build's marker
    hub.Hub.for_chip(env["dir"]).release()
    hub_sync._SYNCS.pop(hub_sync._norm(env["dir"]))
    grid = _grid(env)
    assert "30 recorded changes shown (30 events in the change ledger)" in _t(grid), _t(grid)[-500:]


def test_a_run_the_ledger_holds_is_never_added_twice_and_a_live_open_reads_its_whole_folder(tmp_path):
    """A chip opened live before ingested runs #1 and #2 from its data folder;
    Param History captured both. Then #1's folder was deleted and a run #3
    landed beside #2 that Param History never captured. Viewed archived, its
    ledger is not given #1 a second time from its capture, and #3 is not read
    (only the folders the captures name are); opened live again, the chip
    reads its whole data folder once more (#3 lands)."""
    from tests.test_hub_drawer import write_chip
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    f1, e1, _s1 = _run_folder(data, 1, 1, 1.0e-6)
    f2, e2, _s2 = _run_folder(data, 2, 2, 2.0e-6)
    write_chip(live, chip_state(t1=2.0e-6), data)
    app = make_app(tmp_path)                        # syncs the data folder on open
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    with app.app_context():
        hm = routes._history()
        directory = Path(hm.resolve_chip_dir(str(live))[0])
        for entry in (e1, e2):
            assert hm.ingest_run(str(live), entry)["ingested"] == 1
    env = {"app": app, "client": client, "dir": directory, "key": directory.name}
    assert _ledger(env, "SELECT run_id FROM events WHERE kind='run' ORDER BY run_id") == [(1,), (2,)]
    _open_other(app, tmp_path)
    shutil.rmtree(f1)
    _run_folder(data, 3, 3, 3.0e-6)
    grid = _grid(env)
    assert 'data-note="archive_built"' in grid
    assert _ledger(env, "SELECT run_id, COUNT(*) FROM events WHERE kind='run' GROUP BY run_id")         == [(1, 1), (2, 1)]
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    assert _ledger(env, "SELECT run_id, COUNT(*) FROM events WHERE kind='run' GROUP BY run_id")         == [(1, 1), (2, 1), (3, 1)]


# ======================================================================
# 3. off the request thread, counted, resumed when cut short
# ======================================================================

def _queued_only(monkeypatch) -> list:
    """The projector as it runs in production -- a kick only queues -- with
    the queue held here, so a test runs each slice itself."""
    queued: list = []
    monkeypatch.setattr(hub._PROJECTOR, "inline", False)
    monkeypatch.setattr(hub._PROJECTOR, "kick_sync", queued.append)
    return queued


def _one_slice(env) -> bool:
    h = hub.Hub.for_chip(env["dir"])
    with h.ledger() as store:
        return hub_sync.run(env["dir"], store, 0.0)


def test_the_build_runs_in_the_background_and_says_so(tmp_path, monkeypatch):
    env = _archived(tmp_path, name="Test chip 7")
    queued = _queued_only(monkeypatch)
    first = _grid(env)
    assert queued, "the view queued the build"
    assert 'data-vh-mode="building"' in first and 'hx-trigger="load delay:2000ms"' in first
    assert "being built from this chip's Param History snapshots" in _t(first)
    assert "Try again" not in first and "history-cell" not in first
    assert '<strong class="ph-archive-name">Test chip 7</strong>' in first
    assert _one_slice(env) is True                    # one snapshot looked at, three to go
    second = _grid(env)
    assert 'data-vh-mode="building"' in second
    assert "(1 of 4)" in _t(second), _t(second)
    while _one_slice(env):
        pass
    done = _grid(env)
    assert "4 recorded changes shown" in _t(done) and 'data-vh-mode=' not in done


def test_a_build_cut_short_is_resumed_never_read_as_complete(tmp_path, monkeypatch):
    env = _archived(tmp_path)
    _queued_only(monkeypatch)
    _grid(env)
    assert _one_slice(env) is True
    assert _ledger(env, "SELECT COUNT(*) FROM events") == [(1,)]
    # the process ends mid-build: its RAM goes, the ledger file stays
    hub.Hub.for_chip(env["dir"]).release()
    hub_sync._SYNCS.pop(hub_sync._norm(env["dir"]))
    monkeypatch.undo()                                 # the next process (inline here)
    grid = _grid(env)
    assert "4 recorded changes shown" in _t(grid), _t(grid)[-400:]
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == [("done:runs",)]


# ======================================================================
# 4. end states in plain words: no Try again, no rebuild loop
# ======================================================================

def test_unreadable_snapshots_end_unavailable_in_plain_words(tmp_path):
    env = _archived(tmp_path, corrupt=True, keep_live=True)
    for _ in range(2):                                 # a second view: the same end, no rebuild
        grid = _grid(env)
        assert 'data-vh-mode="unavailable"' in grid and 'data-vh-reason="snapshots_unreadable"' in grid
        assert ("None of this chip's 4 Param History snapshots could be read, so its change "
                "history cannot be built from them.") in _t(grid)
        assert "Try again" not in grid and "load delay:" not in grid and "history-cell" not in grid
        assert f'name="folder" value="{env["live"]}">Open this chip</button>' in grid
        assert _ledger(env, "SELECT outcome, COUNT(*) FROM observed_snapshots GROUP BY outcome") \
            == [("unreadable", 4)]
    body, _row = _drawer(env)
    assert "None of this chip's 4 Param History snapshots" in _t(body)
    assert "Open this cell again in a moment" not in body
    assert f'name="folder" value="{env["live"]}">Open this chip</button>' in body


def test_snapshots_that_cannot_be_listed_end_once_per_process(tmp_path, monkeypatch):
    """The projector could not list the snapshots: the build ends with nothing
    looked at. This process says so and never builds again on every view (a
    loop of "being built"); a new process tries once more."""
    env = _archived(tmp_path)
    real = routes._hub_archive_sources

    def unlistable(hm, target_path, chip_dir):
        def snaps():
            raise OSError("the history folder cannot be read")
        return snaps, real(hm, target_path, chip_dir)[1]
    monkeypatch.setattr(routes, "_hub_archive_sources", unlistable)
    grid = _grid(env)
    assert 'data-vh-reason="snapshots_unreadable"' in grid and "Try again" not in grid
    queued = _queued_only(monkeypatch)
    again = _grid(env)
    assert not queued and 'data-vh-reason="snapshots_unreadable"' in again
    monkeypatch.undo()                                 # readable again, in a new process
    hub.Hub.for_chip(env["dir"]).release()
    hub_sync._SYNCS.pop(hub_sync._norm(env["dir"]))
    assert "4 recorded changes shown" in _t(_grid(env))


def test_a_chip_whose_snapshots_are_all_runs_shows_those_runs(tmp_path):
    env = _archived(tmp_path, runs_only=True)
    grid = _grid(env)
    assert "2 recorded changes shown" in _t(grid)
    assert "History built from 2 run captures whose folders are gone." in _t(grid)
    _body, row = _drawer(env)
    assert [(v["value"], v["label"]) for v in row["values"]] == [
        (9.0e-5, "first recorded in #7"), (8.0e-5, "saved in #8 scan")]


def test_no_ledger_offers_no_try_again(tmp_path):
    env = _archived(tmp_path)
    body = env["client"].get("/param-history", query_string={"chip_key": "__no_such_chip__"},
                             headers=HX).get_data(as_text=True)
    # S10 walk F1 + F3 merged: the reason in plain words
    assert 'data-vh-reason="no_ledger"' in body and "No change history has been built for this chip yet" in body
    assert "Try again" not in body


# ======================================================================
# 5. S10 final review: SM's capture of the live folder after adopting a run
# ======================================================================

@pytest.mark.parametrize("drop_ledger", [False, True])
def test_an_adopt_capture_is_the_live_folder_never_a_run(tmp_path, drop_ledger):
    """Every capture is SM's own capture of the LIVE folder after adopting a
    run's write (``auto``, kind ``exp``); the live folder still opens as the
    chip. The grid and the cell drawer offer Open, nothing counts them as
    captures of runs, and no note says no folder opens as the chip."""
    app = make_app(tmp_path)
    live = tmp_path / "chip"
    _write(live, chip_state(t1=1.0e-5))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    with app.app_context():
        directory = Path(routes._hub_chip_dir(routes._active_ctx()["path"]))
        hm = routes._history()
        for t1 in (2.0e-5, 3.0e-5):
            _write(live, chip_state(t1=t1))
            assert hm.check_and_snapshot(str(live), "auto", kind="exp", force=True) is not None
    other = tmp_path / "other"
    _write(other, chip_state(name="other"), {"network": {"host": "127.0.0.2"}})
    assert c.post("/load", data={"folder": str(other)}).status_code in (200, 302)
    hub.Hub.for_chip(directory).release()
    if drop_ledger:
        from quam_state_manager.core import hub_index
        hub_index.close_readers()
        for n in ("ledger.sqlite", "ledger.sqlite-wal", "ledger.sqlite-shm"):
            (directory / n).unlink(missing_ok=True)
    env = {"client": c, "key": directory.name, "dir": directory}
    grid = _grid(env)
    body, row = _drawer(env)
    for page in (grid, body):
        assert "No folder on disk opens as this chip now" not in page
        assert "run capture" not in page and "from runs" not in page
        assert f'name="folder" value="{live}">Open this chip</button>' in page
    # the states SM saw of the live folder are in the history, as states SM saw
    assert [v["value"] for v in row["values"]][-2:] == [2.0e-5, 3.0e-5]
    assert row["values"][-1]["label"] == "seen by SM (auto snapshot)"


# ======================================================================
# 6. S10 final review: only a wait says wait
# ======================================================================

def test_a_grid_read_that_fails_says_so_and_a_bug_is_a_real_error(tmp_path, monkeypatch, caplog):
    from quam_state_manager.core import ramcache
    from quam_state_manager.web.hub_status import LedgerUnreadable
    env = _archived(tmp_path)
    assert "4 recorded changes shown" in _t(_grid(env))

    def unreadable(*a, **k):
        raise LedgerUnreadable("DatabaseError: database disk image is malformed")
    monkeypatch.setattr(routes, "_hub_grid_rows", unreadable)
    grid = _grid(env)
    # S10 walk F1 + F3 merged: the reason in plain words
    assert 'data-vh-reason="unreadable"' in grid and "The change history file could not be read" in grid
    assert "is busy" not in grid

    def warming(*a, **k):
        raise ramcache.Warming("test", "key", 0)
    monkeypatch.setattr(routes, "_hub_grid_rows", warming)
    grid = _grid(env)
    assert 'data-vh-mode="preparing"' in grid and "is busy" not in grid

    def bug(*a, **k):
        raise RuntimeError("presenter bug")
    monkeypatch.setattr(routes, "_hub_grid_rows", bug)
    env["app"].config["PROPAGATE_EXCEPTIONS"] = False
    r = env["client"].get("/param-history", query_string={"chip_key": env["key"], "since": "all"},
                          headers=HX)
    assert r.status_code == 500 and "is busy" not in r.get_data(as_text=True)
    assert any(rec.exc_info and "presenter bug" in str(rec.exc_info[1]) for rec in caplog.records)
