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

        The fast path's gate must be EQUALITY of the name sets. A subset test
        ("every entry I know still exists") passes here and then serves the
        sidecar wholesale, silently dropping the snapshot that was just
        captured -- the newest one, the one the user is looking for. The
        existing add-and-remove test cannot catch it, because the removal
        breaks the subset and sends it down the slow path anyway; that is
        exactly why this case gets its own pin.
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

    def test_an_out_of_band_meta_edit_is_the_accepted_trade(self, tmp_path):
        """The cost of the fast path, written down as a test rather than left
        as a surprise.

        docs/143 stat'ed every meta.json so a hand edit would be seen. That was
        4,003 syscalls per scan on the customer's chip -- ~7.2 s of one page
        load on their share -- spent defending SM's cache of SM's own files
        against a text editor. docs/155 10g stopped paying it: an edit made by
        something OTHER than SM is invisible until a directory is added or
        removed.

        If this test ever fails, the trade was reversed -- which is allowed,
        but it must be a decision rather than a drift.
        """
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        self._snap(chip, _ts(0))
        assert hm._list_snapshots_in_dir(chip)[0].label is None    # seeds sidecar
        time.sleep(0.01)
        self._snap(chip, _ts(0), label="edited by hand")           # out of band
        hm2 = HistoryManager(tmp_path / "instance")
        assert hm2._list_snapshots_in_dir(chip)[0].label is None, (
            "an out-of-band edit became visible -- the fast path is off, or the "
            "trade was reversed without updating this pin")
        # ...and it self-heals the moment the name set moves.
        self._snap(chip, _ts(1))
        hm3 = HistoryManager(tmp_path / "instance")
        by_ts = {m.timestamp: m for m in hm3._list_snapshots_in_dir(chip)}
        assert by_ts[_ts(0)].label == "edited by hand"

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

    def test_corrupt_manifest_reads_as_a_miss(self, tmp_path):
        hm, qs = _mk_chip(tmp_path)
        chip = hm._history_dir(qs)
        self._snap(chip, _ts(0))
        (chip / "snapshots_manifest.json").write_text("{ nope", encoding="utf-8")
        hm2 = HistoryManager(tmp_path / "instance")
        assert [m.timestamp for m in hm2._list_snapshots_in_dir(chip)] == [_ts(0)]
