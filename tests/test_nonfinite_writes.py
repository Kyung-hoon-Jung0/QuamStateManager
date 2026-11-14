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


# --------------------------------------------------------------------------
# replay and restore: a value the chip already held is never refused
# --------------------------------------------------------------------------

def test_restore_keeps_a_nonfinite_value_the_chip_held():
    m = _mod({"q": {"a": 1.0, "c": {}}})
    m.set_value("q.a", NAN, coerce=False, restore=True)
    assert math.isnan(m.store.state["q"]["a"])
    m.create_subtree("q.c.new", {"b": [1.0, INF]}, restore=True)
    assert m.store.state["q"]["c"]["new"]["b"][1] == INF


def test_restore_skips_only_the_nonfinite_gate():
    m = _mod({"q": {"a": 1.0}})
    with pytest.raises(TypeError, match="q.a"):
        m.set_value("q.a", NAN, coerce=False)                 # no restore: refused
    assert m.store.state["q"]["a"] == 1.0


def test_rename_subtree_moves_a_nonfinite_value():
    m = _mod({"q": {"ops": {"p": {"amp": NAN, "len": 40}}}})
    m.rename_subtree("q.ops.p", "q.ops.p2")
    ops = m.store.state["q"]["ops"]
    assert "p" not in ops and math.isnan(ops["p2"]["amp"]) and ops["p2"]["len"] == 40


def _restore_calls(rel: str, names):
    import ast
    src = (Path(__file__).resolve().parents[1] / rel).read_text(encoding="utf-8")
    out = []
    for f in ast.walk(ast.parse(src)):
        if isinstance(f, ast.FunctionDef) and (names is None or f.name in names):
            for c in ast.walk(f):
                if (isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                        and c.func.attr in ("set_value", "create_subtree")):
                    kw = {k.arg: ast.unparse(k.value) for k in c.keywords}
                    out.append((f.name, c.lineno, kw))
    return out


@pytest.mark.parametrize("rel, names, minimum", [
    ("quam_state_manager/web/routes.py",
     {"_undo_journal_step", "_rollback_walk_step", "_redo_journal_forward", "redo"}, 8),
    ("quam_state_manager/core/autofit/writer.py", {"revert_patches", "restore_values"}, 4),
])
def test_every_replay_site_is_a_restore(rel, names, minimum):
    """One mechanism: every replay/restore write says restore=True (or
    enforce=False, which skips every gate), so none refuses what the chip held."""
    calls = _restore_calls(rel, names)
    assert len(calls) >= minimum, calls
    bad = [(fn, ln) for fn, ln, kw in calls
           if kw.get("restore") != "True" and kw.get("enforce") != "False"]
    assert not bad, bad


def test_rollback_after_a_failed_save_is_a_restore():
    calls = [kw for fn, ln, kw in _restore_calls("quam_state_manager/core/autofit/writer.py",
                                                 {"apply_rows"})
             if kw.get("restore") == "True"]
    assert calls, "the rollback in apply_rows must restore old values with restore=True"


def test_agent_takeback_is_a_restore():
    calls = _restore_calls("quam_state_manager/web/agent_api.py", {"_take_back_saved"})
    assert calls and all(kw.get("restore") == "True" for _, _, kw in calls), calls


# --------------------------------------------------------------------------
# a chip that already holds NaN: delete / rename / undo / redo / duplicate
# --------------------------------------------------------------------------

QC = "quam.components.pulses."
XY = "qubits.qA1.xy.operations"
_JSON = {"Accept": "application/json"}


def _nan_state() -> dict:
    return {
        "qubits": {"qA1": {
            "id": "qA1", "f_01": 6.25e9,
            "fit_extra": {"a": NAN, "b": [1.0, NAN]},
            "xy": {"operations": {
                "x180_DragCosine": {"length": 48, "axis_angle": 0, "amplitude": 0.3,
                                    "alpha": -0.34, "detuning": NAN,
                                    "__class__": QC + "DragCosinePulse"},
                "x90_DragCosine": {"length": 48, "axis_angle": 0, "amplitude": 0.15,
                                   "alpha": "#../x180_DragCosine/alpha", "detuning": 0.0,
                                   "__class__": QC + "DragCosinePulse"},
                "saturation": {"length": 20000, "amplitude": 0.004,
                               "__class__": QC + "SquarePulse"},
            }},
        }},
        "qubit_pairs": {}, "active_qubit_names": ["qA1"],
    }


@pytest.fixture
def nan_chip(tmp_path: Path):
    # json.dumps writes NaN as the literal NaN, as a node's save does
    (tmp_path / "state.json").write_text(json.dumps(_nan_state()), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps(
        {"wiring": {"qubits": {"qA1": {"xy": {"opx_output": "MW-FEM/1/2"}}}}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_app_instance"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(tmp_path)}).status_code in (200, 302)
    return c, tmp_path


def _store_of(c):
    ctxs = [x for x in c.application.config["contexts"].values() if x.get("type") == "quam"]
    assert len(ctxs) == 1
    return ctxs[0]["store"]


def _raw(c, dot_path):
    """The raw store value (the JSON provider would show NaN as null)."""
    return _store_of(c).get_value(dot_path)


def _live(folder: Path) -> dict:
    return json.loads((folder / "state.json").read_text(encoding="utf-8"))


def _apply(c):
    n = len(_store_of(c).change_log)
    r = c.post("/state/apply-to-live", data={"seen_changes": str(n)}, headers=_JSON)
    assert r.status_code == 200, r.get_data(as_text=True)[:300]


def _isnan(v) -> bool:
    return isinstance(v, float) and math.isnan(v)


def test_delete_nan_subtree_undo_restores_and_redo_deletes_live(nan_chip):
    c, folder = nan_chip
    r = c.post("/field/delete", data={"dot_path": "qubits.qA1.fit_extra"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    _apply(c)
    assert "fit_extra" not in _live(folder)["qubits"]["qA1"]
    u = c.post("/undo", headers=_JSON)
    assert u.status_code == 200, u.get_data(as_text=True)[:300]
    assert _isnan(_raw(c, "qubits.qA1.fit_extra.a"))
    live = _live(folder)["qubits"]["qA1"]["fit_extra"]
    assert _isnan(live["a"]) and live["b"][0] == 1.0 and _isnan(live["b"][1])
    y = c.post("/redo", headers=_JSON)
    assert y.status_code == 200, y.get_data(as_text=True)[:300]
    assert "fit_extra" not in _live(folder)["qubits"]["qA1"]


def test_rename_nan_pulse_keeps_nan_and_undo_redo_follow_live(nan_chip):
    c, folder = nan_chip
    r = c.post("/api/pulse/rename", data={"path": f"{XY}.x180_DragCosine",
                                          "new_name": "x180_v2", "retarget": "1"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _isnan(_raw(c, f"{XY}.x180_v2.detuning"))
    _apply(c)
    ops = _live(folder)["qubits"]["qA1"]["xy"]["operations"]
    assert "x180_DragCosine" not in ops and _isnan(ops["x180_v2"]["detuning"])
    u = c.post("/undo", headers=_JSON)
    assert u.status_code == 200, u.get_data(as_text=True)[:300]
    ops = _live(folder)["qubits"]["qA1"]["xy"]["operations"]
    assert "x180_v2" not in ops and _isnan(ops["x180_DragCosine"]["detuning"])
    y = c.post("/redo", headers=_JSON)
    assert y.status_code == 200, y.get_data(as_text=True)[:300]
    ops = _live(folder)["qubits"]["qA1"]["xy"]["operations"]
    assert "x180_DragCosine" not in ops and _isnan(ops["x180_v2"]["detuning"])


def test_duplicate_of_a_nan_pulse_is_a_400_naming_the_field(nan_chip):
    c, _ = nan_chip
    r = c.post("/api/pulse/duplicate", data={"path": f"{XY}.x180_DragCosine",
                                             "new_name": "x180_copy"})
    assert r.status_code == 400, r.get_data(as_text=True)[:300]
    assert f"{XY}.x180_copy.detuning" in r.get_data(as_text=True)
    assert "x180_copy" not in _store_of(c).state["qubits"]["qA1"]["xy"]["operations"]


def test_duplicate_of_a_finite_pulse_still_writes(nan_chip):
    c, _ = nan_chip
    r = c.post("/api/pulse/duplicate", data={"path": f"{XY}.saturation", "new_name": "sat2"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _raw(c, f"{XY}.sat2.amplitude") == 0.004


def _x90_alpha(c):
    return _store_of(c).state["qubits"]["qA1"]["xy"]["operations"]["x90_DragCosine"]["alpha"]


@pytest.mark.parametrize("value", ['"nan"', '"inf"', '"1e999"', '"-Infinity"'])
def test_pulse_literal_edit_refuses_nonfinite(nan_chip, value):
    """A quoted literal parses as text, and the float() cast for a float
    field turns it into NaN or Infinity: judged after the cast."""
    c, _ = nan_chip
    r = c.post("/pulse/edit", data={"path": f"{XY}.x90_DragCosine",
                                    "dot_path": f"{XY}.x90_DragCosine.alpha",
                                    "mode": "literal", "value": value})
    assert r.status_code == 400, r.get_data(as_text=True)[:300]
    assert f"{XY}.x90_DragCosine.alpha" in r.get_data(as_text=True)
    assert _x90_alpha(c) == "#../x180_DragCosine/alpha"          # the link stays


def test_pulse_literal_edit_still_writes_a_finite_value(nan_chip):
    c, _ = nan_chip
    r = c.post("/pulse/edit", data={"path": f"{XY}.x90_DragCosine",
                                    "dot_path": f"{XY}.x90_DragCosine.alpha",
                                    "mode": "literal", "value": "-0.25"})
    assert r.status_code == 200, r.get_data(as_text=True)[:300]
    assert _x90_alpha(c) == -0.25


@pytest.mark.parametrize("value", ["nan", "inf", "1e999", "-Infinity"])
def test_pulse_literal_edit_refuses_bare_nonfinite_text(nan_chip, value):
    c, _ = nan_chip
    r = c.post("/pulse/edit", data={"path": f"{XY}.x90_DragCosine",
                                    "dot_path": f"{XY}.x90_DragCosine.alpha",
                                    "mode": "literal", "value": value})
    assert r.status_code == 400, r.get_data(as_text=True)[:300]
    assert _x90_alpha(c) == "#../x180_DragCosine/alpha"
