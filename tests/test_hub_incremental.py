"""docs/298 -- the ledger surfaces after a run, an undo or an edit: what is
derived again, and that every answer is the from-scratch answer.

Two layers keep work between requests:

* ``value_history.read`` keeps each path's answer and serves it for a later
  ledger state only when nothing it read changed: the ledger's rewrite log
  (kept by triggers) shows no in-place change of a path or event it read, and
  no event added since has a row on a holder spelling it asked about;
* ``hub_status.LedgerTable`` keeps each part with the FACTS of the chip's
  state it read, and serves it after an edit of the chip while they hold.

Every pin compares the served answer with the same request answered from
scratch (every kept part swapped for an empty cache, the read index rebuilt),
and checks what was derived again where that is the point.
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import os
import random
import re
import shutil
import sqlite3
import time
from types import SimpleNamespace

import pytest

from quam_state_manager.core import hub_index, hub_store, hub_sync, ramcache, value_history as vh
from quam_state_manager.web import hub_status, routes
from tests.test_hub_drawer import (  # noqa: F401 -- _inline is an autouse fixture
    _inline, chip_dir, chip_state, patch, run, sm, write_chip)

TRENDS = "/topology/trends?metrics=T1"
TRENDS_ALIAS = "/topology/trends?paths=qubits.qA1.xy.operations.x180.amplitude"
TRENDS_TYPED = "/topology/trends?path=qubits.qA2.f_01"
META = "/topology/metric-meta"
GRID = "/param-history?since=all"
CELL = "/param-history/expand?qubit=qA1&prop=T1"
CHANGES = "/param-history/changes"
DRAWER = "/field/history?path=qubits.qA1.xy.operations.x180.amplitude"
URLS = (TRENDS, TRENDS_ALIAS, TRENDS_TYPED, META, GRID, CELL, CHANGES, DRAWER)


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

@contextmanager
def from_scratch():
    """Every cache the incremental path keeps, swapped for an empty one (the
    read index too), and put back afterwards."""
    saved = (vh._KEYS, hub_status._CACHE, hub_status._ROWS, hub_index.INDEX_CACHE,
             dict(vh._RUN_ERAS), dict(hub_status._UID_MEMOS))
    tmp = [ramcache.KeyedMemo("t_keys", sizeof=lambda e: e.nbytes),
           ramcache.KeyedMemo("t_status"),
           ramcache.KeyedMemo("t_rows", sizeof=lambda v: 1),
           ramcache.KeyedMemo("t_index", sizeof=hub_index._index_bytes)]
    vh._KEYS, hub_status._CACHE, hub_status._ROWS, hub_index.INDEX_CACHE = tmp
    vh._RUN_ERAS.clear()
    hub_status._UID_MEMOS.clear()
    try:
        yield
    finally:
        vh._KEYS, hub_status._CACHE, hub_status._ROWS, hub_index.INDEX_CACHE = saved[:4]
        vh._RUN_ERAS.clear()
        vh._RUN_ERAS.update(saved[4])
        hub_status._UID_MEMOS.clear()
        hub_status._UID_MEMOS.update(saved[5])
        for m in tmp:
            m.clear()
            with ramcache._LOCK:
                if m in ramcache._MEMOS:
                    ramcache._MEMOS.remove(m)


def get(env, url, *, htmx=False) -> bytes:
    r = env["client"].get(url, headers={"HX-Request": "true"} if htmx else {})
    assert r.status_code == 200, (url, r.status_code, r.data[:300])
    return r.data


def scratch(env, url, *, htmx=False) -> bytes:
    with from_scratch():
        return get(env, url, htmx=htmx)


def assert_served_is_scratch(env, urls=URLS):
    for url in urls:
        served = get(env, url)
        assert served == scratch(env, url), f"{url}: the served answer is not the from-scratch one"


def history(env, paths: dict, limit=None) -> dict:
    with env["app"].test_request_context():
        ans = routes._value_history(routes._active_ctx(), paths, limit=limit)
    assert ans["mode"] == "ledger", ans["mode"]
    return ans


def canon(ans) -> str:
    return json.dumps(ans["rows"], sort_keys=True, default=repr)


def now_us() -> int:
    return int(time.time() * 1_000_000)


def new_run(env, rid, state, *, patches=None, t_us=None):
    """A run saved NOW (after every event the ledger holds), synced in."""
    run(env["data"], rid, state, patches=patches, t_us=now_us() if t_us is None else t_us)
    with env["app"].app_context():
        hub_sync.on_roots_moved([str(env["data"])])


def full_sync(env):
    cs = hub_sync.sync_for(chip_dir(env))
    cs.request(full=True)
    hub_sync._kick(cs)


def ledger(env):
    db = sqlite3.connect(str(chip_dir(env) / "ledger.sqlite"))
    db.row_factory = sqlite3.Row
    return db


def edit(env, path, value):
    r = env["client"].post("/field/edit", data={"dot_path": path, "value": value})
    assert r.status_code == 200, r.data[:300]


T1 = {"a": "qubits.qA1.T1", "b": "qubits.qA2.T1", "f": "qubits.qA2.f_01",
      "x": "qubits.qA1.xy.operations.x180.amplitude"}
BASE = dict(alias="#./x180_DragCosine", t1=3.0e-5, f01=5.1e9, amp=0.25)


def counting(monkeypatch, owner, name):
    calls = []
    real = getattr(owner, name)

    def wrapper(*a, **kw):
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(owner, name, wrapper)
    return calls


# ======================================================================
# 1. the rewrite log: every in-place change is logged, an append is not
# ======================================================================

class TestRewriteLog:
    def test_the_trigger_covers_every_events_column_but_the_two_it_names(self, sm):
        with ledger(sm) as db:
            cols = {r[1] for r in db.execute("PRAGMA table_info(events)")}
        assert set(hub_store.REWRITE_EVENT_COLUMNS) == cols - {"t_ord", "ord"}, \
            "a new events column must be decided: logged as the event's change, or why not"

    def test_an_append_logs_nothing_for_an_event_a_reader_saw(self, sm):
        with ledger(sm) as db:
            high = db.execute("SELECT MAX(eid) FROM events").fetchone()[0]
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) FROM rewrite_log").fetchone()[0]
        new_run(sm, 5, chip_state(**dict(BASE, t1=4e-5)), patches=[patch("qubits.qA1.T1", 4e-5, 3e-5)])
        edit(sm, "qubits.qA1.T1", "4.5e-5")
        assert sm["client"].post("/state/apply-to-live").status_code == 200
        with ledger(sm) as db:
            assert db.execute("SELECT COUNT(*) FROM rewrite_log WHERE seq > ? AND eid <= ?",
                              (seq, high)).fetchone()[0] == 0

    def test_an_undo_logs_the_write_it_takes_back(self, sm):
        edit(sm, "qubits.qA1.T1", "4.5e-5")
        assert sm["client"].post("/state/apply-to-live").status_code == 200
        with ledger(sm) as db:
            applied = db.execute("SELECT MAX(eid) FROM events WHERE kind='sm_apply'").fetchone()[0]
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) FROM rewrite_log").fetchone()[0]
        assert sm["client"].post("/undo").status_code == 200
        with ledger(sm) as db:
            every, eids, paths = hub_store.rewritten_since(db, seq, applied)
        assert not every and applied in eids, "the undone write's own facts (its flag) changed in place"

    def test_a_deleted_run_folder_logs_its_event(self, sm):
        with ledger(sm) as db:
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) FROM rewrite_log").fetchone()[0]
            gone = db.execute("SELECT eid FROM events WHERE run_id=2").fetchone()[0]
        shutil.rmtree(next(sm["data"].glob("*/#2_*")))
        full_sync(sm)
        with ledger(sm) as db:
            every, eids, _paths = hub_store.rewritten_since(db, seq, 10 ** 9)
        assert not every and gone in eids

    def test_a_reproof_logs_the_row_it_changed(self, sm):
        """node.json rewritten (its patches), the saved state the same: only
        ``changes.proven`` moves -- the case the read index's own check misses."""
        with ledger(sm) as db:
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) FROM rewrite_log").fetchone()[0]
        node = next(sm["data"].glob("*/#3_*")) / "node.json"
        body = json.loads(node.read_text(encoding="utf-8"))
        body["patches"] = [patch("qubits.qA1.f_01", 5.1e9, 5.0e9)]
        node.write_text(json.dumps(body), encoding="utf-8")
        full_sync(sm)
        with ledger(sm) as db:
            every, eids, paths = hub_store.rewritten_since(db, seq, 10 ** 9)
        assert not every and "qubits.qA1.f_01" in paths

    def test_a_deleted_change_row_is_logged_with_its_path(self, sm):
        """Every SM writer that deletes a change row also updates its event (a
        re-diff) or moves every event; the row's own entry is the trace of a
        delete by any other writer."""
        with ledger(sm) as db:
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) FROM rewrite_log").fetchone()[0]
            db.execute("DELETE FROM changes WHERE eid=(SELECT eid FROM events WHERE run_id=2) "
                       "AND pid=(SELECT pid FROM paths WHERE path='qubits.qA1.T1')")
            db.commit()
            every, eids, paths = hub_store.rewritten_since(db, seq, 10 ** 9)
        assert not every and paths == {"qubits.qA1.T1"} and not eids

    def test_a_changed_order_is_logged_as_every_event(self, sm):
        with ledger(sm) as db:
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) FROM rewrite_log").fetchone()[0]
            db.execute("UPDATE events SET ord = ord + 0.25 WHERE eid = (SELECT MAX(eid) FROM events)")
            db.commit()
            every, _eids, _paths = hub_store.rewritten_since(db, seq, 10 ** 9)
        assert every

    def test_a_removed_event_is_logged_as_every_event(self, sm):
        with ledger(sm) as db:
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) FROM rewrite_log").fetchone()[0]
            eid = db.execute("SELECT eid FROM events WHERE run_id=3").fetchone()[0]
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DELETE FROM events WHERE eid=?", (eid,))
            db.commit()
            every, _eids, _paths = hub_store.rewritten_since(db, seq, 10 ** 9)
        assert every

    def test_a_log_made_by_other_trigger_definitions_vouches_for_nothing(self, sm):
        with ledger(sm) as db:
            assert hub_store.rewrite_mark(db) is not None
            db.execute("UPDATE meta SET v='0-older' WHERE k='rewrite_log_version'")
            db.commit()
            assert hub_store.rewrite_mark(db) is None
        hub_store.HubStore(chip_dir(sm)).close()          # the next writer remakes it
        with ledger(sm) as db:
            assert hub_store.rewrite_mark(db) is not None

    def test_a_ledger_whose_log_is_incomplete_vouches_for_nothing_until_a_writer_remakes_it(self, sm):
        with ledger(sm) as db:
            epoch = hub_store.rewrite_mark(db)[0]
            db.execute("DROP TRIGGER rewrite_changes_upd")
            db.commit()
            assert hub_store.rewrite_mark(db) is None
        hub_store.HubStore(chip_dir(sm)).close()
        with ledger(sm) as db:
            mark = hub_store.rewrite_mark(db)
        assert mark is not None and mark[0] != epoch, "a remade log starts a new epoch"


# ======================================================================
# 2. value_history.read: a path's answer is kept only while it is the answer
# ======================================================================

class TestKeptAnswers:
    def test_a_new_run_rederives_only_the_paths_it_wrote(self, sm):
        a = history(sm, T1)
        new_run(sm, 5, chip_state(**dict(BASE, t1=4e-5)), patches=[patch("qubits.qA1.T1", 4e-5, 3e-5)])
        b = history(sm, T1)
        assert b["serials"]["a"] != a["serials"]["a"], "qA1.T1 has a new row: derived again"
        assert b["serials"]["b"] == a["serials"]["b"] and b["serials"]["x"] == a["serials"]["x"], \
            "a path the run did not write is served as it was"
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_a_new_alias_retarget_rederives_the_alias(self, sm):
        a = history(sm, T1)
        new_run(sm, 5, chip_state(**dict(BASE, alias="#./x180_Gauss")))
        b = history(sm, T1)
        assert b["serials"]["x"] != a["serials"]["x"], "the pointer the alias walks through moved"
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_a_path_that_did_not_exist_and_now_does_is_rederived(self, sm):
        paths = {"n": "qubits.qA2.T2echo"}
        a = history(sm, paths)
        assert a["rows"]["n"]["total"] == 0
        state = chip_state(**BASE)
        state["qubits"]["qA2"]["T2echo"] = 2.5e-5
        new_run(sm, 5, state)
        b = history(sm, paths)
        assert b["rows"]["n"]["total"] == 1, "a path asked about before it existed is watched too"

    def test_an_undo_rederives_the_paths_of_the_write_it_took_back(self, sm):
        edit(sm, "qubits.qA1.T1", "4.5e-5")
        assert sm["client"].post("/state/apply-to-live").status_code == 200
        a = history(sm, T1)
        assert sm["client"].post("/undo").status_code == 200
        b = history(sm, T1)
        assert b["serials"]["a"] != a["serials"]["a"]
        assert any(p.get("undone") for p in b["rows"]["a"]["points"]), "the apply is now marked undone"
        assert b["serials"]["f"] == a["serials"]["f"], "a path the undo never touched is kept"
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_a_flag_set_in_place_rederives_the_paths_that_read_the_event(self, sm):
        a = history(sm, T1)
        shutil.rmtree(next(sm["data"].glob("*/#2_*")))          # the run that set qA1.T1
        full_sync(sm)
        b = history(sm, T1)
        assert b["serials"]["a"] != a["serials"]["a"]
        assert "source_gone" in b["rows"]["a"]["points"][-1]["flags"]
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_a_reproof_rederives_the_path_whose_proof_moved(self, sm):
        a = history(sm, {"f": "qubits.qA1.f_01"})
        assert a["rows"]["f"]["points"][-1]["provenance"] == "run_saved"
        node = next(sm["data"].glob("*/#3_*")) / "node.json"
        body = json.loads(node.read_text(encoding="utf-8"))
        body["patches"] = [patch("qubits.qA1.f_01", 5.1e9, 5.0e9)]
        node.write_text(json.dumps(body), encoding="utf-8")
        full_sync(sm)
        b = history(sm, {"f": "qubits.qA1.f_01"})
        assert b["rows"]["f"]["points"][-1]["provenance"] == "run_proven", \
            "a row re-proven in place is never served as it was"

    def test_another_process_writing_in_place_is_seen(self, sm):
        a = history(sm, T1)
        with ledger(sm) as db:          # another process: its own connection, raw SQL
            eid = db.execute("SELECT eid FROM events WHERE run_id=2").fetchone()[0]
            db.execute("UPDATE events SET flags = flags | ? WHERE eid=?", (hub_store.SOURCE_GONE, eid))
            db.commit()
        b = history(sm, T1)
        assert b["serials"]["a"] != a["serials"]["a"]
        assert "source_gone" in b["rows"]["a"]["points"][-1]["flags"]
        assert b["serials"]["f"] == a["serials"]["f"]

    def test_a_run_placed_between_earlier_ones_keeps_what_it_did_not_touch(self, sm):
        a = history(sm, T1)
        # between run #2 and run #3 of the fixture (10 s apart), writing qA2.f_01 only
        state = chip_state(alias="#./x180_Gauss", t1=3.0e-5)
        state["qubits"]["qA2"]["f_01"] = 6.2e9
        run(sm["data"], 9, state, t_us=routes_t(sm, 2) + 5_000_000)
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        b = history(sm, T1)
        assert b["serials"]["f"] != a["serials"]["f"]
        # (run #3, after it, is re-diffed against it -- its rows on qA1.f_01, the
        # x180 amplitude and qA2.f_01 are rewritten in place: those paths are
        # derived again; qA2.T1 is in neither)
        assert b["serials"]["b"] == a["serials"]["b"], "a path neither run touched is kept"
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_a_run_placed_before_the_first_rederives_everything(self, sm):
        a = history(sm, T1)
        run(sm["data"], 0, chip_state(alias="#./x180_Gauss", t1=1.0e-5), t_us=routes_t(sm, 1) - 60_000_000)
        with sm["app"].app_context():
            hub_sync.on_roots_moved([str(sm["data"])])
        b = history(sm, T1)
        assert all(b["serials"][k] != a["serials"][k] for k in T1), "every key reads the ledger start"
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_a_rename_era_appended_rederives_everything(self, sm):
        a = history(sm, T1)
        state = chip_state(**BASE)
        state["extras"]["qubit_renames"] = [{"id": "r-generic", "qubits": {}}]
        new_run(sm, 5, state)
        b = history(sm, T1)
        assert all(b["serials"][k] != a["serials"][k] for k in T1)

    def test_a_reopened_reader_and_a_new_log_epoch_vouch_for_nothing(self, sm):
        a = history(sm, T1)
        hub_index.close_readers()
        b = history(sm, T1)
        assert all(b["serials"][k] != a["serials"][k] for k in T1), "a reopened ledger is read again"
        with ledger(sm) as db:
            db.execute("UPDATE meta SET v='0000' WHERE k='rewrite_epoch'")
            db.commit()
        new_run(sm, 5, chip_state(**dict(BASE, f01=5.2e9)))
        c = history(sm, T1)
        assert c["serials"]["a"] != b["serials"]["a"], "another epoch is another log"

    def test_a_log_trimmed_past_an_answer_vouches_for_nothing(self, sm):
        a = history(sm, T1)
        with ledger(sm) as db:
            db.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('rewrite_trimmed', '999999')")
            db.commit()
        new_run(sm, 5, chip_state(**dict(BASE, f01=5.2e9)))
        b = history(sm, T1)
        assert b["serials"]["a"] != a["serials"]["a"]

    def test_a_rewritten_run_folder_rederives_a_path_it_now_changes(self, sm):
        """The run's saved state rewritten after it was ingested: its rows are
        re-diffed in place -- here a row on a path it never changed before is
        INSERTED for that old event (the only trace of it in the log)."""
        a = history(sm, T1)
        folder = next(sm["data"].glob("*/#3_*"))
        st = json.loads((folder / "quam_state" / "state.json").read_text(encoding="utf-8"))
        st["qubits"]["qA2"]["T1"] = 7.7e-5
        (folder / "quam_state" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        full_sync(sm)
        b = history(sm, T1)
        # P0-1: old -> new, why: 7.7e-5 was a point of the series; #3 re-measured
        # qA2 and #4 saved 2e-5 back before anything read the chip -- an
        # excursion, so the re-derived row is listed apart, never dropped silently
        row = b["rows"]["b"]
        assert 7.7e-5 in [p["value"] for e in row["excursions"] for p in e["points"]]
        assert 7.7e-5 not in [p["value"] for p in row["points"]]
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))
        assert b["serials"]["b"] != a["serials"]["b"]

    def test_the_newest_run_rewritten_in_place_rederives_a_path_it_now_changes(self, sm):
        """Found by the equality harness: the run rewritten is the NEWEST event,
        so its re-diffed rows are inserted for the newest eid -- a log that took
        such an insert for "the event being created" missed it, and the metric
        meta kept naming an older run as the newest change."""
        new_run(sm, 5, chip_state(**dict(BASE, f01=5.2e9)))
        a = history(sm, T1)
        folder = next(sm["data"].glob("*/#5_*"))
        st = json.loads((folder / "quam_state" / "state.json").read_text(encoding="utf-8"))
        st["qubits"]["qA2"]["T1"] = 6.6e-5
        (folder / "quam_state" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        full_sync(sm)
        b = history(sm, T1)
        assert 6.6e-5 in [p["value"] for p in b["rows"]["b"]["points"]]
        assert b["serials"]["b"] != a["serials"]["b"]
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))
        assert get(sm, META) == scratch(sm, META)

    def test_an_sm_fact_changed_in_place_by_another_process_is_seen(self, sm):
        edit(sm, "qubits.qA1.T1", "4.5e-5")
        assert sm["client"].post("/state/apply-to-live").status_code == 200
        a = history(sm, T1)
        with ledger(sm) as db:
            eid = db.execute("SELECT MAX(eid) FROM events WHERE kind='sm_apply'").fetchone()[0]
            db.execute("UPDATE sm_events SET run_uid='k:9' WHERE eid=?", (eid,))
            db.commit()
        b = history(sm, T1)
        assert b["serials"]["a"] != a["serials"]["a"]
        assert any(p.get("run_uid") == "k:9" for p in b["rows"]["a"]["points"])

    def test_a_root_moved_in_place_rederives_everything(self, sm):
        a = history(sm, T1)
        with ledger(sm) as db:
            db.execute("UPDATE roots SET path = path || '_moved'")
            db.commit()
        b = history(sm, T1)
        assert all(b["serials"][k] != a["serials"][k] for k in T1), "every folder a point names moved"
        assert all("_moved" in (p.get("folder") or "_moved") for p in b["rows"]["a"]["points"])

    def test_an_event_removed_without_its_log_entry_is_still_caught(self, sm):
        """The count of the events an answer saw is checked too: a log that
        lost an entry (here deleted by hand) does not vouch for a removed event."""
        a = history(sm, T1)
        with ledger(sm) as db:
            seq = db.execute("SELECT COALESCE(MAX(seq), 0) FROM rewrite_log").fetchone()[0]
            eid = db.execute("SELECT eid FROM events WHERE run_id=3").fetchone()[0]
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DELETE FROM changes WHERE eid=?", (eid,))
            db.execute("DELETE FROM locations WHERE eid=?", (eid,))
            db.execute("DELETE FROM events WHERE eid=?", (eid,))
            db.execute("DELETE FROM rewrite_log WHERE seq > ?", (seq,))
            db.commit()
        b = history(sm, T1)
        assert b["serials"]["f"] != a["serials"]["f"]
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_an_event_placed_first_rederives_everything(self, sm):
        """An event older than every other one that moves no row (a run whose
        state could not be read: it has no state to diff; written here by
        another process): position 0 -- where every derivation starts -- is
        another event now."""
        a = history(sm, T1)
        with ledger(sm) as db:
            db.execute("INSERT INTO events(kind, t_utc_us, ord, run_id, experiment, n_changes, flags, error) "
                       "VALUES('run', ?, 0.5, 0, 'scan', 0, 0, 'state unreadable')",
                       (routes_t(sm, 1) - 60_000_000,))
            db.commit()
        b = history(sm, T1)
        assert all(b["serials"][k] != a["serials"][k] for k in T1)
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_an_element_of_a_long_list_is_watched_through_its_list(self, sm):
        paths = {"w": "qubits.qA1.wave.3"}
        a = history(sm, paths)
        wave = [0.0] * 20
        wave[3] = 0.5
        new_run(sm, 5, chip_state(**dict(BASE, wave=wave)))
        b = history(sm, paths)
        assert b["serials"]["w"] != a["serials"]["w"], "the list holder moved: its element is read again"
        assert [p["value"] for p in b["rows"]["w"]["points"]][-1] == 0.5

    def test_a_log_that_went_back_vouches_for_nothing(self, sm):
        """A ledger file put back from an older copy carries an older log: an
        answer stamped further along it is never served from it."""
        edit(sm, "qubits.qA1.T1", "4.5e-5")
        assert sm["client"].post("/state/apply-to-live").status_code == 200
        assert sm["client"].post("/undo").status_code == 200          # the log is past 0
        a = history(sm, T1)
        with ledger(sm) as db:
            eid = db.execute("SELECT eid FROM events WHERE run_id=2").fetchone()[0]
            db.execute("UPDATE events SET flags = flags | ? WHERE eid=?", (hub_store.SOURCE_GONE, eid))
            db.execute("DELETE FROM rewrite_log")
            db.execute("UPDATE sqlite_sequence SET seq=0 WHERE name='rewrite_log'")
            db.commit()
        b = history(sm, T1)
        assert b["serials"]["a"] != a["serials"]["a"], "an older log is not the log the answer was read along"
        assert any("source_gone" in p["flags"] for p in b["rows"]["a"]["points"])
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_a_rename_era_written_into_an_old_run_rederives_everything(self, sm):
        a = history(sm, T1)
        folder = next(sm["data"].glob("*/#3_*"))
        st = json.loads((folder / "quam_state" / "state.json").read_text(encoding="utf-8"))
        st["extras"]["qubit_renames"] = [{"id": "r-generic", "qubits": {}}]
        (folder / "quam_state" / "state.json").write_text(json.dumps(st), encoding="utf-8")
        full_sync(sm)
        b = history(sm, T1)
        assert all(b["serials"][k] != a["serials"][k] for k in T1)
        with from_scratch():
            assert canon(b) == canon(history(sm, T1))

    def test_an_alias_to_an_element_of_a_long_list_watches_the_list(self, tmp_path):
        """The path walks to an alias whose target is one element of a long
        list: the list holder is never walked to, only read for the element."""
        from tests.test_hub_drawer import make_app

        def st(k):
            s = chip_state(wave=[0.0] * 3 + [k] + [0.0] * 16)
            s["qubits"]["qA1"]["wave_pick"] = "#./wave/3"
            return s
        data, live = tmp_path / "data", tmp_path / "chips" / "live"
        run(data, 1, st(0.1))
        write_chip(live, st(0.1), data)
        app = make_app(tmp_path)
        env = {"app": app, "client": app.test_client(), "data": data, "live": live, "tmp": tmp_path}
        assert env["client"].post("/load", data={"folder": str(live)}).status_code in (200, 302)
        paths = {"p": "qubits.qA1.wave_pick"}
        a = history(env, paths)
        new_run(env, 2, st(0.7))
        b = history(env, paths)
        assert 0.7 in [p["value"] for p in b["rows"]["p"]["points"]]
        assert b["serials"]["p"] != a["serials"]["p"]
        with from_scratch():
            assert canon(b) == canon(history(env, paths))

    def test_the_ledger_start_is_read_by_a_path_it_holds_no_row_for(self, sm):
        """A path recorded only later still reads the ledger's first event (its
        first segment starts there): that event's own facts changed in place
        re-derive it."""
        state = chip_state(**BASE)
        state["qubits"]["qA2"]["T2echo"] = 2.5e-5
        new_run(sm, 5, state)
        paths = {"n": "qubits.qA2.T2echo"}
        a = history(sm, paths)
        with ledger(sm) as db:
            db.execute("UPDATE events SET t_utc_us = t_utc_us - 1000000 WHERE eid = "
                       "(SELECT eid FROM events ORDER BY ord LIMIT 1)")
            db.commit()
        b = history(sm, paths)
        assert b["serials"]["n"] != a["serials"]["n"]
        with from_scratch():
            assert canon(b) == canon(history(sm, paths))

    def test_a_drawer_limit_is_its_own_answer(self, sm):
        a = history(sm, {"a": "qubits.qA1.T1"}, limit=1)
        b = history(sm, {"a": "qubits.qA1.T1"})
        assert len(a["rows"]["a"]["points"]) == 1 and len(b["rows"]["a"]["points"]) == 2

    def test_shadow_mode_checks_every_kept_answer(self, sm, monkeypatch):
        with sm["app"].test_request_context():
            merged = routes._store().merged
        targets = {k: vh.target(merged, p) for k, p in T1.items()}
        d = chip_dir(sm)
        vh.read(d, targets)
        new_run(sm, 5, chip_state(**dict(BASE, f01=5.2e9)))
        monkeypatch.setenv("SM_RAM_VERIFY", "1")
        vh.read(d, targets)                               # every kept answer checked: all right
        entry = [vh._KEYS.peek(s)[1] for s in vh._KEYS.slots()
                 if s[0] == hub_index._slot(d) and s[1][0] == "qubits.qA1.T1" and s[2] is None][0]
        entry.row["points"][-1]["value"] = 1.0          # a kept answer gone wrong
        new_run(sm, 6, chip_state(**dict(BASE, f01=5.3e9)))
        with pytest.raises(ramcache.StaleCacheError):
            vh.read(d, targets)


def routes_t(env, rid) -> int:
    with ledger(env) as db:
        return db.execute("SELECT t_utc_us FROM events WHERE run_id=?", (rid,)).fetchone()[0]


# ======================================================================
# 3. the surfaces: an edit recomputes only what read what it changed
# ======================================================================

class TestSurfacesAfterAnEdit:
    def test_an_unrelated_edit_recomputes_nothing_and_answers_the_same(self, sm, monkeypatch):
        for url in URLS:
            get(sm, url)
        trends = counting(monkeypatch, routes, "_topology_trends_html")
        meta = counting(monkeypatch, routes, "_hub_metric_meta")
        curated = counting(monkeypatch, hub_status.LedgerTable, "_curated")
        edit(sm, "qubits.qA1.xy.operations.x180_DragCosine.length", "48")
        for url in (TRENDS, TRENDS_ALIAS, META, GRID, CELL):
            get(sm, url)
        assert trends == [] and meta == [] and curated == [], \
            "no part read the field that was edited"
        assert_served_is_scratch(sm)

    def test_a_shown_value_edited_recomputes_the_meta_only(self, sm, monkeypatch):
        for url in URLS:
            get(sm, url)
        trends = counting(monkeypatch, routes, "_topology_trends_html")
        meta = counting(monkeypatch, routes, "_hub_metric_meta")
        edit(sm, "qubits.qA1.T1", "9e-5")
        entry = json.loads(get(sm, META))["q"]["T1"]["qA1"]
        assert entry["matches_current"] is False, "the meta compares with the value NOW"
        get(sm, TRENDS)
        assert len(meta) == 1 and trends == [], "Trends draws recorded points, not the value now"
        assert_served_is_scratch(sm)

    def test_an_alias_retargeted_by_an_edit_recomputes_what_read_through_it(self, sm, monkeypatch):
        for url in URLS:
            get(sm, url)
        trends = counting(monkeypatch, routes, "_topology_trends_html")
        sm["client"].post("/field/edit", data={"dot_path": "qubits.qA1.xy.operations.x180",
                                               "value": "#./x180_Gauss"})
        get(sm, TRENDS_ALIAS)
        assert len(trends) == 1, "the alias names another holder now"
        assert_served_is_scratch(sm)

    def test_a_typed_path_and_the_full_changes_page_read_the_state_outside_the_facts(self, sm, monkeypatch):
        for url in URLS:
            get(sm, url)
        get(sm, CHANGES, htmx=True)
        trends = counting(monkeypatch, routes, "_topology_trends_html")
        listed = counting(monkeypatch, routes, "_hub_param_changes_data")
        pages = counting(monkeypatch, routes, "_hub_param_changes")
        edit(sm, "qubits.qA1.xy.operations.x180_DragCosine.length", "48")
        get(sm, TRENDS_TYPED)
        assert len(trends) == 1, "a typed path asks the state whether it names a leaf: every edit"
        get(sm, CHANGES)
        get(sm, CHANGES, htmx=True)
        assert listed == [], "what Changes lists reads only the ledger"
        assert len(pages) == 1, "the full page (its top bar) is rendered per request; the fragment is kept"
        assert_served_is_scratch(sm, (TRENDS_TYPED, CHANGES))
        assert get(sm, CHANGES, htmx=True) == scratch(sm, CHANGES, htmx=True)

    def test_an_apply_that_changes_no_value_still_moves_the_changes_top_bar(self, sm):
        """The pending tray empties on an apply that writes nothing new (the
        value staged is the value live holds): no edit counter moves, no
        ledger event lands -- a full page kept under that token showed
        "1 unapplied edit" after it (found by the equality harness)."""
        edit(sm, "qubits.qA1.xy.operations.x180", "#./x180_DragCosine")       # what it holds
        before = get(sm, CHANGES)
        assert sm["client"].post("/state/apply-to-live").status_code == 200
        after = get(sm, CHANGES)
        assert after == scratch(sm, CHANGES)
        assert before != after or b'data-change-count="0"' in before

    def test_a_qubit_gone_from_the_state_leaves_trends_and_the_grid(self, sm):
        """The curated rows are one per qubit of the state NOW (a fact): a
        qubit gone from the state has no line, though the ledger holds it."""
        get(sm, TRENDS)
        get(sm, GRID)
        with sm["app"].test_request_context():
            store = routes._store()
        with store._lock:
            store.merged["qubits"].pop("qA2")
            store.mutation_seq += 1
        served = get(sm, TRENDS)
        assert served == scratch(sm, TRENDS)
        data = re.search(rb'id="topo-trends-data"[^>]*>(.*?)</script>', served, re.S).group(1)
        assert all(s["entity"] != "qA2" for c in json.loads(data) for s in c["series"])
        assert get(sm, GRID) == scratch(sm, GRID)

    def test_a_data_folder_unlinked_moves_the_points_links(self, sm):
        """A point opens its run through the registered dataset roots: kept
        rows are valid for one set of roots."""
        before = get(sm, TRENDS)
        assert b'"uid"' in before, "run #2's own patch set qA1.T1: its point opens the run"
        r = sm["client"].post("/workspace/remove", data={"folder": str(sm["data"])})
        assert r.status_code in (200, 302)
        assert get(sm, TRENDS) == scratch(sm, TRENDS)
        assert get(sm, CELL) == scratch(sm, CELL)

    def test_a_part_computed_while_the_chip_changed_is_never_an_exact_hit(self, sm, monkeypatch):
        real = routes._hub_metric_meta

        def edits_meanwhile(table):
            out = real(table)
            with sm["app"].test_request_context():
                st = routes._store()
            st.mutation_seq += 1                      # an edit lands mid-compute
            return out
        monkeypatch.setattr(routes, "_hub_metric_meta", edits_meanwhile)
        get(sm, META)
        held = [hub_status._CACHE.peek(s)[0] for s in hub_status._CACHE.slots()
                if s == (str(chip_dir(sm)), "metric_meta")]
        assert held and all(hub_status._NEVER in tok for tok in held)


def test_a_load_id_edited_moves_the_meta(tmp_path, monkeypatch):
    """The meta reads its panels' load ids from the state (the panel paths
    fact), not from the ledger."""
    from tests.test_hub_drawer import make_app
    st = chip_state()
    st["qubit_pairs"] = {"qA1-qA2": {"id": "qA1-qA2", "macros": {"cz": {"fidelity": {
        "StandardRB": {"average_gate_fidelity": 0.99}, "StandardRB_load_id": 11}}}}}
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    run(data, 1, st)
    write_chip(live, st, data)
    app = make_app(tmp_path)
    env = {"app": app, "client": app.test_client(), "data": data, "live": live, "tmp": tmp_path}
    assert env["client"].post("/load", data={"folder": str(live)}).status_code in (200, 302)
    first = json.loads(get(env, META))
    assert first["p"]["2q:StandardRB:cz"]["qA1-qA2"]["load_id"] == 11
    edit(env, "qubits.qA1.xy.operations.x180_DragCosine.length", "44")       # unrelated
    meta = counting(monkeypatch, routes, "_hub_metric_meta")
    get(env, META)
    assert meta == []
    edit(env, "qubit_pairs.qA1-qA2.macros.cz.fidelity.StandardRB_load_id", "12")
    got = json.loads(get(env, META))
    assert got["p"]["2q:StandardRB:cz"]["qA1-qA2"]["load_id"] == 12
    assert get(env, META) == scratch(env, META)


def test_a_renamed_chip_keeps_what_a_new_run_did_not_touch(tmp_path):
    from tests.test_rename_history import REC, make, run as rename_run, state
    env = make(tmp_path, [state({"q1": 5.0e9, "q2": 6.0e9}), state({"q1": 5.1e9, "q2": 6.1e9}),
                          state({"q0": 5.1e9, "q1": 6.1e9}, [REC]),
                          state({"q0": 5.2e9, "q1": 6.2e9}, [REC])],
               state({"q0": 5.2e9, "q1": 6.2e9}, [REC]))
    paths = {"a": "qubits.q0.f_01", "b": "qubits.q1.f_01"}
    a = history(env, paths)
    rename_run(env["data"], 5, state({"q0": 5.3e9, "q1": 6.2e9}, [REC]))
    with env["app"].app_context():
        hub_sync.on_roots_moved([str(env["data"])])
    b = history(env, paths)
    assert b["serials"]["a"] != a["serials"]["a"] and b["serials"]["b"] == a["serials"]["b"]
    with from_scratch():
        assert canon(b) == canon(history(env, paths))
    for url in ("/topology/trends?metrics=f_01", META, GRID):
        assert get(env, url) == scratch(env, url), url


def _renamed_chip(tmp_path, runs):
    """A chip renamed by a Re-generate (q1 -> q0, q2 -> q1) whose live state
    already carries the rename, so the lineage knows the record before the
    ledger does; *runs* are the runs saved before it."""
    from tests.test_rename_history import REC, make, state
    return make(tmp_path, runs, state({"q0": 5.2e9, "q1": 5.1e9}, [REC]))


def test_a_rename_era_appended_rederives_a_path_its_rows_never_touch(tmp_path):
    """The appended run carries the rename and changes nothing under the old
    spelling the kept answer watched (qubits.q1.f_01 keeps its value): only
    the era it brings moves today's q0 to another holder from there on."""
    from tests.test_rename_history import REC, run as rename_run, state
    env = _renamed_chip(tmp_path, [state({"q1": 5.0e9, "q2": 6.0e9}), state({"q1": 5.1e9, "q2": 6.1e9})])
    paths = {"a": "qubits.q0.f_01"}
    a = history(env, paths)
    rename_run(env["data"], 3, state({"q0": 5.2e9, "q1": 5.1e9}, [REC]))
    with env["app"].app_context():
        hub_sync.on_roots_moved([str(env["data"])])
    b = history(env, paths)
    assert b["serials"]["a"] != a["serials"]["a"]
    assert 5.2e9 in [p["value"] for p in b["rows"]["a"]["effective"]]
    with from_scratch():
        assert canon(b) == canon(history(env, paths))


def test_a_rename_era_written_into_an_old_run_rederives_a_path_its_rows_never_touch(tmp_path):
    from tests.test_rename_history import REC, state
    env = _renamed_chip(tmp_path, [state({"q1": 5.0e9, "q2": 6.0e9}), state({"q1": 5.0e9, "q2": 6.1e9})])
    paths = {"a": "qubits.q0.f_01"}
    a = history(env, paths)
    folder = next(env["data"].glob("*/#2_*"))
    (folder / "quam_state" / "state.json").write_text(
        json.dumps(state({"q0": 5.3e9, "q1": 5.0e9}, [REC])), encoding="utf-8")
    full_sync(env)
    b = history(env, paths)
    assert b["serials"]["a"] != a["serials"]["a"]
    assert 5.3e9 in [p["value"] for p in b["rows"]["a"]["effective"]]
    with from_scratch():
        assert canon(b) == canon(history(env, paths))


def test_the_era_today_is_part_of_a_kept_answer(tmp_path):
    """The same path spelled in another era names another qubit: the live
    state losing its rename record (no ledger change) reads it afresh."""
    from tests.test_rename_history import REC, state
    env = _renamed_chip(tmp_path, [state({"q1": 5.0e9, "q2": 6.0e9}),
                                   state({"q0": 5.1e9, "q1": 6.1e9}, [REC])])
    paths = {"a": "qubits.q1.f_01"}
    a = history(env, paths)
    with env["app"].test_request_context():
        store = routes._store()
    with store._lock:
        store.merged["extras"].pop("qubit_renames")
        store.mutation_seq += 1
    b = history(env, paths)
    with from_scratch():
        assert canon(b) == canon(history(env, paths))
    assert canon(b) != canon(a), "q1 before the rename is another qubit"


def test_a_matrix_alias_that_starts_naming_a_recorded_matrix_brings_its_row(tmp_path):
    """A qubit's confusion matrix is an alias. The ledger saw it name a recorded
    matrix (run #1), then nothing (run #2, the state now): no fidelity row --
    no element path is read, so no holder fact either. Retargeted by an edit to
    the recorded matrix, the row (its history through the alias) is there."""
    from tests.test_hub_drawer import make_app
    st = chip_state()
    st["qubits"]["qA1"]["resonator"]["cm_a"] = [[0.9, 0.1], [0.2, 0.8]]
    st["qubits"]["qA1"]["resonator"]["confusion_matrix"] = "#./cm_a"
    data, live = tmp_path / "data", tmp_path / "chips" / "live"
    run(data, 1, st)
    st2 = json.loads(json.dumps(st))
    st2["qubits"]["qA1"]["resonator"]["confusion_matrix"] = "#./cm_none"
    run(data, 2, st2)
    write_chip(live, st2, data)
    app = make_app(tmp_path)
    env = {"app": app, "client": app.test_client(), "data": data, "live": live, "tmp": tmp_path}
    assert env["client"].post("/load", data={"folder": str(live)}).status_code in (200, 302)
    url = "/param-history?since=all&props=assignment_fidelity"
    row = b'data-qubit="qA1" data-prop="assignment_fidelity"'
    before = get(env, url)
    assert before == scratch(env, url) and row not in before
    edit(env, "qubits.qA1.resonator.confusion_matrix", "#./cm_a")
    after = get(env, url)
    assert after == scratch(env, url)
    assert row in after, "the alias names a matrix the ledger recorded through it: its row appears"


# ======================================================================
# 4. the equality harness, short: random sequences, every surface
# ======================================================================

@pytest.mark.parametrize("seed", [298, 2981])
def test_random_sequences_answer_as_from_scratch(sm, seed):
    rng = random.Random(seed)
    rid = [4]
    alias = ["#./x180_DragCosine"]

    def op_run(late=False):
        rid[0] += 1
        alias[0] = alias[0] if rng.random() < 0.7 else (
            "#./x180_Gauss" if alias[0] == "#./x180_DragCosine" else "#./x180_DragCosine")
        state = chip_state(alias=alias[0], t1=rng.choice([1e-5, 2e-5, 3e-5, 4e-5]),
                           f01=rng.choice([5.0e9, 5.1e9, 5.2e9]), amp=rng.choice([0.2, 0.25, 0.3]))
        patches = [patch("qubits.qA1.T1", state["qubits"]["qA1"]["T1"], None)] if rng.random() < 0.5 else None
        if late:
            run(sm["data"], rid[0], state, patches=patches, t_us=routes_t(sm, 2) + rng.randrange(1, 9) * 1_000_000)
            with sm["app"].app_context():
                hub_sync.on_roots_moved([str(sm["data"])])
        else:
            new_run(sm, rid[0], state, patches=patches)

    def op_edit():
        path, value = rng.choice([
            ("qubits.qA1.T1", "7e-5"), ("qubits.qA2.f_01", "6.3e9"),
            ("qubits.qA1.xy.operations.x180_DragCosine.length", str(rng.randint(30, 60))),
            ("qubits.qA1.xy.operations.x180", rng.choice(["#./x180_Gauss", "#./x180_DragCosine"]))])
        sm["client"].post("/field/edit", data={"dot_path": path, "value": value})

    ops = [lambda: op_run(), lambda: op_run(), lambda: op_run(late=True), op_edit, op_edit,
           lambda: sm["client"].post("/state/apply-to-live"), lambda: sm["client"].post("/undo"),
           lambda: sm["client"].post("/redo")]
    compared = 0
    for _ in range(12):
        rng.choice(ops)()
        for url in rng.sample(URLS, 5):
            assert get(sm, url) == scratch(sm, url), url
            compared += 1
    assert compared == 60
