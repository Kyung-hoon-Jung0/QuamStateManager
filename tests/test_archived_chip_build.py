"""S10 walk: an archived chip with no change ledger gets its history back.

Before S10 Param History drew an archived chip's history from its snapshots.
After S10 every surface reads the change ledger, and a chip that never had one
(last opened before ledgers existed, or its live folder is gone) showed only
"could not be read (no_ledger)" with a Try again that could never succeed.

Pins, on generic synthetic chips in ``tmp_path`` (each mutation-checked):

* the journey: viewing such a chip builds its ledger from its own Param
  History snapshots -- each one that is not a run becomes an ``observed``
  event ("seen by SM", writer unknown); a run's saved copy stays out -- and
  the grid, its sparkline, the cell drawer and Changes show those values with
  that provenance; no live or data folder is written;
* the build runs off the request thread: the first answer is "being built",
  counted in snapshots, and asks again by itself; a build cut short (the
  process ended) is resumed, never read as complete;
* snapshots that cannot be read end ``unavailable`` in plain words, with no
  Try again and no rebuild loop; a chip whose snapshots are all run captures
  says so, and builds nothing;
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


def _archived(tmp_path, *, keep_live: bool = False, corrupt: bool = False,
              runs_only: bool = False, name: str = "device") -> dict:
    """An instance whose Param History captured chip *name* before it had a
    ledger (the chip is never opened here), then another chip opened. Its
    captures: its first state (manual, T1 1e-5), an outside edit SM saw (auto,
    2e-5), a run's saved copy (experiment, 9e-5: never imported) and SM's own
    capture of the live folder after adopting a run (auto, kind exp, 3e-5).
    ``runs_only``: only run captures. ``corrupt``: the non-run captures'
    files are unreadable. ``keep_live``: the chip's live folder stays."""
    app = make_app(tmp_path, sync=False)
    live = tmp_path / "chips" / "archived"
    run_dir = tmp_path / "runs" / "2026-01-01" / "#7_scan_120000"
    with app.app_context():
        hm = routes._history()
        own = []
        if not runs_only:
            own.append(_snap(hm, live, chip_state(t1=1.0e-5, name=name), "manual", kind="manual"))
            own.append(_snap(hm, live, chip_state(t1=2.0e-5, name=name), "auto"))
        _snap(hm, live, chip_state(t1=9.0e-5, name=name), "experiment", kind="exp", run_id=7,
              experiment_name="scan", experiment_folder_path=str(run_dir))
        if runs_only:
            _snap(hm, live, chip_state(t1=8.0e-5, name=name), "experiment", kind="exp", run_id=8,
                  experiment_name="scan", experiment_folder_path=str(run_dir.parent / "#8_scan"))
        else:
            own.append(_snap(hm, live, chip_state(t1=3.0e-5, name=name), "auto", kind="exp"))
        directory = Path(hm.resolve_chip_dir(str(live))[0])
        hm._join_deferred_index()
    other = tmp_path / "chips" / "open"
    _write(other, chip_state(name="other"), {"network": {"host": "127.0.0.2", "cluster_name": "C2"}})
    client = app.test_client()
    assert client.post("/load", data={"folder": str(other)}).status_code in (200, 302)
    if corrupt:
        for meta in own:
            (directory / meta.timestamp / "state.json").write_text("{not json", encoding="utf-8")
    if not keep_live:
        shutil.rmtree(live)
    assert not (directory / "ledger.sqlite").exists()
    return {"app": app, "client": client, "live": live, "dir": directory, "key": directory.name}


def _grid(env, **q) -> str:
    r = env["client"].get("/param-history", query_string={"chip_key": env["key"], "since": "all",
                                                           "props": "T1", **q}, headers=HX)
    assert r.status_code == 200, r.data[:400]
    return r.get_data(as_text=True)


def _drawer(env) -> tuple[str, dict]:
    r = env["client"].get("/param-history/expand", query_string={
        "chip_key": env["key"], "qubit": "qA1", "prop": "T1"}, headers=HX)
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


# ======================================================================
# 1. the journey: no ledger -> built from the snapshots -> values shown
# ======================================================================

def test_an_archived_chip_with_no_ledger_shows_its_snapshots_values(tmp_path):
    env = _archived(tmp_path)
    grid = _grid(env)
    assert 'data-vh-mode="unavailable"' not in grid and "could not be read" not in grid
    assert (env["dir"] / "ledger.sqlite").is_file()
    # the grid: three states SM saw, never the run's saved copy
    assert "3 recorded changes shown (3 events in the change ledger)" in _t(grid)
    spark = _cell(grid, "qA1", "T1")
    dots = re.findall(r'<circle class="hs-pt (hs-pt-\w+)" cx="[\d.]+" cy="[\d.]+" r="1.4"/>', spark)
    assert dots == ["hs-pt-manual", "hs-pt-auto", "hs-pt-auto"], spark
    # the cell drawer: each value with its provenance
    _body, row = _drawer(env)
    pts = [(v["value"], v["provenance"], v["label"], v["sub"]) for v in row["values"]]
    assert pts == [(1.0e-5, "observed", "seen by SM (manual snapshot)", "writer unknown"),
                   (2.0e-5, "observed", "seen by SM (auto snapshot)", "writer unknown"),
                   (3.0e-5, "observed", "seen by SM (auto snapshot)", "writer unknown")], pts
    assert all(v["run"] is None and v["uid"] is None for v in row["values"])
    # Changes: the archived chip's own events, its chip kept on every link
    page = env["client"].get("/param-history/changes", query_string={"chip_key": env["key"]},
                             headers=HX).get_data(as_text=True)
    assert "seen by SM (auto snapshot)" in page and "qubits.qA1.T1" in page
    assert "9e-05" not in page and "9.0e-05" not in page
    assert f"chip_key={env['key']}" in page and "/field/history?path=" not in page
    found = env["client"].get("/param-history/param-search",
                              query_string={"q": "qA1.T1", "chip_key": env["key"]}).get_json()
    assert [(r["path"], r["changes"]) for r in found["results"]
            if r["path"] == "qubits.qA1.T1"] == [("qubits.qA1.T1", 3)], found
    # what was built is said: from its 3 snapshots that are not runs; the run
    # capture is not in it and cannot be added (no folder opens as the chip)
    assert 'data-note="archive_built"' in grid
    assert "Built from this chip's 3 Param History snapshots that are not a run" in _t(grid)
    assert "1 run capture of this chip that its change ledger does not" in _t(grid)
    assert "so its runs cannot be added here" in _t(grid) and "Open this chip" not in grid
    # the build is finished and recorded as one from snapshots
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == [("done",)]
    kinds = _ledger(env, "SELECT kind, COUNT(*) FROM events GROUP BY kind")
    assert kinds == [("observed", 3)], kinds


def test_the_build_reads_and_never_writes_the_live_folder(tmp_path):
    env = _archived(tmp_path, keep_live=True)
    before = _tree_sig(env["live"])
    grid = _grid(env)
    assert "3 recorded changes shown" in _t(grid)
    assert _tree_sig(env["live"]) == before
    # the live folder still opens as this chip: Open stays offered beside the values
    assert f'name="folder" value="{env["live"]}">Open this chip</button>' in grid
    assert "Open this chip, then link the folder its runs are saved in." in grid
    # opened live, the folder keeps the ledger: no longer one built from snapshots alone
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == [("done",)]
    assert env["client"].post("/load", data={"folder": str(env["live"])}).status_code in (200, 302)
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == []


def test_a_snapshot_whose_files_are_gone_is_counted_as_unreadable(tmp_path):
    env = _archived(tmp_path)
    with env["app"].app_context():
        hm = routes._history()
        own = [m for m in hm.list_snapshots(routes._path_for_chip_key(env["key"]))
               if not routes._is_run_snapshot(m)]
    (env["dir"] / own[-1].timestamp / "state.json").unlink()
    grid = _grid(env)
    note = re.search(r'data-note="archive_built"[^>]*>([^<]*)<', grid).group(1)
    assert "Built from this chip&#39;s 3 Param History snapshots" in note
    assert "1 could not be read and is left out." in html.unescape(note), note
    assert "2 recorded changes shown" in _t(grid)


def test_an_index_still_being_prepared_is_a_wait_never_a_grid_with_no_qubit(tmp_path, monkeypatch):
    from quam_state_manager.core import hub_index, ramcache
    env = _archived(tmp_path)
    assert "3 recorded changes shown" in _t(_grid(env))
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
# 2. off the request thread, counted in snapshots, resumed when cut short
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
    assert _one_slice(env) is True                    # one snapshot looked at, two to go
    second = _grid(env)
    assert 'data-vh-mode="building"' in second
    assert "(1 of 3)" in _t(second), _t(second)
    while _one_slice(env):
        pass
    done = _grid(env)
    assert "3 recorded changes shown" in _t(done) and 'data-vh-mode=' not in done


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
    assert "3 recorded changes shown" in _t(grid), _t(grid)[-400:]
    assert _ledger(env, "SELECT v FROM meta WHERE k='archive_build'") == [("done",)]


# ======================================================================
# 3. end states in plain words: no Try again, no rebuild loop
# ======================================================================

def test_unreadable_snapshots_end_unavailable_in_plain_words(tmp_path):
    env = _archived(tmp_path, corrupt=True, keep_live=True)
    for _ in range(2):                                 # a second view: the same end, no rebuild
        grid = _grid(env)
        assert 'data-vh-mode="unavailable"' in grid and 'data-vh-reason="snapshots_unreadable"' in grid
        assert ("None of this chip's 3 Param History snapshots that are not a run could be read, "
                "so its change history cannot be built from them.") in _t(grid)
        assert "Try again" not in grid and "load delay:" not in grid and "history-cell" not in grid
        assert f'name="folder" value="{env["live"]}">Open this chip</button>' in grid
        assert _ledger(env, "SELECT outcome, COUNT(*) FROM observed_snapshots GROUP BY outcome") \
            == [("unreadable", 3)]
    body, _row = _drawer(env)
    assert "None of this chip's 3 Param History snapshots" in _t(body)
    assert "Open this cell again in a moment" not in body
    assert f'name="folder" value="{env["live"]}">Open this chip</button>' in body


def test_snapshots_that_cannot_be_listed_end_once_per_process(tmp_path, monkeypatch):
    """The projector could not list the snapshots (their sources unreadable):
    the build ends with nothing looked at. This process says so and never
    builds again on every view (a loop of "being built"); a new process
    tries once more."""
    env = _archived(tmp_path)
    with env["app"].app_context():
        hm = routes._history()

    def broken(*a, **k):
        raise OSError("the history folder cannot be read")
    monkeypatch.setattr(hm, "snapshot_sources", broken)
    grid = _grid(env)
    assert 'data-vh-reason="snapshots_unreadable"' in grid and "Try again" not in grid
    queued = _queued_only(monkeypatch)
    again = _grid(env)
    assert not queued and 'data-vh-reason="snapshots_unreadable"' in again
    monkeypatch.undo()                                 # readable again, in a new process
    hub.Hub.for_chip(env["dir"]).release()
    hub_sync._SYNCS.pop(hub_sync._norm(env["dir"]))
    assert "3 recorded changes shown" in _t(_grid(env))


def test_a_chip_whose_snapshots_are_all_runs_builds_nothing_and_says_why(tmp_path):
    env = _archived(tmp_path, runs_only=True)
    grid = _grid(env)
    assert 'data-vh-reason="no_snapshots"' in grid
    assert "All 2 of this chip's Param History snapshots are captures of runs." in _t(grid)
    assert "Try again" not in grid and "load delay:" not in grid
    assert not (env["dir"] / "ledger.sqlite").exists()
    body, _row = _drawer(env)
    assert "All 2 of this chip's Param History snapshots are captures of runs." in _t(body)


def test_no_ledger_offers_no_try_again(tmp_path):
    env = _archived(tmp_path)
    body = env["client"].get("/param-history", query_string={"chip_key": "__no_such_chip__"},
                             headers=HX).get_data(as_text=True)
    assert 'data-vh-reason="no_ledger"' in body and "could not be read (no_ledger)" in body
    assert "Try again" not in body


# ======================================================================
# 4. S10 final review: SM's capture of the live folder after adopting a run
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
# 5. S10 final review: only a wait says wait
# ======================================================================

def test_a_grid_read_that_fails_says_so_and_a_bug_is_a_real_error(tmp_path, monkeypatch, caplog):
    from quam_state_manager.core import ramcache
    from quam_state_manager.web.hub_status import LedgerUnreadable
    env = _archived(tmp_path)
    assert "3 recorded changes shown" in _t(_grid(env))

    def unreadable(*a, **k):
        raise LedgerUnreadable("DatabaseError: database disk image is malformed")
    monkeypatch.setattr(routes, "_hub_grid_rows", unreadable)
    grid = _grid(env)
    assert 'data-vh-reason="unreadable"' in grid and "could not be read (unreadable)" in grid
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
