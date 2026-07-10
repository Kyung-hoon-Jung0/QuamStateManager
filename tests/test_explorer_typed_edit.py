"""Regression for Explorer typed-value entry via /field/edit: any JSON type can be
entered (string, list, dict, scalar), the double-quote bug is fixed, and the
container path round-trips through the modifier's type-coercion. Mirrors what the
tree's whole-container JSON editor and the per-leaf editor POST.
"""

from __future__ import annotations

import json


from quam_state_manager.web.app import create_app


def _client(tmp_path):
    state = {"qubits": {"q1": {"id": "q1", "resonator": {
        "confusion_matrix": [[0.98, 0.02], [0.02, 0.98]],
        "exponential_filter": None,
        "label": "hi",
    }}}, "qubit_pairs": {}, "ports": {}}
    wiring = {"wiring": {"qubits": {}}, "network": {"host": "x", "cluster_name": "t"}}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    return c, app


def _val(app, path):
    store = list(app.config["contexts"].values())[0]["store"]
    return store.get_value(path)


def _edit(c, path, value):
    return c.post("/field/edit", data={"dot_path": path, "value": value}).get_json()


def test_quoted_string_no_double_quote(tmp_path):
    c, app = _client(tmp_path)
    assert _edit(c, "qubits.q1.resonator.label", '"02"')["ok"] is True
    assert _val(app, "qubits.q1.resonator.label") == "02"   # not '"02"'


def test_edit_existing_list_wholesale(tmp_path):
    c, app = _client(tmp_path)
    assert _edit(c, "qubits.q1.resonator.confusion_matrix",
                 "[[0.9, 0.1], [0.05, 0.95]]")["ok"] is True
    assert _val(app, "qubits.q1.resonator.confusion_matrix") == [[0.9, 0.1], [0.05, 0.95]]


def test_fill_none_field_with_list(tmp_path):
    c, app = _client(tmp_path)
    assert _edit(c, "qubits.q1.resonator.exponential_filter",
                 "[[0.9, 100.0], [-0.5, 30.0]]")["ok"] is True
    assert _val(app, "qubits.q1.resonator.exponential_filter") == [[0.9, 100.0], [-0.5, 30.0]]


def test_malformed_list_rejected(tmp_path):
    c, app = _client(tmp_path)
    res = _edit(c, "qubits.q1.resonator.confusion_matrix", "[[1, 2")
    assert res["ok"] is False
    # the list field is untouched
    assert _val(app, "qubits.q1.resonator.confusion_matrix") == [[0.98, 0.02], [0.02, 0.98]]


def test_string_field_coerces_list_to_string(tmp_path):
    # type-coercion keeps a field at its OLD type: a list typed into a string field
    # is stringified (not stored as a list). The whole-container JSON editor only
    # exposes the ✎ on list/dict nodes, so this only happens via the per-leaf editor
    # on a genuine string field — the coerce-to-old-type safety, not a regression.
    c, app = _client(tmp_path)
    res = _edit(c, "qubits.q1.resonator.label", "[1, 2, 3]")
    assert res["ok"] is True
    assert _val(app, "qubits.q1.resonator.label") == "[1, 2, 3]"   # str, not list
