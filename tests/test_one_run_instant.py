"""docs/262 -- one run instant everywhere, and the Param History re-key (v4).

Every place that turns a run into a time now reads ``timefmt.run_instant``
(docs/256) through ``core/run_time.py``: the Param History key
(``HistoryManager._entry_timestamp``), the near-real-time ingest
(``routes._exp_entry_for_run``), the runs tier (``routes._run_ts_stamp``), the
dataset table (``RunInfo.instant_us`` -> compact row ``t``), the Datasets Trends
axis (``trend_index.run_instant_ms``) and the cross-folder orderings. The
migration (``core/history_rekey.py``) moves snapshots stored under the old
reading (created_at digits read in THIS machine's zone) to the instant's key.

The fixtures are -04:00 runs (the real Novera archive's zone, docs/256) and
+09:00 runs (this machine's). The OLD key is simulated with an explicit +09:00
machine zone, so every expectation is a fixed string, true on any host. A
mutation that re-introduces a zone-dependent reading is caught on any host
whose zone is not -04:00 (this machine: +09:00).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from quam_state_manager.core import history_rekey, run_time, timefmt
from quam_state_manager.core.history import HistoryManager
from quam_state_manager.core.history_rekey import (
    FLAG_NAME, JOURNAL_DIR, migrate_history_rekey_v4, revert_history_rekey_v4)
from quam_state_manager.core.scanner import _parse_experiment_folder

KST = timezone(timedelta(hours=9))
EDT = timezone(timedelta(hours=-4))
H13 = 13 * 3600


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

def _state(t1: float, f01: float = 5.0e9, note: str | None = None) -> dict:
    st = {"qubits": {"q1": {"id": "q1", "f_01": f01, "T1": t1,
                            "xy": {"RF_frequency": f01}}}}
    if note:
        st["extras"] = {"note": note}
    return st


def _wiring() -> dict:
    return {"wiring": {"qubits": {"q1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
            "network": {"host": "10.0.0.9"}}


def make_run(root: Path, run_id: int, name: str, created_at: str | None, *,
             folder_clock: str | None = None, run_end: str | None = None,
             state: dict | None = None, node: bool = True) -> Path:
    """``<root>/<date>/#<id>_<name>_<HHMMSS>`` with node.json + quam_state.
    The folder clock is created_at's own digits unless given."""
    clock = folder_clock or created_at
    run = root / clock[:10] / f"#{run_id}_{name}_{clock[11:19].replace(':', '')}"
    qs = run / "quam_state"
    qs.mkdir(parents=True, exist_ok=True)
    (qs / "state.json").write_text(json.dumps(state or _state(run_id * 1e-6), indent=2),
                                   encoding="utf-8")
    (qs / "wiring.json").write_text(json.dumps(_wiring(), indent=2), encoding="utf-8")
    if node:
        doc = {"id": run_id, "parents": [],
               "metadata": {"name": name, "status": "finished", "run_end": run_end},
               "data": {"parameters": {"model": {"qubits": ["q1"]}}}}
        if created_at is not None:
            doc["created_at"] = created_at
        (run / "node.json").write_text(json.dumps(doc, indent=2), encoding="utf-8")
    return run


def utc_key(iso: str, run_id: int) -> str:
    """The expected key, computed independently of the code under test."""
    d = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return d.strftime("%Y%m%d_%H%M%S") + f"_{run_id % 1000:03d}"


def old_key(entry, machine_tz=KST) -> str:
    """The pre-docs/262 reading: created_at's date + HH:MM:SS digits, offset
    dropped, read in the machine zone (``_entry_timestamp`` before 262)."""
    date = entry.date_str.replace("-", "")
    hms = entry.timestamp.split("T", 1)[1][:8].replace(":", "")
    naive = datetime.strptime(date + hms, "%Y%m%d%H%M%S")
    return (naive.replace(tzinfo=machine_tz).astimezone(timezone.utc)
            .strftime("%Y%m%d_%H%M%S") + f"_{(entry.run_id or 0) % 1000:03d}")


@pytest.fixture
def chip(tmp_path: Path) -> Path:
    qs = tmp_path / "lab" / "chip" / "quam_state"
    qs.mkdir(parents=True)
    (qs / "state.json").write_text(json.dumps(_state(1e-6)), encoding="utf-8")
    (qs / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    return qs


def _entries(runs: list[Path]) -> list:
    return [_parse_experiment_folder(r / "quam_state") for r in runs]


def _ingest(hm: HistoryManager, chip: Path, runs: list[Path], hist: Path | None = None) -> dict:
    return hm._ingest_entries_into(hist or hm._history_dir(chip), _entries(runs),
                                   fallback_wiring_path=chip / "wiring.json")


def _edt_runs(root: Path) -> list[Path]:
    """Four -04:00 runs; two straddle the -04:00 midnight (a date change in
    UTC and in KST)."""
    return [
        make_run(root, 1172, "cz_phase", "2026-04-02T21:20:42-04:00", state=_state(11e-6)),
        make_run(root, 1181, "xeb", "2026-04-02T23:53:35-04:00", state=_state(12e-6)),
        make_run(root, 1182, "zz_off", "2026-04-03T00:07:48-04:00", state=_state(13e-6)),
        make_run(root, 1194, "t1", "2026-04-03T01:03:26-04:00", state=_state(14e-6)),
    ]


def _build_old_history(tmp_path, chip, runs, monkeypatch) -> tuple[Path, HistoryManager]:
    """What the pre-262 backfill stored on a +09:00 machine."""
    inst = tmp_path / "inst"
    with monkeypatch.context() as m:
        m.setattr(HistoryManager, "_entry_timestamp", staticmethod(old_key))
        hm = HistoryManager(inst)
        _ingest(hm, chip, runs)
        hm.rebuild_leaf_index(chip)
    return inst, hm


def tree_sha(root: Path, *, skip_index: bool = False) -> dict[str, str]:
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            if skip_index and "/index.sqlite" in "/" + rel:
                continue
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def index_rows(idx: Path) -> dict:
    conn = sqlite3.connect(str(idx))
    try:
        return {
            "ph": sorted(conn.execute(
                "SELECT timestamp, qubit, property, value, raw_pointer, trigger, "
                "run_id, experiment FROM param_history")),
            "snaps": sorted(conn.execute(
                "SELECT ts, trigger, run_id, experiment, folder FROM leaf_snaps")),
            "cp": sorted(conn.execute(
                "SELECT p.path, s.ts, c.value, c.kind FROM leaf_cp c "
                "JOIN leaf_paths p ON p.id=c.path_id JOIN leaf_snaps s ON s.id=c.snap_id")),
            "dirty": dict(conn.execute("SELECT key, value FROM leaf_meta")).get("dirty") or "0",
        }
    finally:
        conn.close()


def snap_dirs(hist: Path) -> list[str]:
    return sorted(d.name for d in hist.iterdir() if d.is_dir())


# --------------------------------------------------------------------------
# 1. the key reads the instant
# --------------------------------------------------------------------------

class TestTheKeyIsTheInstant:

    def test_a_minus_four_run_keys_its_utc_second(self, tmp_path):
        run = make_run(tmp_path / "a", 1172, "cz", "2026-04-02T21:20:42-04:00")
        key = HistoryManager._entry_timestamp(_entries([run])[0])
        assert key == "20260403_012042_172" == utc_key("2026-04-02T21:20:42-04:00", 1172)

    def test_a_same_zone_run_keeps_the_key_it_always_had(self, tmp_path):
        """The real KRISS_CZ snapshot 20260907_073939_039 (+09:00 run on a
        +09:00 machine): the new reading is byte-identical, so that history
        needs no re-key."""
        run = make_run(tmp_path / "k", 39, "res_vs_flux", "2026-09-07T16:39:39+09:00")
        assert HistoryManager._entry_timestamp(_entries([run])[0]) == "20260907_073939_039"

    def test_z_and_fractions_floor_to_the_second(self, tmp_path):
        run = make_run(tmp_path / "z", 5, "x", "2026-04-03T04:07:48.999Z",
                       folder_clock="2026-04-03T04:07:48")
        assert HistoryManager._entry_timestamp(_entries([run])[0]) == "20260403_040748_005"

    def test_a_naive_run_is_read_in_its_archives_offset(self, tmp_path):
        root = tmp_path / "n"
        for i, t in enumerate(("2026-04-02T20:00:00-04:00", "2026-04-02T21:00:00-04:00",
                               "2026-04-02T22:00:00-04:00")):
            make_run(root, 10 + i, "sib", t)
        naive = make_run(root, 20, "naive", "2026-04-03T00:07:48")
        key = HistoryManager._entry_timestamp(_entries([naive])[0])
        assert key == utc_key("2026-04-03T00:07:48-04:00", 20)

    def test_a_run_dated_only_by_its_folder_uses_the_archive_offset(self, tmp_path):
        root = tmp_path / "f"
        for i, t in enumerate(("2026-04-02T20:00:00-04:00", "2026-04-02T21:00:00-04:00")):
            make_run(root, 10 + i, "sib", t)
        bare = make_run(root, 30, "bare", None, folder_clock="2026-04-03T00:07:48")
        entry = SimpleNamespace(timestamp="", folder_path=bare, run_id=30,
                                date_str="2026-04-03", experiment_name="bare")
        assert HistoryManager._entry_timestamp(entry) == utc_key("2026-04-03T00:07:48-04:00", 30)

    def test_a_stub_entry_reads_the_runs_own_node_json(self, tmp_path):
        """A scanner stub's created_at is the folder clock, naive -- the
        run's node.json says -04:00, and that decides (even though the
        archive's majority -- its two siblings -- is +09:00)."""
        root = tmp_path / "s"
        make_run(root, 5, "sib", "2026-04-02T20:00:00+09:00")
        make_run(root, 6, "sib", "2026-04-02T21:00:00+09:00")
        run = make_run(root, 7, "x", "2026-04-03T00:07:48-04:00")
        stub = SimpleNamespace(timestamp="2026-04-03T00:07:48", folder_path=run,
                               run_id=7, date_str="2026-04-03", experiment_name="x")
        assert HistoryManager._entry_timestamp(stub) == "20260403_040748_007"


# --------------------------------------------------------------------------
# 2. backfill and live ingest: one run, one key
# --------------------------------------------------------------------------

class TestBackfillAndLiveIngestAgree:

    def _store_run(self, root: Path, run: Path):
        from quam_state_manager.core.dataset import DatasetStore
        st = DatasetStore(root)
        rid = int(run.name[1:].split("_")[0])
        return st, st.runs[rid]

    def test_the_three_paths_give_one_key(self, tmp_path):
        from quam_state_manager.web.routes import _exp_entry_for_run, _run_ts_stamp
        root = tmp_path / "a"
        runs = _edt_runs(root)
        for run in runs:
            _st, info = self._store_run(root, run)
            backfill = HistoryManager._entry_timestamp(_entries([run])[0])
            live = HistoryManager._entry_timestamp(_exp_entry_for_run(info))
            tier = _run_ts_stamp(info)
            assert backfill == live == tier == utc_key(
                json.loads((run / "node.json").read_text())["created_at"], info.run_id)

    def test_the_live_entry_carries_the_runs_own_created_at(self, tmp_path):
        """The near-real-time entry hands over node.json's created_at /
        run_end themselves: with node.json unreadable at ingest time (being
        rewritten) and no sibling to vote an offset, the key is still the
        instant -- not the folder digits read in this machine's zone."""
        from quam_state_manager.web.routes import _exp_entry_for_run
        root = tmp_path / "one"
        run = make_run(root, 1182, "zz_off", "2026-04-03T00:07:48-04:00")
        _st, info = self._store_run(root, run)
        (run / "node.json").unlink()
        entry = _exp_entry_for_run(info)
        assert (entry.timestamp, entry.run_end) == (info.created_at, info.run_end)
        assert HistoryManager._entry_timestamp(entry) == "20260403_040748_182"

    @pytest.mark.parametrize("order", ["backfill_first", "live_first"])
    def test_ingesting_the_same_run_both_ways_stores_it_once(
            self, tmp_path, chip, order):
        from quam_state_manager.web.routes import _exp_entry_for_run
        root = tmp_path / "a"
        run = _edt_runs(root)[2]
        _st, info = self._store_run(root, run)
        hm = HistoryManager(tmp_path / "inst")
        paths = [lambda: _ingest(hm, chip, [run]),
                 lambda: hm.ingest_run(chip, _exp_entry_for_run(info),
                                       fallback_wiring_path=chip / "wiring.json")]
        if order == "live_first":
            paths.reverse()
        assert paths[0]()["ingested"] == 1
        assert paths[1]()["ingested"] == 0
        assert snap_dirs(hm._history_dir(chip)) == ["20260403_040748_182"]


# --------------------------------------------------------------------------
# 3. a taken key is a collision, never "already ingested"
# --------------------------------------------------------------------------

class TestCollisions:

    def test_two_runs_in_one_second_with_one_suffix_are_both_kept(self, tmp_path, chip):
        root = tmp_path / "a"
        a = make_run(root, 39, "rabi", "2026-04-03T00:07:48-04:00", state=_state(1e-5))
        b = make_run(root, 1039, "ramsey", "2026-04-03T00:07:48-04:00", state=_state(2e-5))
        hm = HistoryManager(tmp_path / "inst")
        assert _ingest(hm, chip, [a, b])["ingested"] == 2
        hist = hm._history_dir(chip)
        assert snap_dirs(hist) == ["20260403_040748_039", "20260403_040748_03901"]
        metas = {n: json.loads((hist / n / "meta.json").read_text()) for n in snap_dirs(hist)}
        assert {m["run_id"] for m in metas.values()} == {39, 1039}
        # ... and a re-ingest of both is a no-op
        assert _ingest(hm, chip, [a, b])["ingested"] == 0
        assert len(snap_dirs(hist)) == 2

    def test_a_snapshot_with_no_index_rows_is_never_rewritten_or_deleted(
            self, tmp_path, chip):
        """A state with no tracked property has no param_history rows, so the
        pre-262 'already ingested' test never saw it: the re-ingest wrote into
        the same dir and the dedup branch then deleted it."""
        run = make_run(tmp_path / "a", 3, "tof", "2026-04-03T00:07:48-04:00",
                       state={"extras": {"chip_name": "x"}, "qubits": {}})
        hm = HistoryManager(tmp_path / "inst")
        hist = hm._history_dir(chip)
        assert _ingest(hm, chip, [run], hist)["ingested"] == 1
        before = tree_sha(hist / "20260403_040748_003")
        res = _ingest(hm, chip, [run], hist)
        assert res["ingested"] == 0 and res["skipped_duplicate"] == 0
        assert tree_sha(hist / "20260403_040748_003") == before

    def test_a_snapshot_the_index_has_not_seen_is_never_overwritten(self, tmp_path, chip):
        """The index knows nothing of a snapshot dir (its deferred index write
        has not landed, or the index was rebuilt from scratch): the dir alone
        says the stamp is taken, and by whom. Re-ingesting the same run must
        leave it whole -- before docs/262 the copy was written INTO it and the
        dedup branch then deleted the dir."""
        run = make_run(tmp_path / "a", 3, "tof", "2026-04-03T00:07:48-04:00")
        hm = HistoryManager(tmp_path / "inst")
        hist = hm._history_dir(chip)
        assert _ingest(hm, chip, [run], hist)["ingested"] == 1
        snap = hist / "20260403_040748_003"
        before = tree_sha(snap)
        conn = sqlite3.connect(str(hist / "index.sqlite"))
        conn.execute("DELETE FROM param_history")
        conn.execute("DELETE FROM leaf_cp")
        conn.execute("DELETE FROM leaf_snaps")
        conn.commit()
        conn.close()
        res = HistoryManager(tmp_path / "inst")._ingest_entries_into(
            hist, _entries([run]), fallback_wiring_path=chip / "wiring.json")
        assert res["ingested"] == 0
        assert snap.is_dir() and tree_sha(snap) == before
        assert snap_dirs(hist) == ["20260403_040748_003"]


# --------------------------------------------------------------------------
# 4. the re-key migration
# --------------------------------------------------------------------------

class TestRekeyMigration:

    def test_foreign_zone_snapshots_move_by_exactly_thirteen_hours(
            self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        hist = hm._history_dir(chip)
        old = snap_dirs(hist)
        pre_rows = index_rows(hist / "index.sqlite")
        assert len(old) == 4 and pre_rows["cp"]

        res = migrate_history_rekey_v4(inst, skip_if_peers=False)
        assert res["status"] == "migrated"
        new = snap_dirs(hist)
        want = sorted(HistoryManager._entry_timestamp(e) for e in _entries(runs))
        assert new == want
        shift = {(datetime.strptime(b[:15], "%Y%m%d_%H%M%S")
                  - datetime.strptime(a[:15], "%Y%m%d_%H%M%S")).total_seconds()
                 for a, b in zip(old, new)}
        assert shift == {H13}
        for name in new:
            meta = json.loads((hist / name / "meta.json").read_text())
            assert meta["timestamp"] == name and meta["rekeyed_from"] in old
        # the index follows: every row relabelled, nothing else changed
        o2n = dict(zip(old, new))
        rows = index_rows(hist / "index.sqlite")
        assert rows["ph"] == sorted((o2n[r[0]],) + tuple(r[1:]) for r in pre_rows["ph"])
        assert sorted(r[0] for r in rows["snaps"]) == new
        assert Counter((p, o2n[t], v, k) for p, t, v, k in pre_rows["cp"]) == Counter(rows["cp"])
        assert rows["dirty"] == "0"
        assert json.loads((inst / FLAG_NAME).read_text())["status"] == "migrated"
        # the listing serves the new names (the manifest was rebuilt)
        assert [m.timestamp for m in HistoryManager(inst).list_snapshots(chip)] == new[::-1]

    def test_same_zone_history_is_byte_identical(self, tmp_path, chip, monkeypatch):
        runs = [make_run(tmp_path / "k", 39 + i, "res", f"2026-09-07T16:{30 + i}:39+09:00",
                         state=_state((i + 1) * 1e-5)) for i in range(3)]
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        before = tree_sha(inst / "history")
        res = migrate_history_rekey_v4(inst, skip_if_peers=False)
        assert res["chips"][hm._history_dir(chip).name]["moves"] == 0
        assert tree_sha(inst / "history") == before        # index.sqlite included
        assert not (inst / JOURNAL_DIR).exists()

    def test_a_second_run_moves_nothing_and_writes_nothing(
            self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        migrate_history_rekey_v4(inst, skip_if_peers=False)
        after_first = tree_sha(inst / "history")
        journal = (inst / JOURNAL_DIR / f"{hm._history_dir(chip).name}.json").read_bytes()
        res = migrate_history_rekey_v4(inst, skip_if_peers=False, force=True)
        assert res["chips"][hm._history_dir(chip).name]["moves"] == 0
        assert tree_sha(inst / "history") == after_first
        assert (inst / JOURNAL_DIR / f"{hm._history_dir(chip).name}.json").read_bytes() == journal

    def test_a_taken_target_is_a_collision_and_nothing_merges(
            self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        hist = hm._history_dir(chip)
        # another run already holds the target of #1182 (same second, same suffix)
        intruder = make_run(tmp_path / "b", 2182, "other", "2026-04-03T00:07:48-04:00",
                            state=_state(99e-6))
        hm2 = HistoryManager(inst)
        hm2._ingest_entries_into(hist, _entries([intruder]),
                                 fallback_wiring_path=chip / "wiring.json")
        assert "20260403_040748_182" in snap_dirs(hist)
        res = migrate_history_rekey_v4(inst, skip_if_peers=False)
        assert res["chips"][hist.name]["collisions"] == 1
        names = snap_dirs(hist)
        assert len(names) == 5
        assert "20260403_040748_18201" in names
        moved = json.loads((hist / "20260403_040748_18201" / "meta.json").read_text())
        assert moved["run_id"] == 1182 and moved["rekeyed_from"] == "20260402_150748_182"
        assert json.loads((hist / "20260403_040748_182" / "meta.json").read_text())["run_id"] == 2182
        # the moved run's own re-ingest finds it (no third copy)
        assert hm2._ingest_entries_into(hist, _entries([runs[2]]))["ingested"] == 0

    def test_revert_restores_every_byte(self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        hist = hm._history_dir(chip)
        before = tree_sha(inst / "history", skip_index=True)
        rows_before = index_rows(hist / "index.sqlite")
        mtimes = {n: (hist / n / "meta.json").stat().st_mtime_ns for n in snap_dirs(hist)}
        migrate_history_rekey_v4(inst, skip_if_peers=False)
        assert tree_sha(inst / "history", skip_index=True) != before
        res = revert_history_rekey_v4(inst)
        assert res["chips"][hist.name] == {"reverted": 4, "edited": []}
        assert tree_sha(inst / "history", skip_index=True) == before
        assert index_rows(hist / "index.sqlite") == rows_before
        assert {n: (hist / n / "meta.json").stat().st_mtime_ns
                for n in snap_dirs(hist)} == mtimes
        # the flag says reverted: a restart does not re-apply ...
        assert migrate_history_rekey_v4(inst, skip_if_peers=False)["status"] == "reverted"
        assert tree_sha(inst / "history", skip_index=True) == before
        # ... force does
        migrate_history_rekey_v4(inst, skip_if_peers=False, force=True)
        assert snap_dirs(hist) == sorted(
            HistoryManager._entry_timestamp(e) for e in _entries(runs))

    def test_revert_keeps_a_label_written_after_the_rekey(
            self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        hist = hm._history_dir(chip)
        migrate_history_rekey_v4(inst, skip_if_peers=False)
        target = snap_dirs(hist)[0]
        HistoryManager(inst).annotate_snapshot(chip, target, label="keep me")
        res = revert_history_rekey_v4(inst)
        back = json.loads((hist / "20260402_122042_172" / "meta.json").read_text())
        assert back["label"] == "keep me" and back["timestamp"] == "20260402_122042_172"
        assert "rekeyed_from" not in back
        assert res["chips"][hist.name]["edited"] == ["20260402_122042_172"]

    def test_the_journal_lands_before_the_first_move(self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        seen = []
        real = os.rename

        def spy(a, b):
            jp = inst / JOURNAL_DIR / f"{hm._history_dir(chip).name}.json"
            seen.append(jp.exists() and json.loads(jp.read_text())["state"])
            return real(a, b)
        monkeypatch.setattr(history_rekey.os, "rename", spy)
        migrate_history_rekey_v4(inst, skip_if_peers=False)
        assert seen == ["applying"] * 4

    def test_a_killed_apply_resumes_to_the_same_end_state(
            self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        twin = tmp_path / "twin"
        shutil.copytree(inst, twin)
        clean = migrate_history_rekey_v4(twin, skip_if_peers=False)
        assert clean["status"] == "migrated"

        calls = {"n": 0}
        real = history_rekey._relabel

        def dies(*a, **k):
            calls["n"] += 1
            raise OSError("killed")
        monkeypatch.setattr(history_rekey, "_relabel", dies)
        res = migrate_history_rekey_v4(inst, skip_if_peers=False)
        assert res["status"] == "partial" and not (inst / FLAG_NAME).exists()
        jp = inst / JOURNAL_DIR / f"{hm._history_dir(chip).name}.json"
        assert json.loads(jp.read_text())["state"] == "applying"
        monkeypatch.setattr(history_rekey, "_relabel", real)
        migrate_history_rekey_v4(inst, skip_if_peers=False)
        assert json.loads(jp.read_text())["state"] == "applied"
        hist, thist = hm._history_dir(chip), twin / "history" / hm._history_dir(chip).name
        assert snap_dirs(hist) == snap_dirs(thist)
        # every snapshot file is the same bytes (the manifest records each
        # meta.json's mtime, which two runs cannot share -- its listing is
        # compared instead)
        a, b = tree_sha(hist, skip_index=True), tree_sha(thist, skip_index=True)
        a.pop("snapshots_manifest.json"); b.pop("snapshots_manifest.json")
        assert a == b
        assert ([asdict(m) for m in HistoryManager(inst)._list_snapshots_in_dir(hist)]
                == [asdict(m) for m in HistoryManager(twin)._list_snapshots_in_dir(thist)])
        assert index_rows(hist / "index.sqlite") == index_rows(thist / "index.sqlite")

    def test_a_killed_revert_resumes_on_the_next_start(self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        before = tree_sha(inst / "history", skip_index=True)
        migrate_history_rekey_v4(inst, skip_if_peers=False)
        real = history_rekey._relabel
        monkeypatch.setattr(history_rekey, "_relabel",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("killed")))
        with pytest.raises(OSError):
            revert_history_rekey_v4(inst)
        monkeypatch.setattr(history_rekey, "_relabel", real)
        # the flag still says migrated; the pending journal is resumed anyway
        res = migrate_history_rekey_v4(inst, skip_if_peers=False)
        assert res["status"] == "resumed"
        assert tree_sha(inst / "history", skip_index=True) == before
        assert json.loads((inst / FLAG_NAME).read_text())["status"] == "reverted"
        assert migrate_history_rekey_v4(inst, skip_if_peers=False)["status"] == "reverted"

    def test_an_unreadable_run_folder_keeps_its_key(self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        (runs[0] / "node.json").unlink()
        res = migrate_history_rekey_v4(inst, skip_if_peers=False)
        c = res["chips"][hm._history_dir(chip).name]
        assert c["moves"] == 3 and c["unresolved"] == 1
        assert "20260402_122042_172" in snap_dirs(hm._history_dir(chip))

    @pytest.mark.skipif(datetime(2026, 4, 3).astimezone().utcoffset() == timedelta(0),
                        reason="needs a host zone other than UTC")
    def test_a_run_dated_only_by_assuming_this_machines_zone_keeps_its_key(
            self, tmp_path, chip, monkeypatch):
        """History ingested on a UTC server from naive runs (no offset
        anywhere, no sibling to vote one), then opened here: the new reading
        would ALSO be an assumption (this machine's zone), so nothing moves --
        the journal lists them instead."""
        root = tmp_path / "naive"
        runs = [make_run(root, 40 + i, "naive", f"2026-04-03T0{i}:07:48",
                         state=_state((i + 1) * 1e-5)) for i in range(2)]
        inst = tmp_path / "inst"
        with monkeypatch.context() as m:
            m.setattr(HistoryManager, "_entry_timestamp",
                      staticmethod(lambda e: old_key(e, timezone.utc)))
            hm = HistoryManager(inst)
            _ingest(hm, chip, runs)
        hist = hm._history_dir(chip)
        before = snap_dirs(hist)
        res = migrate_history_rekey_v4(inst, skip_if_peers=False)
        c = res["chips"][hist.name]
        assert c["moves"] == 0 and c["kept_assumed_local"] == 2
        assert snap_dirs(hist) == before

    def test_deferred_while_another_window_is_open(self, tmp_path, chip, monkeypatch):
        runs = _edt_runs(tmp_path / "a")
        inst, hm = _build_old_history(tmp_path, chip, runs, monkeypatch)
        from quam_state_manager.core import instances
        monkeypatch.setattr(instances, "peers", lambda *a, **k: [object()])
        before = tree_sha(inst / "history")
        assert migrate_history_rekey_v4(inst)["status"] == "deferred"
        assert tree_sha(inst / "history") == before and not (inst / FLAG_NAME).exists()


# --------------------------------------------------------------------------
# 5. the dataset table, the trend axis, the cross-folder orderings
# --------------------------------------------------------------------------

class TestDatasetsReadTheInstant:

    def test_runinfo_and_the_compact_row_carry_the_instant(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore, _compact_row
        root = tmp_path / "a"
        _edt_runs(root)
        st = DatasetStore(root)
        r = st.runs[1182]
        want_ms = int(datetime(2026, 4, 3, 4, 7, 48, tzinfo=timezone.utc).timestamp() * 1000)
        assert (r.instant_us, r.instant_q) == (want_ms * 1000, "offset")
        row = _compact_row(r)
        assert row["t"] == want_ms and row["tq"] == "offset"
        assert (row["date"], row["time"]) == ("2026-04-03", "00:07:48")   # acquisition clock

    def test_a_naive_run_is_dated_by_the_archives_vote(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        root = tmp_path / "a"
        _edt_runs(root)
        make_run(root, 1300, "naive", "2026-04-03T02:00:00")
        st = DatasetStore(root)
        r = st.runs[1300]
        assert r.instant_q == "archive_offset"
        assert r.instant_us == int(datetime(2026, 4, 3, 6, 0, tzinfo=timezone.utc).timestamp()) * 10**6

    def test_the_trend_axis_is_the_instant_and_merges_by_it(self, tmp_path):
        from quam_state_manager.core import trend_index
        from quam_state_manager.core.dataset import DatasetStore
        a = DatasetStore(_edt_runs(tmp_path / "a")[0].parent.parent)
        kroot = tmp_path / "k"
        make_run(kroot, 5, "zz_off", "2026-04-03T10:00:00+09:00")    # 01:00Z
        k = DatasetStore(kroot)
        ia = trend_index.ExperimentTrend.build(
            "zz_off", 1, [r for r in a.runs.values() if r.experiment_name == "zz_off"])
        ik = trend_index.ExperimentTrend.build("zz_off", 1, k.runs.values())
        assert ia.t_ms == [int(datetime(2026, 4, 3, 4, 7, 48, tzinfo=timezone.utc).timestamp() * 1000)]
        rows = trend_index._rows([("a", ia), ("k", ik)], None, merged=True)
        # the +09:00 folder's 10:00 is 01:00Z, before the -04:00 folder's 00:07 (04:07Z)
        assert rows == [(1, 0), (0, 0)]

    def test_cross_folder_recency_reads_the_instant(self, tmp_path):
        from quam_state_manager.core.dataset import DatasetStore
        from quam_state_manager.web.routes import (
            _entry_recency_key, _run_age_key, _run_recency)
        a = DatasetStore(_edt_runs(tmp_path / "a")[0].parent.parent).runs[1182]
        kroot = tmp_path / "k"
        # a run id ABOVE the other's, so an id tie-break cannot pass for time
        krun = make_run(kroot, 2000, "zz_off", "2026-04-03T10:00:00+09:00")
        k = DatasetStore(kroot).runs[2000]
        # folder clocks say k (10:00) is newer; the instants say a (04:07Z) is
        assert max([a, k], key=_run_recency) is a
        assert sorted([a, k], key=lambda r: _run_age_key(
            {"instant_us": r.instant_us, "run_id": r.run_id}, 0))[0] is k
        ea = _entries([a.folder_path])[0]
        ek = _entries([krun])[0]
        assert max([ea, ek], key=_entry_recency_key) is ea


class TestThePollReadsTheInstant:

    def test_the_latest_run_and_the_count_go_by_instant(self, tmp_path):
        """Two folders: the -04:00 archive's newest run is 05:03:26Z; the
        +09:00 folder's run says 13:00:00 on the same date (04:00Z). By
        folder digits the +09:00 run is the latest; by instant it is not."""
        from quam_state_manager.web.app import create_app
        a, k = tmp_path / "a", tmp_path / "k"
        _edt_runs(a)
        make_run(k, 5, "zz_off", "2026-04-03T13:00:00+09:00")
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        for f in (a, k):
            c.post("/workspace/add", data={"folder": str(f)})
        ms = lambda *t: int(datetime(*t, tzinfo=timezone.utc).timestamp() * 1000)
        d = c.get("/datasets/poll").get_json()
        assert d["run_id"] == 1194 and d["t"] == ms(2026, 4, 3, 5, 3, 26), d
        d2 = c.get(f"/datasets/poll?since_t={ms(2026, 4, 3, 4, 7, 48)}").get_json()
        assert d2["new_count"] == 1, d2       # only #1194; the +09:00 run is older
        d3 = c.get(f"/datasets/poll?since_t={ms(2026, 4, 3, 3, 0, 0)}").get_json()
        assert d3["new_count"] == 4, d3       # #1181 (03:53Z), the +09:00 run, #1182, #1194


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_the_clients_read_the_instant():
    """The Datasets When column + its sort, and the Trends axis, in jsdom with
    the viewer in Asia/Seoul (tests/run_instant_client_selfcheck.cjs)."""
    import subprocess
    root = Path(__file__).resolve().parent.parent
    r = subprocess.run(["node", str(root / "tests" / "run_instant_client_selfcheck.cjs")],
                       capture_output=True, text=True, encoding="utf-8", cwd=str(root),
                       timeout=180, env={**os.environ, "TZ": "Asia/Seoul"})
    if r.returncode == 2:
        pytest.skip("jsdom not installed")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "all passed" in r.stdout, r.stdout + r.stderr
