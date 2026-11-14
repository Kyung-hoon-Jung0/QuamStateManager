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


def _replay_wrappers(sources):
    """Index one-level package helpers that forward a value to a write."""
    import ast

    wrappers = {}
    for source in sources:
        for fn in ast.walk(ast.parse(source)):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            params = [a.arg for a in fn.args.posonlyargs + fn.args.args]
            params += [a.arg for a in fn.args.kwonlyargs]
            forwarded = set()
            for call in ast.walk(fn):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in ("set_value", "create_subtree")):
                    continue
                if any(k.arg == "restore" and isinstance(k.value, ast.Constant)
                       and k.value.value is True for k in call.keywords):
                    continue
                for value in call.args[1:] + [k.value for k in call.keywords if k.arg]:
                    forwarded.update(n.id for n in ast.walk(value)
                                     if isinstance(n, ast.Name) and n.id in params)
            if forwarded:
                wrappers.setdefault(fn.name, set()).update(
                    (params.index(name), name) for name in forwarded)
    return wrappers


def _replay_violations(source, wrappers=None):
    """Discover replay writes, including values carried through local aliases."""
    import ast

    wrappers = wrappers if wrappers is not None else _replay_wrappers([source])
    bad = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(ast.walk(fn))
        methods = {t.id for n in nodes if isinstance(n, (ast.Assign, ast.AnnAssign))
                   and isinstance(n.value, ast.Attribute)
                   and n.value.attr in ("set_value", "create_subtree")
                   for target in (n.targets if isinstance(n, ast.Assign) else [n.target])
                   for t in ast.walk(target) if isinstance(t, ast.Name)}
        inverse = any(isinstance(n, ast.Call)
                      and ((isinstance(n.func, ast.Attribute) and n.func.attr == "inverse_ops")
                           or (isinstance(n.func, ast.Name) and n.func.id == "inverse_ops"))
                      for n in nodes)
        journal = inverse or any((isinstance(n, ast.Name) and "journal" in n.id)
                                 or (isinstance(n, ast.arg) and "journal" in n.arg)
                                 or (isinstance(n, ast.Subscript)
                                     and isinstance(n.slice, ast.Constant)
                                     and n.slice.value == "entries")
                                 or (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                                     and n.func.attr == "get" and n.args
                                     and isinstance(n.args[0], ast.Constant)
                                     and n.args[0].value == "entries")
                                 for n in nodes)
        aliases = set()
        records = set()

        def journal_record(expr):
            return any((isinstance(n, ast.Name) and n.id in records)
                       or (isinstance(n, ast.Attribute) and n.attr == "change_log")
                       for n in ast.walk(expr))

        def historical(expr):
            for n in ast.walk(expr):
                if isinstance(n, ast.Name) and n.id in aliases:
                    return True
                if (isinstance(n, ast.Attribute) and (n.attr == "old_value"
                        or (n.attr == "new_value" and (journal or journal_record(n.value))))):
                    return True
                if (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                        and (n.slice.value == "old" or (n.slice.value == "new"
                             and (journal or journal_record(n.value))))):
                    return True
                if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "get" and n.args
                        and isinstance(n.args[0], ast.Constant)
                        and (n.args[0].value == "old" or (n.args[0].value == "new"
                             and (journal or journal_record(n.func.value))))):
                    return True
            return False

        # Iterate to follow aliases regardless of assignment order in ast.walk.
        while True:
            before = (aliases.copy(), records.copy())
            for n in nodes:
                if isinstance(n, (ast.Assign, ast.AnnAssign)) and n.value is not None:
                    targets = n.targets if isinstance(n, ast.Assign) else [n.target]
                    if journal_record(n.value):
                        records.update(t.id for target in targets for t in ast.walk(target)
                                       if isinstance(t, ast.Name))
                    if historical(n.value):
                        aliases.update(t.id for target in targets for t in ast.walk(target)
                                       if isinstance(t, ast.Name))
                if isinstance(n, (ast.For, ast.AsyncFor)) and historical(n.iter):
                    aliases.update(t.id for t in ast.walk(n.target) if isinstance(t, ast.Name))
                if isinstance(n, (ast.For, ast.AsyncFor)) and journal_record(n.iter):
                    records.update(t.id for t in ast.walk(n.target) if isinstance(t, ast.Name))
            if (aliases, records) == before:
                break
        for n in nodes:
            if not isinstance(n, ast.Call):
                continue
            func = n.func
            direct = ((isinstance(func, ast.Attribute)
                       and func.attr in ("set_value", "create_subtree"))
                      or (isinstance(func, ast.Name) and func.id in methods)
                      or (isinstance(func, ast.Call) and isinstance(func.func, ast.Name)
                          and func.func.id == "getattr" and len(func.args) >= 2
                          and isinstance(func.args[1], ast.Constant)
                          and func.args[1].value in ("set_value", "create_subtree")))
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            forwarded = wrappers.get(name, set())
            values = (n.args[1:] + [k.value for k in n.keywords] if direct else
                      [n.args[i] for i, _ in forwarded if i < len(n.args)]
                      + [k.value for k in n.keywords if any(k.arg == p for _, p in forwarded)])
            if ((direct or forwarded) and (inverse or any(historical(a) for a in values))):
                if not any(k.arg == "restore" and isinstance(k.value, ast.Constant)
                           and k.value.value is True for k in n.keywords):
                    bad.append((fn.name, n.lineno))
    return bad


def test_every_replay_site_is_a_restore():
    root = Path(__file__).resolve().parents[1] / "quam_state_manager"
    sources = {p: p.read_text(encoding="utf-8-sig") for p in root.rglob("*.py")}
    wrappers = _replay_wrappers(sources.values())
    bad = [(str(p.relative_to(root)), fn, line) for p in root.rglob("*.py")
           for fn, line in _replay_violations(sources[p], wrappers)]
    assert not bad, bad


@pytest.mark.parametrize("body", [
    "ops = journal.inverse_ops(unit)\nfor op, path, value in ops:\n    mod.set_value(path, value)",
    "mod.create_subtree(path, entry.old_value)",
    "saved = entry.old_value\nvalue = saved\nmod.set_value(path, value)",
    'value = entry["old"]\nmod.create_subtree(path, value)',
    'value = entry.get("new")\nmod.set_value(path, value)',
    'for entry in unit["entries"]:\n    mod.create_subtree(path, entry["new"])',
    'for entry in store.change_log:\n    mod.set_value(path, entry.new_value)',
])
def test_replay_scan_flags_a_new_site(body):
    source = "def planted(mod, journal, unit, entry, path):\n" + "\n".join(
        "    " + line for line in body.splitlines())
    assert _replay_violations(source)
    restored = source.replace("mod.set_value(path, value)",
                              "mod.set_value(path, value, restore=True)")
    restored = restored.replace("mod.create_subtree(path, entry.old_value)",
                                "mod.create_subtree(path, entry.old_value, restore=True)")
    restored = restored.replace("mod.create_subtree(path, value)",
                                "mod.create_subtree(path, value, restore=True)")
    restored = restored.replace('mod.create_subtree(path, entry["new"])',
                                'mod.create_subtree(path, entry["new"], restore=True)')
    restored = restored.replace("mod.set_value(path, entry.new_value)",
                                "mod.set_value(path, entry.new_value, restore=True)")
    assert not _replay_violations(restored)


@pytest.mark.parametrize("method", ["set_value", "create_subtree"])
@pytest.mark.parametrize("shape", ["alias", "getattr", "wrapper", "wrapper_keyword"])
def test_replay_scan_flags_indirect_writes(method, shape):
    helper = f"def forward(mod, path, value, **kw):\n    mod.{method}(path, value, **kw)\n"
    bodies = {
        "alias": f"write = mod.{method}\nwrite(path, entry.old_value)",
        "getattr": f'getattr(mod, "{method}")(path, entry.old_value)',
        "wrapper": "forward(mod, path, entry.old_value)",
        "wrapper_keyword": "forward(mod, path, value=entry.old_value)",
    }
    source = helper + "def planted(mod, path, entry):\n" + "\n".join(
        "    " + line for line in bodies[shape].splitlines())
    assert _replay_violations(source)
    restored = source.replace("entry.old_value)", "entry.old_value, restore=True)")
    assert not _replay_violations(restored)
    assert not _replay_violations(source.replace("entry.old_value", "0.5"))


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
    return _load_nan_chip(tmp_path, _nan_state())


def _load_nan_chip(tmp_path, state):
    # json.dumps writes NaN as the literal NaN, as a node's save does
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
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


@pytest.mark.parametrize("action", ["delete", "edit"])
def test_auto_apply_row_revert_restores_nan(nan_chip, action):
    from quam_state_manager.web import routes

    c, folder = nan_chip
    assert c.post("/auto-apply/arm").status_code == 200
    path = "qubits.qA1.fit_extra"
    data = {"dot_path": path} if action == "delete" else {
        "dot_path": path + ".a", "value": "0.5"}
    r = c.post("/field/" + action, data=data)
    assert r.status_code == 200, r.get_data(as_text=True)
    _apply(c)
    if action == "delete":
        assert "fit_extra" not in _live(folder)["qubits"]["qA1"]
    else:
        assert _live(folder)["qubits"]["qA1"]["fit_extra"]["a"] == 0.5
    with c.application.app_context():
        rows = routes._applied_log_rows()
    assert len(rows) == 1
    r = c.post("/auto-apply/revert", data={"unit_id": rows[0]["id"]})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _isnan(_raw(c, path + ".a"))
    _apply(c)  # the armed browser flushes the staged inverse through this door
    restored = _live(folder)["qubits"]["qA1"]["fit_extra"]
    assert _isnan(restored["a"]) and _isnan(restored["b"][1])


def test_field_edit_float_still_refuses_typed_nan(nan_chip):
    c, _ = nan_chip
    r = c.post("/field/edit", data={"dot_path": f"{XY}.saturation.amplitude",
                                    "value": "nan"})
    assert r.status_code == 400
    assert _raw(c, f"{XY}.saturation.amplitude") == 0.004


@pytest.fixture
def nan_pair_chip(tmp_path):
    state = _nan_state()
    state["qubits"]["qA1"]["z"] = {"operations": {"cz_pulse": {
        "__class__": QC + "SquarePulse", "amplitude": NAN, "length": 60}}}
    state["qubits"]["qB2"] = {"id": "qB2", "f_01": 5.0e9,
                                "z": {"operations": {}}}
    state["qubit_pairs"]["pair"] = {
        "id": "pair", "qubit_control": "#/qubits/qA1",
        "qubit_target": "#/qubits/qB2", "moving_qubit": "control",
        "macros": {"cz": {"__class__": "CZGate",
                           "flux_pulse_qubit": {"amplitude": NAN, "length": 60}}}}
    return _load_nan_chip(tmp_path, state)


def test_switch_moves_nan_pulse(nan_pair_chip):
    c, folder = nan_pair_chip
    r = c.post("/pair/pair/gate-inspector/switch-moving", data={"role": "target"})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _raw(c, "qubits.qA1.z.operations") == {}
    assert _isnan(_raw(c, "qubits.qB2.z.operations.cz_pulse.amplitude"))
    assert _raw(c, "qubit_pairs.pair.moving_qubit") == "target"
    _apply(c)
    live = _live(folder)
    assert live["qubits"]["qA1"]["z"]["operations"] == {}
    assert _isnan(live["qubits"]["qB2"]["z"]["operations"]["cz_pulse"]["amplitude"])


@pytest.mark.parametrize("stage", ["delete", "create", "set", "render", "plan"])
@pytest.mark.parametrize("prior", [False, True])
def test_switch_failure_rolls_back_request(nan_pair_chip, monkeypatch, stage, prior):
    from quam_state_manager.web import routes

    c, folder = nan_pair_chip
    store = _store_of(c)
    ctx = next(x for x in c.application.config["contexts"].values() if x.get("type") == "quam")
    modifier = ctx["modifier"]
    if prior:
        modifier.set_value("qubits.qA1.f_01", 6.0e9)
    before = json.dumps(store.state, sort_keys=True)
    live_before = (folder / "state.json").read_bytes()
    log = list(store.change_log)

    def fail(*args, **kwargs):
        raise TypeError("forced switch failure")

    if stage == "render":
        monkeypatch.setattr(routes, "_render_pair_detail", fail)
    elif stage == "plan":
        monkeypatch.setattr(routes.gate_inspector, "plan_switch_moving_qubit", fail)
    else:
        method = {"delete": "delete_subtree", "create": "create_subtree", "set": "set_value"}[stage]
        original = getattr(modifier, method)

        def stage_then_fail(*args, **kwargs):
            original(*args, **kwargs)
            fail()

        monkeypatch.setattr(modifier, method, stage_then_fail)
    r = c.post("/pair/pair/gate-inspector/switch-moving", data={"role": "target"})
    assert r.status_code in (400, 409), r.get_data(as_text=True)
    assert "forced switch failure" in r.get_data(as_text=True)
    assert json.dumps(store.state, sort_keys=True) == before
    assert store.change_log == log
    assert (folder / "state.json").read_bytes() == live_before


@pytest.mark.parametrize("fork", [False, True])
@pytest.mark.parametrize("stage", ["create", "render"])
def test_failed_switch_preserves_intact_redo(nan_pair_chip, monkeypatch, fork, stage):
    from quam_state_manager.web import routes

    c, _ = nan_pair_chip
    path = "qubits.qA1.f_01"
    assert c.post("/field/edit", data={"dot_path": path, "value": "6.3e9"}).status_code == 200
    assert c.post("/undo", headers=_JSON).status_code == 200
    assert _raw(c, path) == 6.25e9
    if fork:
        assert c.post("/field/edit", data={"dot_path": path, "value": "6.1e9"}).status_code == 200

    def fail(*args, **kwargs):
        raise TypeError("forced switch failure")

    with monkeypatch.context() as scoped:
        if stage == "render":
            scoped.setattr(routes, "_render_pair_detail", fail)
        else:
            ctx = next(x for x in c.application.config["contexts"].values()
                       if x.get("type") == "quam")
            scoped.setattr(ctx["modifier"], "create_subtree", fail)
        r = c.post("/pair/pair/gate-inspector/switch-moving", data={"role": "target"})
        assert r.status_code == 409, r.get_data(as_text=True)
    assert c.post("/redo", headers=_JSON).status_code == 200
    assert _raw(c, path) == (6.1e9 if fork else 6.3e9)


def test_live_redo_accepts_its_own_nan(nan_chip):
    c, folder = nan_chip
    path = "qubits.qA1.fit_extra.a"
    assert c.post("/field/edit", data={"dot_path": path, "value": "0.5"}).status_code == 200
    _apply(c)
    assert c.post("/undo", headers=_JSON).status_code == 200
    assert _isnan(_live(folder)["qubits"]["qA1"]["fit_extra"]["a"])
    r = c.post("/redo", headers=_JSON)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert _raw(c, "qubits.qA1.fit_extra.a") == 0.5
    assert _live(folder)["qubits"]["qA1"]["fit_extra"]["a"] == 0.5
    assert c.post("/undo", headers=_JSON).status_code == 200
    assert _isnan(_live(folder)["qubits"]["qA1"]["fit_extra"]["a"])
    assert "NaN" in (folder / "state.json").read_text(encoding="utf-8")


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
