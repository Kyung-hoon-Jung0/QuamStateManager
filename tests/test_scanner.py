"""Tests for quam_state_manager.core.scanner.

Covers both synthetic fixtures (temp directories with minimal JSON) and real
experiment data folders from the examplechip repository.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.core.scanner import (
    DateGroup,
    ExperimentEntry,
    Workspace,
    _extract_date,
    _group_by_date,
    _is_quam_state_folder,
    _normalize_pair_members,
    _scan_root,
    _with_pair_qubits,
    MAX_CACHED_STORES,
)


class TestPairMemberNormalization:
    """A 2Q pair named compactly as "q0-1" means qubits q0 & q1, sharing the "q"
    prefix. The member fold must re-prefix the bare suffix ("1" -> "q1") so qubit
    search + figure navigation use real names, while the pair string stays intact
    for display."""

    def test_compact_shared_prefix(self):
        assert _normalize_pair_members("q0-1") == ["q0", "q1"]
        assert _normalize_pair_members("q12-13") == ["q12", "q13"]

    def test_fully_qualified_untouched(self):
        assert _normalize_pair_members("qA2-qA1") == ["qA2", "qA1"]
        assert _normalize_pair_members("qC1-qC2") == ["qC1", "qC2"]

    def test_three_token(self):
        assert _normalize_pair_members("q0-1-2") == ["q0", "q1", "q2"]

    def test_empty(self):
        assert _normalize_pair_members("") == []

    def test_with_pair_qubits_normalizes_members_keeps_pair(self):
        # Members are normalized for search; the pair string is returned intact.
        qubits, pairs = _with_pair_qubits([], ["q0-1"])
        assert qubits == ["q0", "q1"]          # NOT ["q0", "1"]
        assert pairs == ["q0-1"]               # display value untouched

    def test_with_pair_qubits_dedups_against_existing(self):
        qubits, pairs = _with_pair_qubits(["q5"], ["q0-1"])
        assert qubits == ["q5", "q0", "q1"]
        assert pairs == ["q0-1"]

# ---------------------------------------------------------------------------
# Paths to real data (skip if not available)
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
DATA_FOLDER = _EXAMPLECHIP_ROOT / "data" / "project_name"
STANDALONE_FOLDER = _EXAMPLECHIP_ROOT / "quam_state"
LARGE_STANDALONE = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"

has_data = DATA_FOLDER.exists()
has_standalone = STANDALONE_FOLDER.exists() and (STANDALONE_FOLDER / "state.json").exists()
has_large = LARGE_STANDALONE.exists() and (LARGE_STANDALONE / "state.json").exists()

skip_no_data = pytest.mark.skipif(not has_data, reason="Experiment data folder not found")
skip_no_standalone = pytest.mark.skipif(not has_standalone, reason="Standalone quam_state not found")
skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state not found")


# ---------------------------------------------------------------------------
# Helpers to build synthetic experiment folders
# ---------------------------------------------------------------------------


def _make_quam_state(path: Path) -> None:
    """Create minimal state.json + wiring.json in *path*."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "state.json").write_text(json.dumps({"qubits": {}, "__class__": "Quam"}), encoding="utf-8")
    (path / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}), encoding="utf-8")


def _make_node_json(folder: Path, *, run_id: int, name: str, timestamp: str,
                    status: str = "finished", qubits: list | None = None,
                    outcomes: dict | None = None, parents: list | None = None) -> None:
    """Create a node.json in *folder* with the given metadata."""
    node = {
        "created_at": timestamp,
        "metadata": {
            "name": name,
            "status": status,
            "run_start": timestamp,
            "run_end": timestamp,
        },
        "data": {
            "parameters": {"model": {"qubits": qubits or []}},
            "outcomes": outcomes or {},
            "quam": "./quam_state",
        },
        "id": run_id,
        "parents": parents or [],
    }
    (folder / "node.json").write_text(json.dumps(node, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Synthetic fixture: multi-date project tree
# ---------------------------------------------------------------------------


@pytest.fixture
def project_tree(tmp_path: Path) -> Path:
    """Build a synthetic project tree:

        tmp_path/
          2026-02-19/
            #3_time_of_flight_134241/
              quam_state/ (state.json + wiring.json)
              node.json
            #10_resonator_spectroscopy_135518/
              quam_state/
              node.json
          2026-02-20/
            #55_qubit_spectroscopy_091200/
              quam_state/
              node.json
    """
    d1 = tmp_path / "2026-02-19"

    exp1 = d1 / "#3_time_of_flight_134241"
    exp1.mkdir(parents=True)
    _make_quam_state(exp1 / "quam_state")
    _make_node_json(exp1, run_id=3, name="01_time_of_flight",
                    timestamp="2026-02-19T13:42:41+09:00",
                    qubits=["qC1", "qC2"], outcomes={"qC1": "success", "qC2": "success"})

    exp2 = d1 / "#10_resonator_spectroscopy_135518"
    exp2.mkdir(parents=True)
    _make_quam_state(exp2 / "quam_state")
    _make_node_json(exp2, run_id=10, name="02c_resonator_spectroscopy_vs_flux",
                    timestamp="2026-02-19T13:55:18+09:00",
                    qubits=["qA1", "qA2", "qA3"], status="finished",
                    outcomes={"qA1": "success", "qA2": "failed", "qA3": "success"},
                    parents=[3])

    d2 = tmp_path / "2026-02-20"
    exp3 = d2 / "#55_qubit_spectroscopy_091200"
    exp3.mkdir(parents=True)
    _make_quam_state(exp3 / "quam_state")
    _make_node_json(exp3, run_id=55, name="08_qubit_spectroscopy",
                    timestamp="2026-02-20T09:12:00+09:00",
                    qubits=["qA4", "qA5"], status="failed",
                    outcomes={"qA4": "failed", "qA5": "failed"})

    return tmp_path


@pytest.fixture
def standalone_dir(tmp_path: Path) -> Path:
    """A standalone quam_state folder with no node.json."""
    qs = tmp_path / "my_quam_state"
    _make_quam_state(qs)
    return qs


# ---------------------------------------------------------------------------
# Unit tests: scanning helpers
# ---------------------------------------------------------------------------


class TestIsQuamStateFolder:
    def test_valid(self, tmp_path):
        _make_quam_state(tmp_path)
        assert _is_quam_state_folder(tmp_path) is True

    def test_missing_state(self, tmp_path):
        (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
        assert _is_quam_state_folder(tmp_path) is False

    def test_missing_wiring(self, tmp_path):
        (tmp_path / "state.json").write_text("{}", encoding="utf-8")
        assert _is_quam_state_folder(tmp_path) is False

    def test_empty_dir(self, tmp_path):
        assert _is_quam_state_folder(tmp_path) is False


class TestExtractDate:
    def test_from_timestamp(self):
        assert _extract_date("2026-02-19T17:13:47+09:00", Path("/x")) == "2026-02-19"

    def test_from_folder_path(self):
        assert _extract_date("", Path("/data/project/2026-02-19/#3_tof")) == "2026-02-19"

    def test_no_date(self):
        assert _extract_date("", Path("/data/nope")) == "unknown"


class TestGroupByDate:
    def test_groups_correctly(self, project_tree):
        entries = _scan_root(project_tree)
        groups = _group_by_date(entries)
        assert len(groups) == 2
        assert groups[0].date_str == "2026-02-19"
        assert groups[0].count == 2
        assert groups[1].date_str == "2026-02-20"
        assert groups[1].count == 1

    def test_sorted_by_date(self, project_tree):
        entries = _scan_root(project_tree)
        groups = _group_by_date(entries)
        dates = [g.date_str for g in groups]
        assert dates == sorted(dates)

    def test_entries_sorted_by_numeric_run_id_not_folder_name(self):
        """The sidebar renders dg.entries directly, so a group's entries must be
        in NUMERIC run_id order — not folder-name string order where '#4_…'
        sorts after '#45_…' (because '_' > the digits)."""
        def _e(run_id):
            return ExperimentEntry(
                folder_path=Path(f"/ws/2026-06-17/#{run_id}_exp"),
                quam_state_path=Path(f"/ws/2026-06-17/#{run_id}_exp/quam_state"),
                run_id=run_id, experiment_name="exp",
                timestamp=f"2026-06-17T00:00:{run_id:02d}", status="", qubits=[],
                qubit_pairs=[], outcomes={}, parent_ids=[], date_str="2026-06-17",
                is_standalone=False)
        # Built in the buggy folder-name string order (#45..#49, #4, #50, #5..).
        raw = [_e(i) for i in (45, 46, 47, 48, 49, 4, 50, 5, 9)]
        groups = _group_by_date(raw)
        assert len(groups) == 1
        assert [e.run_id for e in groups[0].entries] == [4, 5, 9, 45, 46, 47, 48, 49, 50]


# ---------------------------------------------------------------------------
# Unit tests: scanning
# ---------------------------------------------------------------------------


class TestScanRoot:
    def test_finds_all_experiments(self, project_tree):
        entries = _scan_root(project_tree)
        assert len(entries) == 3

    def test_parses_run_id(self, project_tree):
        entries = _scan_root(project_tree)
        ids = sorted([e.run_id for e in entries if e.run_id is not None])
        assert ids == [3, 10, 55]

    def test_parses_experiment_name(self, project_tree):
        entries = _scan_root(project_tree)
        names = sorted([e.experiment_name for e in entries])
        assert "01_time_of_flight" in names
        assert "08_qubit_spectroscopy" in names

    def test_parses_qubits(self, project_tree):
        entries = _scan_root(project_tree)
        tof = [e for e in entries if e.run_id == 3][0]
        assert tof.qubits == ["qC1", "qC2"]

    def test_parses_outcomes(self, project_tree):
        entries = _scan_root(project_tree)
        res = [e for e in entries if e.run_id == 10][0]
        assert res.outcomes["qA2"] == "failed"

    def test_parses_parents(self, project_tree):
        entries = _scan_root(project_tree)
        res = [e for e in entries if e.run_id == 10][0]
        assert res.parent_ids == [3]

    def test_parses_status(self, project_tree):
        entries = _scan_root(project_tree)
        spec = [e for e in entries if e.run_id == 55][0]
        assert spec.status == "failed"

    def test_standalone_folder(self, standalone_dir):
        entries = _scan_root(standalone_dir)
        assert len(entries) == 1
        assert entries[0].is_standalone is True
        assert entries[0].experiment_name == "my_quam_state"

    def test_not_standalone_when_node_json_present(self, project_tree):
        entries = _scan_root(project_tree)
        assert all(not e.is_standalone for e in entries)


# ---------------------------------------------------------------------------
# Workspace: add / remove / rescan
# ---------------------------------------------------------------------------


class TestWorkspace:
    def test_add_root(self, project_tree):
        ws = Workspace()
        entries = ws.add_root(project_tree)
        assert len(entries) == 3
        assert len(ws.root_folders) == 1

    def test_add_root_idempotent(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        ws.add_root(project_tree)
        assert len(ws.root_folders) == 1

    def test_version_bumps_on_tree_change(self, project_tree):
        """The monotonic `version` (read by the sidebar's version-gated refresh)
        increases when the tree changes and stays put on a no-op re-add."""
        ws = Workspace()
        v0 = ws.version
        ws.add_root(project_tree)
        v1 = ws.version
        assert v1 > v0                       # add bumps
        ws.add_root(project_tree)            # idempotent re-add: no change
        assert ws.version == v1
        ws.remove_root(project_tree)
        assert ws.version > v1               # remove bumps

    def test_remove_root(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        ws.remove_root(project_tree)
        assert len(ws.root_folders) == 0
        assert len(ws.all_entries) == 0

    def test_rescan_root(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        entries = ws.rescan_root(project_tree)
        assert len(entries) == 3

    def test_tree_structure(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        key = str(project_tree.resolve())
        assert key in ws.tree
        groups = ws.tree[key]
        assert len(groups) == 2

    def test_repr(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        r = repr(ws)
        assert "roots=1" in r
        assert "entries=3" in r

    def test_all_entries_sorted(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        entries = ws.all_entries
        assert entries[0].run_id == 3
        assert entries[1].run_id == 10
        assert entries[2].run_id == 55

    def test_add_standalone(self, standalone_dir):
        ws = Workspace()
        entries = ws.add_root(standalone_dir)
        assert len(entries) == 1
        assert entries[0].is_standalone


# ---------------------------------------------------------------------------
# Workspace: get_entry
# ---------------------------------------------------------------------------


class TestGetEntry:
    def test_lookup_by_path(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        qs_path = project_tree / "2026-02-19" / "#3_time_of_flight_134241" / "quam_state"
        entry = ws.get_entry(qs_path)
        assert entry is not None
        assert entry.run_id == 3

    def test_lookup_missing(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        assert ws.get_entry(Path("/nonexistent")) is None


# ---------------------------------------------------------------------------
# Workspace: filtering
# ---------------------------------------------------------------------------


class TestFiltering:
    def test_date_filter(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        results = ws.get_flat_list(date_filter="2026-02-19")
        assert len(results) == 2

    def test_date_prefix_filter(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        results = ws.get_flat_list(date_filter="2026-02")
        assert len(results) == 3

    def test_experiment_filter(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        results = ws.get_flat_list(experiment_filter="spectroscopy")
        assert len(results) == 2

    def test_experiment_filter_case_insensitive(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        results = ws.get_flat_list(experiment_filter="SPECTROSCOPY")
        assert len(results) == 2

    def test_qubit_filter(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        results = ws.get_flat_list(qubit_filter="qA1")
        assert len(results) == 1
        assert results[0].run_id == 10

    def test_status_filter(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        results = ws.get_flat_list(status_filter="failed")
        assert len(results) == 1
        assert results[0].run_id == 55

    def test_combined_filters(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        results = ws.get_flat_list(date_filter="2026-02-19", experiment_filter="time_of_flight")
        assert len(results) == 1
        assert results[0].run_id == 3

    def test_no_match(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        results = ws.get_flat_list(qubit_filter="qZZZ")
        assert len(results) == 0

    def test_root_filter(self, project_tree, standalone_dir):
        ws = Workspace()
        ws.add_root(project_tree)
        ws.add_root(standalone_dir)
        results = ws.get_flat_list(root=project_tree)
        assert len(results) == 3
        results2 = ws.get_flat_list(root=standalone_dir)
        assert len(results2) == 1


# ---------------------------------------------------------------------------
# Workspace: lazy store loading
# ---------------------------------------------------------------------------


class TestLazyLoading:
    def test_load_store(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        qs_path = project_tree / "2026-02-19" / "#3_time_of_flight_134241" / "quam_state"
        store = ws.load_store(qs_path)
        assert store is not None
        assert store.merged is not None

    def test_cached_on_second_call(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        qs_path = project_tree / "2026-02-19" / "#3_time_of_flight_134241" / "quam_state"
        store1 = ws.load_store(qs_path)
        store2 = ws.load_store(qs_path)
        assert store1 is store2

    def test_evict_store(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        qs_path = project_tree / "2026-02-19" / "#3_time_of_flight_134241" / "quam_state"
        ws.load_store(qs_path)
        ws.evict_store(qs_path)
        assert qs_path.resolve() not in ws._loaded_stores

    def test_lru_eviction(self, tmp_path):
        """When cache exceeds MAX_CACHED_STORES, oldest entry is evicted."""
        ws = Workspace()
        paths = []
        for i in range(MAX_CACHED_STORES + 2):
            d = tmp_path / f"exp_{i}" / "quam_state"
            _make_quam_state(d)
            paths.append(d)
            ws.load_store(d)

        assert len(ws._loaded_stores) == MAX_CACHED_STORES
        assert paths[0].resolve() not in ws._loaded_stores
        assert paths[1].resolve() not in ws._loaded_stores
        assert paths[-1].resolve() in ws._loaded_stores


# ---------------------------------------------------------------------------
# ExperimentEntry: short_label
# ---------------------------------------------------------------------------


class TestShortLabel:
    def test_experiment_label(self, project_tree):
        ws = Workspace()
        ws.add_root(project_tree)
        entry = ws.get_flat_list(date_filter="2026-02-20")[0]
        label = entry.short_label
        assert "#55" in label
        assert "08_qubit_spectroscopy" in label
        assert "failed" in label

    def test_standalone_label(self, standalone_dir):
        ws = Workspace()
        ws.add_root(standalone_dir)
        entry = ws.all_entries[0]
        assert entry.short_label == "my_quam_state"


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_data
class TestRealDataFolder:
    def test_scans_experiment_data(self):
        ws = Workspace()
        entries = ws.add_root(DATA_FOLDER)
        assert len(entries) >= 50

    def test_date_groups(self):
        ws = Workspace()
        ws.add_root(DATA_FOLDER)
        key = str(DATA_FOLDER.resolve())
        groups = ws.tree[key]
        assert len(groups) >= 1
        assert groups[0].date_str == "2026-02-19"

    def test_parses_real_node_json(self):
        ws = Workspace()
        ws.add_root(DATA_FOLDER)
        spectroscopy = ws.get_flat_list(experiment_filter="qubit_spectroscopy")
        assert len(spectroscopy) >= 1
        entry = spectroscopy[0]
        assert entry.run_id is not None
        assert len(entry.qubits) > 0

    def test_filter_by_qubit(self):
        ws = Workspace()
        ws.add_root(DATA_FOLDER)
        results = ws.get_flat_list(qubit_filter="qA4")
        assert len(results) >= 1
        assert all("qA4" in e.qubits for e in results)

    def test_lazy_load_real_store(self):
        ws = Workspace()
        ws.add_root(DATA_FOLDER)
        entry = ws.all_entries[0]
        store = ws.load_store(entry.quam_state_path)
        assert len(store.qubit_names) > 0


@skip_no_standalone
class TestRealStandaloneFolder:
    def test_scans_standalone(self):
        ws = Workspace()
        entries = ws.add_root(STANDALONE_FOLDER)
        assert len(entries) == 1
        assert entries[0].is_standalone

    def test_load_standalone_store(self):
        ws = Workspace()
        ws.add_root(STANDALONE_FOLDER)
        entry = ws.all_entries[0]
        store = ws.load_store(entry.quam_state_path)
        assert len(store.qubit_names) == 3


class TestScannerSafeIO:
    """Red-team Phase 2 finding 0.2: scanner's metadata reads of node.json
    (and the QuamStore loads it triggers) must go through ``safe_io``, so an
    experiment program writing the workspace folder isn't broken by the
    State Manager's read on Windows."""

    def _seed_run(self, base: Path, run_id: int) -> Path:
        """Create a minimal experiment run folder under ``base``."""
        run = base / "2026-05-27" / f"#{run_id}_test_experiment_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "test_experiment", "status": "successful"},
            "data": {"parameters": {"model": {"qubits": ["q1"]}}, "outcomes": {}},
            "id": run_id,
            "parents": [],
            "created_at": "2026-05-27T01:00:00",
        }), encoding="utf-8")
        quam = run / "quam_state"
        quam.mkdir()
        (quam / "state.json").write_text(json.dumps({"qubits": {}}), encoding="utf-8")
        (quam / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
        return quam

    def test_node_json_parse_routes_through_safe_io(self, tmp_path, monkeypatch):
        """``_parse_experiment_folder`` must call ``safe_io.read_json`` for
        node.json — never raw ``open()`` — so we keep FILE_SHARE_DELETE on
        Windows for files an experiment may also be writing.
        """
        from quam_state_manager.core import safe_io, scanner

        self._seed_run(tmp_path, 7)
        calls: list[Path] = []
        real_fn = safe_io.read_json

        def spy(path):
            calls.append(Path(path))
            return real_fn(path)

        monkeypatch.setattr(scanner.safe_io, "read_json", spy)
        ws = Workspace()
        ws.add_root(tmp_path)
        assert any(p.name == "node.json" for p in calls), (
            "scanner._parse_experiment_folder did not route the node.json "
            "read through safe_io.read_json"
        )


class TestScannerParallel:
    """Phase 3 §2.1 — cold-scan parallelism. _scan_root must fan the per-
    folder parse across a ThreadPoolExecutor; the gain on real I/O is
    measurable even with a trivial monkeypatched sleep."""

    def _seed_run(self, base: Path, run_id: int) -> None:
        run = base / "2026-05-28" / f"#{run_id}_test_010000"
        run.mkdir(parents=True)
        (run / "node.json").write_text(json.dumps({
            "metadata": {"name": "test"}, "data": {}, "id": run_id, "parents": [],
            "created_at": "2026-05-28T01:00:00",
        }), encoding="utf-8")
        qs = run / "quam_state"
        qs.mkdir()
        (qs / "state.json").write_text("{}", encoding="utf-8")
        (qs / "wiring.json").write_text("{}", encoding="utf-8")

    def test_parallel_speedup_with_slow_parser(self, tmp_path, monkeypatch):
        """With 16 experiments and each parse sleeping 40 ms, the serial
        time is ~640 ms. The parallel path with 8+ workers must finish
        well under 200 ms."""
        import time
        from quam_state_manager.core import scanner as scanner_mod

        for i in range(16):
            self._seed_run(tmp_path, i)

        real_parse = scanner_mod._parse_experiment_folder

        def slow_parse(path):
            time.sleep(0.04)
            return real_parse(path)

        monkeypatch.setattr(scanner_mod, "_parse_experiment_folder", slow_parse)

        start = time.time()
        entries = scanner_mod._scan_root(tmp_path)
        elapsed = time.time() - start
        assert len(entries) == 16
        assert elapsed < 0.3, (
            f"parallel scan took {elapsed:.2f}s for 16 entries x 40ms = "
            f"640ms serial; parallel should be well under 300ms"
        )


# ---------------------------------------------------------------------------
# Cross-platform audit: clock-skew-immune staleness, ~-expansion + inode
# dedup of roots, symlinked archive discovery + cycle termination
# ---------------------------------------------------------------------------


def _make_exp(date_dir: Path, run_id: int, name: str = "ramsey",
              timestamp: str = "2026-02-19T01:00:00+09:00") -> Path:
    """One synthetic experiment folder (quam_state + node.json) under *date_dir*."""
    exp = date_dir / f"#{run_id}_{name}_010000"
    exp.mkdir(parents=True)
    _make_quam_state(exp / "quam_state")
    _make_node_json(exp, run_id=run_id, name=name, timestamp=timestamp)
    return exp


def _utime_tree(root: Path, ts: float) -> None:
    """Stamp *root* and everything under it with one mtime (a 'server clock')."""
    for p in [root, *root.rglob("*")]:
        os.utime(p, (ts, ts))


class TestClockSkewImmuneStaleness:
    """_is_root_stale must compare the stored observed mtime to the current
    observed mtime — never a child mtime to the local time.time() sampled at
    scan. A network mount whose server clock runs behind ours would freeze
    (new runs invisible forever); one running ahead would thrash a full
    rescan on every poll."""

    def test_server_clock_behind_still_detects_new_run(self, tmp_path, monkeypatch):
        root = tmp_path / "mount"
        _make_exp(root / "2026-02-19", 1)
        past = 1_000_000_000.0            # ~2001 — deep in OUR past
        _utime_tree(root, past)

        ws = Workspace()
        ws.add_root(root)
        assert len(ws.all_entries) == 1

        # New run lands; the file server stamps it 60 s later — still far
        # below this machine's wall clock. Freeze the local clock at an
        # absurd value to prove the staleness gate never consults it.
        _make_exp(root / "2026-02-19", 2, name="t1")
        _utime_tree(root, past + 60)
        monkeypatch.setattr(time, "time", lambda: 9e12)

        assert ws._is_root_stale(root) is True
        assert ws.rescan_if_stale() is True
        assert len(ws.all_entries) == 2

    def test_server_clock_ahead_does_not_thrash(self, tmp_path, monkeypatch):
        root = tmp_path / "mount"
        _make_exp(root / "2026-02-19", 1)
        future = time.time() + 10 * 365 * 24 * 3600   # server 10 years ahead
        _utime_tree(root, future)

        ws = Workspace()
        ws.add_root(root)
        version = ws.version
        monkeypatch.setattr(time, "time", lambda: 0.0)

        # Nothing changed on disk: future-stamped mtimes must NOT look stale
        # (the old wall-clock comparison rescanned on every poll here).
        assert ws._is_root_stale(root) is False
        assert ws.rescan_if_stale() is False
        assert ws.version == version


class TestRootNormalizationAndDedup:
    def test_add_root_expands_tilde(self, tmp_path, monkeypatch):
        """A literal '~/data' used to resolve to $CWD/~/data, get persisted,
        and fail every later session."""
        home = tmp_path / "home"
        data = home / "data"
        _make_exp(data / "2026-02-19", 1)
        monkeypatch.setenv("HOME", str(home))          # Path.expanduser (POSIX)
        monkeypatch.setenv("USERPROFILE", str(home))   # …and Windows

        ws = Workspace()
        entries = ws.add_root("~/data")
        assert len(entries) == 1
        assert ws.root_folders == [data.resolve()]
        assert all("~" not in str(r) for r in ws.root_folders)
        # remove accepts the same spelling
        ws.remove_root("~/data")
        assert ws.root_folders == []

    def test_add_root_dedups_same_inode_spellings(self, tmp_path, monkeypatch):
        """macOS's default FS is case-insensitive/case-preserving: 'Data' and
        'data' are ONE physical directory under two resolved spellings.
        Registering both used to duplicate every run (and give downstream
        consumers two separate caches/locks for one folder). Simulated here
        by forcing os.stat to report one (st_dev, st_ino) for both paths."""
        a = tmp_path / "ChipData"
        _make_exp(a / "2026-02-19", 1)
        b = tmp_path / "chipdata"
        b.mkdir()

        real_stat = os.stat
        a_res, b_res = str(a.resolve()), str(b.resolve())

        def same_inode_stat(path, *args, **kwargs):
            if str(path) == b_res:
                return real_stat(a_res, *args, **kwargs)
            return real_stat(path, *args, **kwargs)

        monkeypatch.setattr(os, "stat", same_inode_stat)

        ws = Workspace()
        ws.add_root(a)
        entries = ws.add_root(b)          # variant spelling, same physical dir
        assert len(ws.root_folders) == 1  # NOT registered twice
        assert len(entries) == 1          # the existing root's entries returned

        ws.remove_root(b)                 # removing via the variant works too
        assert ws.root_folders == []

    def test_add_root_missing_path_still_exact_dedups(self, tmp_path):
        """Inode dedup needs a stat; a missing path falls back to exact-path
        dedup and never raises."""
        ghost = tmp_path / "not_there"
        ws = Workspace()
        ws.add_root(ghost)
        ws.add_root(ghost)
        assert ws.root_folders == [ghost.resolve()]


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink semantics")
class TestSymlinkDiscovery:
    def test_symlinked_date_dir_is_discovered(self, tmp_path):
        """DatasetStore's iterdir-based walk follows symlinks; the workspace
        scanner (os.walk followlinks=False) silently hid the same archive —
        symlinked date/run dirs are normal POSIX archive practice."""
        archive = tmp_path / "cold_archive" / "2026-02-19"
        _make_exp(archive, 7)
        root = tmp_path / "workspace_root"
        root.mkdir()
        (root / "2026-02-19").symlink_to(archive, target_is_directory=True)

        entries = _scan_root(root)
        assert [e.run_id for e in entries] == [7]

    def test_symlink_cycle_terminates(self, tmp_path):
        """A symlink back to an ancestor must terminate (inode visited-set),
        and the real content is still discovered exactly once."""
        root = tmp_path / "root"
        _make_exp(root / "2026-02-19", 1)
        (root / "loop").symlink_to(root, target_is_directory=True)

        entries = _scan_root(root)
        assert [e.run_id for e in entries] == [1]

    def test_two_routes_to_one_dir_discover_once(self, tmp_path):
        """A second (non-cyclic) symlink route to an already-walked dir is
        pruned by the visited set — no duplicate entries."""
        root = tmp_path / "root"
        real_date = root / "2026-02-19"
        _make_exp(real_date, 1)
        (root / "alias").symlink_to(real_date, target_is_directory=True)

        entries = _scan_root(root)
        assert [e.run_id for e in entries] == [1]


def test_scan_dir_cap_bounds_runaway_symlink_walks(tmp_path, monkeypatch, caplog):
    # The inode visited-set stops cycles, not SCOPE: a symlink at the workspace
    # root pointing into a huge foreign tree must not walk it unboundedly.
    from quam_state_manager.core import scanner as sc
    big = tmp_path / "big"
    for i in range(30):
        (big / f"d{i}" / "sub").mkdir(parents=True)
    root = tmp_path / "ws"
    root.mkdir()
    (root / "quam_state").mkdir()
    (root / "quam_state" / "state.json").write_text("{}", encoding="utf-8")
    (root / "quam_state" / "wiring.json").write_text("{}", encoding="utf-8")
    (root / "link_to_big").symlink_to(big)
    monkeypatch.setattr(sc, "_SCAN_DIR_CAP", 10)
    import logging
    with caplog.at_level(logging.WARNING):
        entries = sc._scan_root(root)
    assert any("stopped at" in r.message for r in caplog.records)
    # the walk terminated early instead of visiting all ~60 big-tree dirs
    assert len(entries) <= 1
