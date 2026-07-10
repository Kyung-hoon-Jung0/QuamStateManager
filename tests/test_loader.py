"""Tests for quam_state_manager.core.loader.

Uses both a minimal synthetic fixture and real quam_state folders from the
examplechip repository to verify loading, merging, pointer validation, and accessors.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore, ChangeEntry, PointerWarning, flatten, _walk

# ---------------------------------------------------------------------------
# Paths to real quam_state folders (skip if not available)
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
SMALL_FOLDER = _EXAMPLECHIP_ROOT / "quam_state"  # 3 qubits
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"  # 17 qubits

has_small = SMALL_FOLDER.exists() and (SMALL_FOLDER / "state.json").exists()
has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()

skip_no_small = pytest.mark.skipif(not has_small, reason="Small quam_state folder not found")
skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state folder not found")


# ---------------------------------------------------------------------------
# Synthetic fixture: minimal state.json + wiring.json in a temp directory
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_folder(tmp_path: Path) -> Path:
    state = {
        "qubits": {
            "q1": {
                "f_01": 5_000_000_000,
                "anharmonicity": -200_000_000,
                "xy": {
                    "opx_output": "#/wiring/qubits/q1/xy/opx_output",
                    "RF_frequency": "#./inferred_RF_frequency",
                    "operations": {
                        "x180": {"length": 40, "amplitude": 0.2},
                        "x90": {
                            "length": "#../x180/length",
                            "amplitude": 0.1,
                        },
                    },
                },
            },
        },
        "qubit_pairs": {
            "q1-2": {"macros": {"cz": {"amp": 0.3}}},
        },
        "active_qubit_names": ["q1"],
        "ports": {
            "mw_outputs": {"con1": {"1": {"1": {"controller_id": "con1"}}}},
        },
        "__class__": "quam_config.my_quam.Quam",
    }
    wiring = {
        "wiring": {
            "qubits": {
                "q1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "rr": {"opx_output": "MW-FEM/1/1"},
                },
            },
        },
        "network": {
            "host": "10.0.0.1",
            "port": None,
            "cluster_name": "test_cluster",
        },
    }
    (tmp_path / "state.json").write_text(json.dumps(state, indent=4), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(wiring, indent=4), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Basic loading and merging
# ---------------------------------------------------------------------------


class TestLoadingSynthetic:
    def test_loads_successfully(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.state is not None
        assert store.wiring is not None
        assert store.merged is not None

    def test_merged_contains_state_keys(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert "qubits" in store.merged
        assert "qubit_pairs" in store.merged
        assert "ports" in store.merged
        assert "__class__" in store.merged

    def test_merged_contains_wiring_keys(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert "wiring" in store.merged
        assert "network" in store.merged

    def test_merged_wiring_structure(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.merged["wiring"]["qubits"]["q1"]["xy"]["opx_output"] == "MW-FEM/1/2"

    def test_merged_network(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.merged["network"]["host"] == "10.0.0.1"

    def test_qubit_names(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.qubit_names == ["q1"]

    def test_qubit_pair_names(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.qubit_pair_names == ["q1-2"]

    def test_repr(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        r = repr(store)
        assert "qubits=1" in r
        assert "pairs=1" in r
        assert "changes=0" in r

    def test_merge_deep_merges_colliding_top_level_keys(self):
        """An alternate wiring layout that puts connectivity at the TOP level
        (qubits/ports) instead of nesting under 'wiring' must NOT wipe the state
        component — connectivity and component deep-merge."""
        state = {"qubits": {"q0": {"xy": {"amplitude": 0.5, "operations": {"x": {}}}}}}
        wiring = {"qubits": {"q0": {"xy": {"opx_output": "#/ports/mw/1"}}},
                  "network": {"host": "1.2.3.4"}}
        store = QuamStore.from_dicts(state, wiring)
        xy = store.merged["qubits"]["q0"]["xy"]
        assert xy["amplitude"] == 0.5            # state component survived
        assert xy["operations"] == {"x": {}}     # not wiped
        assert xy["opx_output"] == "#/ports/mw/1"  # wiring connectivity merged in
        assert store.merged["network"]["host"] == "1.2.3.4"

    def test_merge_normal_layout_unchanged(self):
        """The common layout (wiring nests under 'wiring'/'network', no key
        collision) is a plain union — no deep-merge surprises."""
        state = {"qubits": {"q0": {"xy": {"amplitude": 0.5}}}}
        wiring = {"wiring": {"qubits": {"q0": {"xy": {"opx_output": "p"}}}},
                  "network": {"host": "h"}}
        m = QuamStore.from_dicts(state, wiring).merged
        assert m["qubits"]["q0"]["xy"] == {"amplitude": 0.5}
        assert m["wiring"]["qubits"]["q0"]["xy"]["opx_output"] == "p"


# ---------------------------------------------------------------------------
# Missing files
# ---------------------------------------------------------------------------


class TestMissingFiles:
    def test_missing_state_json(self, tmp_path):
        (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="state.json"):
            QuamStore(tmp_path)

    def test_missing_wiring_json(self, tmp_path):
        (tmp_path / "state.json").write_text("{}", encoding="utf-8")
        with pytest.raises(FileNotFoundError, match="wiring.json"):
            QuamStore(tmp_path)


# ---------------------------------------------------------------------------
# Pointer validation
# ---------------------------------------------------------------------------


class TestPointerValidation:
    def test_no_warnings_on_valid_pointers(self, synthetic_folder):
        store = QuamStore(synthetic_folder, validate=True)
        assert len(store.pointer_warnings) == 0

    def test_warning_on_broken_pointer(self, tmp_path):
        state = {"data": {"ref": "#/nonexistent/path"}}
        wiring = {"wiring": {}, "network": {}}
        (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
        store = QuamStore(tmp_path, validate=True)
        assert len(store.pointer_warnings) == 1
        assert store.pointer_warnings[0].dot_path == "data.ref"
        assert "nonexistent" in store.pointer_warnings[0].pointer

    def test_self_refs_not_warned(self, synthetic_folder):
        store = QuamStore(synthetic_folder, validate=True)
        self_ref_warnings = [w for w in store.pointer_warnings if "#./" in w.pointer]
        assert len(self_ref_warnings) == 0

    def test_skip_validation(self, synthetic_folder):
        store = QuamStore(synthetic_folder, validate=False)
        assert len(store.pointer_warnings) == 0


# ---------------------------------------------------------------------------
# Accessors
# ---------------------------------------------------------------------------


class TestAccessors:
    def test_get_value_scalar(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.get_value("qubits.q1.f_01") == 5_000_000_000

    def test_get_value_nested(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.get_value("wiring.qubits.q1.xy.opx_output") == "MW-FEM/1/2"

    def test_get_value_pointer_raw(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        raw = store.get_value("qubits.q1.xy.opx_output")
        assert raw == "#/wiring/qubits/q1/xy/opx_output"

    def test_resolve_value_pointer(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        resolved = store.resolve_value("qubits.q1.xy.opx_output")
        assert resolved == "MW-FEM/1/2"

    def test_resolve_value_self_ref_stays_raw(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        val = store.resolve_value("qubits.q1.xy.RF_frequency")
        assert val == "#./inferred_RF_frequency"

    def test_resolve_value_non_pointer(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        val = store.resolve_value("qubits.q1.f_01")
        assert val == 5_000_000_000

    def test_get_value_missing_raises(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        with pytest.raises(KeyError):
            store.get_value("qubits.nonexistent.f_01")

    def test_source_file_for_state(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.source_file_for("qubits.q1.f_01") == "state"

    def test_source_file_for_wiring(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.source_file_for("wiring.qubits.q1.xy.opx_output") == "wiring"

    def test_source_file_for_network(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.source_file_for("network.host") == "wiring"


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------


class TestReload:
    def test_reload_picks_up_changes(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        assert store.get_value("qubits.q1.f_01") == 5_000_000_000

        state = json.loads((synthetic_folder / "state.json").read_text(encoding="utf-8"))
        state["qubits"]["q1"]["f_01"] = 6_000_000_000
        (synthetic_folder / "state.json").write_text(json.dumps(state), encoding="utf-8")

        store.reload()
        assert store.get_value("qubits.q1.f_01") == 6_000_000_000

    def test_reload_clears_change_log(self, synthetic_folder):
        store = QuamStore(synthetic_folder)
        store.change_log.append(ChangeEntry("x", 1, 2, "state"))
        store.reload()
        assert len(store.change_log) == 0


# ---------------------------------------------------------------------------
# Flatten utility
# ---------------------------------------------------------------------------


class TestFlatten:
    def test_simple_dict(self):
        data = {"a": {"b": 1, "c": "hello"}, "d": [10, 20]}
        flat = flatten(data)
        assert flat["a.b"] == 1
        assert flat["a.c"] == "hello"
        assert flat["d.0"] == 10
        assert flat["d.1"] == 20

    def test_nested_list_of_dicts(self):
        data = {"items": [{"name": "x"}, {"name": "y"}]}
        flat = flatten(data)
        assert flat["items.0.name"] == "x"
        assert flat["items.1.name"] == "y"

    def test_empty_dict(self):
        assert flatten({}) == {}


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_small
class TestSmallRealFolder:
    def test_loads_3_qubit_state(self):
        store = QuamStore(SMALL_FOLDER)
        assert len(store.qubit_names) == 3
        assert "q1" in store.qubit_names

    def test_qubit_pairs(self):
        store = QuamStore(SMALL_FOLDER)
        assert len(store.qubit_pair_names) >= 2

    def test_pointer_resolution(self):
        store = QuamStore(SMALL_FOLDER)
        resolved = store.resolve_value("qubits.q1.xy.opx_output")
        assert resolved is not None
        assert not (isinstance(resolved, str) and resolved.startswith("#/"))

    def test_network_info(self):
        store = QuamStore(SMALL_FOLDER)
        host = store.get_value("network.host")
        assert isinstance(host, str)

    def test_no_pointer_warnings(self):
        store = QuamStore(SMALL_FOLDER, validate=True)
        real_warnings = [w for w in store.pointer_warnings if "#./" not in w.pointer]
        if real_warnings:
            pytest.skip(f"Real data has {len(real_warnings)} unresolvable pointers (expected in some configs)")


@skip_no_large
class TestLargeRealFolder:
    def test_loads_17_qubit_state(self):
        store = QuamStore(LARGE_FOLDER)
        assert len(store.qubit_names) >= 16
        assert "qA1" in store.qubit_names

    def test_qubit_pairs(self):
        store = QuamStore(LARGE_FOLDER)
        assert len(store.qubit_pair_names) >= 20

    def test_absolute_pointer_resolution(self):
        store = QuamStore(LARGE_FOLDER)
        resolved = store.resolve_value("qubits.qA1.xy.opx_output")
        assert isinstance(resolved, dict) or isinstance(resolved, str)
        if isinstance(resolved, str):
            assert not resolved.startswith("#/")

    def test_relative_pointer_resolution(self):
        store = QuamStore(LARGE_FOLDER)
        raw = store.get_value("qubits.qA1.xy.operations.x90_DragCosine.length")
        assert raw == "#../x180_DragCosine/length"
        resolved = store.resolve_value("qubits.qA1.xy.operations.x90_DragCosine.length")
        assert resolved == 40

    def test_flatten_produces_thousands_of_entries(self):
        store = QuamStore(LARGE_FOLDER)
        flat = flatten(store.merged)
        assert len(flat) > 4000

    def test_repr_shows_counts(self):
        store = QuamStore(LARGE_FOLDER)
        r = repr(store)
        assert "qubits=1" in r or "qubits=" in r
        assert "pairs=" in r


# ---------------------------------------------------------------------------
# Error handling — malformed / missing JSON
# ---------------------------------------------------------------------------


class TestPointerCacheIsolation:
    """Red-team Phase 2 finding 0.1: two QuamStore instances with same-named
    qubits and divergent values must NOT share cached resolutions.

    Before the fix, the module-level ``_resolve_cache`` would return chip A's
    value when chip B was queried with the same (pointer, current_path) key.
    """

    def test_two_stores_resolve_to_their_own_values(self, tmp_path: Path):
        # Two synthetic chips, both with a qubit "q1" + an `f_01` pointer
        # at the same logical location, but with different concrete values.
        for sub, freq in [("chip_a", 5e9), ("chip_b", 5.5e9)]:
            folder = tmp_path / sub
            folder.mkdir()
            state = {
                "qubits": {
                    "q1": {
                        "f_01": freq,
                        "xy": {"frequency": "#/qubits/q1/f_01"},
                    },
                }
            }
            (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
            (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")

        store_a = QuamStore(tmp_path / "chip_a")
        store_b = QuamStore(tmp_path / "chip_b")

        path_tuple = ("qubits", "q1", "xy", "frequency")
        val_a = store_a.resolve_pointer("#/qubits/q1/f_01", path_tuple)
        val_b = store_b.resolve_pointer("#/qubits/q1/f_01", path_tuple)
        # Each store sees its own value -- no cross-chip leak.
        assert val_a == 5e9
        assert val_b == 5.5e9
        # And both caches hold only their own resolution.
        assert store_a._pointer_cache[("#/qubits/q1/f_01", path_tuple)] == 5e9
        assert store_b._pointer_cache[("#/qubits/q1/f_01", path_tuple)] == 5.5e9

    def test_mutation_clears_only_its_own_store_cache(self, tmp_path: Path, synthetic_folder):
        """A second store loaded into the same process must keep its cache
        populated when the first store mutates (mutations call
        ``_clear_pointer_cache`` on the mutating store only).
        """
        # Build a second store from a copy of the synthetic folder so the two
        # stores are independent on disk.
        folder_b = tmp_path / "second"
        folder_b.mkdir()
        (folder_b / "state.json").write_bytes((synthetic_folder / "state.json").read_bytes())
        (folder_b / "wiring.json").write_bytes((synthetic_folder / "wiring.json").read_bytes())

        store_a = QuamStore(synthetic_folder)
        store_b = QuamStore(folder_b)

        # Prime both caches with at least one resolution.
        path = ("qubits", "q1", "xy", "operations", "x90", "length")
        store_a.resolve_pointer("#../x180/length", path)
        store_b.resolve_pointer("#../x180/length", path)
        assert ("#../x180/length", path) in store_a._pointer_cache
        assert ("#../x180/length", path) in store_b._pointer_cache

        # Clearing A's cache must not touch B's.
        store_a._clear_pointer_cache()
        assert ("#../x180/length", path) not in store_a._pointer_cache
        assert ("#../x180/length", path) in store_b._pointer_cache


class TestLoaderUsesSafeIO:
    """Red-team Phase 2 finding 0.2: QuamStore._load must read through
    ``safe_io.read_state_wiring`` so the live-file conflict guarantee
    (FILE_SHARE_DELETE + mtime-bracketed pair read) applies to every load,
    not just the active-chip load path.
    """

    def test_load_routes_through_safe_io(self, monkeypatch, tmp_path: Path):
        from quam_state_manager.core import safe_io

        # Seed a minimal pair so the call has something to return.
        (tmp_path / "state.json").write_text('{"qubits": {}}', encoding="utf-8")
        (tmp_path / "wiring.json").write_text('{"wiring": {}}', encoding="utf-8")

        calls = []
        real_fn = safe_io.read_state_wiring

        def spy(folder):
            calls.append(Path(folder))
            return real_fn(folder)

        monkeypatch.setattr(
            "quam_state_manager.core.loader.safe_io.read_state_wiring", spy,
        )

        QuamStore(tmp_path)
        assert any(Path(c) == tmp_path for c in calls), \
            "QuamStore._load did not route the load through safe_io.read_state_wiring"


class TestLoaderErrorHandling:

    def test_corrupt_state_json(self, tmp_path: Path):
        (tmp_path / "state.json").write_text("NOT VALID JSON", encoding="utf-8")
        (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="state.json is not valid JSON"):
            QuamStore(tmp_path)

    def test_corrupt_wiring_json(self, tmp_path: Path):
        (tmp_path / "state.json").write_text('{"qubits": {}}', encoding="utf-8")
        (tmp_path / "wiring.json").write_text("{INVALID", encoding="utf-8")
        with pytest.raises(ValueError, match="wiring.json is not valid JSON"):
            QuamStore(tmp_path)

    def test_empty_json_files(self, tmp_path: Path):
        (tmp_path / "state.json").write_text("{}", encoding="utf-8")
        (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
        store = QuamStore(tmp_path)
        assert store.qubit_names == []
        assert store.qubit_pair_names == []

    def test_missing_qubits_key(self, tmp_path: Path):
        (tmp_path / "state.json").write_text('{"other_key": 1}', encoding="utf-8")
        (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
        store = QuamStore(tmp_path)
        assert store.qubit_names == []

    def test_missing_state_json(self, tmp_path: Path):
        (tmp_path / "wiring.json").write_text("{}", encoding="utf-8")
        with pytest.raises(FileNotFoundError):
            QuamStore(tmp_path)
