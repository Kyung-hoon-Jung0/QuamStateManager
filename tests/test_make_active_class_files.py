"""Make active and the status line agree about recorded class files."""
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
        advice = f"Save {path.name} to quam_config first" if state == "missing" else "Re-check the gate first"
        assert advice in message
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


def test_raw_reader_preserves_records_that_live_write_must_refuse(tmp_path):
    for record in ({"file": 7}, {"sha256": "abc"}, {"file": "Shape.py"}):
        macro = _macro([record, None, "ignored"])
        assert gate_status.class_file_records(macro) == [record]
        payload = {"qubit_pairs": {"p": {"macros": {"g": macro}}}}
        gates = class_file_check.gates_to_check(payload, {})
        assert gates == [("p", "g", [record])]
        assert "no file name or content hash" in class_file_check._file_problem(tmp_path, record)


def test_class_file_record_parser_has_one_implementation():
    root = Path(__file__).resolve().parents[1] / "quam_state_manager" / "core"
    for name in ("gate_status", "class_file_check"):
        tree = ast.parse((root / (name + ".py")).read_text(encoding="utf-8"))
        definitions = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        readers = {"sm_class_files", "class_file_records"} & definitions
        assert readers == ({"sm_class_files", "class_file_records"} if name == "gate_status" else set())
        parsing = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                   and isinstance(n.func, ast.Attribute) and n.func.attr == "get"
                   and n.args and isinstance(n.args[0], ast.Constant)
                   and n.args[0].value in ("extras", "sm_class_files")]
        assert len(parsing) == (2 if name == "gate_status" else 0)
