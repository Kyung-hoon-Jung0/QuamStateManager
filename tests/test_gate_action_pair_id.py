"""The builder's pair id is the only coupler address grammar."""
import ast
import inspect

from quam_state_manager.core import gate_action


def test_spec_pair_addresses_builder_coupler():
    spec = {"qubit_pairs": [["q1", "q2"]],
            "lines": [{"element": "q1-q2", "line": "coupler"}]}
    got = gate_action.resolve(spec, "q1-q2", {
        "gate": {"name": "cz_bm", "macro_class": "pkg.Gate"},
        "items": [{"role": "coupler", "pulse_class": "pkg.Pulse", "fields": {"length": 40}}]})
    assert got["ok"], got
    assert got["additions"][0]["target"] == "qubit_pairs.q1-2.coupler.operations"
    assert got["additions"][-1]["fields"]["coupler_flux_pulse"] == "#/qubit_pairs/q1-2/coupler/operations/cz_bm_coupler"


def test_no_handmade_pair_id():
    tree = ast.parse(inspect.getsource(gate_action))
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for a, b, c in zip(node.values, node.values[1:], node.values[2:]):
                assert not (isinstance(a, ast.FormattedValue) and isinstance(b, ast.Constant)
                            and b.value == "-" and isinstance(c, ast.FormattedValue))
