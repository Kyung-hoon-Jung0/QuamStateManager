"""SU-11: Live Edit accepts a pointer to a whole container into a numeric leaf.

``/field/edit`` value ``#/qubits`` into a float cell (a T1, a threshold) is
staged and, on Apply, written to the chip: the leaf now resolves to the whole
qubits collection. ``/pulse/edit`` refuses the same text ("That pointer
resolves to a container"); the grid door has no such check.
Harness: EDIT_INVALID kind=pointer (accepted on both chips, every time).
Contract: the type policy (docs/56) -- a numeric field takes a number or a
pointer to one.
"""
import pytest

from tests.state_model.repros._rig import Chip


def test_a_container_pointer_is_refused_in_a_numeric_cell(tmp_path):
    chip = Chip(tmp_path)
    r = chip.c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "#/qubits"})
    assert r.status_code == 400, (r.status_code, chip.log())
    assert "container" in (r.get_json() or {}).get("error", ""), r.get_json()
    assert chip.log() == [], "nothing staged"


def test_a_leaf_pointer_still_lands(tmp_path):
    chip = Chip(tmp_path)
    r = chip.c.post("/field/edit", data={"dot_path": "qubits.q1.T1", "value": "#/qubits/q2/T1"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_data(as_text=True)[:300]
