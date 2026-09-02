"""docs/142b — months of accumulation must not slow the hot paths.

A site keeps one data folder for months: 10,000+ snapshots accumulate under
instance/history/<chip>/ and millions of rows in its index. Nothing is
auto-deleted; instead the OLD content leaves every hot check:

* ``param_history_cp`` — the change-point companion of param_history,
  maintained by a rowid watermark (_ensure_cp_fresh): new rows are O(new),
  out-of-order/REPLACE'd partitions rebuild individually, deletions
  invalidate explicitly at the two DELETE sites. compress="changes" reads
  with no time/trigger filter come from it (measured 16-22 s -> 2.4 s warm
  at a 10,000-snapshot chip, exact-equivalent to the unthinned windowed SQL).
* the snapshot-list manifest — _list_snapshots_in_dir re-parses only dirs
  whose meta.json is new or moved (measured 7.4 s -> 1.8 s cold, and the
  same delta cost after every capture/ingest invalidation instead of a full
  re-scan per run).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest

from quam_state_manager.core import history as H
from quam_state_manager.core.history import HistoryManager


PROPS12 = ("T1", "T2ramsey")


def _mk_chip(tmp_path) -> tuple[HistoryManager, Path]:
    hm = HistoryManager(tmp_path / "instance")
    qs = tmp_path / "quam_state"
    qs.mkdir()
    (qs / "state.json").write_text("{}", encoding="utf-8")
    (qs / "wiring.json").write_text("{}", encoding="utf-8")
    hm._history_dir(qs).mkdir(parents=True, exist_ok=True)
    H._ensure_param_history_schema(hm._index_path(qs))
    return hm, qs


def _insert(hm, qs, rows):
    conn = sqlite3.connect(str(hm._index_path(qs)), isolation_level=None)
    try:
        conn.execute("BEGIN")
        conn.executemany(
            "INSERT OR REPLACE INTO param_history "
            "(timestamp, qubit, property, value, raw_pointer, trigger, run_id, experiment) "
            "VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.execute("COMMIT")
    finally:
        conn.close()


def _ts(i):
    return f"20260301_12{i // 60:02d}{i % 60:02d}_000"


def _series(hm, qs, prop="T1", qubit="q1", **kw):
    rows = hm.extract_property_history(qs, [prop], qubit_filter=[qubit],
                                       compress="changes", **kw)
    (b,) = [r for r in rows if r["qubit"] == qubit and r["property"] == prop]
    return [(p["timestamp"], p["value"]) for p in b["values"]]


class TestChangePointCompanion:
    def test_fast_path_serves_from_cp_and_matches_expected(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        vals = [1.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 3.0]
        _insert(hm, qs, [(_ts(i), "q1", "T1", v, None, "save", i, "e")
                         for i, v in enumerate(vals)])
        got = _series(hm, qs)
        assert got == [(_ts(0), 1.0), (_ts(2), 1.0), (_ts(3), 2.0),
                       (_ts(4), 2.0), (_ts(5), 3.0), (_ts(7), 3.0)]
        # and it actually came from the companion
        conn = sqlite3.connect(str(hm._index_path(qs)))
        n_cp = conn.execute("SELECT COUNT(*) FROM param_history_cp").fetchone()[0]
        tok = conn.execute(
            "SELECT v FROM param_history_cp_meta WHERE k='rowid'").fetchone()
        conn.close()
        assert n_cp > 0 and tok is not None

    def test_incremental_append_is_o_new(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        _insert(hm, qs, [(_ts(i), "q1", "T1", 1.0, None, "save", i, "e")
                         for i in range(5)])
        assert _series(hm, qs) == [(_ts(0), 1.0), (_ts(4), 1.0)]
        _insert(hm, qs, [(_ts(5), "q1", "T1", 2.0, None, "save", 5, "e")])
        hm2 = HistoryManager(tmp_path / "instance")   # cold caches
        assert _series(hm2, qs) == [
            (_ts(0), 1.0), (_ts(4), 1.0), (_ts(5), 2.0)]

    def test_out_of_order_arrival_rebuilds_the_partition(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        _insert(hm, qs, [(_ts(i), "q1", "T1", 1.0, None, "save", i, "e")
                         for i in (0, 4)])
        _series(hm, qs)                       # cp built: [ts0, ts4]
        # a backfill imports an OLDER snapshot with a different value
        _insert(hm, qs, [(_ts(2), "q1", "T1", 9.0, None, "save", 2, "e")])
        hm2 = HistoryManager(tmp_path / "instance")
        assert _series(hm2, qs) == [
            (_ts(0), 1.0), (_ts(2), 9.0), (_ts(4), 1.0)]

    def test_replace_of_an_existing_row_is_seen(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        _insert(hm, qs, [(_ts(i), "q1", "T1", 1.0, None, "save", i, "e")
                         for i in range(4)])
        _series(hm, qs)
        # re-ingest corrects the NEWEST row's value in place
        _insert(hm, qs, [(_ts(3), "q1", "T1", 7.0, None, "save", 3, "e")])
        hm2 = HistoryManager(tmp_path / "instance")
        assert _series(hm2, qs) == [
            (_ts(0), 1.0), (_ts(2), 1.0), (_ts(3), 7.0)]

    def test_delete_site_invalidation_rebuilds(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        _insert(hm, qs, [(_ts(i), "q1", "T1", float(i), None, "save", i, "e")
                         for i in range(4)])
        _series(hm, qs)
        conn = sqlite3.connect(str(hm._index_path(qs)), isolation_level=None)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM param_history WHERE timestamp=?", (_ts(3),))
        H._cp_invalidate(conn)                # what both DELETE sites do
        conn.execute("COMMIT"); conn.close()
        hm2 = HistoryManager(tmp_path / "instance")
        assert _series(hm2, qs) == [(_ts(0), 0.0), (_ts(1), 1.0), (_ts(2), 2.0)]

    def test_filtered_calls_bypass_cp_and_agree(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        _insert(hm, qs, [(_ts(i), "q1", "T1", 1.0 if i < 3 else 2.0,
                          None, "save", i, "e") for i in range(6)])
        fast = _series(hm, qs)
        hm2 = HistoryManager(tmp_path / "instance")
        windowed = _series(hm2, qs, triggers=["save"])   # filter -> old path
        assert fast == windowed

    def test_multi_qubit_partitions_stay_separate(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        rows = []
        for i in range(4):
            rows.append((_ts(i), "q1", "T1", 1.0, None, "save", i, "e"))
            rows.append((_ts(i), "q2", "T1", float(i), None, "save", i, "e"))
        _insert(hm, qs, rows)
        assert _series(hm, qs, qubit="q1") == [(_ts(0), 1.0), (_ts(3), 1.0)]
        assert len(_series(hm, qs, qubit="q2")) == 4


class TestSnapshotManifest:
    def _snap(self, chip_dir, ts, label=None):
        d = chip_dir / ts
        d.mkdir(parents=True, exist_ok=True)
        meta = {"timestamp": ts, "trigger": "manual",
                "diff_summary": {"added": 0, "removed": 0, "modified": 0, "total": 0},
                "new_experiments": [], "source_path": "x"}
        if label:
            meta["label"] = label
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_manifest_round_trip_and_delta_parse(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        first = hm._list_snapshots_in_dir(chip)
        assert [m.timestamp for m in first] == [_ts(2), _ts(1), _ts(0)]
        assert (chip / "snapshots_manifest.json").exists()
        # a fresh manager serves the same list from the manifest
        hm2 = HistoryManager(tmp_path / "instance")
        again = hm2._list_snapshots_in_dir(chip)
        assert [m.timestamp for m in again] == [_ts(2), _ts(1), _ts(0)]

    def test_new_and_removed_snapshots_are_reflected(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)
        self._snap(chip, _ts(9))                       # new snapshot lands
        import shutil
        shutil.rmtree(chip / _ts(0))                   # one pruned
        hm2 = HistoryManager(tmp_path / "instance")
        got = [m.timestamp for m in hm2._list_snapshots_in_dir(chip)]
        assert got == [_ts(9), _ts(2), _ts(1)]

    def test_a_new_snapshot_appears_though_the_sidecar_predates_it(self, tmp_path):
        """ADDITION ONLY -- nothing removed, nothing edited.

        The listing must render what is on DISK, not what the sidecar knows.
        A path that serves the sidecar wholesale drops the snapshot that was
        just captured -- the newest one, the one the user is looking for. The
        existing add-and-remove test cannot catch that, because its removal
        also changes the name set and would send a set-comparing gate down
        the slow path anyway; that is why this case has its own pin.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        assert len(hm._list_snapshots_in_dir(chip)) == 3        # seeds sidecar
        self._snap(chip, _ts(9))                                # capture lands
        hm2 = HistoryManager(tmp_path / "instance")
        got = [m.timestamp for m in hm2._list_snapshots_in_dir(chip)]
        assert got == [_ts(9), _ts(2), _ts(1), _ts(0)], got

    def test_an_in_place_label_edit_through_sm_is_picked_up(self, tmp_path):
        """A label/pin edit rewrites meta.json IN PLACE, so the directory-name
        set does not move and the fast path (docs/155 10g) would happily serve
        the old copy. ``annotate_snapshot`` refreshes the sidecar entry itself;
        this pins that it does. Break that wiring and a user's label silently
        never appears.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        self._snap(chip, _ts(0))
        assert hm._list_snapshots_in_dir(chip)[0].label is None    # seeds sidecar
        time.sleep(0.01)
        hm.annotate_snapshot(qs, _ts(0), label="golden baseline")
        hm2 = HistoryManager(tmp_path / "instance")                # cold process
        assert hm2._list_snapshots_in_dir(chip)[0].label == "golden baseline"

    def test_an_out_of_band_edit_costs_the_request_path_nothing(self, tmp_path):
        """The request path never re-reads a meta.json the sidecar knows.

        That is the whole point: on the customer's chip those stats were
        ~7.2 s of their share, in one block, while somebody waited. An edit
        made by something other than SM is therefore not visible to the
        LISTING -- it becomes visible when the background sweep gets to it,
        which is the next test. Deleting the sweep must not make this pass
        quietly, so this test asserts the cost, not the staleness.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(6):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)                            # seeds sidecar
        time.sleep(0.01)
        self._snap(chip, _ts(0), label="edited by hand")           # out of band

        import os as _os
        calls: list[str] = []
        real = _os.stat

        def spy(path, *a, **k):
            calls.append(str(path))
            return real(path, *a, **k)

        hm2 = HistoryManager(tmp_path / "instance")
        _os.stat = spy
        try:
            got = hm2._list_snapshots_in_dir(chip)
        finally:
            _os.stat = real
        assert len(got) == 6
        metas = [c for c in calls if c.endswith("meta.json") and str(chip) in c]
        assert metas == [], f"the listing re-read known entries: {metas[:3]}"

    def test_the_background_sweep_heals_an_out_of_band_edit(self, tmp_path):
        """...and the staleness is temporary, not a trade.

        docs/143 paid for this on every read. docs/155 10h pays for it once
        in a while, on a daemon thread, in chunks -- which is the same
        correctness for none of the latency.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        self._snap(chip, _ts(0))
        assert hm._list_snapshots_in_dir(chip)[0].label is None    # seeds sidecar
        time.sleep(0.01)
        self._snap(chip, _ts(0), label="edited by hand")           # out of band

        hm2 = HistoryManager(tmp_path / "instance")
        # list_snapshots (not the _in_dir helper) is the CACHED public read --
        # the one a page actually calls, and the one that would keep serving
        # the stale label forever if the sweep healed only the disk.
        assert hm2.list_snapshots(qs)[0].label is None             # not yet
        res = hm2.verify_manifest(chip, pause=0)
        assert res["refreshed"] == 1, res
        # a cold process now reads the healed sidecar...
        hm3 = HistoryManager(tmp_path / "instance")
        assert hm3.list_snapshots(qs)[0].label == "edited by hand"
        # ...and the sweeping process dropped its own cached list, so it does
        # not keep serving the value it just corrected on disk.
        assert hm2.list_snapshots(qs)[0].label == "edited by hand", (
            "the sweep healed the sidecar but left its own cache stale")

    def test_the_sweep_says_nothing_changed_when_nothing_did(self, tmp_path):
        """A sweep that rewrites the sidecar every time would move the chip
        dir's mtime every time, and history_seq_for reads that as "another
        process captured something" -- repainting every open Versions panel
        on a timer, forever."""
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)
        side = chip / hm._MANIFEST_NAME
        before = side.stat().st_mtime_ns
        res = hm.verify_manifest(chip, pause=0)
        assert res == {"checked": 3, "refreshed": 0, "dropped": 0}, res
        assert side.stat().st_mtime_ns == before, "sidecar rewritten for nothing"

    def _sweep_with_interleave(self, hm, chip, action):
        """Run a sweep, performing ``action()`` inside its first pause."""
        fired: list[int] = []

        def once(_secs):
            if fired:
                return
            fired.append(1)
            action()

        orig_sleep = H.time.sleep
        H.time.sleep = once
        try:
            res = hm.verify_manifest(chip, chunk=1, pause=0.001)
        finally:
            H.time.sleep = orig_sleep
        assert fired, "the interleave never happened -- the pause was skipped"
        return res

    def test_a_users_label_beats_the_sweeps_older_read(self, tmp_path):
        """The sweep must not write its own copy over a NEWER one.

        It read that meta.json seconds or minutes ago, at share latency. If
        the user renames the version in SM while the sweep is walking, the
        sweep's copy is the older read -- writing it back takes the label the
        user just typed straight off the screen until the next sweep. So the
        write is compare-and-swap on the entry's signature.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)                       # seeds sidecar
        time.sleep(0.01)
        self._snap(chip, _ts(0), label="edited by hand")      # something to fix

        hm2 = HistoryManager(tmp_path / "instance")
        res = self._sweep_with_interleave(
            hm2, chip,
            # the sweep has already read ts(0); now the user renames it
            lambda: hm2.annotate_snapshot(qs, _ts(0), label="typed in SM"))
        assert res["refreshed"] == 0, "the sweep overwrote a newer entry"

        hm3 = HistoryManager(tmp_path / "instance")
        by_ts = {m.timestamp: m for m in hm3._list_snapshots_in_dir(chip)}
        assert by_ts[_ts(0)].label == "typed in SM", (
            "the sweep's older read replaced the label the user just typed")

    def test_the_sweep_does_not_undo_a_capture_that_landed_mid_sweep(self, tmp_path):
        """The sweep merges onto the sidecar as it is when it WRITES, never
        the copy it started from -- the failure mode of every
        read-modify-write. A capture during a long sweep is the normal case
        on a live chip.

        Note what this pin can and cannot show: dropping the new entry from
        the SIDECAR does not lose the snapshot, because the listing walks
        disk and re-parses anything the sidecar does not know. What it costs
        is that re-parse, on every listing, until some scan rewrites it --
        so this asserts the sidecar's contents, not the rendered list.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)                       # seeds sidecar
        time.sleep(0.01)
        self._snap(chip, _ts(0), label="edited by hand")      # something to fix

        def capture():
            self._snap(chip, _ts(9))                          # a run finishes
            HistoryManager(tmp_path / "instance")._list_snapshots_in_dir(chip)

        hm2 = HistoryManager(tmp_path / "instance")
        self._sweep_with_interleave(hm2, chip, capture)

        side = json.loads((chip / hm._MANIFEST_NAME).read_text(encoding="utf-8"))
        assert _ts(9) in side["entries"], "the sweep wrote back its stale copy"
        assert side["entries"][_ts(0)]["meta"]["label"] == "edited by hand"
        got = [m.timestamp for m in
               HistoryManager(tmp_path / "instance")._list_snapshots_in_dir(chip)]
        assert got == [_ts(9), _ts(2), _ts(1), _ts(0)], got

    def test_the_sweep_writes_nothing_when_every_fix_was_out_voted(self, tmp_path):
        """A sweep that finished with nothing to apply must not touch the
        sidecar at all.

        The sidecar lives INSIDE the chip dir, so writing it moves that dir's
        mtime -- and history_seq_for reads exactly that as "another process
        captured something", repainting every open Versions panel. A sweep
        whose only fix lost the compare-and-swap has applied nothing, and
        must therefore write nothing.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)
        time.sleep(0.01)
        self._snap(chip, _ts(0), label="edited by hand")      # the only fix

        hm2 = HistoryManager(tmp_path / "instance")
        side = chip / hm._MANIFEST_NAME
        before = None

        def out_vote():
            nonlocal before
            hm2.annotate_snapshot(qs, _ts(0), label="typed in SM")
            before = side.stat().st_mtime_ns                  # after THAT write

        res = self._sweep_with_interleave(hm2, chip, out_vote)
        assert res == {"checked": 3, "refreshed": 0, "dropped": 0}, res
        assert side.stat().st_mtime_ns == before, (
            "the sweep rewrote the sidecar although it applied nothing")

    def test_the_sweep_drops_an_entry_whose_meta_is_gone(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)
        (chip / _ts(0) / "meta.json").unlink()                # dir stays, meta goes
        res = hm.verify_manifest(chip, pause=0)
        assert res["dropped"] == 1, res
        side = json.loads((chip / hm._MANIFEST_NAME).read_text(encoding="utf-8"))
        assert _ts(0) not in side["entries"]
        # and the listing now skips it rather than serving a remembered copy
        got = [m.timestamp for m in
               HistoryManager(tmp_path / "instance")._list_snapshots_in_dir(chip)]
        assert got == [_ts(2), _ts(1)], got

    def test_the_sweep_keeps_an_entry_that_came_back_while_it_swept(self, tmp_path):
        """"Its meta.json is gone" was true minutes ago, at share latency.

        Between the sweep seeing that and the sweep writing, a capture can
        have recreated the very same timestamp. Dropping it then would delete
        a live entry, so the removal is re-checked under the lock.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)
        (chip / _ts(0) / "meta.json").unlink()

        fired: list[int] = []

        def recreate_mid_sweep(_secs):
            if fired:
                return
            fired.append(1)
            self._snap(chip, _ts(0), label="came back")

        orig_sleep = H.time.sleep
        H.time.sleep = recreate_mid_sweep
        try:
            res = hm.verify_manifest(chip, chunk=1, pause=0.001)
        finally:
            H.time.sleep = orig_sleep
        assert fired, "the interleave never happened"
        assert res["dropped"] == 0, "dropped an entry that exists on disk"
        side = json.loads((chip / hm._MANIFEST_NAME).read_text(encoding="utf-8"))
        assert _ts(0) in side["entries"]

    def test_arming_is_off_under_the_suite_flag_and_runs_without_it(
            self, tmp_path, monkeypatch):
        """The sweep is armed by an ordinary listing. The suite disables it
        (a thread over a tmp dir pytest is about to delete), so the arming
        itself needs a test that turns it back on -- otherwise the whole
        mechanism could be dead in production and every other pin here, which
        calls verify_manifest directly, would still be green.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        self._snap(chip, _ts(0))
        hm._list_snapshots_in_dir(chip)                       # seeds sidecar
        time.sleep(0.01)
        self._snap(chip, _ts(0), label="edited by hand")

        monkeypatch.setenv("SM_DISABLE_HISTORY_VERIFY", "1")
        hm2 = HistoryManager(tmp_path / "instance")
        hm2._list_snapshots_in_dir(chip)
        hm2.join_manifest_verify(5)
        assert hm2._verify_threads == {}, "armed while the suite flag was set"

        monkeypatch.delenv("SM_DISABLE_HISTORY_VERIFY", raising=False)
        hm3 = HistoryManager(tmp_path / "instance")
        hm3._list_snapshots_in_dir(chip)                      # this arms it
        hm3.join_manifest_verify(10)
        assert hm3._verify_threads, "an ordinary listing armed no sweep"
        side = json.loads((chip / hm3._MANIFEST_NAME).read_text(encoding="utf-8"))
        assert side["entries"][_ts(0)]["meta"]["label"] == "edited by hand"

    def test_a_second_listing_does_not_start_a_second_sweep(self, tmp_path,
                                                            monkeypatch):
        """Every listing arming its own thread would put the per-entry stats
        back on top of the request rate they were removed from."""
        monkeypatch.delenv("SM_DISABLE_HISTORY_VERIFY", raising=False)
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        self._snap(chip, _ts(0))
        hm._list_snapshots_in_dir(chip)

        started: list[Path] = []
        real = HistoryManager._verify_worker

        def counting(self_, d):
            started.append(d)
            return real(self_, d)

        monkeypatch.setattr(HistoryManager, "_verify_worker", counting)
        hm2 = HistoryManager(tmp_path / "instance")
        for _ in range(5):
            hm2._list_snapshots_in_dir(chip)
        hm2.join_manifest_verify(10)
        assert len(started) == 1, f"{len(started)} sweeps for 5 listings"

    def test_the_scan_costs_one_stat_per_snapshot_not_two(self, tmp_path):
        """The enumeration already carries each child's kind.

        `iterdir()` + `child.is_dir()` spent one stat per snapshot dir on an
        answer os.scandir hands over for free -- half of this scan's syscalls,
        and this scan runs on every process-cold read and after every
        capture/ingest invalidation. On a customer chip with 4,003 snapshots
        that was 8,008 stats; on their SMB share, ~14 s of one page load
        (docs/155 10f). 10g then removed the sidecar's own freshness stat as
        well, so a warm scan of ANY size costs three file operations flat --
        one guard stat, one read of the sidecar, one scandir. This test spies
        `os.stat` alone, so it pins the per-entry term (the one that scales)
        and not that constant. What pays for it is
        test_an_in_place_label_edit_through_sm_is_picked_up.
        """
        import os as _os
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        n = 12
        for i in range(n):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)                # seed the manifest

        calls = []
        real = _os.stat
        def spy(path, *a, **k):
            calls.append(str(path))
            return real(path, *a, **k)
        hm2 = HistoryManager(tmp_path / "instance")
        _os.stat = spy
        try:
            got = hm2._list_snapshots_in_dir(chip)
        finally:
            _os.stat = real

        assert len(got) == n
        under = [c for c in calls if str(chip) in c]
        meta = [c for c in under if c.endswith("meta.json")]
        # ZERO per-entry syscalls. iterdir() + is_dir() spent one stat per
        # snapshot on what the enumeration already knew, and the sidecar's
        # freshness check spent another; docs/155 10f and 10g removed both. On
        # the customer's 4,003-snapshot chip that pair was 8,008 stats -- about
        # 14 s of a single page load on their SMB share.
        assert meta == [], f"per-entry freshness stats are back: {meta[:3]}"
        # the one legitimate stat is the hist_dir guard itself
        extra = [c for c in under
                 if not c.endswith("meta.json") and c != str(chip)]
        assert extra == [], f"per-child stats the enumeration already answered: {extra}"

    def _stat_spy(self, chip, fn):
        """Run ``fn()`` with os.stat spied; return the calls under ``chip``."""
        import os as _os
        calls: list[str] = []
        real = _os.stat

        def spy(path, *a, **k):
            calls.append(str(path))
            return real(path, *a, **k)

        _os.stat = spy
        try:
            fn()
        finally:
            _os.stat = real
        return [c for c in calls if str(chip) in c]

    def test_a_capture_costs_o_new_not_o_n(self, tmp_path):
        """The listing after a capture must pay for the NEW snapshot only.

        10g gated the sidecar on the two name sets being EQUAL, so one new
        directory sent the whole listing down the full scan: measured on the
        customer's 4,003-snapshot chip, 4,007 ops for the writer's own
        mid-capture listing and 4,010 for the next one -- about 14 s of their
        share per run, and they capture one per run. The gate is a DELTA now.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        n = 30
        for i in range(n):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)                    # seeds the sidecar
        self._snap(chip, _ts(99))                          # one capture lands

        hm2 = HistoryManager(tmp_path / "instance")
        got: list = []
        under = self._stat_spy(
            chip, lambda: got.extend(hm2._list_snapshots_in_dir(chip)))
        assert len(got) == n + 1, "the new snapshot is missing from the listing"
        metas = [c for c in under if c.endswith("meta.json")]
        assert len(metas) == 1, f"expected one stat (the new dir), got {len(metas)}"
        assert _ts(99) in metas[0]

    def test_a_half_written_capture_is_never_remembered_as_absent(self, tmp_path):
        """A capture creates its directory BEFORE writing meta.json, and the
        writer lists priors in between. An earlier draft memoised the dirs
        that had no meta.json so it could stop stat'ing them -- and registered
        the half-written snapshot as one to ignore, permanently. Nothing may
        remember an absence.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        for i in range(3):
            self._snap(chip, _ts(i))
        hm._list_snapshots_in_dir(chip)
        (chip / _ts(9)).mkdir()                            # capture starts
        mid = hm._list_snapshots_in_dir(chip)              # writer lists priors
        assert [m.timestamp for m in mid] == [_ts(2), _ts(1), _ts(0)]
        self._snap(chip, _ts(9))                           # meta.json lands
        for mgr in (hm, HistoryManager(tmp_path / "instance")):
            got = [m.timestamp for m in mgr._list_snapshots_in_dir(chip)]
            assert got == [_ts(9), _ts(2), _ts(1), _ts(0)], got

    def test_corrupt_manifest_reads_as_a_miss(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        self._snap(chip, _ts(0))
        (chip / "snapshots_manifest.json").write_text("{ nope", encoding="utf-8")
        hm2 = HistoryManager(tmp_path / "instance")
        assert [m.timestamp for m in hm2._list_snapshots_in_dir(chip)] == [_ts(0)]
