"""docs/171 -- the persisted run table (the docs/142 A' shape for DatasetStore).

Every SM start used to re-read and re-parse every run's node.json + data.json
before the Datasets panel was complete: ~11 file operations per run, 29,000
on a 2,655-run archive, ~52 s at a share's 1.8 ms per operation. With a
cache_dir the previous session's runs + fingerprints are loaded first and the
ORDINARY incremental scan verifies them -- one stat per unchanged date dir.

What is pinned:
  * a second session opens from the cache with NO run parsed, byte-equal rows
  * the scan is the verification: a run added, deleted or rewritten between
    sessions is picked up, and only IT is parsed
  * a mid-write run is never persisted, so it re-parses next session
  * a version / root / shape mismatch is a miss (cold scan, no crash)
  * no cache_dir means no file, ever
  * the write is debounced off the scan thread and never recreates a
    vanished instance dir
  * the route wires the instance dir in, and the cold-start cost of a warm
    session is a handful of operations under the archive
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from quam_state_manager.core import dataset as D
from quam_state_manager.core.dataset import DatasetStore


def _seed_run(root: Path, run_id: int, *, date: str, hhmmss: str = "010000",
              name: str = "test_experiment", t1: float = 8.0e-6,
              description: str = "a long node docstring " * 20) -> Path:
    date_dir = root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    run = date_dir / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir()
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful", "description": description,
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": [f"q{run_id}"], "reset_type": "active"}},
                 "outcomes": {f"q{run_id}": "successful"}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({
        "fit_results": {f"q{run_id}": {"T1": t1, "success": True}},
    }), encoding="utf-8")
    return run


def _archive(root: Path, n: int = 6) -> None:
    for i in range(1, n + 1):
        _seed_run(root, i, date=f"2026-05-{(i % 3) + 1:02d}", hhmmss=f"{i:02d}0000")


def _rows(store: DatasetStore) -> list[dict]:
    return sorted(store.list_runs_compact(), key=lambda r: r["id"])


def _cache_files(cache: Path) -> list[Path]:
    return sorted(cache.glob("ds_*.json"))


class TestSecondSessionOpensFromTheCache:
    def test_no_run_is_parsed_and_the_rows_are_equal(self, tmp_path, monkeypatch):
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        _archive(root)
        s1 = DatasetStore(root, cache_dir=cache)
        assert s1.flush_store_cache() is True
        assert len(_cache_files(cache)) == 1
        rows1 = _rows(s1)
        parsed: list[Path] = []
        real = DatasetStore._parse_run_folder

        def spy(self, run_entry, *a, **k):
            parsed.append(run_entry)
            return real(self, run_entry, *a, **k)
        monkeypatch.setattr(DatasetStore, "_parse_run_folder", spy)
        s2 = DatasetStore(root, cache_dir=cache)
        assert s2.cache_hit_runs == 6
        assert parsed == [], f"a warm session parsed {len(parsed)} runs"
        assert _rows(s2) == rows1
        assert s2.scan_truncated is False
        # what the detail view reads survived the round trip too
        r1, r2 = s1.runs[3], s2.runs[3]
        assert r2.description == r1.description and r2.parameters == r1.parameters
        assert r2.fit_results == r1.fit_results and r2.outcomes == r1.outcomes
        assert r2.content_fp == r1.content_fp and r2.last_parsed == r1.last_parsed
        assert r2.sort_scalars == r1.sort_scalars and r2.filter_params == r1.filter_params

    def test_the_descriptions_are_interned(self, tmp_path):
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        _archive(root)
        s = DatasetStore(root, cache_dir=cache)
        s.flush_store_cache()
        raw = json.loads(_cache_files(cache)[0].read_text(encoding="utf-8"))
        assert len(raw["descriptions"]) == 1          # one docstring, six runs
        assert all(isinstance(r["description"], int) for r in raw["runs"])
        assert raw["v"] == D._STORE_CACHE_V and raw["root"] == str(root)


class TestTheScanIsTheVerification:
    def _warm(self, tmp_path):
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        _archive(root)
        s1 = DatasetStore(root, cache_dir=cache)
        s1.flush_store_cache()
        return root, cache

    def _parses(self, monkeypatch):
        parsed: list[Path] = []
        real = DatasetStore._parse_run_folder

        def spy(self, run_entry, *a, **k):
            parsed.append(run_entry)
            return real(self, run_entry, *a, **k)
        monkeypatch.setattr(DatasetStore, "_parse_run_folder", spy)
        return parsed

    def test_a_run_added_between_sessions_is_the_only_one_parsed(self, tmp_path, monkeypatch):
        root, cache = self._warm(tmp_path)
        time.sleep(0.02)
        new = _seed_run(root, 7, date="2026-05-02", hhmmss="070000")
        os.utime(root / "2026-05-02", None)
        parsed = self._parses(monkeypatch)
        s2 = DatasetStore(root, cache_dir=cache)
        assert set(s2.runs) == set(range(1, 8))
        assert parsed == [new]

    def test_a_run_deleted_between_sessions_is_dropped(self, tmp_path, monkeypatch):
        root, cache = self._warm(tmp_path)
        gone = next((root / "2026-05-03").iterdir())
        shutil.rmtree(gone)
        os.utime(root / "2026-05-03", None)
        parsed = self._parses(monkeypatch)
        s2 = DatasetStore(root, cache_dir=cache)
        assert len(s2.runs) == 5 and parsed == []
        assert all(r.folder_path != gone for r in s2.runs.values())

    def test_a_run_rewritten_between_sessions_is_re_read(self, tmp_path, monkeypatch):
        root, cache = self._warm(tmp_path)
        run = next(r for r in (root / "2026-05-01").iterdir() if r.name.startswith("#3_"))
        time.sleep(0.02)
        (run / "data.json").write_text(json.dumps({"fit_results": {"q3": {"T1": 1.5e-6}}}),
                                       encoding="utf-8")
        os.utime(root / "2026-05-01", None)      # the date dir moved (a sibling landed)
        parsed = self._parses(monkeypatch)
        s2 = DatasetStore(root, cache_dir=cache)
        assert s2.runs[3].fit_results["q3"]["T1"] == pytest.approx(1.5e-6)
        assert parsed == [run]

    def test_an_untouched_archive_costs_one_stat_per_date_dir(self, tmp_path):
        root, cache = self._warm(tmp_path)
        under: list[str] = []
        real_stat = os.stat

        def spy(path, *a, **k):
            try:
                sp = os.fsdecode(path) if not isinstance(path, str) else path
                if sp.startswith(str(root)):
                    under.append(sp)
            except Exception:
                pass
            return real_stat(path, *a, **k)
        import unittest.mock as um
        with um.patch("os.stat", spy):
            s2 = DatasetStore(root, cache_dir=cache)
        assert s2.cache_hit_runs == 6
        # the root + 3 date dirs, each stat'ed a few times (the pre-walk
        # sample, is_dir, the mtime) -- and NOTHING per run: a cold build
        # stats each run folder four times and opens two files in it
        assert not [p for p in under if "#" in p], "a run folder was touched"
        assert len(under) <= 4 * (1 + 3) + 2, under


class TestWhatIsNeverPersisted:
    def test_a_mid_write_run_re_parses_next_session(self, tmp_path, monkeypatch):
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        _archive(root)
        half = _seed_run(root, 9, date="2026-05-01", hhmmss="090000")
        (half / "node.json").write_text("{\"metadata\": {", encoding="utf-8")
        s1 = DatasetStore(root, cache_dir=cache)
        assert s1.runs[9].incomplete is True
        s1.flush_store_cache()
        raw = json.loads(_cache_files(cache)[0].read_text(encoding="utf-8"))
        assert all(r["run_id"] != 9 for r in raw["runs"])
        assert str(half) not in raw["folder_fp"]
        # the writer finished while SM was down -- SAME date-dir mtime or not,
        # the run is re-read because it was never trusted
        (half / "node.json").write_text(json.dumps({
            "metadata": {"name": "test_experiment", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q9"]}}, "outcomes": {}},
        }), encoding="utf-8")
        parsed: list[Path] = []
        real = DatasetStore._parse_run_folder

        def spy(self, run_entry, *a, **k):
            parsed.append(run_entry)
            return real(self, run_entry, *a, **k)
        monkeypatch.setattr(DatasetStore, "_parse_run_folder", spy)
        s2 = DatasetStore(root, cache_dir=cache)
        assert s2.runs[9].status == "successful" and s2.runs[9].incomplete is False
        assert half in parsed

    def test_a_truncated_walk_is_not_saved(self, tmp_path, monkeypatch):
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        _archive(root, 12)
        monkeypatch.setattr(D, "_COLD_SCAN_BUDGET_S", 0.0)
        s = DatasetStore(root, cache_dir=cache)
        assert s.scan_truncated is True
        assert s.flush_store_cache() is False and not _cache_files(cache)
        s.rescan_if_stale()                       # the continuation completes
        assert s.flush_store_cache() is True
        assert len(json.loads(_cache_files(cache)[0].read_text(encoding="utf-8"))["runs"]) == 12


class TestAMissIsAMiss:
    def test_no_cache_dir_means_no_file(self, tmp_path):
        root = tmp_path / "data"
        _archive(root)
        s = DatasetStore(root)
        assert s.flush_store_cache() is False
        assert not list(tmp_path.rglob("ds_*.json"))

    @pytest.mark.parametrize("damage", ["garbage", "version", "root", "shape"])
    def test_a_damaged_cache_reads_as_a_cold_scan(self, tmp_path, damage):
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        _archive(root)
        s1 = DatasetStore(root, cache_dir=cache)
        s1.flush_store_cache()
        f = _cache_files(cache)[0]
        if damage == "garbage":
            f.write_text("{not json", encoding="utf-8")
        else:
            raw = json.loads(f.read_text(encoding="utf-8"))
            if damage == "version":
                raw["v"] = D._STORE_CACHE_V + 1
            elif damage == "root":
                raw["root"] = str(root) + "_other"
            else:
                raw["runs"][0] = {"run_id": "not-an-int"}
            f.write_text(json.dumps(raw), encoding="utf-8")
        s2 = DatasetStore(root, cache_dir=cache)
        assert s2.cache_hit_runs == 0
        assert _rows(s2) == _rows(s1)

    def test_the_write_never_recreates_a_vanished_instance_dir(self, tmp_path):
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        _archive(root)
        s = DatasetStore(root, cache_dir=cache)
        shutil.rmtree(cache.parent)
        assert s.flush_store_cache() is False
        assert not cache.parent.exists()


class TestTheWiring:
    def test_the_route_builds_stores_with_the_instance_cache_dir(self, tmp_path):
        from quam_state_manager.web import routes
        from quam_state_manager.web.app import create_app
        root = tmp_path / "data"
        _archive(root)
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        assert c.post("/workspace/add", data={"folder": str(root)}).status_code in (200, 204)
        with app.app_context():
            store = routes._get_or_create_store(root.resolve())
            assert store is not None
            assert store.cache_dir == Path(app.instance_path) / "workspace_cache"
            assert store.flush_store_cache() is True
        assert _cache_files(Path(app.instance_path) / "workspace_cache")

    def test_the_debounce_writes_once_for_a_burst(self, tmp_path, monkeypatch):
        root, cache = tmp_path / "data", tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir()
        _archive(root)
        monkeypatch.setattr(D, "_STORE_CACHE_DEBOUNCE_S", 0.15)
        writes: list[Path] = []
        real = D.safe_io.atomic_write_json

        def spy(path, data, **k):
            writes.append(Path(path))
            return real(path, data, **k)
        monkeypatch.setattr(D.safe_io, "atomic_write_json", spy)
        s = DatasetStore(root, cache_dir=cache)          # the first complete scan arms it
        for i in range(7, 10):                          # three runs land in a burst
            _seed_run(root, i, date="2026-05-02", hhmmss=f"{i:02d}0000")
            os.utime(root / "2026-05-02", (time.time() + i, time.time() + i))
            s.rescan_if_stale()
        deadline = time.time() + 3.0
        while time.time() < deadline and not writes:
            time.sleep(0.02)
        time.sleep(0.4)
        assert len(writes) == 1, writes
        assert len(json.loads(_cache_files(cache)[0].read_text(encoding="utf-8"))["runs"]) == 9
