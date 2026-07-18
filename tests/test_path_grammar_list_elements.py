"""The dot-form list-element path grammar (Phase 1 of the typed-edit feature).

List/matrix elements are addressed with numeric dot segments
(``confusion_matrix.0.1``) through the SAME traversal the number-keyed ports
dicts use (``ports.mw_outputs.con1.1.2.band``) — disambiguated structurally
(the actual parent container's type), never by syntax. Pins:

* set/undo/discard on elements, incl. inside a list-of-lists;
* dict-routed number keys untouched by the grammar;
* strict ``^\\d+$`` index gate (negative/`+3`/hex → KeyError, never a
  silent wrong-element write);
* out-of-range → KeyError (routes translate to 400, not 500);
* rename of a list element → clear ValueError;
* whole-array replacement still works;
* ``coerce=False`` writes on element paths (the pull-replay path);
* the non-finite guard in ``_type_coerce`` (Infinity → TypeError).
"""

from __future__ import annotations

import pytest

from quam_state_manager.core.edit_policy import editability_reason
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier, _type_coerce


def _mixed_state() -> dict:
    """One fixture holding BOTH shapes: a true list-of-lists (matrix) and a
    number-keyed ports dict — the grammar must route each correctly."""
    return {
        "qubits": {
            "qA1": {
                "id": "qA1",
                "resonator": {
                    "confusion_matrix": [[0.98, 0.02], [0.03, 0.97]],
                    "gef_centers": [[1.0, -2.0], [3.5, 0.25], [0.0, 4.0]],
                    "operations": {"readout": {"integration_weights": [1.0, 0.5, 0.25]}},
                },
            },
        },
        "ports": {"mw_outputs": {"con1": {"1": {"2": {"band": 2, "upconverter_frequency": 5.05e9}}}}},
        "active_qubit_names": ["qA1"],
    }


@pytest.fixture
def store() -> QuamStore:
    return QuamStore.from_dicts(_mixed_state(), {"wiring": {}})


@pytest.fixture
def mod(store) -> Modifier:
    return Modifier(store)


class TestElementEdits:
    def test_matrix_element_set_and_readback(self, mod, store):
        mod.set_value("qubits.qA1.resonator.confusion_matrix.0.1", 0.05)
        assert store.merged["qubits"]["qA1"]["resonator"]["confusion_matrix"][0][1] == 0.05
        # the source dict (state) got the same write
        assert store.state["qubits"]["qA1"]["resonator"]["confusion_matrix"][0][1] == 0.05

    def test_flat_list_element_set(self, mod, store):
        mod.set_value("qubits.qA1.resonator.operations.readout.integration_weights.2", 0.125)
        assert store.merged["qubits"]["qA1"]["resonator"]["operations"]["readout"][
            "integration_weights"][2] == 0.125

    def test_element_type_pinned_by_old_element(self, mod, store):
        # float element + "abc" → the coercer rejects, exactly like a scalar leaf
        with pytest.raises(TypeError):
            mod.set_value("qubits.qA1.resonator.confusion_matrix.0.0", "abc")
        assert store.merged["qubits"]["qA1"]["resonator"]["confusion_matrix"][0][0] == 0.98

    def test_ports_number_keys_still_dict_routed(self, mod, store):
        # same numeric-segment syntax, DICT parent — string-key routing intact
        mod.set_value("ports.mw_outputs.con1.1.2.band", 3)
        assert store.merged["ports"]["mw_outputs"]["con1"]["1"]["2"]["band"] == 3
        assert "1" in store.merged["ports"]["mw_outputs"]["con1"]  # key stayed a string

    def test_undo_restores_element(self, mod, store):
        entry = mod.set_value("qubits.qA1.resonator.confusion_matrix.1.0", 0.5)
        mod._revert_entry(entry)
        assert store.merged["qubits"]["qA1"]["resonator"]["confusion_matrix"][1][0] == 0.03

    def test_group_undo_spanning_elements_and_scalars(self, mod, store):
        gid = mod.new_group_id()
        mod.set_value("qubits.qA1.resonator.confusion_matrix.0.0", 0.5, group_id=gid)
        mod.set_value("ports.mw_outputs.con1.1.2.band", 3, group_id=gid)
        undone = mod.undo_group()   # undoes the LAST group atomically
        assert len(undone) == 2
        assert store.merged["qubits"]["qA1"]["resonator"]["confusion_matrix"][0][0] == 0.98
        assert store.merged["ports"]["mw_outputs"]["con1"]["1"]["2"]["band"] == 2

    def test_replay_style_coerce_false_element_write(self, mod, store):
        # pull-replay replays stash values verbatim (coerce=False) — element
        # paths must take that path too
        mod.set_value("qubits.qA1.resonator.confusion_matrix.0.1", 0.07, coerce=False)
        assert store.merged["qubits"]["qA1"]["resonator"]["confusion_matrix"][0][1] == 0.07

    def test_whole_array_replacement_still_works(self, mod, store):
        mod.set_value("qubits.qA1.resonator.confusion_matrix", [[1.0, 0.0], [0.0, 1.0]])
        assert store.merged["qubits"]["qA1"]["resonator"]["confusion_matrix"] == [[1.0, 0.0], [0.0, 1.0]]


class TestStrictIndexGate:
    def test_out_of_range_raises_keyerror(self, mod):
        with pytest.raises(KeyError):
            mod.set_value("qubits.qA1.resonator.confusion_matrix.7.7", 0.5)

    def test_negative_index_rejected(self, mod, store):
        with pytest.raises(KeyError):
            mod.set_value("qubits.qA1.resonator.confusion_matrix.0.-1", 0.5)
        # nothing written anywhere (negative indexing would have hit the LAST cell)
        assert store.merged["qubits"]["qA1"]["resonator"]["confusion_matrix"][0][1] == 0.02

    @pytest.mark.parametrize("bad", ["+1", "0x2", "1.0", " 1", ""])
    def test_pathological_index_forms_rejected(self, mod, bad):
        with pytest.raises((KeyError, ValueError)):
            mod.set_value(f"qubits.qA1.resonator.confusion_matrix.0.{bad}", 0.5)

    def test_rename_of_list_element_clear_error(self, mod):
        with pytest.raises(ValueError, match="list element"):
            mod.rename_subtree("qubits.qA1.resonator.confusion_matrix.0",
                               "qubits.qA1.resonator.confusion_matrix.9")


class TestEditPolicyV2:
    def test_list_elements_no_longer_policy_blocked(self, store):
        assert editability_reason(store, "qubits.qA1.resonator.confusion_matrix.0.1") is None

    def test_membership_arrays_still_blocked(self, store):
        reason = editability_reason(store, "active_qubit_names.0")
        assert reason is not None and "membership" in reason

    def test_identity_keys_still_blocked(self, store):
        assert editability_reason(store, "qubits.qA1.id") is not None
        assert editability_reason(store, "qubits.qA1.__class__") is not None

    def test_digital_marker_now_editable(self, store):
        assert editability_reason(store, "qubits.qA1.xy.operations.x.digital_marker") is None


class TestNonFiniteGuard:
    def test_infinity_into_float_field_rejected(self):
        with pytest.raises(TypeError, match="[Nn]on-finite"):
            _type_coerce(1.5, float("inf"))

    def test_nan_into_int_field_rejected(self):
        with pytest.raises(TypeError, match="[Nn]on-finite"):
            _type_coerce(40, float("nan"))

    def test_finite_values_unaffected(self):
        assert _type_coerce(1.5, 2) == 2.0
        assert _type_coerce(40, 41.0) == 41
