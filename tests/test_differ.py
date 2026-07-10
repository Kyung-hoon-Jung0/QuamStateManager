"""Tests for quam_state_manager.core.differ.Differ.

Covers 2-way diff (added, removed, modified, float tolerance, ignore_keys),
multi_compare (N-way trend extraction), and real data comparisons.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.differ import Differ, DiffEntry, _values_equal
from quam_state_manager.core.experiment_data import ExperimentContext
from quam_state_manager.core.loader import QuamStore

# ---------------------------------------------------------------------------
# Paths to real quam_state folders
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"
DATA_ROOT = _EXAMPLECHIP_ROOT / "data" / "project_name" / "2026-02-19"

has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()
has_data = DATA_ROOT.exists()

skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state not found")
skip_no_data = pytest.mark.skipif(not has_data, reason="Experiment data folder not found")


# ---------------------------------------------------------------------------
# Synthetic fixture helpers
# ---------------------------------------------------------------------------

def _base_state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "T1": 8834,
                "T2ramsey": 1.5e-6,
                "anharmonicity": -220e6,
                "grid_location": "0,2",
                "xy": {
                    "RF_frequency": 6.25e9,
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40, "__class__": "DragCosinePulse"},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "operations": {"readout": {"amplitude": 0.042}},
                },
                "z": {"joint_offset": 0.081},
                "gate_fidelity": {"averaged": 0.991},
            },
            "qA2": {
                "id": "qA2",
                "f_01": 5.8e9,
                "T1": 7500,
                "T2ramsey": 1.2e-6,
                "anharmonicity": -210e6,
                "grid_location": "1,2",
                "xy": {"RF_frequency": 5.8e9},
                "resonator": {"f_01": 7.1e9},
                "z": {"joint_offset": 0.0},
                "gate_fidelity": {"averaged": 0.985},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1", "qA2"],
    }


def _base_wiring():
    return {
        "wiring": {
            "qubits": {
                "qA1": {"xy": {"opx_output": "MW-FEM/1/2"}, "z": {"opx_output": "LF-FEM/5/1"}},
                "qA2": {"xy": {"opx_output": "MW-FEM/1/3"}, "z": {"opx_output": "LF-FEM/5/2"}},
            },
        },
        "network": {"host": "10.1.1.18"},
    }


def _write_quam_state(folder: Path, state: dict, wiring: dict) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring, indent=2), encoding="utf-8")


@pytest.fixture
def folder_a(tmp_path: Path) -> Path:
    path = tmp_path / "a"
    _write_quam_state(path, _base_state(), _base_wiring())
    return path


@pytest.fixture
def folder_b_modified(tmp_path: Path) -> Path:
    """State B: modified f_01, T1, readout amplitude for qA1."""
    state = _base_state()
    state["qubits"]["qA1"]["f_01"] = 6.3e9
    state["qubits"]["qA1"]["T1"] = 9000
    state["qubits"]["qA1"]["resonator"]["operations"]["readout"]["amplitude"] = 0.05
    path = tmp_path / "b_mod"
    _write_quam_state(path, state, _base_wiring())
    return path


@pytest.fixture
def folder_b_added(tmp_path: Path) -> Path:
    """State B: added a new qubit qA3."""
    state = _base_state()
    state["qubits"]["qA3"] = {
        "id": "qA3",
        "f_01": 6.0e9,
        "T1": 6000,
        "grid_location": "2,2",
    }
    path = tmp_path / "b_add"
    _write_quam_state(path, state, _base_wiring())
    return path


@pytest.fixture
def folder_b_removed(tmp_path: Path) -> Path:
    """State B: removed qA2."""
    state = _base_state()
    del state["qubits"]["qA2"]
    state["active_qubit_names"] = ["qA1"]
    path = tmp_path / "b_rem"
    _write_quam_state(path, state, _base_wiring())
    return path


@pytest.fixture
def differ() -> Differ:
    return Differ()


# ---------------------------------------------------------------------------
# _values_equal helper
# ---------------------------------------------------------------------------


class TestValuesEqual:
    def test_identical_ints(self):
        assert _values_equal(10, 10, 1e-12) is True

    def test_different_ints(self):
        assert _values_equal(10, 11, 1e-12) is False

    def test_identical_floats(self):
        assert _values_equal(6.25e9, 6.25e9, 1e-12) is True

    def test_float_within_tolerance(self):
        a = 6.250000000000000e9
        b = 6.250000000000001e9
        assert _values_equal(a, b, 1e-12) is True

    def test_float_outside_tolerance(self):
        assert _values_equal(6.25e9, 6.3e9, 1e-12) is False

    def test_different_types(self):
        assert _values_equal(10, 10.0, 1e-12) is False

    def test_strings(self):
        assert _values_equal("a", "a", 1e-12) is True
        assert _values_equal("a", "b", 1e-12) is False

    def test_none(self):
        assert _values_equal(None, None, 1e-12) is True
        assert _values_equal(None, 1, 1e-12) is False

    def test_zero_floats(self):
        assert _values_equal(0.0, 0.0, 1e-12) is True


# ---------------------------------------------------------------------------
# 2-way diff: modified
# ---------------------------------------------------------------------------


class TestDiffModified:
    def test_detects_modifications(self, differ, folder_a, folder_b_modified):
        entries = differ.diff(folder_a, folder_b_modified)
        modified = [e for e in entries if e.change_type == "modified"]
        paths = {e.dot_path for e in modified}
        assert "qubits.qA1.f_01" in paths
        assert "qubits.qA1.T1" in paths
        assert "qubits.qA1.resonator.operations.readout.amplitude" in paths

    def test_old_new_values(self, differ, folder_a, folder_b_modified):
        entries = differ.diff(folder_a, folder_b_modified)
        f01 = [e for e in entries if e.dot_path == "qubits.qA1.f_01"][0]
        assert f01.old_value == 6.25e9
        assert f01.new_value == 6.3e9

    def test_unchanged_not_in_diff(self, differ, folder_a, folder_b_modified):
        entries = differ.diff(folder_a, folder_b_modified)
        paths = {e.dot_path for e in entries}
        assert "qubits.qA2.f_01" not in paths
        assert "qubits.qA1.grid_location" not in paths


# ---------------------------------------------------------------------------
# 2-way diff: added / removed
# ---------------------------------------------------------------------------


class TestDiffAddedRemoved:
    def test_detects_additions(self, differ, folder_a, folder_b_added):
        entries = differ.diff(folder_a, folder_b_added)
        added = [e for e in entries if e.change_type == "added"]
        paths = {e.dot_path for e in added}
        assert "qubits.qA3.f_01" in paths
        assert "qubits.qA3.id" in paths

    def test_added_has_none_old(self, differ, folder_a, folder_b_added):
        entries = differ.diff(folder_a, folder_b_added)
        qa3 = [e for e in entries if e.dot_path == "qubits.qA3.f_01"][0]
        assert qa3.old_value is None
        assert qa3.new_value == 6.0e9

    def test_detects_removals(self, differ, folder_a, folder_b_removed):
        entries = differ.diff(folder_a, folder_b_removed)
        removed = [e for e in entries if e.change_type == "removed"]
        paths = {e.dot_path for e in removed}
        assert "qubits.qA2.f_01" in paths
        assert "qubits.qA2.id" in paths

    def test_removed_has_none_new(self, differ, folder_a, folder_b_removed):
        entries = differ.diff(folder_a, folder_b_removed)
        qa2 = [e for e in entries if e.dot_path == "qubits.qA2.f_01"][0]
        assert qa2.old_value == 5.8e9
        assert qa2.new_value is None


# ---------------------------------------------------------------------------
# 2-way diff: identical
# ---------------------------------------------------------------------------


class TestDiffIdentical:
    def test_no_diff_for_identical(self, differ, folder_a):
        entries = differ.diff(folder_a, folder_a)
        assert len(entries) == 0


# ---------------------------------------------------------------------------
# 2-way diff: ignore_keys
# ---------------------------------------------------------------------------


class TestDiffIgnoreKeys:
    def test_class_ignored_by_default(self, differ, folder_a, folder_b_modified):
        entries = differ.diff(folder_a, folder_b_modified)
        paths = {e.dot_path for e in entries}
        class_paths = {p for p in paths if "__class__" in p}
        assert len(class_paths) == 0

    def test_custom_ignore(self, differ, folder_a, folder_b_modified):
        entries = differ.diff(folder_a, folder_b_modified, ignore_keys={"__class__", "T1"})
        paths = {e.dot_path for e in entries}
        assert "qubits.qA1.T1" not in paths
        assert "qubits.qA1.f_01" in paths


# ---------------------------------------------------------------------------
# 2-way diff: float tolerance
# ---------------------------------------------------------------------------


class TestDiffFloatTolerance:
    def test_tight_tolerance(self, differ, tmp_path):
        state_a = _base_state()
        state_b = _base_state()
        state_b["qubits"]["qA1"]["f_01"] = 6.25e9 + 1.0  # +1 Hz difference
        _write_quam_state(tmp_path / "ta", state_a, _base_wiring())
        _write_quam_state(tmp_path / "tb", state_b, _base_wiring())

        entries_tight = differ.diff(tmp_path / "ta", tmp_path / "tb", float_tolerance=1e-12)
        assert any(e.dot_path == "qubits.qA1.f_01" for e in entries_tight)

    def test_loose_tolerance(self, differ, tmp_path):
        state_a = _base_state()
        state_b = _base_state()
        state_b["qubits"]["qA1"]["f_01"] = 6.25e9 + 1.0
        _write_quam_state(tmp_path / "ta", state_a, _base_wiring())
        _write_quam_state(tmp_path / "tb", state_b, _base_wiring())

        entries_loose = differ.diff(tmp_path / "ta", tmp_path / "tb", float_tolerance=1e-6)
        assert not any(e.dot_path == "qubits.qA1.f_01" for e in entries_loose)


# ---------------------------------------------------------------------------
# 2-way diff: accepts QuamStore objects
# ---------------------------------------------------------------------------


class TestDiffAcceptsStores:
    def test_diff_with_stores(self, differ, folder_a, folder_b_modified):
        store_a = QuamStore(folder_a, validate=False)
        store_b = QuamStore(folder_b_modified, validate=False)
        entries = differ.diff(store_a, store_b)
        paths = {e.dot_path for e in entries}
        assert "qubits.qA1.f_01" in paths


# ---------------------------------------------------------------------------
# 2-way diff: sorted output
# ---------------------------------------------------------------------------


class TestDiffSorted:
    def test_entries_sorted_by_path(self, differ, folder_a, folder_b_modified):
        entries = differ.diff(folder_a, folder_b_modified)
        paths = [e.dot_path for e in entries]
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# summary
# ---------------------------------------------------------------------------


class TestSummary:
    def test_summary_counts(self, differ, folder_a, folder_b_modified):
        entries = differ.diff(folder_a, folder_b_modified)
        s = Differ.summary(entries)
        assert s["total"] == len(entries)
        assert s["modified"] >= 3
        assert s["added"] == 0
        assert s["removed"] == 0

    def test_summary_with_additions(self, differ, folder_a, folder_b_added):
        entries = differ.diff(folder_a, folder_b_added)
        s = Differ.summary(entries)
        assert s["added"] >= 3


# ---------------------------------------------------------------------------
# multi_compare: synthetic
# ---------------------------------------------------------------------------


class TestMultiCompare:
    def test_basic(self, differ, tmp_path):
        states = []
        for i, freq in enumerate([6.25e9, 6.26e9, 6.27e9]):
            s = _base_state()
            s["qubits"]["qA1"]["f_01"] = freq
            folder = tmp_path / f"snap_{i}"
            _write_quam_state(folder, s, _base_wiring())
            states.append(QuamStore(folder, validate=False))

        labels = ["run_0", "run_1", "run_2"]
        results = differ.multi_compare(states, labels, ["f_01"])

        qa1_f01 = [r for r in results if r["qubit"] == "qA1" and r["property"] == "f_01"]
        assert len(qa1_f01) == 1
        values = qa1_f01[0]["values"]
        assert len(values) == 3
        assert values[0]["value"] == 6.25e9
        assert values[1]["value"] == 6.26e9
        assert values[2]["value"] == 6.27e9
        assert values[0]["label"] == "run_0"

    def test_multiple_properties(self, differ, tmp_path):
        states = []
        for i in range(2):
            s = _base_state()
            s["qubits"]["qA1"]["f_01"] = 6.25e9 + i * 1e7
            s["qubits"]["qA1"]["T1"] = 8834 + i * 100
            folder = tmp_path / f"mp_{i}"
            _write_quam_state(folder, s, _base_wiring())
            states.append(QuamStore(folder, validate=False))

        results = differ.multi_compare(states, ["a", "b"], ["f_01", "T1"])
        qa1_results = [r for r in results if r["qubit"] == "qA1"]
        props = {r["property"] for r in qa1_results}
        assert props == {"f_01", "T1"}

    def test_qubit_filter(self, differ, tmp_path):
        states = []
        for i in range(2):
            folder = tmp_path / f"qf_{i}"
            _write_quam_state(folder, _base_state(), _base_wiring())
            states.append(QuamStore(folder, validate=False))

        results = differ.multi_compare(states, ["a", "b"], ["f_01"], qubit_filter=["qA1"])
        qubits = {r["qubit"] for r in results}
        assert qubits == {"qA1"}

    def test_mismatched_lengths_raises(self, differ):
        with pytest.raises(ValueError, match="same length"):
            differ.multi_compare([], ["a"], ["f_01"])

    def test_missing_qubit_in_some_stores(self, differ, tmp_path):
        state_with = _base_state()
        state_without = _base_state()
        del state_without["qubits"]["qA2"]

        folder_a = tmp_path / "mc_a"
        folder_b = tmp_path / "mc_b"
        _write_quam_state(folder_a, state_with, _base_wiring())
        _write_quam_state(folder_b, state_without, _base_wiring())

        stores = [QuamStore(folder_a, validate=False), QuamStore(folder_b, validate=False)]
        results = differ.multi_compare(stores, ["a", "b"], ["f_01"])

        qa2 = [r for r in results if r["qubit"] == "qA2"]
        assert len(qa2) == 1
        assert qa2[0]["values"][0]["value"] == 5.8e9
        assert qa2[0]["values"][1]["value"] is None

    def test_empty_stores(self, differ):
        results = differ.multi_compare([], [], ["f_01"])
        assert results == []


# ---------------------------------------------------------------------------
# MultiDiff (differences-only filter)
# ---------------------------------------------------------------------------


class TestMultiDiff:
    def test_keeps_only_different_rows(self, differ, tmp_path):
        states = []
        for i, freq in enumerate([6.25e9, 6.26e9]):
            s = _base_state()
            s["qubits"]["qA1"]["f_01"] = freq
            folder = tmp_path / f"md_{i}"
            _write_quam_state(folder, s, _base_wiring())
            states.append(QuamStore(folder, validate=False))

        results = differ.multi_diff(states, ["a", "b"], ["f_01", "T1"])
        qa1_f01 = [r for r in results if r["qubit"] == "qA1" and r["property"] == "f_01"]
        assert len(qa1_f01) == 1
        qa1_t1 = [r for r in results if r["qubit"] == "qA1" and r["property"] == "T1"]
        assert len(qa1_t1) == 0

    def test_all_same_returns_empty(self, differ, tmp_path):
        states = []
        for i in range(3):
            folder = tmp_path / f"same_{i}"
            _write_quam_state(folder, _base_state(), _base_wiring())
            states.append(QuamStore(folder, validate=False))

        results = differ.multi_diff(states, ["a", "b", "c"], ["f_01"])
        assert results == []

    def test_none_vs_value_counts_as_diff(self, differ, tmp_path):
        state_with = _base_state()
        state_without = _base_state()
        del state_without["qubits"]["qA2"]

        folder_a = tmp_path / "nv_a"
        folder_b = tmp_path / "nv_b"
        _write_quam_state(folder_a, state_with, _base_wiring())
        _write_quam_state(folder_b, state_without, _base_wiring())

        stores = [QuamStore(folder_a, validate=False), QuamStore(folder_b, validate=False)]
        results = differ.multi_diff(stores, ["a", "b"], ["f_01"])
        qa2 = [r for r in results if r["qubit"] == "qA2"]
        assert len(qa2) == 1


# ---------------------------------------------------------------------------
# compare_parameters
# ---------------------------------------------------------------------------


class TestCompareParameters:
    def test_detects_parameter_differences(self):
        ctx_a = ExperimentContext(parameters={"num_shots": 100, "multiplexed": True})
        ctx_b = ExperimentContext(parameters={"num_shots": 200, "multiplexed": True})
        rows = Differ.compare_parameters([ctx_a, ctx_b], ["a", "b"])
        keys = [r["key"] for r in rows]
        assert "num_shots" in keys
        assert "multiplexed" not in keys

    def test_all_same_returns_empty(self):
        ctx_a = ExperimentContext(parameters={"num_shots": 100})
        ctx_b = ExperimentContext(parameters={"num_shots": 100})
        rows = Differ.compare_parameters([ctx_a, ctx_b], ["a", "b"])
        assert rows == []

    def test_missing_key_counts_as_diff(self):
        ctx_a = ExperimentContext(parameters={"num_shots": 100, "extra_param": 5})
        ctx_b = ExperimentContext(parameters={"num_shots": 100})
        rows = Differ.compare_parameters([ctx_a, ctx_b], ["a", "b"])
        keys = [r["key"] for r in rows]
        assert "extra_param" in keys
        extra_row = [r for r in rows if r["key"] == "extra_param"][0]
        assert extra_row["values"][0]["value"] == 5
        assert extra_row["values"][1]["value"] is None

    def test_empty_contexts(self):
        rows = Differ.compare_parameters([], [])
        assert rows == []


# ---------------------------------------------------------------------------
# compare_fit_results
# ---------------------------------------------------------------------------


class TestCompareFitResults:
    def test_detects_fit_differences(self):
        ctx_a = ExperimentContext(fit_results={
            "qC1": {"frequency": 7.04e9, "fwhm": 1e7},
        })
        ctx_b = ExperimentContext(fit_results={
            "qC1": {"frequency": 7.05e9, "fwhm": 1e7},
        })
        rows = Differ.compare_fit_results([ctx_a, ctx_b], ["a", "b"])
        props = [(r["qubit"], r["property"]) for r in rows]
        assert ("qC1", "frequency") in props
        assert ("qC1", "fwhm") not in props

    def test_missing_qubit_counts_as_diff(self):
        ctx_a = ExperimentContext(fit_results={
            "qC1": {"frequency": 7e9},
            "qC2": {"frequency": 7.3e9},
        })
        ctx_b = ExperimentContext(fit_results={
            "qC1": {"frequency": 7e9},
        })
        rows = Differ.compare_fit_results([ctx_a, ctx_b], ["a", "b"])
        qc2_rows = [r for r in rows if r["qubit"] == "qC2"]
        assert len(qc2_rows) == 1
        assert qc2_rows[0]["values"][1]["value"] is None

    def test_qubit_filter(self):
        ctx_a = ExperimentContext(fit_results={
            "qC1": {"freq": 7e9},
            "qC2": {"freq": 7.3e9},
        })
        ctx_b = ExperimentContext(fit_results={
            "qC1": {"freq": 7.1e9},
            "qC2": {"freq": 7.4e9},
        })
        rows = Differ.compare_fit_results([ctx_a, ctx_b], ["a", "b"], qubit_filter=["qC1"])
        qubits = {r["qubit"] for r in rows}
        assert qubits == {"qC1"}

    def test_different_experiment_types(self):
        ctx_a = ExperimentContext(fit_results={
            "qC1": {"frequency": 7e9, "fwhm": 1e7},
        })
        ctx_b = ExperimentContext(fit_results={
            "qC1": {"resonator_frequency": 7.1e9, "optimal_power": -51.0},
        })
        rows = Differ.compare_fit_results([ctx_a, ctx_b], ["a", "b"])
        keys = {r["property"] for r in rows}
        assert "frequency" in keys
        assert "fwhm" in keys
        assert "resonator_frequency" in keys
        assert "optimal_power" in keys

    def test_empty_contexts(self):
        rows = Differ.compare_fit_results([], [])
        assert rows == []


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_large
class TestRealDiff:
    def test_self_diff_empty(self):
        differ = Differ()
        entries = differ.diff(LARGE_FOLDER, LARGE_FOLDER)
        assert len(entries) == 0

    def test_diff_large_vs_modified(self, tmp_path):
        store_a = QuamStore(LARGE_FOLDER)
        from quam_state_manager.core.modifier import Modifier
        from quam_state_manager.core.saver import Saver

        mod = Modifier(store_a)
        mod.set_value("qubits.qA1.f_01", 6.5e9)
        mod.set_value("qubits.qA1.T1", 10000)
        out = tmp_path / "modified"
        Saver(store_a).save(out)

        differ = Differ()
        entries = differ.diff(LARGE_FOLDER, out)
        modified = [e for e in entries if e.change_type == "modified"]
        paths = {e.dot_path for e in modified}
        assert "qubits.qA1.f_01" in paths
        assert "qubits.qA1.T1" in paths


@skip_no_data
class TestRealMultiCompare:
    def _get_experiment_folders(self, n: int = 5) -> list[Path]:
        folders = sorted(DATA_ROOT.iterdir())
        quam_folders = [f / "quam_state" for f in folders if (f / "quam_state" / "state.json").exists()]
        return quam_folders[:n]

    def test_multi_compare_real_experiments(self):
        folders = self._get_experiment_folders(5)
        if len(folders) < 3:
            pytest.skip("Need at least 3 experiment folders")

        stores = [QuamStore(f, validate=False) for f in folders]
        labels = [f.parent.name for f in folders]

        differ = Differ()
        results = differ.multi_compare(stores, labels, ["f_01", "T2ramsey"])

        assert len(results) > 0
        for r in results:
            assert "qubit" in r
            assert "property" in r
            assert "values" in r
            assert len(r["values"]) == len(stores)

    def test_trend_values_are_numeric_or_none(self):
        folders = self._get_experiment_folders(3)
        if len(folders) < 2:
            pytest.skip("Need at least 2 experiment folders")

        stores = [QuamStore(f, validate=False) for f in folders]
        labels = [f.parent.name for f in folders]

        differ = Differ()
        results = differ.multi_compare(stores, labels, ["f_01"])

        for r in results:
            for v in r["values"]:
                assert v["value"] is None or isinstance(v["value"], (int, float))

    def test_diff_between_two_experiments(self):
        folders = self._get_experiment_folders(2)
        if len(folders) < 2:
            pytest.skip("Need at least 2 experiment folders")

        differ = Differ()
        entries = differ.diff(folders[0], folders[1])
        s = Differ.summary(entries)
        assert s["total"] >= 0
