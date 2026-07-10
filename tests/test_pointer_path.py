"""Tests for core/pointer_path.py — the mid-path QUAM-pointer follower used by
pointer-aware click-to-edit. Synthetic tests always run; real-data tests
auto-skip when the quam_states folder is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from quam_state_manager.core.pointer_path import find_shared_by, resolve_field_target


def _merged() -> dict:
    return {
        "qubits": {
            "qA1": {
                "f_01": 6.25e9,
                "xy": {
                    "intermediate_frequency": "#./inferred_if",  # runtime alias, no sibling
                    "operations": {
                        "x180": "#./x180_DragCosine",
                        "x90": "#./x90_DragCosine",
                        "y180": "#./y180_DragCosine",
                        "x180_DragCosine": {"amplitude": 0.115, "length": 40},
                        "x90_DragCosine": {
                            "amplitude": 0.057,
                            "length": "#../x180_DragCosine/length",
                        },
                        "y180_DragCosine": {
                            "amplitude": "#../x180_DragCosine/amplitude",
                            "length": 40,
                        },
                    },
                },
            },
        },
    }


def test_resolves_self_ref_mid_path():
    ft = resolve_field_target(_merged(), "qubits.qA1.xy.operations.x180.amplitude")
    assert ft["resolved_path"] == "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
    assert ft["resolved_value"] == 0.115
    assert ft["is_pointer"] is True and ft["resolvable"] is True
    assert len(ft["chain"]) == 1
    assert ft["chain"][0]["pointer"] == "#./x180_DragCosine"
    assert ft["candidates"][-1]["path"] == ft["resolved_path"]


def test_resolves_leaf_pointer_chain():
    # y180 (mid pointer) → y180_DragCosine; its .amplitude (leaf pointer) → x180.
    ft = resolve_field_target(_merged(), "qubits.qA1.xy.operations.y180.amplitude")
    assert ft["resolved_path"] == "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
    assert ft["resolved_value"] == 0.115
    # candidates: the y180_DragCosine.amplitude (pointer) and the final literal.
    paths = [c["path"] for c in ft["candidates"]]
    assert paths == [
        "qubits.qA1.xy.operations.y180_DragCosine.amplitude",
        "qubits.qA1.xy.operations.x180_DragCosine.amplitude",
    ]
    assert ft["candidates"][0]["is_pointer"] is True
    assert ft["candidates"][-1]["is_pointer"] is False
    assert len(ft["chain"]) == 2


def test_leaf_relative_pointer():
    ft = resolve_field_target(_merged(), "qubits.qA1.xy.operations.x90_DragCosine.length")
    assert ft["resolved_path"] == "qubits.qA1.xy.operations.x180_DragCosine.length"
    assert ft["resolved_value"] == 40


def test_runtime_alias_degrades_gracefully():
    ft = resolve_field_target(_merged(), "qubits.qA1.xy.intermediate_frequency")
    assert ft["is_pointer"] is True
    assert ft["resolvable"] is False  # sibling 'inferred_if' does not exist
    assert ft["candidates"]  # best-effort, non-empty
    # No exception; the deepest real path is still offered as a target.
    assert ft["resolved_path"] == "qubits.qA1.xy.intermediate_frequency"


def test_non_pointer_path_is_trivial():
    ft = resolve_field_target(_merged(), "qubits.qA1.f_01")
    assert ft["is_pointer"] is False and ft["resolvable"] is True
    assert ft["chain"] == []
    assert len(ft["candidates"]) == 1
    assert ft["candidates"][0]["path"] == "qubits.qA1.f_01"
    assert ft["resolved_value"] == 6.25e9


def test_cycle_guard():
    merged = {"a": {"x": "#./y", "y": "#./x"}}
    ft = resolve_field_target(merged, "a.x")
    assert ft["resolvable"] is False  # a.x → a.y → a.x cycle
    assert ft["is_pointer"] is True   # terminates without RecursionError


def test_list_index_traversal():
    merged = {"qubits": {"qA1": {"arr": [{"v": 1}, {"v": 2}]}}}
    ft = resolve_field_target(merged, "qubits.qA1.arr.1.v")
    assert ft["resolvable"] is True
    assert ft["resolved_value"] == 2


def test_square_pulse_label_not_hardcoded():
    merged = {
        "qubits": {"qA1": {"xy": {"operations": {
            "x180": "#./x180_Square",
            "x180_Square": {"amplitude": 0.2},
        }}}}
    }
    ft = resolve_field_target(merged, "qubits.qA1.xy.operations.x180.amplitude")
    assert ft["resolved_path"].endswith("x180_Square.amplitude")
    assert ft["candidates"][-1]["label"] == "x180_Square.amplitude"


def test_find_shared_by():
    merged = _merged()
    resolved = "qubits.qA1.xy.operations.x180_DragCosine.amplitude"
    shared = find_shared_by(merged, resolved, scope_qubit="qA1", input_op="x180")
    assert "y180" in shared          # y180.amplitude → x180_DragCosine.amplitude
    assert "x180" not in shared      # the clicked alias is excluded
    assert "x90" not in shared       # x90 has its own literal amplitude


# --- real-data ---------------------------------------------------------
_QS = Path("<quam-states>/example_lab")
skip_no_real = pytest.mark.skipif(
    not (_QS / "state.json").exists(), reason="real quam_states folder not found")


@skip_no_real
def test_real_x180_resolution():
    from quam_state_manager.core.loader import QuamStore
    store = QuamStore(str(_QS))
    q = store.qubit_names[0]
    ft = resolve_field_target(store.merged, f"qubits.{q}.xy.operations.x180.amplitude")
    assert ft["is_pointer"] and ft["resolvable"]
    assert ft["resolved_path"].endswith("_DragCosine.amplitude")
    assert isinstance(ft["resolved_value"], (int, float))
    assert any(h["pointer"].startswith("#./") for h in ft["chain"])


@skip_no_real
def test_real_shared_by_excludes_self():
    from quam_state_manager.core.loader import QuamStore
    store = QuamStore(str(_QS))
    q = store.qubit_names[0]
    ft = resolve_field_target(store.merged, f"qubits.{q}.xy.operations.x180.amplitude")
    shared = find_shared_by(store.merged, ft["resolved_path"], scope_qubit=q, input_op="x180")
    assert "x180" not in shared
    assert "y180" in shared
