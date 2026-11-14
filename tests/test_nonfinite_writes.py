"""NaN and Infinity never reach state.json through a user-facing write.

Pins the one checker (``type_policy.nonfinite_where``), the typed-text parsers
(``parse_value`` / ``parse_with_expected``), the modifier funnel (``set_value``
/ ``create_subtree`` with ``enforce=True``) and the /field/edit-batch and
/field/edit doors, including a null leaf (no type to coerce against).
"""

from __future__ import annotations

import decimal
import json
import math
from pathlib import Path

import numpy as np
import pytest

from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier
from quam_state_manager.core.type_policy import (
    Expected, nonfinite_where, parse_value, parse_with_expected)
from quam_state_manager.web.app import create_app

NAN = float("nan")
INF = float("inf")


# --------------------------------------------------------------------------
# the checker
# --------------------------------------------------------------------------

@pytest.mark.parametrize("value, where", [
    ((1.0, NAN), ".1"),
    ([1, (2.0, INF)], ".1.1"),
    ({"a": (0.5, -INF)}, ".a.1"),
    (decimal.Decimal("NaN"), ""),
    (decimal.Decimal("Infinity"), ""),
    ([decimal.Decimal("-Infinity")], ".0"),
    (complex(NAN, 0.0), ""),
    (complex(0.0, INF), ""),
    (np.array([1.0, NAN]), ".1"),
    (np.array(INF), ""),
    (np.float64(NAN), ""),
    ({1.0, NAN}, ""),
    (frozenset({INF}), ""),
])
def test_nonfinite_where_finds_every_kind(value, where):
    assert nonfinite_where(value) == where


@pytest.mark.parametrize("value", [
    (1.0, 2), decimal.Decimal("1.5"), complex(1, 2), np.array([1.0, 2.0]),
    np.array(3.0), np.int64(7), {1, 2}, "NaN", b"inf", None, True, {"a": [1, "x"]},
])
def test_nonfinite_where_passes_finite_values(value):
    assert nonfinite_where(value) is None


# --------------------------------------------------------------------------
# typed text
# --------------------------------------------------------------------------

@pytest.mark.parametrize("raw", ["[NaN]", '{"a": Infinity}', "[1e999]", '{"a": [1, -Infinity]}'])
def test_parse_value_refuses_nonfinite_json_literals(raw):
    with pytest.raises(ValueError, match="non-finite"):
        parse_value(raw)


@pytest.mark.parametrize("base, raw", [("list", "[NaN]"), ("dict", '{"a": Infinity}'),
                                       ("list", "[1e999]")])
def test_parse_with_expected_refuses_nonfinite_json(base, raw):
    exp = Expected(spec={"base": base}, source="env")
    with pytest.raises(ValueError, match="non-finite"):
        parse_with_expected(raw, exp)


def test_parse_keeps_finite_json_literals():
    assert parse_value("[1, 2.5]") == [1, 2.5]
    assert parse_with_expected('{"a": 1}', Expected(spec={"base": "dict"}, source="env")) == {"a": 1}


# --------------------------------------------------------------------------
# the modifier funnel
# --------------------------------------------------------------------------

def _mod(state: dict) -> Modifier:
    return Modifier(QuamStore.from_dicts(state, {}))


@pytest.mark.parametrize("new", [NAN, [1.0, INF], (1.0, NAN), {"x": -INF}])
def test_set_value_refuses_new_nonfinite_on_a_null_leaf(new):
    m = _mod({"q": {"a": None}})
    with pytest.raises(TypeError, match="q.a"):
        m.set_value("q.a", new, coerce=False)
    assert m.store.state["q"]["a"] is None


def test_set_value_passes_an_unchanged_nonfinite_value():
    m = _mod({"q": {"a": [1.0, NAN]}})
    m.set_value("q.a", [1.0, NAN], coerce=False)          # same content: not new
    with pytest.raises(TypeError):
        m.set_value("q.a", [2.0, NAN], coerce=False)       # different content: refused


def test_replay_without_enforce_keeps_what_the_chip_holds():
    m = _mod({"q": {"a": 1.0}})
    m.set_value("q.a", NAN, coerce=False, enforce=False)
    assert math.isnan(m.store.state["q"]["a"])


def test_create_subtree_refuses_nonfinite():
    m = _mod({"q": {}})
    with pytest.raises(TypeError, match="q.new.b"):
        m.create_subtree("q.new", {"b": [INF]})
    assert "new" not in m.store.state["q"]


# --------------------------------------------------------------------------
# the HTTP doors
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path: Path):
    state = {"qubits": {"qA1": {"id": "qA1", "f_12": None, "T1": 2.0e-5}},
             "qubit_pairs": {}, "active_qubit_names": ["qA1"]}
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    return c


def _peek(client, key):
    return client.get(f"/field/peek?dot_path={key}").get_json()["values"][key]


def _batch_applied(jb) -> bool:
    return bool(jb.get("ok")) and any(r.get("applied") for r in jb.get("results", []))


@pytest.mark.parametrize("value", [NAN, INF, [1.0, NAN], {"a": -INF}, "[NaN]", "[1e999]",
                                   '{"a": Infinity}', "nan", "inf"])
def test_edit_batch_json_refuses_nonfinite_on_a_null_leaf(client, value):
    jb = client.post("/field/edit-batch", data=json.dumps({"updates": [
        {"dot_path": "qubits.qA1.f_12", "value": value}]}),
        content_type="application/json").get_json()
    assert not _batch_applied(jb), jb
    assert _peek(client, "qubits.qA1.f_12") is None


@pytest.mark.parametrize("value", ["[NaN]", '{"a": Infinity}', "nan", "-inf"])
def test_edit_batch_form_refuses_nonfinite(client, value):
    jb = client.post("/field/edit-batch", data={"dot_path": "qubits.qA1.f_12",
                                                 "value": value}).get_json()
    assert not _batch_applied(jb), jb
    assert _peek(client, "qubits.qA1.f_12") is None


@pytest.mark.parametrize("value", ["[NaN]", '{"a": Infinity}', "[1e999]"])
def test_field_edit_refuses_nonfinite_on_a_null_leaf(client, value):
    client.post("/field/edit", data={"dot_path": "qubits.qA1.f_12", "value": value})
    assert _peek(client, "qubits.qA1.f_12") is None


def test_edit_batch_still_writes_a_finite_value(client):
    jb = client.post("/field/edit-batch", json={"updates": [
        {"dot_path": "qubits.qA1.f_12", "value": [1.0, 2.0]}]}).get_json()
    assert _batch_applied(jb), jb
    assert _peek(client, "qubits.qA1.f_12") == [1.0, 2.0]


@pytest.mark.parametrize("value, expect", [("[NaN]", None), ('{"a": Infinity}', "dict"),
                                           ("[1e999]", None), ("NaN", "number")])
def test_field_create_refuses_nonfinite(client, value, expect):
    data = {"dot_path": "qubits.qA1.extras_new", "value": value}
    if expect:
        data["expect_type"] = expect
    client.post("/field/create", data=data)
    peek = client.get("/field/peek?dot_path=qubits.qA1.extras_new").get_json()
    assert peek["values"].get("qubits.qA1.extras_new") is None, peek


def test_field_create_still_creates_a_finite_list(client):
    r = client.post("/field/create", data={"dot_path": "qubits.qA1.extras_new", "value": "[1, 2.5]"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _peek(client, "qubits.qA1.extras_new") == [1, 2.5]
