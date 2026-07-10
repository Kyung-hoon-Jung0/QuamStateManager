"""Tests for Modifier.delete_subtree / rename_subtree and the deleted-entry
undo/discard semantics (ChangeEntry.deleted).

Also regression-tests the undo() ordering fix (revert-then-pop): a failing
revert must leave the entry in the change log instead of silently dropping
both the entry and the revert.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.saver import Saver
from quam_state_manager.core.search_index import SearchIndex


def _make_state():
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "f_01": 6.25e9,
                "xy": {
                    "operations": {
                        "x180_DragCosine": {
                            "amplitude": 0.115, "length": 40, "alpha": -1.75,
                            "__class__": "quam.components.pulses.DragCosinePulse",
                        },
                        "x90_DragCosine": {
                            "amplitude": 0.057,
                            "length": "#../x180_DragCosine/length",
                            "__class__": "quam.components.pulses.DragCosinePulse",
                        },
                        "x180": "#./x180_DragCosine",
                        "saturation": {"amplitude": 0.004, "length": 20000},
                    },
                },
                "resonator": {
                    "operations": {"readout": {"amplitude": 0.042, "length": 1000}},
                },
            },
        },
        "qubit_pairs": {
            "qA1-qA2": {
                "macros": {
                    "cz_unipolar": {
                        "flux_pulse_qubit": {"amplitude": 0.05, "length": 100},
                        "coupler_flux_pulse": None,
                    },
                    "cz": "#./cz_unipolar",
                },
            },
        },
        "extras": {"scratch": 1},
        "active_qubit_names": ["qA1"],
    }


def _make_wiring():
    return {
        "wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}},
        "network": {"host": "10.1.1.18", "cluster_name": "test"},
    }


@pytest.fixture
def synth_folder(tmp_path: Path) -> Path:
    (tmp_path / "state.json").write_text(json.dumps(_make_state(), indent=2),
                                         encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(_make_wiring(), indent=2),
                                          encoding="utf-8")
    return tmp_path


@pytest.fixture
def store(synth_folder) -> QuamStore:
    return QuamStore(synth_folder)


@pytest.fixture
def modifier(store) -> Modifier:
    return Modifier(store)


@pytest.fixture
def modifier_with_index(store) -> Modifier:
    store.search_index = SearchIndex.build(
        store.merged, wiring_keys=set(store.wiring.keys()))
    return Modifier(store)


OPS = "qubits.qA1.xy.operations"


# ---------------------------------------------------------------------------
# delete_subtree
# ---------------------------------------------------------------------------

class TestDeleteSubtree:
    def test_delete_dict_subtree(self, modifier):
        entry = modifier.delete_subtree(f"{OPS}.saturation")
        assert entry.deleted is True and entry.created is False
        assert entry.new_value is None
        assert entry.old_value == {"amplitude": 0.004, "length": 20000}
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "saturation" not in ops
        # source dict too (shared nested containers)
        state_ops = modifier.store.state["qubits"]["qA1"]["xy"]["operations"]
        assert "saturation" not in state_ops

    def test_delete_string_alias(self, modifier):
        # a string-leaf operation (alias) — must not stringify or wrap
        entry = modifier.delete_subtree(f"{OPS}.x180")
        assert entry.deleted and entry.old_value == "#./x180_DragCosine"
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "x180" not in ops

    def test_delete_top_level_key(self, modifier):
        entry = modifier.delete_subtree("extras")
        assert entry.deleted
        assert "extras" not in modifier.store.merged
        assert "extras" not in modifier.store.state

    def test_delete_missing_path_raises(self, modifier):
        with pytest.raises(KeyError):
            modifier.delete_subtree(f"{OPS}.nope")
        with pytest.raises(KeyError):
            modifier.delete_subtree("qubits.qZZ.xy.operations.x")

    def test_delete_invalid_path_raises(self, modifier):
        with pytest.raises(ValueError):
            modifier.delete_subtree("")
        with pytest.raises(ValueError):
            modifier.delete_subtree("qubits..x")

    def test_old_value_is_deep_copy(self, modifier):
        entry = modifier.delete_subtree(f"{OPS}.x180_DragCosine")
        # recreate the key and mutate the live tree; the log copy must not move
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        ops["x180_DragCosine"] = {"amplitude": 999}
        assert entry.old_value["amplitude"] == 0.115

    def test_search_index_leaves_removed(self, modifier_with_index):
        index = modifier_with_index.store.search_index
        assert f"{OPS}.x180_DragCosine.amplitude" in index.path_to_idx
        modifier_with_index.delete_subtree(f"{OPS}.x180_DragCosine")
        assert f"{OPS}.x180_DragCosine.amplitude" not in index.path_to_idx
        assert f"{OPS}.x180_DragCosine.length" not in index.path_to_idx

    def test_pointer_cache_cleared(self, modifier):
        store = modifier.store
        store.resolve_pointer("#./x180_DragCosine",
                              ("qubits", "qA1", "xy", "operations", "x180"))
        modifier.delete_subtree(f"{OPS}.x180_DragCosine")
        assert not store._pointer_cache

    def test_mutation_seq_increments(self, modifier):
        seq0 = modifier.store.mutation_seq
        modifier.delete_subtree(f"{OPS}.saturation")
        assert modifier.store.mutation_seq == seq0 + 1

    def test_delete_none_valued_key(self, modifier):
        # coupler_flux_pulse is None — still a key, still deletable
        entry = modifier.delete_subtree(
            "qubit_pairs.qA1-qA2.macros.cz_unipolar.coupler_flux_pulse")
        assert entry.deleted and entry.old_value is None
        macro = modifier.store.merged["qubit_pairs"]["qA1-qA2"]["macros"]["cz_unipolar"]
        assert "coupler_flux_pulse" not in macro


# ---------------------------------------------------------------------------
# undo / discard of deletions
# ---------------------------------------------------------------------------

class TestUndoDelete:
    def test_undo_restores_subtree(self, modifier):
        before = json.dumps(modifier.store.merged, sort_keys=True)
        modifier.delete_subtree(f"{OPS}.x180_DragCosine")
        reverted = modifier.undo()
        assert reverted.deleted
        assert json.dumps(modifier.store.merged, sort_keys=True) == before
        assert not modifier.store.change_log

    def test_undo_restores_string_alias(self, modifier):
        modifier.delete_subtree(f"{OPS}.x180")
        modifier.undo()
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert ops["x180"] == "#./x180_DragCosine"

    def test_undo_restores_top_level_in_both_dicts(self, modifier):
        modifier.delete_subtree("extras")
        modifier.undo()
        assert modifier.store.merged["extras"] == {"scratch": 1}
        assert modifier.store.state["extras"] == {"scratch": 1}

    def test_undo_restores_fresh_copy_no_aliasing(self, modifier):
        entry = modifier.delete_subtree(f"{OPS}.saturation")
        modifier.undo()
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        ops["saturation"]["amplitude"] = 123.0
        # the (now popped) entry's old_value must not have moved
        assert entry.old_value["amplitude"] == 0.004

    def test_undo_reregisters_search_index(self, modifier_with_index):
        index = modifier_with_index.store.search_index
        modifier_with_index.delete_subtree(f"{OPS}.x180_DragCosine")
        modifier_with_index.undo()
        assert f"{OPS}.x180_DragCosine.amplitude" in index.path_to_idx

    def test_discard_delete_by_index(self, modifier):
        modifier.set_value("qubits.qA1.f_01", 6.3e9)
        modifier.delete_subtree(f"{OPS}.saturation")
        discarded = modifier.discard(1)
        assert discarded.deleted
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "saturation" in ops
        # the unrelated edit is still pending
        assert len(modifier.store.change_log) == 1

    def test_restore_refuses_to_clobber_recreated_key(self, modifier):
        modifier.delete_subtree(f"{OPS}.saturation")
        modifier.create_subtree(f"{OPS}.saturation", {"amplitude": 0.9, "length": 8})
        # discarding the *delete* (index 0) would overwrite the new key — refuse
        with pytest.raises(KeyError):
            modifier.discard(0)
        # the log still holds both entries (revert-then-pop kept it)
        assert len(modifier.store.change_log) == 2
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert ops["saturation"]["amplitude"] == 0.9

    def test_delete_create_undo_undo_roundtrip(self, modifier):
        before = json.dumps(modifier.store.merged, sort_keys=True)
        modifier.delete_subtree(f"{OPS}.saturation")
        modifier.create_subtree(f"{OPS}.saturation", {"amplitude": 0.9, "length": 8})
        modifier.undo()  # removes the recreated key
        modifier.undo()  # restores the original
        assert json.dumps(modifier.store.merged, sort_keys=True) == before

    def test_create_edit_delete_undo_chain(self, modifier):
        before = json.dumps(modifier.store.merged, sort_keys=True)
        modifier.create_subtree(f"{OPS}.probe", {"amplitude": 0.1, "length": 16})
        modifier.set_value(f"{OPS}.probe.amplitude", 0.2)
        modifier.delete_subtree(f"{OPS}.probe")
        modifier.undo()  # restore probe (with the edited amplitude)
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert ops["probe"]["amplitude"] == 0.2
        modifier.undo()  # undo the edit
        assert ops["probe"]["amplitude"] == 0.1
        modifier.undo()  # undo the create
        assert json.dumps(modifier.store.merged, sort_keys=True) == before


class TestUndoOrderRegression:
    def test_failed_revert_keeps_entry_in_log(self, modifier):
        """L2 regression: undo() must revert-then-pop, so a failing revert
        leaves the log intact instead of losing the entry."""
        modifier.delete_subtree(f"{OPS}.saturation")
        # Sabotage: re-create the key so the deleted-restore clobber-guard fires.
        modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]["saturation"] = {"x": 1}
        with pytest.raises(KeyError):
            modifier.undo()
        assert len(modifier.store.change_log) == 1
        assert modifier.store.change_log[0].deleted


# ---------------------------------------------------------------------------
# rename_subtree
# ---------------------------------------------------------------------------

class TestRenameSubtree:
    def test_rename_basic(self, modifier):
        entries = modifier.rename_subtree(f"{OPS}.saturation", f"{OPS}.sat_long")
        assert len(entries) == 2
        assert entries[0].created and entries[0].dot_path == f"{OPS}.sat_long"
        assert entries[1].deleted and entries[1].dot_path == f"{OPS}.saturation"
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "saturation" not in ops
        assert ops["sat_long"] == {"amplitude": 0.004, "length": 20000}

    def test_rename_string_alias(self, modifier):
        modifier.rename_subtree(f"{OPS}.x180", f"{OPS}.x180_alias")
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert ops["x180_alias"] == "#./x180_DragCosine"

    def test_rename_collision_raises_before_destroying(self, modifier):
        with pytest.raises(KeyError):
            modifier.rename_subtree(f"{OPS}.saturation", f"{OPS}.x180_DragCosine")
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "saturation" in ops  # nothing was destroyed
        assert not modifier.store.change_log

    def test_rename_same_path_raises(self, modifier):
        with pytest.raises(ValueError):
            modifier.rename_subtree(f"{OPS}.saturation", f"{OPS}.saturation")

    def test_rename_missing_source_raises(self, modifier):
        with pytest.raises(KeyError):
            modifier.rename_subtree(f"{OPS}.nope", f"{OPS}.new")
        assert not modifier.store.change_log

    def test_rename_undo_lifo_restores(self, modifier):
        before = json.dumps(modifier.store.merged, sort_keys=True)
        modifier.rename_subtree(f"{OPS}.saturation", f"{OPS}.sat_long")
        modifier.undo()  # restores old name
        modifier.undo()  # removes new name
        assert json.dumps(modifier.store.merged, sort_keys=True) == before

    def test_rename_with_substituted_value(self, modifier):
        modifier.rename_subtree(f"{OPS}.saturation", f"{OPS}.sat2",
                                new_value={"amplitude": 0.5, "length": 10})
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert ops["sat2"]["amplitude"] == 0.5

    def test_rename_updates_search_index(self, modifier_with_index):
        index = modifier_with_index.store.search_index
        modifier_with_index.rename_subtree(f"{OPS}.saturation", f"{OPS}.sat_long")
        assert f"{OPS}.sat_long.amplitude" in index.path_to_idx
        assert f"{OPS}.saturation.amplitude" not in index.path_to_idx


# ---------------------------------------------------------------------------
# persistence: deletions must vanish from the saved JSON
# ---------------------------------------------------------------------------

class TestDeletePersistence:
    def test_save_persists_deletion(self, modifier, synth_folder):
        modifier.delete_subtree(f"{OPS}.saturation")
        Saver(modifier.store).save()
        on_disk = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert "saturation" not in on_disk["qubits"]["qA1"]["xy"]["operations"]
        assert not modifier.store.change_log  # save clears the log

    def test_save_persists_top_level_deletion(self, modifier, synth_folder):
        modifier.delete_subtree("extras")
        Saver(modifier.store).save()
        on_disk = json.loads((synth_folder / "state.json").read_text(encoding="utf-8"))
        assert "extras" not in on_disk

    def test_reload_after_unsaved_delete_resurrects(self, modifier, synth_folder):
        # reload (without save) re-reads the files — deletion was in-memory only
        modifier.delete_subtree(f"{OPS}.saturation")
        modifier.store.reload()
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "saturation" in ops


class TestDiscardCreateDeletePair:
    """Adversarial-review finding: FIFO-discarding a create-then-delete pair
    used to resurrect the created subtree with an empty change log."""

    def test_discard_create_with_later_delete_refuses(self, modifier):
        modifier.create_subtree(f"{OPS}.probe", {"amplitude": 0.1, "length": 16})
        modifier.delete_subtree(f"{OPS}.probe")
        with pytest.raises(KeyError):
            modifier.discard(0)  # the create — a later delete is pending
        assert len(modifier.store.change_log) == 2  # both entries intact
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "probe" not in ops

    def test_discard_delete_then_create_is_fine(self, modifier):
        # LIFO order works: discard the delete first, then the create
        modifier.create_subtree(f"{OPS}.probe", {"amplitude": 0.1, "length": 16})
        modifier.delete_subtree(f"{OPS}.probe")
        assert modifier.discard(1).deleted     # restore probe
        assert modifier.discard(0).created     # remove probe
        ops = modifier.store.merged["qubits"]["qA1"]["xy"]["operations"]
        assert "probe" not in ops
        assert not modifier.store.change_log

    def test_discard_create_under_deleted_ancestor_refuses(self, modifier):
        modifier.create_subtree(f"{OPS}.probe", {"amplitude": 0.1, "length": 16})
        modifier.set_value(f"{OPS}.probe.amplitude", 0.2)
        # entry[1] is the inner edit; deleting the parent subsumes it
        modifier.delete_subtree(f"{OPS}.probe")
        with pytest.raises(KeyError):
            modifier.discard(0)


class TestReloadBumpsMutationSeq:
    def test_reload_advances_seq(self, modifier):
        seq0 = modifier.store.mutation_seq
        modifier.store.reload()
        assert modifier.store.mutation_seq == seq0 + 1
