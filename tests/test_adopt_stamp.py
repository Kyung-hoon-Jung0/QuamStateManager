"""The adopt-before-ingest timestamp (docs/196 open item, reproduced in docs/200).

The customer's own scenario: run 50–70 experiments with SM **closed**, then open
it. The chip holds the last run's state, history has never seen that content, so
the adopt snapshot cannot dedup — it lands stamped with the moment SM opened,
``kind="exp"``. The run's own ingest arrives afterwards, finds the content
already stored, and ENRICHES it (docs/132) — adding ``run_id`` and the
experiment name, but never the timestamp.

The result is a data point on Trends sitting at the time someone opened SM
rather than the time the experiment ran, which is the one thing the customer
said must not happen:

    "비록 sync버튼을 뒤늦게 눌렀더라도, trends의 그래프에 찍히는 value들의 날짜는
     반드시 실험을 수행한 그 날짜/시간 이어야한다"

Every OTHER run in such a batch is fine — they are ingested from their own
folders and carry their own stamps (``_entry_timestamp``). This is specifically
the one whose content the chip was already holding when SM opened.

It was recorded as an ``xfail`` asserting the desired behaviour. The re-stamp
landed (docs/200 §5): when the run's ingest finds its content held by a NOTICE
snapshot (``trigger == "auto"``, no run yet) stamped later than the run, the
snapshot MOVES to the run's own stamp -- the outcome ingest-first already
produced. ``TestTheOrderOfArrivalDoesNotMatter`` pins that equivalence and every
guard; ``TestAKilledReStampConverges`` pins the crash states.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from quam_state_manager.core.history import HistoryManager
from quam_state_manager.core.scanner import ExperimentEntry


def _chip(folder: Path, t1: float) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(
        {"qubits": {"q1": {"id": "q1", "T1": t1}}, "qubit_pairs": {},
         "active_qubit_names": ["q1"]}), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(
        {"network": {}, "wiring": {"qubits": {}}}), encoding="utf-8")


def _run(root: Path, run_id: int, day: str, hhmmss: str, t1: float) -> ExperimentEntry:
    d = root / day / f"#{run_id}_some_node_{hhmmss}"
    _chip(d / "quam_state", t1)
    (d / "node.json").write_text(json.dumps(
        {"created_at": f"{day}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}",
         "metadata": {"name": "some_node"}}), encoding="utf-8")
    return ExperimentEntry(
        folder_path=d, quam_state_path=d / "quam_state", run_id=run_id,
        experiment_name="some_node",
        timestamp=f"{day}T{hhmmss[:2]}:{hhmmss[2:4]}:{hhmmss[4:]}",
        status="finished", qubits=["q1"], qubit_pairs=[], outcomes={},
        parent_ids=[], date_str=day, is_standalone=False)


def _stamps(inst: Path) -> list[str]:
    root = inst / "history"
    return sorted(d.name for d in root.rglob("2*") if d.is_dir() and d.name[:4].isdigit())


class TestALateSyncKeepsTheRunsOwnTime:
    def test_ingest_first_is_already_correct(self, tmp_path):
        """The control: when the run is ingested BEFORE the adopt, everything
        is right — which is why the scheduler hook was reordered (docs/132)."""
        inst, chip, data = tmp_path / "_i", tmp_path / "chip", tmp_path / "data"
        _chip(chip, 1.0e-5)
        hm = HistoryManager(str(inst))
        entry = _run(data, 77, "2026-09-10", "141516", 4.2e-5)

        hm.ingest_run(str(chip), entry)                 # the run lands first
        _chip(chip, 4.2e-5)                             # then SM adopts it
        hm.check_and_snapshot(str(chip), "auto", kind="exp")

        assert any(s.startswith("20260910") for s in _stamps(inst)), \
            "the run's own date is in history"

    def test_adopt_first_still_keeps_the_runs_own_time(self, tmp_path):
        """The customer's scenario: SM was CLOSED while the run wrote the chip."""
        inst, chip, data = tmp_path / "_i", tmp_path / "chip", tmp_path / "data"
        _chip(chip, 1.0e-5)
        hm = HistoryManager(str(inst))
        entry = _run(data, 77, "2026-09-10", "141516", 4.2e-5)

        _chip(chip, 4.2e-5)                             # the run wrote it, days ago
        hm.check_and_snapshot(str(chip), "auto", kind="exp")   # SM opens and adopts
        hm.ingest_run(str(chip), entry)                 # the ingest follows

        stamps = _stamps(inst)
        assert any(s.startswith("20260910") for s in stamps), (
            "the run ran on 2026-09-10, so its value belongs at that date; "
            f"history holds {stamps}")

    def test_the_enrich_does_attach_the_run_even_today(self, tmp_path):
        """What the current behaviour DOES get right, pinned so a fix keeps it:
        the linkage is never lost, only the time is wrong."""
        inst, chip, data = tmp_path / "_i", tmp_path / "chip", tmp_path / "data"
        _chip(chip, 1.0e-5)
        hm = HistoryManager(str(inst))
        entry = _run(data, 77, "2026-09-10", "141516", 4.2e-5)

        _chip(chip, 4.2e-5)
        hm.check_and_snapshot(str(chip), "auto", kind="exp")
        res = hm.ingest_run(str(chip), entry)

        assert res.get("skipped_duplicate") == 1
        assert res.get("enriched") == 1
        metas = [json.loads(p.read_text(encoding="utf-8"))
                 for p in (inst / "history").rglob("meta.json")]
        assert any(m.get("run_id") == 77 for m in metas), \
            "the run linkage survives the re-stamp"


# ----------------------------------------------------------------------
# docs/200 §5 -- the re-stamp
# ----------------------------------------------------------------------

def _hist(inst: Path) -> Path:
    return next(d for d in (inst / "history").iterdir()
                if d.is_dir() and (d / "index.sqlite").exists())


def _rows(inst: Path) -> dict:
    """Everything the order of arrival could leave different, stamp-keyed."""
    import sqlite3
    hd = _hist(inst)
    con = sqlite3.connect(str(hd / "index.sqlite"))
    try:
        return {
            "dirs": sorted(d.name for d in hd.iterdir() if d.is_dir()),
            "param_history": sorted(con.execute(
                "SELECT timestamp, qubit, property, value, trigger, run_id, "
                "experiment FROM param_history").fetchall()),
            "leaf_snaps": sorted(con.execute(
                "SELECT ts, trigger, run_id, experiment FROM leaf_snaps").fetchall()),
        }
    finally:
        con.close()


def _metas(hm: HistoryManager, chip: Path) -> list[tuple]:
    return [(s.timestamp, s.trigger, s.kind, s.run_id, s.experiment_name,
             s.diff_summary) for s in hm.list_snapshots(str(chip))]


def _t1_points(hm: HistoryManager, chip: Path) -> list[tuple]:
    out = hm.extract_property_history(str(chip), ["T1"], downsample=None)
    return sorted((p["timestamp"], p["value"]) for s in out for p in s["values"])


def _world(tmp_path: Path, name: str):
    inst, chip, data = (tmp_path / name / "_i", tmp_path / name / "chip",
                        tmp_path / name / "data")
    _chip(chip, 1.0e-5)
    hm = HistoryManager(str(inst))
    # the chip's past: an earlier run already in history, so the move has a
    # real prior and a real diff_summary to keep
    r76 = _run(data, 76, "2026-09-09", "101010", 3.0e-5)
    hm.ingest_run(str(chip), r76)
    r77 = _run(data, 77, "2026-09-10", "141516", 4.2e-5)
    return inst, chip, data, hm, r77


class TestTheOrderOfArrivalDoesNotMatter:
    def test_notice_first_ends_exactly_as_ingest_first(self, tmp_path):
        """The equivalence the fix is FOR: ingest-first (the docs/132 order)
        was already right, so notice-first must end byte-for-byte there --
        same stamps, same trigger/kind/run, same diff, same index rows."""
        inst_a, chip_a, _, hm_a, r77_a = _world(tmp_path, "a")
        hm_a.ingest_run(str(chip_a), r77_a)
        _chip(chip_a, 4.2e-5)
        assert hm_a.check_and_snapshot(str(chip_a), "auto", kind="exp") is None

        inst_b, chip_b, _, hm_b, r77_b = _world(tmp_path, "b")
        _chip(chip_b, 4.2e-5)
        notice = hm_b.check_and_snapshot(str(chip_b), "auto", kind="exp")
        assert notice is not None
        res = hm_b.ingest_run(str(chip_b), r77_b)

        assert res["restamped"] == 1
        assert _metas(hm_b, chip_b) == _metas(hm_a, chip_a)
        assert _rows(inst_b) == _rows(inst_a)
        assert notice.timestamp not in _rows(inst_b)["dirs"]

    def test_the_trends_point_moves_even_when_the_companion_was_built(
            self, tmp_path):
        """Trends reads the change-point companion, which a rowid watermark
        keeps fresh -- blind to an UPDATE. Build it BEFORE the move, so only an
        in-place relabel of the companion keeps the point where it belongs."""
        inst, chip, _, hm, r77 = _world(tmp_path, "c")
        _chip(chip, 4.2e-5)
        notice = hm.check_and_snapshot(str(chip), "auto", kind="exp")
        before = hm.extract_property_history(str(chip), ["T1"],
                                             compress="changes")
        assert any(p["timestamp"] == notice.timestamp
                   for s in before for p in s["values"]), "the setup is real"

        hm.ingest_run(str(chip), r77)
        after = hm.extract_property_history(str(chip), ["T1"],
                                            compress="changes")
        stamps = sorted(p["timestamp"] for s in after for p in s["values"])
        assert notice.timestamp not in stamps
        assert any(s.startswith("20260910") for s in stamps), stamps
        assert _t1_points(hm, chip)[-1][1] == pytest.approx(4.2e-5)

    def test_a_late_take_live_moves_too(self, tmp_path):
        """The customer's words were about the SYNC button: /state/sync's pull
        is ``trigger="auto", kind="manual"`` -- a notice, not a write."""
        inst, chip, _, hm, r77 = _world(tmp_path, "d")
        _chip(chip, 4.2e-5)
        hm.check_and_snapshot(str(chip), "auto", kind="manual")
        assert hm.ingest_run(str(chip), r77)["restamped"] == 1
        newest = hm.list_snapshots(str(chip))[0]
        assert newest.timestamp.startswith("20260910")
        assert (newest.trigger, newest.kind, newest.run_id) == ("experiment", "exp", 77)

    def test_what_sm_wrote_itself_keeps_its_time(self, tmp_path):
        """A ``save`` is SM writing live at that moment (an apply). docs/132:
        the human's time is the truth there -- the run is attached, the
        stamp stays."""
        inst, chip, _, hm, r77 = _world(tmp_path, "e")
        _chip(chip, 4.2e-5)
        saved = hm.check_and_snapshot(str(chip), "save", kind="manual")
        res = hm.ingest_run(str(chip), r77)
        assert res["restamped"] == 0 and res["enriched"] == 1
        newest = hm.list_snapshots(str(chip))[0]
        assert newest.timestamp == saved.timestamp
        assert newest.run_id == 77

    def test_a_snapshot_in_between_blocks_the_move(self, tmp_path):
        """A move that crossed another snapshot would reorder history and
        invalidate every change point on both sides of it -- never."""
        inst, chip, data, hm, r77 = _world(tmp_path, "f")
        hm.ingest_run(str(chip), _run(data, 78, "2026-09-11", "090000", 5.0e-5))
        _chip(chip, 4.2e-5)
        notice = hm.check_and_snapshot(str(chip), "auto", kind="exp")
        res = hm.ingest_run(str(chip), r77)
        assert res["restamped"] == 0 and res["enriched"] == 1
        assert hm.list_snapshots(str(chip))[0].timestamp == notice.timestamp

    @pytest.mark.parametrize("remembered_by", [
        "dir only",                   # its index insert failed / is deferred
        "param_history only",         # pruned; the leaf index lost it
        "leaf_snaps only",            # pruned; the curated index lost it
    ])
    def test_every_record_of_a_snapshot_in_between_blocks_it(
            self, tmp_path, remembered_by):
        """Pruning deletes the DIR and keeps the index rows (docs/83: pruned
        history survives); an index insert can fail and leave only the dir.
        Each of the three records alone must block the move."""
        import shutil
        import sqlite3
        inst, chip, data, hm, r77 = _world(tmp_path, "g")
        hm.ingest_run(str(chip), _run(data, 78, "2026-09-11", "090000", 5.0e-5))
        between = next(d for d in _hist(inst).iterdir()
                       if d.is_dir() and d.name.startswith("20260911"))
        con = sqlite3.connect(str(_hist(inst) / "index.sqlite"),
                              isolation_level=None)
        try:
            if remembered_by != "param_history only":
                con.execute("DELETE FROM param_history WHERE timestamp = ?",
                            (between.name,))
            if remembered_by != "leaf_snaps only":
                con.execute("DELETE FROM leaf_snaps WHERE ts = ?", (between.name,))
        finally:
            con.close()
        if remembered_by != "dir only":
            shutil.rmtree(between)
        hm.clear_cache()
        _chip(chip, 4.2e-5)
        notice = hm.check_and_snapshot(str(chip), "auto", kind="exp")
        assert hm.ingest_run(str(chip), r77)["restamped"] == 0
        assert notice.timestamp in _rows(inst)["dirs"]

    def test_a_run_later_than_the_notice_never_moves_it(self, tmp_path):
        inst, chip, data, hm, _ = _world(tmp_path, "h")
        _chip(chip, 4.2e-5)
        notice = hm.check_and_snapshot(str(chip), "auto", kind="exp")
        future = _run(data, 99, "2099-01-01", "000000", 4.2e-5)
        assert hm.ingest_run(str(chip), future)["restamped"] == 0
        assert hm.list_snapshots(str(chip))[0].timestamp == notice.timestamp

    def test_an_attributed_row_is_never_moved_again(self, tmp_path):
        """Once a run owns the row, a second run with identical content (a
        node that changed nothing) must not drag it around."""
        inst, chip, data, hm, r77 = _world(tmp_path, "i")
        _chip(chip, 4.2e-5)
        hm.check_and_snapshot(str(chip), "auto", kind="exp")
        assert hm.ingest_run(str(chip), r77)["restamped"] == 1
        twin = _run(data, 70, "2026-09-09", "235959", 4.2e-5)
        assert hm.ingest_run(str(chip), twin)["restamped"] == 0
        newest = hm.list_snapshots(str(chip))[0]
        assert newest.run_id == 77 and newest.timestamp.startswith("20260910")

    def test_a_notice_the_enrich_path_attributed_is_never_moved(self, tmp_path):
        """A notice the move could NOT take (something in between) is
        enriched instead -- it keeps ``trigger="auto"`` and gains run #77.
        A later identical-content run must not steal it and drag it along."""
        inst, chip, data, hm, r77 = _world(tmp_path, "p")
        hm.ingest_run(str(chip), _run(data, 78, "2026-09-11", "090000", 5.0e-5))
        _chip(chip, 4.2e-5)
        notice = hm.check_and_snapshot(str(chip), "auto", kind="exp")
        assert hm.ingest_run(str(chip), r77)["enriched"] == 1
        r80 = _run(data, 80, "2026-09-12", "120000", 4.2e-5)
        assert hm.ingest_run(str(chip), r80)["restamped"] == 0
        newest = hm.list_snapshots(str(chip))[0]
        assert (newest.timestamp, newest.run_id) == (notice.timestamp, 77)

    def test_two_holders_of_the_content_are_left_alone(self, tmp_path):
        """Which of two rows holding the content the run 'is' would be a
        guess. Unreachable through today's callers -- content dedup keeps one
        holder per hash, and every forced capture is manual/backup -- so the
        guard is exercised with a FORCED auto notice after an older holder."""
        inst, chip, data, hm, r77 = _world(tmp_path, "j")
        hm.ingest_run(str(chip), _run(data, 70, "2026-09-09", "235959", 4.2e-5))
        _chip(chip, 4.2e-5)
        hm.check_and_snapshot(str(chip), "auto", force=True, kind="exp")
        assert hm.ingest_run(str(chip), r77)["restamped"] == 0

    def test_the_bulk_backfill_moves_it_as_well(self, tmp_path):
        """The Param-History backfill ingests without enrichment and inside a
        batch transaction; the move must still happen, and must not deadlock
        on the batch's own write lock."""
        inst, chip, data, hm, r77 = _world(tmp_path, "k")
        _chip(chip, 4.2e-5)
        hm.check_and_snapshot(str(chip), "auto", kind="exp")
        # a fresh run FIRST, so the batch transaction is open when r77 lands
        r75 = _run(data, 75, "2026-09-08", "080000", 2.0e-5)
        res = hm._ingest_entries_into(_hist(inst), [r75, r77])
        assert res["ingested"] == 1 and res["restamped"] == 1
        assert hm.list_snapshots(str(chip))[0].timestamp.startswith("20260910")


class TestAKilledReStampConverges:
    def test_killed_after_the_copy_the_next_read_finishes_it(
            self, tmp_path, monkeypatch):
        """Killed between the copy and the index relabel: the intent, BOTH
        dirs, index rows under the old stamp. The next index read must end
        with one row at the run's time -- never a second point beside it."""
        inst, chip, _, hm, r77 = _world(tmp_path, "m")
        _chip(chip, 4.2e-5)
        notice = hm.check_and_snapshot(str(chip), "auto", kind="exp")
        real = HistoryManager._finish_restamp
        monkeypatch.setattr(HistoryManager, "_finish_restamp",
                            lambda self, *a, **k: None)
        hm.ingest_run(str(chip), r77)
        hd = _hist(inst)
        assert (hd / HistoryManager._RESTAMP_INTENT).exists()
        assert notice.timestamp in _rows(inst)["dirs"], "the kill state is real"
        monkeypatch.setattr(HistoryManager, "_finish_restamp", real)
        hm.clear_cache()

        points = _t1_points(hm, chip)
        assert not (hd / HistoryManager._RESTAMP_INTENT).exists()
        assert notice.timestamp not in _rows(inst)["dirs"]
        assert [t for t, _ in points if not t.startswith("20260909")] == [
            s for s in _rows(inst)["dirs"] if s.startswith("20260910")]

    def _half_copied(self, tmp_path, name):
        inst, chip, _, hm, r77 = _world(tmp_path, name)
        _chip(chip, 4.2e-5)
        notice = hm.check_and_snapshot(str(chip), "auto", kind="exp")
        hd = _hist(inst)
        run_ts = HistoryManager._entry_timestamp(r77)
        (hd / run_ts).mkdir()
        (hd / run_ts / "state.json").write_text("{", encoding="utf-8")
        (hd / HistoryManager._RESTAMP_INTENT).write_text(json.dumps(
            {"from": notice.timestamp, "to": run_ts, "meta": {}}), encoding="utf-8")
        return inst, chip, hm, r77, notice, hd, run_ts

    def test_killed_mid_copy_the_next_read_abandons_the_copy(self, tmp_path):
        """Killed before meta.json landed: the next index read throws the
        half copy away -- the notice is still whole, nothing is left behind."""
        inst, chip, hm, r77, notice, hd, run_ts = self._half_copied(tmp_path, "q")
        _t1_points(hm, chip)
        assert not (hd / run_ts).exists()
        assert not (hd / HistoryManager._RESTAMP_INTENT).exists()
        assert notice.timestamp in _rows(inst)["dirs"]

    def test_killed_mid_copy_the_next_ingest_still_makes_the_move(self, tmp_path):
        inst, chip, hm, r77, notice, hd, run_ts = self._half_copied(tmp_path, "n")
        res = hm.ingest_run(str(chip), r77)
        assert res["restamped"] == 1
        assert _rows(inst)["dirs"][-1] == run_ts
        assert notice.timestamp not in _rows(inst)["dirs"]
        assert not (hd / HistoryManager._RESTAMP_INTENT).exists()

    def test_rows_already_under_the_new_stamp_win(self, tmp_path):
        """If something indexed the copy before the relabel ran, the old rows
        go -- a primary-key clash must not abort the recovery forever."""
        import sqlite3
        inst, chip, _, hm, r77 = _world(tmp_path, "o")
        _chip(chip, 4.2e-5)
        notice = hm.check_and_snapshot(str(chip), "auto", kind="exp")
        hd = _hist(inst)
        con = sqlite3.connect(str(hd / "index.sqlite"), isolation_level=None)
        try:
            con.execute(
                "INSERT INTO param_history (timestamp, qubit, property, value, "
                "trigger) SELECT '20260910_051516_077', qubit, property, value, "
                "'experiment' FROM param_history WHERE timestamp = ?",
                (notice.timestamp,))
            HistoryManager._relabel_index_rows(
                con, notice.timestamp, "20260910_051516_077", {"run_id": 77})
            left = {r[0] for r in con.execute(
                "SELECT DISTINCT timestamp FROM param_history")}
        finally:
            con.close()
        assert notice.timestamp not in left
        assert "20260910_051516_077" in left
