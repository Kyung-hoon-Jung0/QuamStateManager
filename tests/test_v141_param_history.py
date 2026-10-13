"""Param History must show live derived values and open saved source runs."""
import json
import re

import pytest

from tests.test_hub_chip_status import load, matrix_state
from tests.test_hub_drawer import _inline  # noqa: F401


@pytest.mark.parametrize("prop,matrix,value", [
    ("assignment_fidelity", [[0.8, 0.2], [0.219, 0.781]], 0.7905),
    ("assignment_fidelity_gef", [[0.9, 0.1, 0], [0.2, 0.8, 0], [0.1, 0.2, 0.7]], 0.8),
])
def test_live_derived_value_in_grid_and_drawer(tmp_path, prop, matrix, value):
    state = matrix_state(matrix)
    if prop.endswith("_gef"):
        state["qubits"]["qA1"]["resonator"]["gef_confusion_matrix"] = matrix
    env = load(tmp_path, [(state, None)], state)
    grid = env["client"].get(f"/param-history?props={prop}&qubits=qA1&since=all").get_data(as_text=True)
    drawer = env["client"].get(f"/param-history/expand?qubit=qA1&prop={prop}").get_data(as_text=True)
    assert re.search(r'class="history-cell-value[^>]*>\s*' + re.escape(f"{value:.4g}"), grid), grid[-3000:]
    assert f'current: <strong>{value:.6g}</strong>' in drawer
