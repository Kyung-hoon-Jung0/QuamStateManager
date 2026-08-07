"""Every numeric parameter's history as change points (docs/83).

Param History tracked eleven curated properties; everything else fell back to
``field_history``'s capped scan — measured at 555 ms and truncated at 150
snapshots on a real 264-snapshot chip. Indexing every leaf of every snapshot
would be 887k rows / 200 MB for that chip. Indexing only the TRANSITIONS is
~10k rows / 1.5 MB, because between consecutive snapshots of a real chip the
number of numeric leaves that change has a median of 2-4 out of 8,000.

Three invariants carry the design, and each has its own class here:

* **Replay is the truth.** Applying every change point in order must reproduce
  the file exactly. That is checked against the real archive too, where it
  covers 8,000+ parameters per chip including the pointer-resolved ones.
* **Incremental == rebuilt.** The capture path appends one snapshot at a time;
  the repair path recomputes from disk. They must agree, or a repair would
  silently change history.
* **A rebuild never loses a pruned snapshot's rows.** This is the only place in
  the feature where data can be destroyed.
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from quam_state_manager.core import leaf_index as li
from quam_state_manager.core.history import HistoryManager

# A real Param-History store (``instance/history``). Point QSM_HISTORY_ARCHIVE
# at one to run the corpus class; without it those tests skip, like every other
# real-data test in this suite.
_ARCHIVE = Path(os.environ.get("QSM_HISTORY_ARCHIVE")
                or (Path(r"<data-root>") / "history"))


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:", isolation_level=None)
    li.ensure_schema(c)
    yield c
    c.close()


def _ingest(conn, ts, state, wiring=None, **meta):
    return li.ingest_snapshot(
        conn, ts=ts, trigger=meta.get("trigger", "manual"),
        run_id=meta.get("run_id"), experiment=meta.get("experiment"),
        folder=meta.get("folder"), state=state, wiring=wiring)


def _chip(t1=1.0e-5, amp=0.1, extra=None):
    q = {"id": "qA1", "T1": t1, "grid_location": "0,0", "active": True,
         "xy": {"operations": {"x180": {"amplitude": amp}}}}
    if extra:
        q.update(extra)
    return {"qubits": {"qA1": q}, "active_qubit_names": ["qA1"]}


def _values(conn, path):
    return [r[1] for r in li.series(conn, path)]


# ──────────────────────────────────────────────────────────────────────────


class TestTheWalk:
    def test_numbers_in_booleans_and_strings_out(self):
        nums, kinds, trunc = li.numeric_leaves(_chip())
        assert nums["qubits.qA1.T1"] == 1.0e-5
        assert "qubits.qA1.active" not in nums          # True is not a parameter
        assert "qubits.qA1.grid_location" not in nums   # "0,0" is not a number
        assert trunc is False

    def test_list_elements_use_the_dot_index_grammar(self):
        st = {"qubits": {"qA1": {"confusion_matrix": [[0.9, 0.1], [0.2, 0.8]]}}}
        nums, _k, _t = li.numeric_leaves(st)
        assert nums["qubits.qA1.confusion_matrix.0.1"] == 0.1
        assert nums["qubits.qA1.confusion_matrix.1.0"] == 0.2

    def test_wiring_merges_but_state_wins_a_collision(self):
        """Same rule as HistoryManager._scan_field_series, so a dot path means
        one thing in every tier."""
        nums, _k, _t = li.numeric_leaves({"a": {"x": 1}}, {"a": {"x": 2}, "b": {"y": 3}})
        assert nums["a.x"] == 1 and nums["b.y"] == 3

    def test_pointers_are_followed_to_their_number(self):
        st = _chip(extra={"ref": "#/qubits/qA1/T1"})
        nums, kinds, _t = li.numeric_leaves(st)
        assert nums["qubits.qA1.ref"] == 1.0e-5
        assert kinds["qubits.qA1.ref"] == li.KIND_PTR_NUM

    def test_a_pointer_to_nothing_is_recorded_but_carries_no_value(self):
        st = _chip(extra={"ref": "#/qubits/qA1/nowhere"})
        nums, kinds, _t = li.numeric_leaves(st)
        assert "qubits.qA1.ref" not in nums
        assert kinds["qubits.qA1.ref"] == li.KIND_PTR

    def test_the_walk_cap_is_honest(self):
        st = {"big": list(range(50))}
        nums, _k, trunc = li.numeric_leaves(st, cap=10)
        assert trunc is True and len(nums) <= 10


class TestChangePoints:
    def test_an_unchanged_snapshot_adds_no_rows(self, conn):
        assert _ingest(conn, "20260101_000000", _chip())["rows"] > 0
        assert _ingest(conn, "20260101_000100", _chip())["rows"] == 0
        assert li.stats(conn)["snapshots"] == 2

    def test_only_the_changed_leaf_is_stored(self, conn):
        _ingest(conn, "20260101_000000", _chip(t1=1e-5))
        r = _ingest(conn, "20260101_000100", _chip(t1=2e-5))
        assert r["rows"] == 1
        assert _values(conn, "qubits.qA1.T1") == [1e-5, 2e-5]

    def test_a_re_ingest_of_a_known_timestamp_is_a_no_op(self, conn):
        _ingest(conn, "20260101_000000", _chip())
        assert _ingest(conn, "20260101_000000", _chip(t1=9e-5))["status"] == "known"

    def test_a_removed_leaf_is_a_change(self, conn):
        _ingest(conn, "20260101_000000", _chip())
        st = _chip()
        del st["qubits"]["qA1"]["T1"]
        _ingest(conn, "20260101_000100", st)
        rows = li.series(conn, "qubits.qA1.T1")
        assert len(rows) == 2 and rows[-1][1] is None

    def test_a_pointer_tracks_its_targets_changes(self, conn):
        _ingest(conn, "20260101_000000", _chip(t1=1e-5, extra={"ref": "#/qubits/qA1/T1"}))
        _ingest(conn, "20260101_000100", _chip(t1=3e-5, extra={"ref": "#/qubits/qA1/T1"}))
        assert _values(conn, "qubits.qA1.ref") == [1e-5, 3e-5]
        assert li.path_needs_scan(conn, "qubits.qA1.ref") is False

    def test_a_dangling_pointer_is_handed_to_the_scan(self, conn):
        _ingest(conn, "20260101_000000", _chip(extra={"ref": "#/qubits/qA1/nowhere"}))
        assert li.path_needs_scan(conn, "qubits.qA1.ref") is True

    def test_series_carries_the_producing_experiment(self, conn):
        _ingest(conn, "20260101_000000", _chip(t1=1e-5))
        _ingest(conn, "20260101_000100", _chip(t1=2e-5), trigger="experiment",
                run_id=42, experiment="06_ramsey", folder="/runs/42")
        ts, val, trig, run_id, exp, folder = li.series(conn, "qubits.qA1.T1")[-1]
        assert (val, trig, run_id, exp, folder) == (
            2e-5, "experiment", 42, "06_ramsey", "/runs/42")


class TestOrdering:
    def test_an_out_of_order_snapshot_writes_nothing_and_marks_dirty(self, conn):
        _ingest(conn, "20260101_000200", _chip(t1=1e-5))
        r = _ingest(conn, "20260101_000100", _chip(t1=2e-5))
        assert r["status"] == "dirty" and r["rows"] == 0
        assert li.is_dirty(conn) is True
        # nothing wrong was recorded — the older value never entered the series
        assert _values(conn, "qubits.qA1.T1") == [1e-5]

    def test_the_rebuild_repairs_the_order_and_clears_dirty(self, conn):
        states = {"20260101_000200": _chip(t1=3e-5),
                  "20260101_000100": _chip(t1=2e-5),
                  "20260101_000000": _chip(t1=1e-5)}
        for ts in ("20260101_000200", "20260101_000100"):
            _ingest(conn, ts, states[ts])
        li.rebuild(conn, timestamps=list(states),
                   load=lambda t: ({"ts": t, "trigger": "manual"}, states[t], None))
        assert li.is_dirty(conn) is False
        assert _values(conn, "qubits.qA1.T1") == [1e-5, 2e-5, 3e-5]


class TestRebuildNeverLosesPrunedHistory:
    """The one place in this feature where data can be destroyed."""

    def _seed(self, conn):
        for i, t1 in enumerate((1e-5, 2e-5, 3e-5, 4e-5)):
            _ingest(conn, f"2026010{i}_000000", _chip(t1=t1))

    def test_pruned_snapshots_keep_their_rows(self, conn):
        self._seed(conn)
        # Snapshots 0 and 1 pruned from disk: the rebuild is told only about 2,3.
        survivors = {"20260102_000000": _chip(t1=3e-5),
                     "20260103_000000": _chip(t1=4e-5)}
        res = li.rebuild(conn, timestamps=list(survivors),
                         load=lambda t: ({"ts": t, "trigger": "manual"},
                                         survivors[t], None))
        assert res["kept"] == 2
        assert _values(conn, "qubits.qA1.T1") == [1e-5, 2e-5, 3e-5, 4e-5]

    def test_the_oldest_survivor_does_not_claim_everything_changed(self, conn):
        """Without replaying the retained rows in order, the first snapshot the
        rebuild can actually read looks like it introduced all 8,000 leaves."""
        self._seed(conn)
        before = len(li.series(conn, "qubits.qA1.xy.operations.x180.amplitude"))
        survivors = {"20260102_000000": _chip(t1=3e-5),
                     "20260103_000000": _chip(t1=4e-5)}
        li.rebuild(conn, timestamps=list(survivors),
                   load=lambda t: ({"ts": t, "trigger": "manual"},
                                   survivors[t], None))
        after = li.series(conn, "qubits.qA1.xy.operations.x180.amplitude")
        assert len(after) == before == 1, "an unchanged leaf gained a fake change"

    def test_a_snapshot_that_became_unreadable_keeps_its_history(self, conn):
        self._seed(conn)
        li.rebuild(conn, timestamps=["20260103_000000"], load=lambda t: None)
        assert _values(conn, "qubits.qA1.T1") == [1e-5, 2e-5, 3e-5, 4e-5]


class TestIncrementalEqualsRebuilt:
    def test_same_rows_same_series(self, conn):
        states = {f"2026010{i}_000000": _chip(t1=(i + 1) * 1e-5, amp=0.1 + i / 100)
                  for i in range(6)}
        for ts, st in states.items():
            _ingest(conn, ts, st)
        rebuilt = sqlite3.connect(":memory:", isolation_level=None)
        li.ensure_schema(rebuilt)
        li.rebuild(rebuilt, timestamps=list(states),
                   load=lambda t: ({"ts": t, "trigger": "manual"}, states[t], None))
        for p in ("qubits.qA1.T1", "qubits.qA1.xy.operations.x180.amplitude"):
            assert li.series(conn, p) == li.series(rebuilt, p)
        assert li.stats(conn)["rows"] == li.stats(rebuilt)["rows"]
        rebuilt.close()


class TestReplayIsTheTruth:
    def test_replaying_every_change_point_reproduces_the_file(self, conn):
        states = {}
        for i in range(8):
            states[f"2026010{i}_000000"] = _chip(
                t1=(i + 1) * 1e-5, amp=0.1 + i / 100,
                extra={"ref": "#/qubits/qA1/T1"} if i % 2 else None)
        for ts, st in states.items():
            _ingest(conn, ts, st)
        newest = states[max(states)]
        truth, _kinds, _t = li.numeric_leaves(newest)
        pmap = {i: p for i, p in conn.execute("SELECT id, path FROM leaf_paths")}
        recon = {pmap[pid]: v for pid, (v, k) in li._latest_values(conn).items()
                 if k in (li.KIND_NUM, li.KIND_PTR_NUM)}
        assert recon == truth


class TestTheFeedAndSearch:
    def test_recent_changes_are_newest_first_with_the_previous_value(self, conn):
        _ingest(conn, "20260101_000000", _chip(t1=1e-5))
        _ingest(conn, "20260101_000100", _chip(t1=2e-5), trigger="experiment",
                experiment="06_ramsey")
        rows = li.recent_changes(conn, limit=10)
        top = next(r for r in rows if r["path"] == "qubits.qA1.T1")
        assert top["value"] == 2e-5 and top["previous"] == 1e-5
        assert top["experiment"] == "06_ramsey" and top["is_first"] is False

    def test_the_first_ever_value_says_so(self, conn):
        _ingest(conn, "20260101_000000", _chip(t1=1e-5))
        row = next(r for r in li.recent_changes(conn, limit=50)
                   if r["path"] == "qubits.qA1.T1")
        assert row["is_first"] is True and row["previous"] is None

    def test_a_prefix_filter_scopes_the_feed(self, conn):
        _ingest(conn, "20260101_000000", _chip())
        assert li.recent_changes(conn, limit=50, prefix="qubits.qA1.xy")
        assert not li.recent_changes(conn, limit=50, prefix="nothing.here")

    def test_search_ranks_by_how_often_a_path_moved(self, conn):
        for i, t1 in enumerate((1e-5, 2e-5, 3e-5)):
            _ingest(conn, f"2026010{i}_000000", _chip(t1=t1))
        hits = li.search_paths(conn, "T1")
        assert hits and hits[0]["path"] == "qubits.qA1.T1"
        assert hits[0]["changes"] == 3

    def test_search_is_empty_for_an_empty_query(self, conn):
        _ingest(conn, "20260101_000000", _chip())
        assert li.search_paths(conn, "  ") == []


class TestPagingBySnapshot:
    """A regenerate rewrites thousands of parameters in one snapshot (2,716
    measured). Paging by row would spend a page on it and hide every other
    event, so the feed's unit is the snapshot."""

    def test_a_group_reports_its_true_count_while_capping_rows(self, conn):
        _ingest(conn, "20260101_000000", _chip())
        big = _chip()
        big["qubits"]["qA1"]["bulk"] = {f"k{i}": i for i in range(50)}
        _ingest(conn, "20260101_000100", big)
        g = li.changes_by_snapshot(conn, limit_snaps=5, rows_per_snap=10)[0]
        assert g["total"] == 50 and g["shown"] == 10

    def test_groups_are_newest_first_and_carry_their_meta(self, conn):
        _ingest(conn, "20260101_000000", _chip(t1=1e-5))
        _ingest(conn, "20260101_000100", _chip(t1=2e-5), trigger="experiment",
                experiment="05_T1", run_id=7)
        groups = li.changes_by_snapshot(conn, limit_snaps=5)
        assert groups[0]["timestamp"] == "20260101_000100"
        assert groups[0]["experiment"] == "05_T1" and groups[0]["run_id"] == 7

    def test_at_ts_opens_exactly_one_snapshot(self, conn):
        _ingest(conn, "20260101_000000", _chip(t1=1e-5))
        _ingest(conn, "20260101_000100", _chip(t1=2e-5))
        groups = li.changes_by_snapshot(conn, at_ts="20260101_000000")
        assert len(groups) == 1 and groups[0]["timestamp"] == "20260101_000000"

    def test_before_ts_excludes_the_newest(self, conn):
        _ingest(conn, "20260101_000000", _chip(t1=1e-5))
        _ingest(conn, "20260101_000100", _chip(t1=2e-5))
        groups = li.changes_by_snapshot(conn, before_ts="20260101_000100")
        assert [g["timestamp"] for g in groups] == ["20260101_000000"]

    def test_a_snapshot_with_no_matching_change_is_not_an_empty_group(self, conn):
        _ingest(conn, "20260101_000000", _chip())
        _ingest(conn, "20260101_000100", _chip(t1=9e-5))
        groups = li.changes_by_snapshot(conn, prefix="qubits.qA1.xy")
        assert [g["timestamp"] for g in groups] == ["20260101_000000"]


class TestThroughTheHistoryManager:
    """The capture path writes it, the read path heals it."""

    @pytest.fixture
    def hm(self, tmp_path):
        return HistoryManager(tmp_path / "inst", max_snapshots=50, cache_size=3)

    def _write(self, folder: Path, state: dict):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (folder / "wiring.json").write_text(json.dumps(
            {"network": {"host": "1.1.1.1"}, "ports": {"a": {"delay": 4}}}),
            encoding="utf-8")

    def test_capture_populates_it_and_field_history_uses_it(self, hm, tmp_path):
        live = tmp_path / "chip"
        for t1 in (1e-5, 2e-5, 3e-5):
            self._write(live, _chip(t1=t1))
            hm.check_and_snapshot(str(live), "manual", force=True)
        out = hm.field_history(live, "qubits.qA1.xy.operations.x180.amplitude")
        assert out["source"] == "leaf-index"
        st = hm.leaf_stats(live)
        assert st["snapshots"] == 3 and st["rows"] > 0 and st["dirty"] is False

    def test_a_missing_index_is_rebuilt_from_the_snapshots_on_disk(self, hm, tmp_path):
        live = tmp_path / "chip"
        for t1 in (1e-5, 2e-5):
            self._write(live, _chip(t1=t1))
            hm.check_and_snapshot(str(live), "manual", force=True)
        conn = hm._open_index(live)
        conn.execute("DELETE FROM leaf_cp")
        conn.execute("DELETE FROM leaf_snaps")
        conn.close()
        # Repair is LAZY — it happens when a reader actually needs tier 0.
        out = hm.field_history(live, "qubits.qA1.xy.operations.x180.amplitude")
        assert out["source"] == "leaf-index"
        assert hm.leaf_stats(live)["snapshots"] == 2      # healed on read

    def test_a_curated_property_still_answers_from_the_old_index(self, hm, tmp_path):
        """The eleven tracked properties keep their tier — this feature adds a
        tier BELOW them and must not re-route what already worked."""
        live = tmp_path / "chip"
        for t1 in (1e-5, 2e-5):
            self._write(live, _chip(t1=t1))
            hm.check_and_snapshot(str(live), "manual", force=True)
        out = hm.field_history(live, "qubits.qA1.T1")
        assert out["source"] == "index"
        assert [p["value"] for p in out["points"]] == [2e-5, 1e-5]

    def test_the_feed_and_search_work_off_the_manager(self, hm, tmp_path):
        live = tmp_path / "chip"
        for t1 in (1e-5, 2e-5):
            self._write(live, _chip(t1=t1))
            hm.check_and_snapshot(str(live), "manual", force=True)
        assert any(r["path"] == "qubits.qA1.T1" for r in hm.leaf_changes(live))
        assert any(h["path"] == "qubits.qA1.T1" for h in hm.leaf_search(live, "T1"))

    def test_wiring_leaves_are_indexed_too(self, hm, tmp_path):
        live = tmp_path / "chip"
        self._write(live, _chip())
        hm.check_and_snapshot(str(live), "manual", force=True)
        assert hm.leaf_field_series(live, "ports.a.delay")


@pytest.mark.skipif(not _ARCHIVE.is_dir(), reason="real history archive absent")
class TestRealArchive:
    """The synthetic cases above are shapes; these are the actual chips."""

    def _chips(self):
        for d in sorted(_ARCHIVE.iterdir()):
            snaps = [p for p in d.iterdir() if p.is_dir() and p.name[:2] == "20"] \
                if d.is_dir() else []
            if len(snaps) >= 20:
                yield d, sorted(snaps)

    def test_replay_reproduces_every_chips_newest_state(self):
        checked = 0
        for chip_dir, snaps in self._chips():
            conn = sqlite3.connect(":memory:", isolation_level=None)
            li.ensure_schema(conn)
            newest = None
            for s in snaps:
                try:
                    state = json.loads((s / "state.json").read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                w = s / "wiring.json"
                try:
                    wiring = json.loads(w.read_text(encoding="utf-8")) if w.exists() else None
                except (OSError, ValueError):
                    wiring = None
                _ingest(conn, s.name, state, wiring)
                newest = (state, wiring)
            truth, _k, _t = li.numeric_leaves(*newest)
            pmap = {i: p for i, p in conn.execute("SELECT id, path FROM leaf_paths")}
            recon = {pmap[pid]: v for pid, (v, k) in li._latest_values(conn).items()
                     if k in (li.KIND_NUM, li.KIND_PTR_NUM)}
            assert recon == truth, f"{chip_dir.name}: replay != file"
            # The economics the design rests on.
            rows = li.stats(conn)["rows"]
            assert rows < len(truth) * len(snaps) / 4, (
                f"{chip_dir.name}: {rows} rows is not change-point-shaped")
            checked += 1
            conn.close()
        assert checked >= 1, "archive present but no chip had enough snapshots"
