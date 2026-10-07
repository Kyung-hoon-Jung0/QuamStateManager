"""A Re-generate rebuild keeps which qubits, pairs and TWPAs the chip has
ACTIVE. The wizard has no control for it, so the build writes its default
(every qubit active, no pair): before this, every rebuild wiped the chip's
active pairs and re-activated every qubit."""
from __future__ import annotations

from quam_state_manager.core.regen_merge import merge_states, rename_source_qubits, source_renames


def _state(qubits, pairs, active_q, active_p):
    return {"qubits": {q: {"id": q} for q in qubits},
            "qubit_pairs": {p: {"id": p} for p in pairs},
            "active_qubit_names": list(active_q), "active_qubit_pair_names": list(active_p)}


def test_the_chips_active_choice_survives_a_rebuild():
    old = _state(["q1", "q2", "q3"], ["q1-q2", "q2-q3"], ["q1", "q3"], ["q2-q3"])
    new = _state(["q1", "q2", "q3"], ["q1-q2", "q2-q3"], ["q1", "q2", "q3"], [])
    r = merge_states(old, new)
    assert r.merged["active_qubit_names"] == ["q1", "q3"]
    assert r.merged["active_qubit_pair_names"] == ["q2-q3"]
    assert sorted(r.stats.active_kept) == ["active_qubit_names", "active_qubit_pair_names"]


def test_a_removed_entity_leaves_and_a_new_one_takes_the_builds_default():
    old = _state(["q1", "q2"], ["q1-q2"], ["q1", "q2"], ["q1-q2"])
    new = _state(["q1", "q3"], ["q1-q3"], ["q1", "q3"], [])
    r = merge_states(old, new)
    assert r.merged["active_qubit_names"] == ["q1", "q3"], "q2 is gone; q3 is new: the build's default"
    assert r.merged["active_qubit_pair_names"] == [], "the old pair is gone; the new one: build default"


def test_a_renamed_chip_keeps_its_active_pairs_under_their_new_ids():
    old = _state(["q1", "q2", "q3"], ["q1-q2", "q2-q3"], ["q1", "q2", "q3"], ["q1-q2", "q2-q3"])
    for pid, (c, t) in {"q1-q2": ("q1", "q2"), "q2-q3": ("q2", "q3")}.items():
        old["qubit_pairs"][pid].update(qubit_control=f"#/qubits/{c}", qubit_target=f"#/qubits/{t}")
    new = _state(["q0", "q1", "q2"], ["q0-q1", "q1-q2"], ["q0", "q1", "q2"], [])
    for pid, (c, t) in {"q0-q1": ("q0", "q1"), "q1-q2": ("q1", "q2")}.items():
        new["qubit_pairs"][pid].update(qubit_control=f"#/qubits/{c}", qubit_target=f"#/qubits/{t}")
    ren = source_renames({"q0": "q1", "q1": "q2", "q2": "q3"}, old, new["qubits"])
    os2, _w, _q, _p = rename_source_qubits(old, None, ren, new, None)
    r = merge_states(os2, new)
    assert r.merged["active_qubit_pair_names"] == ["q0-q1", "q1-q2"]
    assert r.merged["active_qubit_names"] == ["q0", "q1", "q2"]


def test_a_source_with_no_list_takes_the_builds():
    old = _state(["q1"], [], ["q1"], [])
    del old["active_qubit_pair_names"]
    new = _state(["q1"], [], ["q1"], [])
    assert merge_states(old, new).merged["active_qubit_pair_names"] == []
