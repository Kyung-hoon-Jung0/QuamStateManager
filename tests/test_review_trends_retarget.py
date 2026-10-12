"""A node-proven retarget remains a proven point after an exact return."""
import pytest

from tests.test_trends_oracle_cases import (
    _attr_at, _doubt, _open, _ptr_state, _real, _series, _stamp,
)

PATH = "qubits.qA1.xy.operations.x180.amplitude"
POINTER = "/quam/qubits/qA1/xy/operations/x180"
LEAF = "/quam/qubits/qA1/xy/operations/x180_Square/amplitude"


@pytest.mark.parametrize("changed_leaf", [False, True])
def test_proven_retarget_survives_exact_return(tmp_path, changed_leaf):
    patches = [{"op": "replace", "path": POINTER, "value": "#./x180_Square"}]
    if changed_leaf:
        patches.append({"op": "replace", "path": LEAF, "value": 0.40})
    runs = [
        (1, "100000", _ptr_state("#./x180_DragCosine", 0.35 if changed_leaf else 0.40)),
        (2, "101000", _ptr_state("#./x180_Square", 0.40), {"patches": patches}),
        (3, "102000", _ptr_state("#./x180_DragCosine", 0.40), {"targets": ("qA2",)}),
        (4, "103000", _ptr_state("#./x180_DragCosine", 0.40), {"targets": ("qA2",)}),
    ]
    client, _ = _open(tmp_path, runs, runs[-1][2])
    series = _series(client, "metrics=&paths=" + PATH)
    assert (_stamp("101000"), 0.40) in _real(series)
    attr = _attr_at(series, _stamp("101000"))
    assert attr["provenance"] == "run_proven"
    assert _doubt(attr) is None
    assert attr["uid"].endswith(":2")


def test_proven_destination_does_not_prove_an_unproven_pointer(tmp_path):
    runs = [
        (1, "100000", _ptr_state("#./x180_DragCosine", 0.35)),
        (2, "101000", _ptr_state("#./x180_Square", 0.40), {"patches": [
            {"op": "replace", "path": LEAF, "value": 0.40}]}),
    ]
    client, _ = _open(tmp_path, runs, runs[-1][2])
    series = _series(client, "metrics=&paths=" + PATH)
    attr = _attr_at(series, _stamp("101000"))
    assert attr["provenance"] != "run_proven"
    assert _doubt(attr) == "no later run has read the chip yet"
