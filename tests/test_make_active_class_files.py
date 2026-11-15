"""Make active and the status line agree about recorded class files, and
the records have one parser (core/gate_status.py)."""
from __future__ import annotations

import ast
import copy
from pathlib import Path

import pytest

from quam_state_manager.core import class_file_check, gate_check_log, gate_status
from quam_state_manager.web import gate_status_routes as routes
from tests.test_gate_class_files import _entry, _items, _macro
from tests.test_gate_status_routes import _ctx, _ma, _merged, rig  # noqa: F401

MACRO = "qubit_pairs.q1-q2.macros.cz_sq"


def _record(rig, record):
    _ctx(rig)["modifier"].set_value(MACRO + ".extras", {"sm_class_files": [record]})


@pytest.mark.parametrize("state", ["present", "missing", "changed"])
def test_make_active_checks_the_published_file(rig, tmp_path, monkeypatch, state):
    path = tmp_path / "GateShape.py"
    record = _entry(path)
    _record(rig, record)
    if state == "missing":
        path.unlink()
    elif state == "changed":
        path.write_bytes(b"class A: changed = True\n")
    lookups = []

    def published(instance, scope, qclass):
        lookups.append((instance, scope, qclass))
        return str(path)

    monkeypatch.setattr(routes.quam_config_save, "published_path", published)
    store = _ctx(rig)["store"]
    before = copy.deepcopy(store.state)
    changes, seq = len(store.change_log), store.mutation_seq
    response = _ma(rig)
    assert lookups and lookups[0][0] == rig._app.instance_path
    assert lookups[0][2] == record["qclass"]
    if state == "present":
        assert response.status_code == 200
        assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_sq"
        assert rig.post("/undo").status_code == 200
        assert store.state == before
    else:
        assert response.status_code == 409
        message = response.get_json()["error"]
        assert path.name in message
        advice = (f"Save {path.name} to quam_config first." if state == "missing"
                  else "changed since the gate was checked. Re-check the gate first.")
        assert advice in message and message.endswith("It was not made active.")
        assert store.state == before
        assert store.mutation_seq == seq and len(store.change_log) == changes
        assert rig._calls == []
        folder = gate_check_log.chip_dir(rig._app.config["history_manager"], _ctx(rig)["path"])
        assert gate_check_log.entry(folder, "q1-q2", "cz_sq") is None
        assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_two"
        assert rig.post("/undo").status_code == 200
        assert not _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"].get("extras")


def test_make_active_without_sm_records_keeps_its_behavior(rig):
    assert _ma(rig).status_code == 200
    assert rig._calls
    assert _merged(rig)["qubit_pairs"]["q1-q2"]["macros"]["cz"] == "#./cz_sq"


@pytest.mark.parametrize("name", [7, None, ""])
def test_bad_record_is_missing_and_refuses_activation(rig, name):
    record = {"file": name, "qclass": "quam_config.Shape.Shape", "sha256": "abc"}
    _record(rig, record)
    items, _ = _items(_macro([record]))
    assert items["class"]["tone"] == "warn"
    assert items["class"]["text"] == "class file missing"
    assert items["class"]["why"] == "its class file record has no file name"
    store = _ctx(rig)["store"]
    before, seq, changes = copy.deepcopy(store.state), store.mutation_seq, len(store.change_log)
    response = _ma(rig)
    assert response.status_code == 409
    assert "no file name" in response.get_json()["error"]
    assert store.state == before and store.mutation_seq == seq
    assert len(store.change_log) == changes and rig._calls == []


@pytest.mark.parametrize("digest", [None, "", 5, "missing-key"])
def test_record_without_a_hash_is_missing_and_refuses_activation(rig, tmp_path, digest):
    path = tmp_path / "GateShape.py"
    record = dict(_entry(path), qclass="quam_config.Shape.Shape")
    if digest == "missing-key":
        del record["sha256"]
    else:
        record["sha256"] = digest
    assert path.exists()
    _record(rig, record)
    items, _ = _items(_macro([record]))
    assert items["class"]["tone"] == "warn"
    assert items["class"]["text"] == "class file missing"
    assert items["class"]["why"] == "its class file record has no content hash"
    assert "no file name or content hash" in class_file_check._file_problem(tmp_path, record)
    store = _ctx(rig)["store"]
    before, seq, changes = copy.deepcopy(store.state), store.mutation_seq, len(store.change_log)
    response = _ma(rig)
    assert response.status_code == 409
    error = response.get_json()["error"]
    assert "no content hash" in error and "Delete the gate and make it again" in error
    assert store.state == before and store.mutation_seq == seq
    assert len(store.change_log) == changes and rig._calls == []


def test_raw_reader_preserves_records_that_live_write_must_refuse(tmp_path):
    for record in ({"file": 7}, {"sha256": "abc"}, {"file": "Shape.py"}):
        macro = _macro([record, None, "ignored"])
        assert gate_status.class_file_records(macro) == [record]
        payload = {"qubit_pairs": {"p": {"macros": {"g": macro}}}}
        gates = class_file_check.gates_to_check(payload, {})
        assert gates == [("p", "g", [record])]
        assert "no file name or content hash" in class_file_check._file_problem(tmp_path, record)


def test_an_upper_case_recorded_hash_reads_present_as_the_live_check(tmp_path):
    path = tmp_path / "GateShape.py"
    record = dict(_entry(path), sha256=_entry(path)["sha256"].upper())
    assert gate_status.file_state(record, str(path)) == "present"
    assert class_file_check._file_problem(tmp_path, record) is None


def test_every_missing_and_changed_file_is_named(tmp_path):
    a, b = tmp_path / "ShapeA.py", tmp_path / "ShapeB.py"
    ra, rb = dict(_entry(a), qclass="qa"), dict(_entry(b), qclass="qb")
    a.unlink()
    b.write_bytes(b"class B: changed = True\n")
    paths = {"qa": str(a), "qb": str(b)}
    states = gate_status.class_file_states(_macro([ra, rb]), paths.get)
    assert [st for _f, st in states] == ["missing", "changed"]
    text = routes._class_file_refusal(states)
    assert "Save ShapeA.py to quam_config first." in text
    assert "The class file ShapeB.py changed since the gate was checked. Re-check the gate first." in text
    assert text.endswith("It was not made active.")
    assert routes._class_file_refusal([]) is None


def test_class_file_record_parser_has_one_implementation():
    """The records are parsed in core/gate_status.py only; every other
    module reads them through it."""
    pkg = Path(__file__).resolve().parents[1] / "quam_state_manager"
    readers = {"sm_class_files", "class_file_records", "class_file_states"}
    defined = []
    for path in pkg.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        defined += [(path.name, n.name) for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in readers]
    assert sorted(defined) == [("gate_status.py", n) for n in sorted(readers)]
    assert class_file_check.sm_class_files is gate_status.class_file_records


def _is_extras(node, names):
    """True for ``x.get("extras")``, ``x["extras"]``, either with an ``or`` default or a conditional,
    or a name bound from one."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.IfExp):
        return _is_extras(node.body, names) or _is_extras(node.orelse, names)
    if isinstance(node, ast.BoolOp):
        return any(_is_extras(v, names) for v in node.values)
    if isinstance(node, ast.Subscript):
        return isinstance(node.slice, ast.Constant) and node.slice.value == "extras"
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and bool(node.args)
            and isinstance(node.args[0], ast.Constant) and node.args[0].value == "extras")


def _extras_reads(tree):
    """Reads of the ``sm_class_files`` key out of a macro's extras: a ``.get``
    or subscript of it on an extras value (inline or through a name bound from
    one), or a ``"sm_class_files" in extras`` test."""
    names = {t.id for n in ast.walk(tree) if isinstance(n, ast.Assign) and _is_extras(n.value, set())
             for t in n.targets if isinstance(t, ast.Name)}
    found = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                and n.args and isinstance(n.args[0], ast.Constant)
                and n.args[0].value == "sm_class_files" and _is_extras(n.func.value, names)):
            found.append(n)
        elif (isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Constant)
                and n.slice.value == "sm_class_files" and _is_extras(n.value, names)):
            found.append(n)
        elif (isinstance(n, ast.Compare) and isinstance(n.left, ast.Constant)
                and n.left.value == "sm_class_files"
                and any(isinstance(op, ast.In) for op in n.ops)
                and any(_is_extras(c, names) for c in n.comparators)):
            found.append(n)
    return found


def test_only_gate_status_reads_the_class_file_extras_key():
    """No module but core/gate_status.py reads ``sm_class_files`` out of a
    macro's extras, however it names the variables."""
    pkg = Path(__file__).resolve().parents[1] / "quam_state_manager"
    own = pkg / "core" / "gate_status.py"
    assert len(_extras_reads(ast.parse(own.read_text(encoding="utf-8")))) == 1
    for path in pkg.rglob("*.py"):
        if path != own:
            assert not _extras_reads(ast.parse(path.read_text(encoding="utf-8"))), path.name
