"""Tests for quam_state_manager.core.modifier.Modifier.

Covers set_value, batch_set, undo, type coercion, rollback, and
integration with search_index and pointer_resolver cache.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier, _type_coerce
from quam_state_manager.core.search_index import SearchIndex

# ---------------------------------------------------------------------------
# Paths to real quam_state folders
# ---------------------------------------------------------------------------

_EXAMPLECHIP_ROOT = Path(r"<data-root>/qualibration_graphs/superconducting")
SMALL_FOLDER = _EXAMPLECHIP_ROOT / "quam_state"
LARGE_FOLDER = _EXAMPLECHIP_ROOT / "quam_states_arv" / "quam_state_examplechip_variantb"

has_small = SMALL_FOLDER.exists() and (SMALL_FOLDER / "state.json").exists()
has_large = LARGE_FOLDER.exists() and (LARGE_FOLDER / "state.json").exists()

skip_no_small = pytest.mark.skipif(not has_small, reason="Small quam_state not found")
skip_no_large = pytest.mark.skipif(not has_large, reason="Large quam_state not found")


# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

def _make_state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "anharmonicity": -220e6,
                "T1": 8834,
                "T2ramsey": 1.5e-6,
                "T2echo": None,
                "grid_location": "0,2",
                "xy": {
                    "RF_frequency": 6.25e9,
                    "opx_output": "#/wiring/qubits/qA1/xy/opx_output",
                    "operations": {
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40, "alpha": -1.75},
                    },
                },
                "resonator": {
                    "f_01": 7.64e9,
                    "operations": {"readout": {"amplitude": 0.042, "length": 1000}},
                },
                "z": {"joint_offset": 0.081, "flux_point": "joint"},
                "gate_fidelity": {"averaged": 0.991},
            },
        },
        "qubit_pairs": {},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring():
    return {
        "wiring": {
            "qubits": {
                "qA1": {
                    "xy": {"opx_output": "MW-FEM/1/2"},
                    "rr": {"opx_output": "MW-FEM/1/1"},
                    "z": {"opx_output": "LF-FEM/5/1"},
                },
            },
        },
        "network": {"host": "10.1.1.18", "cluster_name": "test"},
    }


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(synth_folder) -> QuamStore:
    return QuamStore(synth_folder)


@pytest.fixture
def modifier(store) -> Modifier:
    return Modifier(store)


@pytest.fixture
def modifier_with_index(store) -> Modifier:
    store.search_index = SearchIndex.build(store.merged)
    return Modifier(store)


def test_set_value_accepts_pointer_on_numeric_field():
    """A JSON ``#/`` pointer is a valid value for ANY field, so set_value accepts it
    with OR without coerce. This is the fix for the 'Convert to pointer' diagnostics
    fix reverting after Pull&apply: the change-log REPLAY re-applies edits with the
    default coerce=True, and the old behaviour (coerce rejects pointer→numeric) made
    the replay silently drop the pointer conversion, so the literal came back."""
    s = {"ports": {"mw_inputs": {"con1": {"1": {"1": {"downconverter_frequency": 7.0e9}}}}}}
    store = QuamStore.from_dicts(s, {"wiring": {}})
    mod = Modifier(store)
    path = "ports.mw_inputs.con1.1.1.downconverter_frequency"
    ptr = "#/ports/mw_outputs/con1/1/1/upconverter_frequency"
    # default coercion now PASSES the pointer through (previously it raised)
    mod.set_value(path, ptr)
    assert store.merged["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"] == ptr
    # coerce=False still writes it raw too
    mod.set_value(path, ptr, coerce=False)
    assert store.merged["ports"]["mw_inputs"]["con1"]["1"]["1"]["downconverter_frequency"] == ptr


# ---------------------------------------------------------------------------
# Type coercion
# ---------------------------------------------------------------------------


class TestTypeCoerce:
    def test_float_to_float(self):
        assert _type_coerce(1.0, 2) == 2.0
        assert isinstance(_type_coerce(1.0, 2), float)

    def test_float_from_string(self):
        assert _type_coerce(1.0, "3.14") == 3.14

    def test_int_to_int(self):
        # A fractional edit to an int field promotes to float rather than
        # silently truncating (the old int(20.7)==20 was data loss — see
        # tests/test_persistence_staleness.py::TestIntCoercionNoTruncation).
        assert _type_coerce(10, 20.7) == 20.7
        assert isinstance(_type_coerce(10, 20.7), float)
        assert isinstance(_type_coerce(10, 20), int)        # integral stays int
        assert isinstance(_type_coerce(10, 20.0), int)      # exactly integral → int

    def test_int_from_string(self):
        assert _type_coerce(10, "42") == 42

    def test_int_from_float_string(self):
        # fractional string → float (no truncation); integral string → int
        assert _type_coerce(10, "42.9") == 42.9
        assert isinstance(_type_coerce(10, "42.9"), float)
        assert _type_coerce(10, "42.0") == 42
        assert isinstance(_type_coerce(10, "42.0"), int)

    def test_str_to_str(self):
        assert _type_coerce("hello", 42) == "42"

    def test_bool_coerce(self):
        assert _type_coerce(True, "false") is False
        assert _type_coerce(False, "true") is True
        assert _type_coerce(True, "yes") is True
        assert _type_coerce(False, "no") is False

    def test_bool_coerce_on_off_and_synonyms(self):
        # on/off (Phase F) plus t/f, y/n, 1/0, case- + whitespace-insensitive.
        for truthy in ("on", "ON", " On ", "t", "y", "1", "TRUE", "Yes"):
            assert _type_coerce(False, truthy) is True
        for falsy in ("off", "OFF", " Off ", "f", "n", "0", "FALSE", "No"):
            assert _type_coerce(True, falsy) is False

    def test_pointer_passthrough_for_any_field(self):
        # A #/ , #./ , #../ pointer is valid for any field → never coerced/rejected,
        # so a literal→pointer convert survives a replay (default coerce=True).
        ptr = "#/ports/mw_outputs/con1/1/1/upconverter_frequency"
        assert _type_coerce(7.46e9, ptr) == ptr      # float field
        assert _type_coerce(123, ptr) == ptr          # int field
        assert _type_coerce("x", ptr) == ptr          # str field
        assert _type_coerce(None, ptr) == ptr
        assert _type_coerce(1.0, "#./self_ref") == "#./self_ref"
        assert _type_coerce(1.0, "#../sibling") == "#../sibling"
        # a literal string that merely starts with '#' is NOT a pointer → still coerced
        assert _type_coerce("note", "#hashtag") == "#hashtag"   # str→str, unchanged
        with pytest.raises(TypeError):
            _type_coerce(1.0, "#notapointer")     # float field, non-pointer string → rejected

    def test_bool_rejects_unrecognized_string(self):
        # A typo must NOT silently become True (old bool("flase") == True footgun).
        with pytest.raises(TypeError):
            _type_coerce(True, "flase")
        with pytest.raises(TypeError):
            _type_coerce(False, "maybe")

    def test_none_accepts_anything(self):
        assert _type_coerce(None, 42) == 42
        assert _type_coerce(None, "hello") == "hello"

    def test_list_accepts_list(self):
        assert _type_coerce([1, 2], [3, 4]) == [3, 4]

    def test_list_rejects_non_list(self):
        with pytest.raises(TypeError):
            _type_coerce([1, 2], "not a list")

    def test_dict_accepts_dict(self):
        assert _type_coerce({"a": 1}, {"b": 2}) == {"b": 2}

    def test_dict_rejects_non_dict(self):
        with pytest.raises(TypeError):
            _type_coerce({"a": 1}, "not a dict")

    def test_float_rejects_invalid(self):
        with pytest.raises(TypeError):
            _type_coerce(1.0, "abc")

    def test_int_rejects_invalid(self):
        with pytest.raises(TypeError):
            _type_coerce(10, "abc")


# ---------------------------------------------------------------------------
# set_value
# ---------------------------------------------------------------------------


class TestSetValue:
    def test_set_float_value(self, modifier):
        entry = modifier.set_value("qubits.qA1.f_01", 6.3e9)
        assert entry.old_value == 6.25e9
        assert entry.new_value == 6.3e9
        assert entry.source_file == "state"
        assert modifier.store.merged["qubits"]["qA1"]["f_01"] == 6.3e9
        assert modifier.store.state["qubits"]["qA1"]["f_01"] == 6.3e9

    def test_set_int_value(self, modifier):
        entry = modifier.set_value("qubits.qA1.T1", 9000)
        assert entry.old_value == 8834
        assert entry.new_value == 9000
        assert isinstance(modifier.store.merged["qubits"]["qA1"]["T1"], int)

    def test_set_string_value(self, modifier):
        entry = modifier.set_value("qubits.qA1.grid_location", "1,3")
        assert entry.old_value == "0,2"
        assert entry.new_value == "1,3"

    def test_set_none_field(self, modifier):
        entry = modifier.set_value("qubits.qA1.T2echo", 2.5e-6)
        assert entry.old_value is None
        assert entry.new_value == 2.5e-6

    def test_set_nested_value(self, modifier):
        path = "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
        entry = modifier.set_value(path, 0.12)
        assert entry.old_value == 0.115
        assert abs(entry.new_value - 0.12) < 1e-10
        assert modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]["x180_DragCosine"]["amplitude"] == 0.12
        assert modifier.store.state["qubits"]["qA1"]["xy"]["operations"]["x180_DragCosine"]["amplitude"] == 0.12

    def test_set_wiring_value(self, modifier):
        path = "wiring.qubits.qA1.xy.opx_output"
        entry = modifier.set_value(path, "MW-FEM/2/5")
        assert entry.source_file == "wiring"
        assert entry.old_value == "MW-FEM/1/2"
        assert modifier.store.merged["wiring"]["qubits"]["qA1"]["xy"]["opx_output"] == "MW-FEM/2/5"
        assert modifier.store.wiring["wiring"]["qubits"]["qA1"]["xy"]["opx_output"] == "MW-FEM/2/5"

    def test_set_pointer_string_directly(self, modifier):
        path = "qubits.qA1.xy.opx_output"
        old = modifier.store.get_value(path)
        assert old == "#/wiring/qubits/qA1/xy/opx_output"
        entry = modifier.set_value(path, "#/wiring/qubits/qA1/xy/new_port")
        assert entry.new_value == "#/wiring/qubits/qA1/xy/new_port"

    def test_change_log_grows(self, modifier):
        assert len(modifier.store.change_log) == 0
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        assert len(modifier.store.change_log) == 1
        modifier.set_value("qubits.qA1.T1", 9000)
        assert len(modifier.store.change_log) == 2

    def test_invalid_path_raises(self, modifier):
        with pytest.raises(KeyError):
            modifier.set_value("qubits.nonexistent.f_01", 1.0)

    def test_type_mismatch_raises(self, modifier):
        with pytest.raises(TypeError):
            modifier.set_value("qubits.qA1.f_01", "not_a_number")


# ---------------------------------------------------------------------------
# set_value with search index
# ---------------------------------------------------------------------------


class TestSetValueWithIndex:
    def test_index_updated(self, modifier_with_index):
        mod = modifier_with_index
        idx = mod.store.search_index

        results_before = idx.search("6250000000")
        mod.set_value("qubits.qA1.f_01", 6.3e9)
        results_after = idx.search("6300000000")

        found_paths = [r.dot_path for r in results_after]
        assert "qubits.qA1.f_01" in found_paths

    def test_old_value_removed_from_index(self, modifier_with_index):
        mod = modifier_with_index
        idx = mod.store.search_index

        mod.set_value("qubits.qA1.T1", 9999)
        results = idx.search("8834")
        found_paths = [r.dot_path for r in results]
        assert "qubits.qA1.T1" not in found_paths


# ---------------------------------------------------------------------------
# batch_set
# ---------------------------------------------------------------------------


class TestBatchSet:
    def test_batch_success(self, modifier):
        entries = modifier.batch_set({
            "qubits.qA1.f_01": 6.3e9,
            "qubits.qA1.T1": 9000,
            "qubits.qA1.grid_location": "1,3",
        })
        assert len(entries) == 3
        assert modifier.store.merged["qubits"]["qA1"]["f_01"] == 6.3e9
        assert modifier.store.merged["qubits"]["qA1"]["T1"] == 9000
        assert modifier.store.merged["qubits"]["qA1"]["grid_location"] == "1,3"
        assert len(modifier.store.change_log) == 3

    def test_batch_rollback_on_failure(self, modifier):
        original_f01 = modifier.store.merged["qubits"]["qA1"]["f_01"]
        with pytest.raises(KeyError):
            modifier.batch_set({
                "qubits.qA1.f_01": 6.3e9,
                "qubits.qA1.nonexistent.field": 42,
            })
        assert modifier.store.merged["qubits"]["qA1"]["f_01"] == original_f01
        assert len(modifier.store.change_log) == 0

    def test_batch_rollback_on_type_error(self, modifier):
        original_f01 = modifier.store.merged["qubits"]["qA1"]["f_01"]
        original_t1 = modifier.store.merged["qubits"]["qA1"]["T1"]
        with pytest.raises(TypeError):
            modifier.batch_set({
                "qubits.qA1.f_01": 6.3e9,
                "qubits.qA1.T1": 9000,
                "qubits.qA1.gate_fidelity": "not_a_dict",
            })
        assert modifier.store.merged["qubits"]["qA1"]["f_01"] == original_f01
        assert modifier.store.merged["qubits"]["qA1"]["T1"] == original_t1
        assert len(modifier.store.change_log) == 0

    def test_batch_with_index(self, modifier_with_index):
        mod = modifier_with_index
        entries = mod.batch_set({
            "qubits.qA1.f_01": 6.3e9,
            "qubits.qA1.T1": 9999,
        })
        assert len(entries) == 2
        results = mod.store.search_index.search("9999")
        found_paths = [r.dot_path for r in results]
        assert "qubits.qA1.T1" in found_paths

    def test_batch_empty(self, modifier):
        entries = modifier.batch_set({})
        assert entries == []
        assert len(modifier.store.change_log) == 0

    def test_batch_rollback_keeps_unrestorable_entries_in_log(self, modifier, monkeypatch):
        """If a rollback write itself raises, the entry must stay in change_log.

        Otherwise the log claims a change was reverted that wasn't, and the
        next save() would silently drop the actual on-disk change.
        """
        from quam_state_manager.core import modifier as mod_module

        original_navigate = mod_module._navigate_to_parent
        navigate_calls = {"count": 0}

        def failing_navigate(root, dot_path):
            navigate_calls["count"] += 1
            # Let set_value navigate normally; fail only in the FIRST rollback call.
            # Rollback iterates in reverse so the first one is the last entry.
            # In our scenario set_value is called twice (succeed, succeed),
            # then a third call fails which triggers rollback of 2 entries.
            # So navigates: 1 (set f_01), 2 (set T1), 3 (set bogus -> raises KeyError before getting parent),
            # then rollback navigates: 4 (T1) -> we make this fail.
            if navigate_calls["count"] == 4:
                raise RuntimeError("simulated rollback failure")
            return original_navigate(root, dot_path)

        monkeypatch.setattr(mod_module, "_navigate_to_parent", failing_navigate)

        with pytest.raises(KeyError):
            modifier.batch_set({
                "qubits.qA1.f_01": 6.3e9,
                "qubits.qA1.T1": 9000,
                "qubits.qA1.nonexistent": 42,
            })
        # The T1 rollback failed -- so that entry stays in the change log,
        # honest about the fact that the value was changed and not reverted.
        log_paths = [e.dot_path for e in modifier.store.change_log]
        assert "qubits.qA1.T1" in log_paths
        # f_01 rolled back fine -> removed from log.
        assert "qubits.qA1.f_01" not in log_paths


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------


class TestUndo:
    def test_undo_single(self, modifier):
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        assert modifier.store.merged["qubits"]["qA1"]["f_01"] == 6.3e9

        undone = modifier.undo()
        assert undone is not None
        assert undone.dot_path == "qubits.qA1.f_01"
        assert modifier.store.merged["qubits"]["qA1"]["f_01"] == 6.25e9
        assert modifier.store.state["qubits"]["qA1"]["f_01"] == 6.25e9

    def test_undo_restores_both_dicts(self, modifier):
        modifier.set_value("wiring.qubits.qA1.xy.opx_output", "MW-FEM/2/5")
        modifier.undo()
        assert modifier.store.merged["wiring"]["qubits"]["qA1"]["xy"]["opx_output"] == "MW-FEM/1/2"
        assert modifier.store.wiring["wiring"]["qubits"]["qA1"]["xy"]["opx_output"] == "MW-FEM/1/2"

    def test_undo_pops_from_log(self, modifier):
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        modifier.set_value("qubits.qA1.T1", 9000)
        assert len(modifier.store.change_log) == 2

        modifier.undo()
        assert len(modifier.store.change_log) == 1
        assert modifier.store.change_log[0].dot_path == "qubits.qA1.f_01"

    def test_undo_empty_returns_none(self, modifier):
        assert modifier.undo() is None

    def test_undo_multiple(self, modifier):
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        modifier.set_value("qubits.qA1.T1", 9000)

        modifier.undo()
        assert modifier.store.merged["qubits"]["qA1"]["T1"] == 8834

        modifier.undo()
        assert modifier.store.merged["qubits"]["qA1"]["f_01"] == 6.25e9

    def test_undo_with_index(self, modifier_with_index):
        mod = modifier_with_index
        mod.set_value("qubits.qA1.T1", 9999)
        mod.undo()
        results = mod.store.search_index.search("8834")
        found_paths = [r.dot_path for r in results]
        assert "qubits.qA1.T1" in found_paths


# ---------------------------------------------------------------------------
# get_change_log
# ---------------------------------------------------------------------------


class TestGetChangeLog:
    def test_returns_copy(self, modifier):
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        log = modifier.get_change_log()
        log.clear()
        assert len(modifier.store.change_log) == 1

    def test_order(self, modifier):
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        modifier.set_value("qubits.qA1.T1", 9000)
        log = modifier.get_change_log()
        assert log[0].dot_path == "qubits.qA1.f_01"
        assert log[1].dot_path == "qubits.qA1.T1"


# ---------------------------------------------------------------------------
# has_unsaved_changes
# ---------------------------------------------------------------------------


class TestHasUnsavedChanges:
    def test_initially_false(self, modifier):
        assert modifier.has_unsaved_changes is False

    def test_true_after_edit(self, modifier):
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        assert modifier.has_unsaved_changes is True


# ---------------------------------------------------------------------------
# Source file determination
# ---------------------------------------------------------------------------


class TestSourceFile:
    def test_state_paths(self, modifier):
        entry = modifier.set_value("qubits.qA1.f_01", 6.3e9)
        assert entry.source_file == "state"

    def test_wiring_paths(self, modifier):
        entry = modifier.set_value("wiring.qubits.qA1.xy.opx_output", "MW-FEM/2/5")
        assert entry.source_file == "wiring"

    def test_network_paths(self, modifier):
        entry = modifier.set_value("network.host", "10.2.2.20")
        assert entry.source_file == "wiring"


# ---------------------------------------------------------------------------
# Pointer cache invalidation
# ---------------------------------------------------------------------------


class TestCacheInvalidation:
    def test_resolved_value_updates_after_set(self, modifier):
        store = modifier.store
        resolved_before = store.resolve_value("qubits.qA1.xy.opx_output")
        assert resolved_before == "MW-FEM/1/2"

        modifier.set_value("wiring.qubits.qA1.xy.opx_output", "MW-FEM/2/5")
        resolved_after = store.resolve_value("qubits.qA1.xy.opx_output")
        assert resolved_after == "MW-FEM/2/5"


# ---------------------------------------------------------------------------
# Real data tests
# ---------------------------------------------------------------------------


@skip_no_large
class TestLargeRealData:
    def test_set_and_undo_qubit_frequency(self):
        store = QuamStore(LARGE_FOLDER)
        mod = Modifier(store)

        original = store.merged["qubits"]["qA1"]["f_01"]
        mod.set_value("qubits.qA1.f_01", 6.5e9)
        assert store.merged["qubits"]["qA1"]["f_01"] == 6.5e9

        mod.undo()
        assert store.merged["qubits"]["qA1"]["f_01"] == original

    def test_batch_edit_multiple_qubits(self):
        store = QuamStore(LARGE_FOLDER)
        mod = Modifier(store)

        updates = {}
        for name in store.qubit_names[:5]:
            updates[f"qubits.{name}.T1"] = 10000
        entries = mod.batch_set(updates)
        assert len(entries) == 5
        for name in store.qubit_names[:5]:
            assert store.merged["qubits"][name]["T1"] == 10000

    def test_set_readout_amplitude(self):
        store = QuamStore(LARGE_FOLDER)
        mod = Modifier(store)

        path = "qubits.qA1.resonator.operations.readout.amplitude"
        original = store.get_value(path)
        mod.set_value(path, 0.05)
        assert store.get_value(path) == 0.05
        assert store.state["qubits"]["qA1"]["resonator"]["operations"]["readout"]["amplitude"] == 0.05

        mod.undo()
        assert store.get_value(path) == original

    def test_set_with_search_index(self):
        store = QuamStore(LARGE_FOLDER)
        store.search_index = SearchIndex.build(store.merged)
        mod = Modifier(store)

        mod.set_value("qubits.qA1.f_01", 6.5e9)
        results = store.search_index.search("6500000000")
        found_paths = [r.dot_path for r in results]
        assert "qubits.qA1.f_01" in found_paths

    def test_batch_rollback_preserves_original(self):
        store = QuamStore(LARGE_FOLDER)
        mod = Modifier(store)

        originals = {f"qubits.{n}.f_01": store.get_value(f"qubits.{n}.f_01") for n in store.qubit_names[:3]}
        bad_updates = {**{k: 7e9 for k in originals}, "qubits.nonexistent_qubit.f_01": 1.0}
        with pytest.raises(KeyError):
            mod.batch_set(bad_updates)

        for path, val in originals.items():
            assert store.get_value(path) == val
        assert len(store.change_log) == 0


# ---------------------------------------------------------------------------
# create_subtree
# ---------------------------------------------------------------------------


CZ_TEMPLATE = {
    "fidelity": {},
    "flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
    "coupler_flux_pulse": {"amplitude": 0.1, "length": 100},
    "phase_shift_control": 0.0,
    "phase_shift_target": 0.0,
}


def _state_with_pair():
    state = _make_state()
    state["qubits"]["qA2"] = {"id": "qA2", "f_01": 6.3e9}
    state["active_qubit_names"] = ["qA1", "qA2"]
    state["qubit_pairs"] = {
        "qA1-A2": {
            "id": "qA1-A2",
            "qubit_control": "#/qubits/qA1",
            "qubit_target": "#/qubits/qA2",
            "macros": {},
        }
    }
    return state


@pytest.fixture
def modifier_with_pair(tmp_path: Path) -> Modifier:
    (tmp_path / "state.json").write_text(json.dumps(_state_with_pair(), indent=2), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2), encoding="utf-8")
    store = QuamStore(tmp_path)
    return Modifier(store)


class TestCreateSubtree:
    def test_creates_nested_dict(self, modifier_with_pair):
        entry = modifier_with_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        assert entry.created is True
        assert entry.old_value is None
        assert entry.new_value == CZ_TEMPLATE
        assert entry.source_file == "state"

        macros = modifier_with_pair.store.merged["qubit_pairs"]["qA1-A2"]["macros"]
        assert "cz_v3" in macros
        assert macros["cz_v3"]["flux_pulse_qubit"]["amplitude"] == 0.05
        # source dict is updated too (nested dicts are shared with merged)
        assert (
            modifier_with_pair.store.state["qubit_pairs"]["qA1-A2"]["macros"]["cz_v3"]
            == CZ_TEMPLATE
        )

    def test_creates_scalar_leaf(self, modifier_with_pair):
        entry = modifier_with_pair.create_subtree("qubits.qA1.custom_note", "hello")
        assert entry.created is True
        assert modifier_with_pair.store.merged["qubits"]["qA1"]["custom_note"] == "hello"

    def test_creates_top_level_key(self, modifier_with_pair):
        modifier_with_pair.create_subtree("new_section", {"a": 1})
        assert modifier_with_pair.store.merged["new_section"] == {"a": 1}
        assert modifier_with_pair.store.state["new_section"] == {"a": 1}
        # not in wiring (it's a state key)
        assert "new_section" not in modifier_with_pair.store.wiring

    def test_rejects_existing_key(self, modifier_with_pair):
        with pytest.raises(KeyError, match="already exists"):
            modifier_with_pair.create_subtree("qubits.qA1.f_01", 1.0)

    def test_rejects_missing_parent(self, modifier_with_pair):
        with pytest.raises(KeyError, match="not found"):
            modifier_with_pair.create_subtree(
                "qubit_pairs.nonexistent.macros.cz_v3", CZ_TEMPLATE
            )

    def test_rejects_empty_path(self, modifier_with_pair):
        with pytest.raises(ValueError):
            modifier_with_pair.create_subtree("", "x")

    def test_logs_one_entry(self, modifier_with_pair):
        modifier_with_pair.create_subtree("qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE)
        log = modifier_with_pair.get_change_log()
        assert len(log) == 1
        assert log[0].created is True

    def test_deep_copies_value(self, modifier_with_pair):
        template = {"flux_pulse_qubit": {"amplitude": 0.05}}
        modifier_with_pair.create_subtree("qubit_pairs.qA1-A2.macros.cz_v3", template)
        # Mutating the input dict must not affect the store
        template["flux_pulse_qubit"]["amplitude"] = 999.0
        assert (
            modifier_with_pair.store.merged["qubit_pairs"]["qA1-A2"]["macros"]["cz_v3"][
                "flux_pulse_qubit"
            ]["amplitude"]
            == 0.05
        )

    def test_value_can_be_edited_after_creation(self, modifier_with_pair):
        modifier_with_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        modifier_with_pair.set_value(
            "qubit_pairs.qA1-A2.macros.cz_v3.flux_pulse_qubit.amplitude", 0.123
        )
        assert (
            modifier_with_pair.store.merged["qubit_pairs"]["qA1-A2"]["macros"]["cz_v3"][
                "flux_pulse_qubit"
            ]["amplitude"]
            == 0.123
        )


class TestUndoCreate:
    def test_undo_deletes_created_subtree(self, modifier_with_pair):
        modifier_with_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        reverted = modifier_with_pair.undo()
        assert reverted is not None
        assert reverted.created is True
        macros = modifier_with_pair.store.merged["qubit_pairs"]["qA1-A2"]["macros"]
        assert "cz_v3" not in macros
        # change log is cleared
        assert modifier_with_pair.has_unsaved_changes is False

    def test_undo_recreates_after_subsequent_edit(self, modifier_with_pair):
        modifier_with_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        modifier_with_pair.set_value(
            "qubit_pairs.qA1-A2.macros.cz_v3.flux_pulse_qubit.amplitude", 0.222
        )
        # Undo the set_value first
        modifier_with_pair.undo()
        # The created subtree is still there with original amplitude
        assert (
            modifier_with_pair.store.merged["qubit_pairs"]["qA1-A2"]["macros"]["cz_v3"][
                "flux_pulse_qubit"
            ]["amplitude"]
            == 0.05
        )
        # Now undo the creation
        modifier_with_pair.undo()
        assert (
            "cz_v3"
            not in modifier_with_pair.store.merged["qubit_pairs"]["qA1-A2"]["macros"]
        )

    def test_discard_deletes_created(self, modifier_with_pair):
        modifier_with_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        modifier_with_pair.set_value("qubits.qA1.f_01", 6.5e9)
        # Discard the create (index 0), keep the set_value
        modifier_with_pair.discard(0)
        assert (
            "cz_v3"
            not in modifier_with_pair.store.merged["qubit_pairs"]["qA1-A2"]["macros"]
        )
        # set_value entry survives
        assert modifier_with_pair.store.merged["qubits"]["qA1"]["f_01"] == 6.5e9

    def test_recreate_after_undo(self, modifier_with_pair):
        modifier_with_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        modifier_with_pair.undo()
        # Should be re-creatable since key was deleted
        entry = modifier_with_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        assert entry.created is True


class TestCreateSubtreeSearchIndex:
    @pytest.fixture
    def modifier_with_index_and_pair(self, tmp_path: Path) -> Modifier:
        (tmp_path / "state.json").write_text(
            json.dumps(_state_with_pair(), indent=2), encoding="utf-8"
        )
        (tmp_path / "wiring.json").write_text(
            json.dumps(_make_wiring(), indent=2), encoding="utf-8"
        )
        store = QuamStore(tmp_path)
        store.search_index = SearchIndex.build(store.merged)
        return Modifier(store)

    def test_leaves_appear_in_search_index(self, modifier_with_index_and_pair):
        modifier_with_index_and_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        index = modifier_with_index_and_pair.store.search_index
        assert "qubit_pairs.qA1-A2.macros.cz_v3.flux_pulse_qubit.amplitude" in index.path_to_idx
        assert (
            "qubit_pairs.qA1-A2.macros.cz_v3.phase_shift_control" in index.path_to_idx
        )
        # Empty dict {} fidelity is preserved as a leaf
        assert "qubit_pairs.qA1-A2.macros.cz_v3.fidelity" in index.path_to_idx

    def test_undo_removes_leaves_from_search_index(self, modifier_with_index_and_pair):
        modifier_with_index_and_pair.create_subtree(
            "qubit_pairs.qA1-A2.macros.cz_v3", CZ_TEMPLATE
        )
        modifier_with_index_and_pair.undo()
        index = modifier_with_index_and_pair.store.search_index
        assert (
            "qubit_pairs.qA1-A2.macros.cz_v3.flux_pulse_qubit.amplitude"
            not in index.path_to_idx
        )


class TestUndoGroup:
    """Ctrl+Z undoes one USER ACTION atomically: a batch/rename undoes as a unit
    (LIFO within the group); a standalone edit undoes on its own."""

    def test_single_edit_undoes_one(self, modifier):
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        assert len(modifier.store.change_log) == 1
        reverted = modifier.undo_group()
        assert len(reverted) == 1
        assert modifier.store.merged["qubits"]["qA1"]["f_01"] == 6.25e9
        assert modifier.store.change_log == []

    def test_batch_undoes_atomically(self, modifier):
        modifier.batch_set({
            "qubits.qA1.f_01": 6.3e9,
            "qubits.qA1.T1": 9000,
            "qubits.qA1.anharmonicity": -210e6,
        })
        assert len(modifier.store.change_log) == 3
        gid = modifier.store.change_log[-1].group_id
        assert gid is not None
        assert all(e.group_id == gid for e in modifier.store.change_log)
        reverted = modifier.undo_group()   # ONE Ctrl+Z
        assert len(reverted) == 3
        assert modifier.store.change_log == []
        q = modifier.store.merged["qubits"]["qA1"]
        assert q["f_01"] == 6.25e9 and q["T1"] == 8834 and q["anharmonicity"] == -220e6

    def test_batch_then_single_undo_peels_single_first(self, modifier):
        modifier.batch_set({"qubits.qA1.f_01": 6.3e9, "qubits.qA1.T1": 9000})
        modifier.set_value("qubits.qA1.T2ramsey", 2.0e-6)   # standalone, later
        # First Ctrl+Z removes only the standalone edit…
        assert len(modifier.undo_group()) == 1
        assert modifier.store.merged["qubits"]["qA1"]["T2ramsey"] == 1.5e-6
        assert len(modifier.store.change_log) == 2
        # …the next removes the whole batch.
        assert len(modifier.undo_group()) == 2
        assert modifier.store.change_log == []

    def test_empty_log_returns_empty(self, modifier):
        assert modifier.undo_group() == []

    def test_new_group_id_is_unique(self, modifier):
        a = modifier.new_group_id()
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        b = modifier.new_group_id()
        assert a != b
