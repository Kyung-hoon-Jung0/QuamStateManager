"""The one status line of a CZ gate (core.gate_status), item by item."""
from __future__ import annotations

import copy
import types

from quam_state_manager.core import gate_check_log as log
from quam_state_manager.core import gate_status as gs
from tests.cz_gate_chip import add_gate, chip


class _Pair:
    def __init__(self, state):
        self.state, self.wiring = state, {}


def _store(state):
    return types.SimpleNamespace(merged=state)


def _ctx(monkeypatch, live, *, broken=False):
    from quam_state_manager.core import working_copy

    def read(wc, attempts=None):
        if broken:
            raise OSError("the live files are locked")
        return _Pair(live)
    monkeypatch.setattr(working_copy, "read_live_shared", read)
    return {"working_copy": object()}


def _by_key(items):
    return {i["key"]: i for i in items}


def test_where_only_in_working_copy_then_on_the_chip(monkeypatch):
    s, _ = add_gate(chip())
    items = _by_key(gs.status(_store(s), _ctx(monkeypatch, chip()), "q1-q2", "cz_sq"))
    assert items["where"]["text"] == "only in the working copy"
    items = _by_key(gs.status(_store(s), _ctx(monkeypatch, s), "q1-q2", "cz_sq"))
    assert items["where"]["text"] == "on the chip"


def test_where_live_unreadable_and_no_live(monkeypatch):
    s, _ = add_gate(chip())
    items = _by_key(gs.status(_store(s), _ctx(monkeypatch, s, broken=True), "q1-q2", "cz_sq"))
    assert items["where"]["text"].startswith("cannot tell: ")
    assert "locked" in items["where"]["why"]
    items = _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq"))
    assert items["where"]["text"] == "cannot tell: no live chip"


def test_active_and_not_active(monkeypatch):
    s, _ = add_gate(chip())
    assert _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq"))["active"]["text"] == "not active"
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "#./cz_sq"
    assert _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq"))["active"]["text"] == "active"


def test_fixed_items_and_the_class_not_importable():
    s, _ = add_gate(chip())
    items = _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq"))
    assert items["class"]["text"] == "not a class SM made"
    assert items["scaling"]["text"] == "amplitude scaling: not known"
    cls = s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]["__class__"]
    items = _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq",
                              missing_classes=[cls], env_name="labenv"))
    assert items["class"]["text"] == "class CZGate not importable in labenv"
    assert items["class"]["tone"] == "warn"


def test_spectator_item_only_with_spectators():
    s, _ = add_gate(chip())
    assert "spectators" not in _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq"))
    m = s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]
    m["spectator_qubits"] = {"q3": "#/qubits/q3"}
    m["spectator_qubits_phase_shift"] = {"q3": 0.0}
    assert _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq"))["spectators"]["text"] == \
        "spectator phases not calibrated"
    m["spectator_qubits_phase_shift"] = {"q3": 0.12}
    assert "spectators" not in _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq"))


def test_check_item_in_each_state(tmp_path):
    s, _ = add_gate(chip())

    def check():
        return _by_key(gs.status(_store(s), None, "q1-q2", "cz_sq",
                                 check_folder=tmp_path))["check"]
    assert check()["text"] == "no check record"
    h = log.content_hash(s, "q1-q2", "cz_sq")
    log.record(tmp_path, "q1-q2", "cz_sq", result=log.CHECKED, reason=None, content=h)
    assert check()["text"].startswith("checked with the lab's code ")
    log.record(tmp_path, "q1-q2", "cz_sq", result=log.NOT_CHECKED, reason="no env", content=h)
    assert check()["text"] == "not checked: no env"
    log.record(tmp_path, "q1-q2", "cz_sq", result=log.CHECKED, reason=None, content=h)
    s["qubit_pairs"]["q1-q2"]["macros"]["cz_sq"]["flux_pulse_qubit"]["amplitude"] = 0.25
    assert check()["text"] == "values changed since the check"
    assert check()["tone"] == "warn"


def test_a_gate_that_is_gone():
    items = gs.status(_store(chip()), None, "q1-q2", "cz_sq")
    assert items[0]["key"] == "gone"


def test_cz_gates_skips_the_alias():
    s, _ = add_gate(chip())
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "#./cz_sq"
    assert gs.cz_gates(s, "q1-q2") == ["cz_sq"]


def test_plan_default_row_follows_the_active_gate():
    from quam_state_manager.core import cz_gate_plan
    rows = cz_gate_plan.OperationRows()
    rows.active["p"] = "cz_b"
    ops = [{"operation": "cz"}, {"operation": "cz_a"}, {"operation": "cz_b"}]
    assert cz_gate_plan._default_row(ops, rows.active["p"])["operation"] == "cz_b"
    assert cz_gate_plan._default_row(ops, None)["operation"] == "cz"
    assert cz_gate_plan._default_row(ops[1:], None)["operation"] == "cz_a"


def test_plan_rows_record_the_active_gate():
    from quam_state_manager.core import cz_gate_plan
    s, _ = add_gate(chip())
    s, _ = add_gate(s, name="cz_two")
    s["qubit_pairs"]["q1-q2"]["macros"]["cz"] = "#./cz_two"
    rows, _ = cz_gate_plan.operations(copy.deepcopy(s))
    assert rows.active["q1-q2"] == "cz_two"
    assert rows.active["q2-q3"] is None
