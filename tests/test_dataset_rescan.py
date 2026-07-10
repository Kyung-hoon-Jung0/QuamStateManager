"""Incremental-rescan scoping for :class:`DatasetStore` (audit finding B27).

A steady-state ``rescan_if_stale`` must not re-walk (``iterdir`` + 3 stats per
run) date dirs whose mtime is unchanged — only date dirs that actually moved.
A run can only be added to / removed from a date dir by bumping that date dir's
own mtime, so the unchanged ones are provably stable and served from cache.

These tests assert BOTH halves:
  * correctness — a new run in a new date dir is found; touched dirs re-scan;
    untouched dirs keep serving their runs.
  * scoping — touching one date dir does not re-stat run folders living under
    other, untouched date dirs.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core.dataset import DatasetStore


def _seed_run(root: Path, run_id: int, *, date: str, hhmmss: str = "010000",
              name: str = "test_experiment", t1: float = 8.0e-6) -> Path:
    """Create ``root/<date>/#<run_id>_<name>_<hhmmss>`` with node + data json."""
    date_dir = root / date
    date_dir.mkdir(parents=True, exist_ok=True)
    run = date_dir / f"#{run_id}_{name}_{hhmmss}"
    run.mkdir()
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful",
                     "run_start": f"{date}T01:00:00", "run_end": f"{date}T01:00:01"},
        "data": {"parameters": {"model": {"qubits": [f"q{run_id}"]}}, "outcomes": {}},
        "id": run_id, "parents": [], "created_at": f"{date}T01:00:00",
    }), encoding="utf-8")
    (run / "data.json").write_text(json.dumps({
        "fit_results": {f"q{run_id}": {"T1": t1}},
    }), encoding="utf-8")
    return run


def _bump_mtime(path: Path, when: float) -> None:
    """Force a deterministic mtime (avoids coarse-FS same-second collisions)."""
    os.utime(path, (when, when))


def test_new_run_in_new_date_dir_is_found(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 1, date="2026-05-01")
    store = DatasetStore(root)
    assert set(store.runs.keys()) == {1}

    # New run lands in a brand-new date dir; the root + new dir mtimes move,
    # so rescan_if_stale must detect it.
    _seed_run(root, 2, date="2026-05-02")
    _bump_mtime(root, store._last_mtime + 10)
    _bump_mtime(root / "2026-05-02", store._last_mtime + 10)

    found_new = store.rescan_if_stale()
    assert set(store.runs.keys()) == {1, 2}
    assert found_new is True


def test_new_run_in_existing_date_dir_is_found(tmp_path):
    root = tmp_path / "data"
    _seed_run(root, 1, date="2026-05-01", hhmmss="010000")
    store = DatasetStore(root)
    assert set(store.runs.keys()) == {1}

    # Second run added INSIDE the same date dir — adding a child bumps the
    # date dir's mtime, so the unchanged-dir short-circuit must NOT hide it.
    _seed_run(root, 2, date="2026-05-01", hhmmss="020000")
    date_dir = root / "2026-05-01"
    _bump_mtime(date_dir, store._last_mtime + 10)
    _bump_mtime(root, store._last_mtime + 10)

    store.rescan_if_stale()
    assert set(store.runs.keys()) == {1, 2}


def test_vanished_run_in_touched_date_dir_is_dropped(tmp_path):
    import shutil

    root = tmp_path / "data"
    _seed_run(root, 1, date="2026-05-01", hhmmss="010000")
    r2 = _seed_run(root, 2, date="2026-05-01", hhmmss="020000")
    store = DatasetStore(root)
    assert set(store.runs.keys()) == {1, 2}

    shutil.rmtree(r2)  # deleting a child bumps the date dir mtime
    date_dir = root / "2026-05-01"
    _bump_mtime(date_dir, store._last_mtime + 10)
    _bump_mtime(root, store._last_mtime + 10)

    store.rescan_if_stale()
    assert set(store.runs.keys()) == {1}


def test_untouched_date_dirs_are_not_rewalked(tmp_path, monkeypatch):
    """Scoping guarantee: bumping ONE date dir must not re-stat runs under
    OTHER, untouched date dirs."""
    root = tmp_path / "data"
    # Two runs each in three separate date dirs.
    for d in ("2026-05-01", "2026-05-02", "2026-05-03"):
        _seed_run(root, int(d[-2:]) * 10 + 1, date=d, hhmmss="010000")
        _seed_run(root, int(d[-2:]) * 10 + 2, date=d, hhmmss="020000")
    store = DatasetStore(root)
    assert len(store.runs) == 6

    # Count per-run stat calls (folder/node/data) by run-folder path. The
    # short-circuited date dirs never call _stat_mtime on their child runs.
    real_stat = DatasetStore._stat_mtime
    stat_counts: dict[Path, int] = {}

    def _counting_stat(p):
        # Only count stats on run folders + their node/data files, keyed by
        # the run folder (the parent of node.json/data.json, or self).
        run_dir = p.parent if p.name in ("node.json", "data.json") else p
        if run_dir.name.startswith("#"):
            stat_counts[run_dir] = stat_counts.get(run_dir, 0) + 1
        return real_stat(p)

    monkeypatch.setattr(DatasetStore, "_stat_mtime", staticmethod(_counting_stat))

    # Touch only one new run inside ONE date dir.
    _seed_run(root, 99, date="2026-05-03", hhmmss="030000")
    touched = root / "2026-05-03"
    _bump_mtime(touched, store._last_mtime + 10)
    _bump_mtime(root, store._last_mtime + 10)

    store.rescan_if_stale()
    assert 99 in store.runs

    # Runs under the two untouched date dirs were served from cache: zero
    # stats. The touched dir's existing + new runs were walked: >0 stats.
    for d in ("2026-05-01", "2026-05-02"):
        for run_dir in (root / d).iterdir():
            assert stat_counts.get(run_dir, 0) == 0, f"untouched run re-stated: {run_dir}"
    touched_walked = sum(
        stat_counts.get(run_dir, 0) for run_dir in touched.iterdir()
    )
    assert touched_walked > 0


def test_unchanged_workspace_serves_all_from_cache(tmp_path, monkeypatch):
    """A poll with no FS change must not re-stat any run folder at all."""
    root = tmp_path / "data"
    _seed_run(root, 1, date="2026-05-01")
    _seed_run(root, 2, date="2026-05-02")
    store = DatasetStore(root)

    real_stat = DatasetStore._stat_mtime
    run_stats: list[Path] = []

    def _counting_stat(p):
        run_dir = p.parent if p.name in ("node.json", "data.json") else p
        if run_dir.name.startswith("#"):
            run_stats.append(run_dir)
        return real_stat(p)

    monkeypatch.setattr(DatasetStore, "_stat_mtime", staticmethod(_counting_stat))

    # No mtime change at all → rescan_if_stale returns early (gate fails)
    # OR, if forced, the date-dir short-circuit skips all run stats.
    assert store.rescan_if_stale() is False
    store._scan()  # force a full scan to exercise the short-circuit path
    assert run_stats == []  # nothing re-stated; all date dirs unchanged
    assert set(store.runs.keys()) == {1, 2}
