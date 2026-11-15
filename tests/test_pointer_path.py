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


# ---------------------------------------------------------------------------
# cross: a pointer whose target path passes through another pointer
# ---------------------------------------------------------------------------

def _crossed() -> dict:
    return {"a": {"ref": "#/b"}, "b": {"x": "#./y", "y": 5},
            "q": {"leaf": "#/a/ref/x"}}


def test_a_target_path_through_a_pointer_is_a_dead_end_by_default():
    r = resolve_field_target(_crossed(), "q.leaf")
    assert r["resolvable"] is False and r["resolved_value"] == "#/a/ref/x"


def test_cross_follows_the_crossed_pointer_and_its_self_reference():
    # the #./ at b.x is relative to b, the object really reached
    r = resolve_field_target(_crossed(), "q.leaf", cross=True)
    assert r["resolvable"] is True
    assert r["resolved_path"] == "b.y" and r["resolved_value"] == 5
    assert [c["path"] for c in r["candidates"]] == ["q.leaf", "b.x", "b.y"]


def test_cross_keeps_a_crossed_dangling_or_looping_path_a_dead_end():
    m = _crossed()
    m["a"]["ref"] = "#/nowhere"
    assert resolve_field_target(m, "q.leaf", cross=True)["resolvable"] is False
    m["a"]["ref"] = "#/a/ref/x"      # the crossed pointer names a path through itself
    assert resolve_field_target(m, "q.leaf", cross=True)["resolvable"] is False


def test_cross_stops_a_cycle_at_the_cycle_not_at_the_pointer_limit():
    # the cycle and the limit give the same result keys; the chain length tells them apart
    m = _crossed()
    m["b"]["x"] = "#/a/ref/x"        # b.x crosses a.ref back to itself
    r = resolve_field_target(m, "q.leaf", cross=True)
    assert r["resolvable"] is False
    assert [h["from_path"] for h in r["chain"]] == ["q.leaf", "a.ref", "b.x"]
    plain = {"a": "#/b", "b": "#/a", "q": {"leaf": "#/a"}}
    r = resolve_field_target(plain, "q.leaf", cross=True)
    assert r["resolvable"] is False and len(r["chain"]) == 3


def test_cross_forgets_a_finished_crossing():
    # a.ref is met first with y/x still to walk, later with y only: not a cycle
    m = {"a": {"ref": "#/b"}, "b": {"y": {"x": "#/a/ref/y"}}, "q": {"leaf": "#/a/ref/y/x"}}
    r = resolve_field_target(m, "q.leaf", cross=True)
    assert r["resolvable"] is True and r["resolved_path"] == "b.y"
    assert [h["from_path"] for h in r["chain"]] == ["q.leaf", "a.ref", "b.y.x", "a.ref"]

def _chain(hops: int) -> dict:
    """q.leaf -> h0 -> ... -> h{hops-2} -> number: exactly *hops* pointers followed."""
    m = {"h": {f"h{i}": f"#/h/h{i + 1}" for i in range(hops - 1)}, "q": {"leaf": "#/h/h0"}}
    m["h"][f"h{hops - 1}"] = 26
    return m


@pytest.mark.parametrize("cross", [False, True])
def test_the_hop_limit_boundary(cross):
    from quam_state_manager.core.pointer_path import _MAX_HOPS
    ok = resolve_field_target(_chain(_MAX_HOPS), "q.leaf", cross=cross)
    assert ok["resolvable"] is True and ok["resolved_value"] == 26
    cut = resolve_field_target(_chain(_MAX_HOPS + 1), "q.leaf", cross=cross)
    assert cut["resolvable"] is False and cut["resolved_value"].startswith("#/h/")


def test_cross_names_leaves_by_their_real_keys():
    m = {"s": {"a.b": "#/s/c.d", "c.d": 26}, "q.1": {"leaf": "#/s/a.b"}}
    r = resolve_field_target(m, ["q.1", "leaf"], cross=True)
    assert r["resolvable"] is True and r["resolved_value"] == 26
    assert r["resolved_segments"] == ["s", "c.d"]
    assert [h["to_segments"] for h in r["chain"]] == [["s", "a.b"], ["s", "c.d"]]
    # the default result keeps its keys
    plain = resolve_field_target(_crossed(), "q.leaf")
    assert "resolved_segments" not in plain and all("to_segments" not in h for h in plain["chain"])


def test_cross_counts_every_pointer_of_the_walk_once():
    # a crossed run of 63 pointers plus the leaf: 64 resolve; one more is cut
    from quam_state_manager.core.pointer_path import _MAX_HOPS
    for extra, ok in ((0, True), (1, False)):
        n = _MAX_HOPS - 1 + extra
        m = {"h": {f"h{i}": f"#/h/h{i + 1}" for i in range(n - 1)}, "q": {"leaf": "#/h/h0/x"}}
        m["h"][f"h{n - 1}"] = "#/h/end"
        m["h"]["end"] = {"x": 26}
        r = resolve_field_target(m, "q.leaf", cross=True)
        assert r["resolvable"] is ok and len(r["chain"]) == min(n + 1, _MAX_HOPS)
