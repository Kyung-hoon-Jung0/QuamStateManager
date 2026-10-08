"""RAM P7 -- the workspace scanner's new-run path.

A new run bumps its date dir, and the incremental rescan used to re-walk and
re-parse EVERY run of that day (649 node.json reads on the lab-I archive's
busiest day, for one new run). It now reuses a run the walk would reach when
nothing its parse read can have moved (run dir mtime, node.json
(mtime_ns, size), quam_state dir mtime). These pins hold the result of every
rescan equal to a from-scratch scan of the same disk, over a randomized
sequence of the events a live archive sees, and pin each reuse condition on
its own (break one and the matching test goes red).
"""
from __future__ import annotations

import json
import os
import random
import shutil
import time
from pathlib import Path

import pytest

from quam_state_manager.core import scanner
from quam_state_manager.core.scanner import Workspace

_TICK = 0.03     # NTFS timestamps come from a ~15.6 ms clock: space the events


def _node(rid: int, date: str, name: str, status: str, qubit: str) -> dict:
    return {"id": rid, "created_at": f"{date}T12:00:{rid % 60:02d}+00:00",
            "metadata": {"name": name, "status": status},
            "data": {"parameters": {"model": {"qubits": [qubit]}}}}


def _mk_run(root: Path, date: str, rid: int, *, name: str = "04_power_rabi",
            status: str = "finished", qubit: str = "q1",
            node: bool = True) -> Path:
    run = root / date / f"#{rid}_{name}_1200{rid % 60:02d}"
    qs = run / "quam_state"
    qs.mkdir(parents=True)
    (qs / "state.json").write_text(json.dumps({"qubits": {qubit: {}}}), encoding="utf-8")
    (qs / "wiring.json").write_text("{}", encoding="utf-8")
    if node:
        (run / "node.json").write_text(json.dumps(_node(rid, date, name, status, qubit)),
                                       encoding="utf-8")
    return run


def _view(entries) -> list[tuple]:
    """Everything a rescan's entry carries that a user can see."""
    return sorted((str(e.quam_state_path), e.run_id, e.experiment_name, e.status,
                   tuple(e.qubits), e.date_str, e.is_standalone, e.timestamp,
                   json.dumps(e.filter_params, sort_keys=True))
                  for e in entries)


def _fresh_view(root: Path) -> list[tuple]:
    ws = Workspace()
    ws.add_root(root)
    return _view(ws.all_entries)


def _rescan(ws: Workspace) -> None:
    ws.rescan_if_stale()


class TestRandomizedEquivalence:
    """Every rescan after a structural event equals a cold scan."""

    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_random_event_sequence(self, tmp_path, seed):
        rng = random.Random(seed)
        root = tmp_path / "data"
        dates = ["2026-03-01", "2026-03-02"]
        rid = 0
        runs: list[Path] = []
        for d in dates:
            for _ in range(6):
                rid += 1
                runs.append(_mk_run(root, d, rid, qubit=f"q{rid % 3}"))
        ws = Workspace()
        ws.add_root(root)
        assert _view(ws.all_entries) == _fresh_view(root)

        half_written: list[Path] = []
        for step in range(40):
            time.sleep(_TICK)
            ev = rng.choice(["new", "new", "newday", "half", "finish", "rewrite",
                             "delete", "drop_state"])
            if ev == "new":
                rid += 1
                runs.append(_mk_run(root, rng.choice(dates), rid))
            elif ev == "newday":
                d = f"2026-03-{len(dates) + 1:02d}"
                dates.append(d)
                rid += 1
                runs.append(_mk_run(root, d, rid))
            elif ev == "half":
                rid += 1
                r = _mk_run(root, dates[-1], rid, node=False)
                half_written.append(r)
                runs.append(r)
            elif ev == "finish" and half_written:
                r = half_written.pop(0)
                n = int(r.name[1:].split("_")[0])
                (r / "node.json").write_text(json.dumps(_node(
                    n, r.parent.name, "04_power_rabi", "finished", "q2")), encoding="utf-8")
            elif ev == "rewrite" and runs:
                # a node.json rewritten IN PLACE (no dir mtime moves), then a
                # new run in the SAME day triggers the rescan that must see it
                r = rng.choice(runs)
                if (r / "node.json").exists():
                    n = int(r.name[1:].split("_")[0])
                    (r / "node.json").write_text(json.dumps(_node(
                        n, r.parent.name, "04_power_rabi",
                        "failed-" + "x" * rng.randint(1, 9), "q7")), encoding="utf-8")
                    time.sleep(_TICK)
                    rid += 1
                    runs.append(_mk_run(root, r.parent.name, rid))
            elif ev == "delete" and runs:
                r = runs.pop(rng.randrange(len(runs)))
                if r in half_written:
                    half_written.remove(r)
                shutil.rmtree(r)
            elif ev == "drop_state" and runs:
                # quam_state loses state.json: the run leaves the tree; a new
                # run in the same day triggers the rescan
                r = rng.choice(runs)
                sp = r / "quam_state" / "state.json"
                if sp.exists():
                    sp.unlink()
                    time.sleep(_TICK)
                    rid += 1
                    runs.append(_mk_run(root, r.parent.name, rid))
            _rescan(ws)
            assert _view(ws.all_entries) == _fresh_view(root), f"step {step} ({ev})"


class TestReuseConditions:
    """Each reuse condition, alone: an event that changes only what that
    condition watches must still re-parse the run (a mutation that drops the
    condition turns the matching test red)."""

    def _setup(self, tmp_path):
        root = tmp_path / "data"
        a = _mk_run(root, "2026-03-01", 1)
        _mk_run(root, "2026-03-01", 2)
        ws = Workspace()
        ws.add_root(root)
        return root, ws, a

    def _land_new(self, root):
        time.sleep(_TICK)
        _mk_run(root, "2026-03-01", 99)

    def test_node_json_rewritten_in_place_is_reparsed(self, tmp_path):
        root, ws, a = self._setup(tmp_path)
        time.sleep(_TICK)
        (a / "node.json").write_text(json.dumps(_node(1, "2026-03-01", "04_power_rabi",
                                                      "failed-late", "q5")), encoding="utf-8")
        self._land_new(root)
        _rescan(ws)
        e = next(e for e in ws.all_entries if e.run_id == 1)
        assert e.status == "failed-late" and e.qubits == ["q5"]

    def test_same_size_rewrite_is_still_seen_by_mtime(self, tmp_path):
        root, ws, a = self._setup(tmp_path)
        body = (a / "node.json").read_text(encoding="utf-8")
        new = body.replace('"finished"', '"finishee"')
        assert len(new) == len(body)
        time.sleep(_TICK)
        (a / "node.json").write_text(new, encoding="utf-8")
        self._land_new(root)
        _rescan(ws)
        e = next(e for e in ws.all_entries if e.run_id == 1)
        assert e.status == "finishee"

    def test_quam_state_losing_a_file_drops_the_run(self, tmp_path):
        root, ws, a = self._setup(tmp_path)
        time.sleep(_TICK)
        (a / "quam_state" / "wiring.json").unlink()
        self._land_new(root)
        _rescan(ws)
        assert 1 not in {e.run_id for e in ws.all_entries}

    def test_run_dir_mtime_moving_reparses(self, tmp_path):
        root, ws, a = self._setup(tmp_path)
        before = next(e for e in ws.all_entries if e.run_id == 1)
        time.sleep(_TICK)
        (a / "figure.png").write_bytes(b"x")          # moves only the run dir
        self._land_new(root)
        _rescan(ws)
        after = next(e for e in ws.all_entries if e.run_id == 1)
        assert after is not before

    def test_a_rescan_that_finds_nothing_new_keeps_the_version(self, tmp_path):
        """Reused entries rejoin in their old order, so rescan_root's
        identity check sees "unchanged" and the sidebar memo survives."""
        root, ws, a = self._setup(tmp_path)
        for rid in (3, 4, 5):
            _mk_run(root, "2026-03-01", rid)
        for rid in (6, 7):
            _mk_run(root, "2026-03-02", rid)       # an UNCHANGED day after it
        time.sleep(_TICK)
        ws.rescan_root(root.resolve())
        ws.rescan_if_stale()        # the one healing rescan a new spine dir forces
        v = ws.version
        time.sleep(_TICK)
        junk = root / "2026-03-01" / "tmp.txt"      # moves the date dir only
        junk.write_text("x", encoding="utf-8")
        junk.unlink()
        assert ws.rescan_if_stale() is True
        assert ws.version == v

    def test_a_rewrite_racing_the_parse_is_seen_next_time(self, tmp_path, monkeypatch):
        """The signature is taken BEFORE node.json is read: a rewrite landing
        between the read and a later stat must not be mistaken for what was
        parsed."""
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 1)
        ws = Workspace()
        ws.add_root(root)
        time.sleep(_TICK)
        r = _mk_run(root, "2026-03-01", 2)
        real = scanner.safe_io.scan_json
        fired: list[int] = []

        def racing(path, *a, **k):
            out = real(path, *a, **k)
            if Path(path).parent == r and not fired:
                fired.append(1)
                time.sleep(_TICK)
                Path(path).write_text(json.dumps(_node(2, "2026-03-01", "04_power_rabi",
                                                       "rewritten-meanwhile", "q9")),
                                      encoding="utf-8")
            return out
        monkeypatch.setattr(scanner.safe_io, "scan_json", racing)
        _rescan(ws)
        assert fired
        monkeypatch.setattr(scanner.safe_io, "scan_json", real)
        self._land_new(root)
        _rescan(ws)
        e = next(e for e in ws.all_entries if e.run_id == 2)
        assert e.status == "rewritten-meanwhile"

    def test_untouched_runs_of_the_day_are_reused_not_reparsed(self, tmp_path, monkeypatch):
        root, ws, a = self._setup(tmp_path)
        parsed: list[str] = []
        real = scanner._parse_experiment_folder
        monkeypatch.setattr(scanner, "_parse_experiment_folder",
                            lambda qs: parsed.append(qs.parent.name) or real(qs))
        self._land_new(root)
        _rescan(ws)
        assert parsed == ["#99_04_power_rabi_120039"]
        assert {e.run_id for e in ws.all_entries} == {1, 2, 99}


class TestHalfWrittenRun:
    def test_node_json_landing_later_makes_the_root_stale(self, tmp_path):
        """The run folder is not on the spine; before RAM P7 a run caught
        without its node.json stayed standalone until an unrelated run
        landed."""
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 1)
        ws = Workspace()
        ws.add_root(root)
        time.sleep(_TICK)
        r = _mk_run(root, "2026-03-01", 2, node=False)
        _rescan(ws)
        e = next(e for e in ws.all_entries if e.folder_path == r)
        assert e.is_standalone
        time.sleep(_TICK)
        (r / "node.json").write_text(json.dumps(_node(2, "2026-03-01", "04_power_rabi",
                                                      "finished", "q1")), encoding="utf-8")
        assert ws.rescan_if_stale() is True
        e = next(e for e in ws.all_entries if e.folder_path == r)
        assert not e.is_standalone and e.run_id == 2

    def test_a_standalone_run_no_longer_forces_a_full_rescan(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 1)
        ws = Workspace()
        ws.add_root(root)
        time.sleep(_TICK)
        _mk_run(root, "2026-03-01", 2, node=False)
        _rescan(ws)
        calls: list[Path] = []
        real = scanner._scan_root
        monkeypatch.setattr(scanner, "_scan_root", lambda p: calls.append(p) or real(p))
        time.sleep(_TICK)
        _mk_run(root, "2026-03-01", 3)
        _rescan(ws)
        assert calls == []
        assert {e.run_id for e in ws.all_entries if not e.is_standalone} == {1, 3}

    def test_a_standalone_root_still_rescans_in_full(self, tmp_path, monkeypatch):
        qs = tmp_path / "chip"
        qs.mkdir()
        (qs / "state.json").write_text("{}", encoding="utf-8")
        (qs / "wiring.json").write_text("{}", encoding="utf-8")
        ws = Workspace()
        ws.add_root(qs)
        calls: list[Path] = []
        real = scanner._scan_root
        monkeypatch.setattr(scanner, "_scan_root", lambda p: calls.append(p) or real(p))
        ws.rescan_root(qs)
        assert calls, "a standalone ROOT must keep the full rescan"


class TestListingSaveOffThread:
    def test_rescan_returns_before_the_listing_is_written(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 1)
        ws = Workspace()
        ws.cache_dir = tmp_path / "cache"
        ws.add_root(root)
        ws.save_debounce_s = 0.3
        writes: list[float] = []
        real = scanner.Workspace._save_listing_cache
        monkeypatch.setattr(scanner.Workspace, "_save_listing_cache",
                            lambda self, k: writes.append(time.monotonic()) or real(self, k))
        time.sleep(_TICK)
        _mk_run(root, "2026-03-01", 2)
        t0 = time.monotonic()
        ws.rescan_if_stale()
        assert writes == [] or writes[0] >= t0 + 0.25, "the save ran on the rescan's thread"
        assert ws.flush_listing_saves(10.0)
        assert len(writes) == 1
        cached = ws._load_listing_cache(root.resolve())
        assert cached is not None and {e.run_id for e in cached[0]} == {1, 2}

    def test_a_burst_of_rescans_writes_once(self, tmp_path, monkeypatch):
        root = tmp_path / "data"
        _mk_run(root, "2026-03-01", 1)
        ws = Workspace()
        ws.cache_dir = tmp_path / "cache"
        ws.add_root(root)
        ws.save_debounce_s = 0.4
        writes: list[str] = []
        real = scanner.Workspace._save_listing_cache
        monkeypatch.setattr(scanner.Workspace, "_save_listing_cache",
                            lambda self, k: writes.append(k) or real(self, k))
        for rid in (2, 3, 4):
            time.sleep(_TICK)
            _mk_run(root, "2026-03-01", rid)
            ws.rescan_if_stale()
        assert ws.flush_listing_saves(10.0)
        assert len(writes) == 1
