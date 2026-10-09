"""S10 walk (perf): while the change ledger is being built, every history
surface answers from the build's status alone -- fast, never waiting on a lock
something else holds -- the build keeps going while requests run, and its
progress names what it is doing from the first second.

Pins, on generic synthetic chips and run folders in ``tmp_path``:

* every surface the walk opened during a catch-up answers "being built"
  while the chip's state lock, the ledger's writer lock and the run sync's
  own lock are ALL held by another thread (the walk: a first diagnostics
  lint held the chip's state lock for seconds, and every surface waited on
  it just to say "building");
* the Calibration log answers "building" without indexing the data folders
  first (the walk: 3-9 s of Datasets scanning to say "building");
* progress phases: looking for run folders -> looking through run folders
  (n of N) -> matching a new data folder's runs to the history (n of N) ->
  n of N runs; every surface says the same words;
* the projector shares the interpreter with requests instead of stopping:
  a slice keeps working for a short quantum while a request is in flight;
* a data folder that moved is matched to the runs the ledger holds -- a run
  whose identity AND saved-state hash agree is re-pointed (a location), a run
  whose saved state changed is ingested as its own event;
* the moved-folder catch-up's two measured wastes are gone without changing
  an answer: the pair hash is the same digest without concatenating the
  pair, and the alternate state layout is searched only when the standard
  one is absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from quam_state_manager.core import hub, hub_build, hub_rules as rules, hub_sync
from quam_state_manager.core.hub_store import HubStore

from tests.test_hub_drawer import chip_dir, chip_state, make_app, run, write_chip  # noqa: F401
from tests.test_hub_drawer import _inline  # noqa: F401  (autouse: project inline)

HX = {"HX-Request": "true"}


@pytest.fixture
def building(tmp_path):
    """A live chip with a declared data folder of three runs whose ledger
    catch-up has NOT run (the open registered it; nothing ingested): every
    history surface is in ``building``."""
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    for i in (1, 2, 3):
        run(data, i, chip_state(t1=1.0e-5 * i))
    write_chip(live, chip_state(t1=3.0e-5), data)
    app = make_app(tmp_path, sync=False)
    c = app.test_client()
    assert c.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    sm = {"app": app, "client": c, "live": live, "data": data, "tmp": tmp_path}
    assert hub_sync.status(chip_dir(sm))["state"] == "building"
    return sm


def _store(sm):
    with sm["app"].app_context():
        from quam_state_manager.web import routes
        return routes._store()


@contextmanager
def held(sm):
    """Another thread holds the chip's state lock, the ledger's writer lock
    and the run sync's lock until the block ends."""
    locks = [_store(sm)._lock, hub._writer_for(chip_dir(sm)), hub_sync.sync_for(chip_dir(sm)).lock]
    got, release = threading.Event(), threading.Event()

    def holder():
        for lk in locks:
            lk.acquire()
        got.set()
        release.wait(60)
        for lk in reversed(locks):
            lk.release()
    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert got.wait(10)
    try:
        yield
    finally:
        release.set()
        t.join(10)


COLUMN = ("POST", "/bulk/column-history",
          {"col_key": "T1", "label": "T1", "unit": "s", "paths": json.dumps({"qA1": "qubits.qA1.T1"})})
#: the surfaces the walk opened at once during the catch-up, as the page asks them
SURFACES = {
    "drawer": ("GET", "/field/history?path=qubits.qA1.T1", None),
    "column": COLUMN,
    "agent": ("GET", "/api/agent/field-history?path=qubits.qA1.T1", None),
    "trends": ("GET", "/topology/trends?metrics=T1", None),
    "metric_meta": ("GET", "/topology/metric-meta", None),
    "param_history": ("GET", "/param-history?since=all", None),
    "changes": ("GET", "/param-history/changes", None),
    "report_trends": ("GET", "/chip-status/report/section/trends?redact=0&window=all", None),
    # the Calibration log's body, which its "building the history" line asks
    # for again every 2 s (the page itself carries the page shell, which reads
    # the chip's state like every page)
    "journal_day": ("GET", "/journal/day", None),
}


def ask(sm, name, timeout=20.0):
    """One surface request on its own thread: (seconds, body), or None when it
    did not answer within *timeout* (it is waiting on something)."""
    method, url, form = SURFACES[name]
    out = {}

    def go():
        t0 = time.perf_counter()
        r = (sm["client"].post(url, data=form, headers=HX) if method == "POST"
             else sm["client"].get(url, headers=HX))
        out["r"] = (time.perf_counter() - t0, r.status_code, r.data.decode())
    t = threading.Thread(target=go, daemon=True)
    t.start()
    t.join(timeout)
    return out.get("r")


def said(body: str) -> str:
    if body.lstrip().startswith("{"):
        return body
    body = re.sub(r"<script.*?</script>", " ", body, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).replace("&#39;", "'")


# ======================================================================
# 1. a request during building answers from the status alone
# ======================================================================

class TestAnswersWithoutWaiting:
    @pytest.mark.parametrize("name", sorted(SURFACES))
    def test_a_surface_says_building_while_every_lock_is_held(self, building, name):
        ask(building, name)                     # warm: a first request may import templates
        with held(building):
            got = ask(building, name, timeout=15.0)
            assert got is not None, f"{name} waited on a held lock to say 'building'"
        secs, status, body = got
        assert status == 200, (name, status, said(body)[:300])
        assert "being built" in said(body) or "building the history" in said(body), said(body)[:400]


class TestCalibrationLogBuilding:
    def test_the_log_says_building_without_indexing_the_data_folders(self, building, monkeypatch):
        from quam_state_manager.web import routes
        calls = []
        real = routes._dataset_store
        monkeypatch.setattr(routes, "_dataset_store", lambda *a, **k: calls.append(1) or real(*a, **k))
        body = said(building["client"].get("/journal/day", headers=HX).data.decode())
        assert "building the history" in body and not calls, \
            "the data folders were indexed just to say 'building'"
        # not vacuous: once the history answers, the day reads the data folders
        monkeypatch.setattr(hub_sync, "status", lambda _d: {"state": "ready", "done": 3, "total": 3})
        building["client"].get("/journal/day", headers=HX)
        assert calls, "the log never reads the data folders at all"


class TestFirstLint:
    def test_the_synth_modules_are_imported_before_the_state_lock_is_taken(self, building, monkeypatch):
        """The first lint used to import scipy's window / filter modules INSIDE
        its walk, holding the chip's state lock all the while (seconds on a busy
        cold start). It imports them first, outside the lock."""
        from quam_state_manager.core import diagnostics, waveform_synth
        store = _store(building)
        seen = []
        monkeypatch.setattr(waveform_synth, "preload_scipy",
                            lambda: seen.append(store._lock._is_owned()))
        diagnostics._lint_state_cache.pop(store, None)
        diagnostics.lint_state(store)
        assert seen == [False], f"preload ran {len(seen)} times / under the lock: {seen}"


# ======================================================================
# 2. progress that names what the build is doing
# ======================================================================

def _slices_until(cs, st, pred, cap=200):
    """One item per slice (a request 'waits' the whole time) until *pred*."""
    for _ in range(cap):
        if pred(cs.status()):
            return cs.status()
        if not cs.run_slice(st, None, should_yield=lambda: True):
            break
    return cs.status()


class TestProgressPhases:
    def test_a_fresh_history_looks_for_then_through_run_folders_then_counts_runs(self, tmp_path):
        data, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 6):
            run(data, i, chip_state(t1=1.0e-5 * i))
        cs = hub_sync.open_chip(chip, [(str(data), "declared")], kick=False)
        st0 = cs.status()
        assert st0["state"] == "building"
        # S10 walk (N8): ": looking for run folders" -> the step it is on (each step counts its own work)
        assert hub_sync.progress_words(st0) == ": step 1 of 2, looking for run folders"
        with HubStore(chip) as st:
            first = _slices_until(cs, st, lambda s: s["phase"] == "reading")
            assert first["total"] == 5 and first["looked"] >= 1
            assert hub_sync.progress_words(first) == (
                f": step 1 of 2, looking through run folders ({first['looked']} of 5)")
            later = _slices_until(cs, st, lambda s: s["looked"] > first["looked"])
            assert later["looked"] > first["looked"], "the count stood still while folders were read"
            ing = _slices_until(cs, st, lambda s: s["phase"] in ("ingesting", "matching"))
            assert ing["phase"] == "ingesting", "a first build is not a match of a moved folder"
            # S10 walk (N8): " (n of 5 runs)" -> its own step, so the count never reads as going down
            assert hub_sync.progress_words(ing) == f": step 2 of 2, adding runs ({ing['done']} of 5)"
            while cs.run_slice(st, None):
                pass
        assert cs.status()["state"] == "ready"

    def test_a_moved_folder_is_matched_and_its_identical_runs_re_pointed(self, tmp_path):
        """The walk's case: the ledger holds the runs of one folder, the chip
        now names another path holding the same run folders. Each run whose
        identity AND saved-state hash agree becomes a location of the event
        the ledger holds (never ingested again); a run whose saved state
        differs in the moved copy is ingested as its own event."""
        old, new, chip = tmp_path / "old" / "data", tmp_path / "moved" / "data", tmp_path / "chip"
        for i in range(1, 6):
            run(old, i, chip_state(t1=1.0e-5 * i))
        hub_sync.open_chip(chip, [(str(old), "declared")])          # inline: builds fully
        with HubStore(chip) as st:
            before = {r["eid"]: r["state_hash"] for r in st.conn.execute(
                "SELECT eid, state_hash FROM events WHERE kind='run'")}
        assert len(before) == 5
        shutil.copytree(old, new)
        # one run of the moved copy now saves another state (same run, other bytes)
        changed = next(new.glob("*/#3_*")) / "quam_state" / "state.json"
        changed.write_text(json.dumps(chip_state(t1=9.9e-5)), encoding="utf-8")
        cs = hub_sync.open_chip(chip, [(str(new), "declared")], kick=False)
        with HubStore(chip) as st:
            m = _slices_until(cs, st, lambda s: s["phase"] in ("ingesting", "matching"))
            assert m["phase"] == "matching", m["phase"]
            # S10 walk (N8): the step it is on, before the phase's own count
            assert hub_sync.progress_words(m) == (
                f": step 2 of 2, matching a new data folder's runs to the runs recorded ({m['done']} of 5)")
            while cs.run_slice(st, None):
                pass
            runs = {r["eid"]: dict(r) for r in st.conn.execute("SELECT * FROM events WHERE kind='run'")}
            new_id = st.conn.execute("SELECT root_id FROM roots WHERE path=?",
                                     (os.path.normcase(str(new.resolve())),)).fetchone()[0]
            locs = {r[0]: r[1] for r in st.conn.execute(
                "SELECT rel_path, eid FROM locations WHERE root_id=?", (new_id,))}
        assert cs.counts["location"] == 4, dict(cs.counts)
        assert len(runs) == 6, "the changed run was not ingested as its own event"
        moved3 = locs[next(r for r in locs if "/#3_" in r)]
        assert moved3 not in before and runs[moved3]["run_id"] == 3
        assert runs[moved3]["state_hash"] == rules.state_hash(*hub_build.read_pair(changed.parent.parent))
        for rel, eid in locs.items():
            if "/#3_" not in rel:
                assert eid in before, f"{rel} was ingested again instead of re-pointed"
                assert runs[eid]["state_hash"] == rules.state_hash(*hub_build.read_pair(new / rel))
        assert hub_sync.status(chip)["state"] == "ready"

    # S10 walk (N8): a status that names its steps (data folders, the states SM saw) says
    # which step it is on -- "1864 of 3521" then "925 of 3521" read as a count going down
    @pytest.mark.parametrize("phase,extra,words", [
        ("idle", {"total": 0}, "being built: step 1 of 3, looking for run folders."),
        ("reading", {"total": 40, "looked": 7}, "being built: step 1 of 3, looking through run folders (7 of 40)."),
        ("matching", {"total": 40, "done": 12},
         "being built: step 2 of 3, matching a new data folder's runs to the runs recorded (12 of 40)."),
        ("ingesting", {"total": 40, "done": 12}, "being built: step 2 of 3, adding runs (12 of 40)."),
        ("observing", {"total": 40, "done": 40, "observed_done": 5, "observed_total": 9},
         "being built: step 3 of 3, importing the states SM saw (5 of 9)."),
    ])
    def test_every_surface_says_the_same_phase(self, building, monkeypatch, phase, extra, words):
        st = dict({"state": "building", "phase": phase, "done": 0, "roots": [{"path": "x"}],
                   "observes": True}, **extra)
        monkeypatch.setattr(hub_sync, "status", lambda _d: st)
        c = building["client"]
        for name in ("drawer", "column", "trends", "param_history", "changes"):
            got = ask(building, name)
            assert words in said(got[2]), (name, said(got[2])[:300])
        agent = c.get("/api/agent/field-history?path=qubits.qA1.T1").get_json()
        assert words in agent["note"]
        versions = said(c.get("/state/versions", headers=HX).data.decode())
        assert words[:-1] + ". Until it is complete" in versions, versions[:400]
        short = said(c.get("/journal/day", headers=HX).data.decode())
        want = {"idle": "building the history: step 1 of 3, looking for run folders",
                "reading": "building the history: step 1 of 3, looking through run folders (7/40)",
                "matching": "building the history: step 2 of 3, matching a new data folder's runs to "
                            "the runs recorded (12/40)",
                "ingesting": "building the history: step 2 of 3, adding runs (12/40)",
                "observing": "building the history: step 3 of 3, importing the states SM saw (5/9)"}[phase]
        assert want in short, short[:400]
        report = said(c.get("/chip-status/report/section/calibration_log?redact=0").data.decode())
        assert want + ": days that hold only runs" in report, report[:400]


# ======================================================================
# 3. the projector shares the interpreter with requests
# ======================================================================

class TestProjectorShares:
    def test_a_slice_keeps_working_while_a_request_is_in_flight(self, tmp_path, monkeypatch):
        from quam_state_manager.core import run_ingest
        data, chip = tmp_path / "data", tmp_path / "chip"
        for i in range(1, 13):
            run(data, i, chip_state(t1=1.0e-5 * i))
        cs = hub_sync.open_chip(chip, [(str(data), "declared")], kick=False)
        waits = []
        real_wait = run_ingest.FOREGROUND.wait_idle
        monkeypatch.setattr(run_ingest.FOREGROUND, "wait_idle",
                            lambda quiet_s=0.25, max_s=3.0: waits.append(max_s) or real_wait(quiet_s, 0.0))
        monkeypatch.setattr(hub_sync, "BUSY_QUANTUM_S", 30.0)       # a slow PC: still > 1 item
        proj = hub._Projector()
        proj.kick_sync = lambda h: None                                # one slice, no follow-up
        run_ingest.FOREGROUND.enter()                                  # a request is in flight
        try:
            proj._sync_one(hub.Hub.for_chip(chip))
        finally:
            run_ingest.FOREGROUND.exit()
            hub.Hub.for_chip(chip).release()
        assert waits and max(waits) <= hub_sync.BUSY_PAUSE_S, \
            f"the slice waited up to {max(waits) if waits else None} s for a quiet moment"
        assert cs.looked > 1, "the slice stopped after one item while a request was in flight"


# ======================================================================
# 4. the moved-folder catch-up's measured wastes, same answers
# ======================================================================

class TestSameAnswersCheaper:
    @pytest.mark.parametrize("sizes", [(0, 0), (2, 8), (70_000, 3_000), (1_000_000, 0)])
    def test_the_pair_hash_is_the_joined_pair_digest(self, sizes):
        import random
        rnd = random.Random(sum(sizes))
        state, wiring = rnd.randbytes(sizes[0]), rnd.randbytes(sizes[1])
        assert rules.state_hash(state, wiring) == hashlib.sha1(state + b"\0" + wiring).hexdigest()

    def test_the_alternate_layouts_are_searched_only_when_no_standard_state(self, tmp_path, monkeypatch):
        std = tmp_path / "std"
        (std / "quam_state").mkdir(parents=True)
        (std / "quam_state" / "state.json").write_text("{}")
        (std / "zz").mkdir()
        (std / "zz" / "state.json").write_text("{}")
        top = tmp_path / "top"
        top.mkdir()
        (top / "state.json").write_text("{}")
        alt = tmp_path / "alt"
        (alt / "b").mkdir(parents=True)
        (alt / "b" / "state.json").write_text("{}")
        (alt / "a").mkdir()
        (alt / "a" / "state.json").write_text("{}")
        none = tmp_path / "none"
        none.mkdir()
        assert hub_build.state_paths(alt)[0] == alt / "a" / "state.json"
        assert hub_build.state_paths(none)[0] == none / "quam_state" / "state.json"
        globbed = []
        real_glob = Path.glob
        monkeypatch.setattr(Path, "glob", lambda self, pat, **kw: globbed.append(self) or real_glob(self, pat, **kw))
        assert hub_build.state_paths(std) == (std / "quam_state" / "state.json",
                                              std / "quam_state" / "wiring.json")
        assert hub_build.state_paths(top)[0] == top / "state.json"
        assert not globbed, "a run with a standard saved state was searched for other layouts"


class TestColumnPayload:
    def test_a_paths_list_is_a_bad_request_not_a_server_error(self, building):
        try:
            r = building["client"].post("/bulk/column-history", data={
                "col_key": "T1", "label": "T1", "paths": json.dumps(["qubits.qA1.T1"])}, headers=HX)
            status, data = r.status_code, r.data
        except AttributeError as exc:       # a TESTING app re-raises what a server answers with a 500
            status, data = 500, str(exc).encode()
        assert status == 400 and b"bad paths payload" in data, (status, data[:200])
