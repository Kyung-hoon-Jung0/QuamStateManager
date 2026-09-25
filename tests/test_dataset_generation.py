"""DatasetStore RAM-cache tokens (design ram_design.md §1.2, P0).

``generation`` / ``exp_gen[experiment]`` must move at EVERY ``self.runs``
insert, replace and pop, and ``meta_generation`` at every tag / note /
bookmark write (including a write's rollback). A RAM cache keyed on them is
correct only if no mutation slips past its counter, so every mutation SITE is
pinned by its own test below: removing any one bump turns one of them red
(the mutation sweep is recorded in the P3 report).
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

from quam_state_manager.core import dataset as ds
from quam_state_manager.core.dataset import DatasetStore


def _run(root: Path, rid: int, name: str = "05_rabi", *, date: str = "2026-09-01",
         hhmmss: str = "010000", value: float = 1.0, data: bool = True) -> Path:
    run = root / date / f"#{rid}_{name}_{hhmmss}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "node.json").write_text(json.dumps({
        "metadata": {"name": name, "status": "successful"},
        "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
        "id": rid, "parents": []}), encoding="utf-8")
    if data:
        (run / "data.json").write_text(json.dumps({"fit_results": {"q1": {"amp": value}}}),
                                       encoding="utf-8")
    return run


def _touch_gate(store: DatasetStore) -> None:
    """Open the rescan staleness gate the way a moved date-dir mtime does,
    without depending on the file system's mtime resolution."""
    store._last_mtime = (0.0, -2)


def _fingerprint(store: DatasetStore):
    return {rid: (id(r), r.experiment_name) for rid, r in store.runs.items()}


def _meta(store: DatasetStore):
    return {rid: (tuple(r.tags), r.bookmarked, r.note) for rid, r in store.runs.items()}


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "data"
    _run(r, 1, "05_rabi")
    _run(r, 2, "07_ramsey", hhmmss="020000")
    return r


class TestRunsGeneration:
    def test_cold_build_inserts_bump(self, root):
        store = DatasetStore(root)
        assert store.generation >= 2
        assert store.exp_gen["05_rabi"] > 0 and store.exp_gen["07_ramsey"] > 0

    def test_insert(self, root):
        store = DatasetStore(root)
        g, e_rabi, e_ram = store.generation, store.exp_gen["05_rabi"], store.exp_gen["07_ramsey"]
        _run(root, 3, "05_rabi", hhmmss="030000")
        _touch_gate(store)
        store.rescan_if_stale()
        assert 3 in store.runs
        assert store.generation > g
        assert store.exp_gen["05_rabi"] > e_rabi
        assert store.exp_gen["07_ramsey"] == e_ram, "an untouched experiment moved"

    def test_replace(self, root):
        store = DatasetStore(root)
        before = store.runs[1]
        g = store.exp_gen["05_rabi"]
        run = next((root / "2026-09-01").glob("#1_*"))
        (run / "data.json").write_text(json.dumps({"fit_results": {"q1": {"amp": 9.0}}}),
                                       encoding="utf-8")
        store.force_rescan()          # the Rescan button: re-reads every run
        assert store.runs[1] is not before
        assert store.runs[1].fit_results["q1"]["amp"] == 9.0
        assert store.exp_gen["05_rabi"] > g

    def test_pop(self, root):
        store = DatasetStore(root)
        g = store.exp_gen["07_ramsey"]
        shutil.rmtree(next((root / "2026-09-01").glob("#2_*")))
        _touch_gate(store)
        store.rescan_if_stale()
        assert 2 not in store.runs
        assert store.exp_gen["07_ramsey"] > g

    def test_root_gone_clears_and_bumps(self, root):
        store = DatasetStore(root)
        g = store.generation
        shutil.rmtree(root)
        _touch_gate(store)
        store._scan()
        assert store.runs == {}
        assert store.generation > g
        assert store.exp_gen["05_rabi"] > 0

    def test_a_store_loaded_from_the_persisted_cache_has_live_generations(self, root, tmp_path):
        """docs/171: runs arrive by ``self.runs = runs`` and the verifying
        scan re-serves them without a single insert -- that load is the only
        mutation and must carry the bump (else every experiment reads as
        generation 0, the value of a store that never had it)."""
        cache = tmp_path / "inst" / "workspace_cache"
        cache.parent.mkdir(parents=True)
        first = DatasetStore(root, cache_dir=cache)
        assert first.flush_store_cache()
        second = DatasetStore(root, cache_dir=cache)
        assert second.cache_hit_runs == 2
        assert second.generation > 0
        assert second.exp_gen.get("05_rabi", 0) > 0 and second.exp_gen.get("07_ramsey", 0) > 0
        # and the event log cannot describe a bulk load: an incremental reader
        # from before it must rebuild
        _g, touched, runs = second.experiment_snapshot("05_rabi", since_gen=0, append_above=-1)
        assert touched is None and [r.run_id for r in runs] == [1]

    def test_every_mutation_is_followed_by_a_generation_change(self, root):
        """The sweep-proof form: across a sequence of real disk events, any
        change in WHICH RunInfo objects the store holds moved generation."""
        store = DatasetStore(root)
        steps = [
            lambda: _run(root, 10, "05_rabi", hhmmss="100000"),
            lambda: _run(root, 11, "09_t1", date="2026-09-02", hhmmss="110000"),
            lambda: shutil.rmtree(next((root / "2026-09-01").glob("#10_*"))),
            lambda: _run(root, 12, "05_rabi", date="2026-09-02", hhmmss="120000", data=False),
        ]
        for step in steps:
            fp, g = _fingerprint(store), store.generation
            step()
            _touch_gate(store)
            store.rescan_if_stale()
            if _fingerprint(store) != fp:
                assert store.generation != g


class TestEventLog:
    def test_newer_runs_only_is_an_append_delta(self, root):
        store = DatasetStore(root)
        g = store.exp_gen["05_rabi"]
        _run(root, 5, "05_rabi", hhmmss="050000")
        _touch_gate(store)
        store.rescan_if_stale()
        gen, touched, runs = store.experiment_snapshot("05_rabi", since_gen=g, append_above=1)
        assert touched == {5} and [r.run_id for r in runs] == [5] and gen == store.exp_gen["05_rabi"]

    def test_an_older_id_is_a_rebuild(self, root):
        store = DatasetStore(root)
        g = store.exp_gen["05_rabi"]
        _run(root, 0, "05_rabi", hhmmss="000500")
        _touch_gate(store)
        store.rescan_if_stale()
        _gen, touched, runs = store.experiment_snapshot("05_rabi", since_gen=g, append_above=1)
        assert touched is None and sorted(r.run_id for r in runs) == [0, 1]

    def test_a_log_that_no_longer_reaches_back_is_a_rebuild(self, root, monkeypatch):
        monkeypatch.setattr(ds, "_RUN_EVENT_LOG_MAX", 1)
        store = DatasetStore(root)
        g = store.exp_gen["05_rabi"]
        _run(root, 5, "05_rabi", hhmmss="050000")
        _run(root, 6, "07_ramsey", hhmmss="060000")
        _touch_gate(store)
        store.rescan_if_stale()
        _gen, touched, _runs = store.experiment_snapshot("05_rabi", since_gen=g, append_above=1)
        assert touched is None


class TestMetaGeneration:
    @pytest.fixture
    def store(self, root):
        return DatasetStore(root)

    def _changes(self, store, fn):
        m0, meta0 = store.meta_generation, _meta(store)
        fn()
        assert _meta(store) != meta0, "fixture: the write changed nothing"
        return store.meta_generation != m0

    def test_toggle_bookmark(self, store):
        assert self._changes(store, lambda: store.toggle_bookmark(1))

    def test_add_tag(self, store):
        assert self._changes(store, lambda: store.add_tag(1, "good"))

    def test_remove_tag(self, store):
        store.add_tag(1, "good")
        assert self._changes(store, lambda: store.remove_tag(1, "good"))

    def test_set_note(self, store):
        assert self._changes(store, lambda: store.set_note(1, "hello"))

    def test_load_tags_from_another_window(self, store, root):
        (root / "quashboard_tags.json").write_text(json.dumps(
            {"bookmarks": [], "tags": {"2": ["x"]}, "notes": {"1": "from B"}}), encoding="utf-8")
        assert self._changes(store, store._load_tags)

    def test_apply_tags_to_runs(self, store):
        store._tags_data = {"bookmarks": [], "tags": {"1": ["y"]}, "notes": {}}
        assert self._changes(store, store._apply_tags_to_runs)

    @pytest.mark.parametrize("op", ["toggle_bookmark", "add_tag", "remove_tag", "set_note"])
    def test_the_change_is_counted_before_the_save_window(self, store, monkeypatch, op):
        """The file write is the slow part of a tag write, and readers do not
        hold _tags_lock: during it the RunInfo already carries the new tags,
        so the generation must already have moved (else a reader during the
        write caches the new state under the old generation's name, or keeps
        serving the old state as current)."""
        if op == "remove_tag":
            store.add_tag(1, "good")
        before_gen, before_meta = store.meta_generation, _meta(store)
        seen = {}
        real_save = store._save_tags

        def spying_save(touched=None):
            seen["gen"], seen["meta"] = store.meta_generation, _meta(store)
            return real_save(touched)

        monkeypatch.setattr(store, "_save_tags", spying_save)
        args = {"toggle_bookmark": (1,), "add_tag": (1, "new"), "remove_tag": (1, "good"),
                "set_note": (1, "hello")}[op]
        getattr(store, op)(*args)
        assert seen["meta"] != before_meta, "fixture: nothing changed before the save"
        assert seen["gen"] != before_gen

    @pytest.mark.parametrize("op", ["toggle_bookmark", "add_tag", "remove_tag", "set_note"])
    def test_a_failed_write_rolls_back_with_its_own_bump(self, store, monkeypatch, op):
        """A reader between the forward change and the rollback saw the
        forward state at some generation; the rollback changes the RunInfo
        again and must move the generation again."""
        if op == "remove_tag":
            store.add_tag(1, "good")
        seen = {}

        def failing_save(touched=None):
            seen["gen"] = store.meta_generation
            seen["meta"] = _meta(store)
            raise OSError("read-only")

        monkeypatch.setattr(store, "_save_tags", failing_save)
        args = {"toggle_bookmark": (1,), "add_tag": (1, "new"), "remove_tag": (1, "good"),
                "set_note": (1, "hello")}[op]
        with pytest.raises(OSError):
            getattr(store, op)(*args)
        assert _meta(store) != seen["meta"], "fixture: the rollback restored nothing"
        assert store.meta_generation != seen["gen"]

    def test_a_tag_write_does_not_move_the_runs_generation(self, store):
        g = store.generation
        store.add_tag(1, "x")
        store.set_note(2, "n")
        assert store.generation == g
