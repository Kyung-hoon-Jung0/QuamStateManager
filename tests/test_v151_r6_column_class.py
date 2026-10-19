"""v1.5.1 (suite): a Live-Edit column heads no unit when its values are text or
true/false (R4 b9), so a plain write that moves a leaf between value classes
must move the column token -- else the patched grid keeps "Hz" over a text
value that a cold build heads with no unit (test_ram_liveedit seed 22)."""
from __future__ import annotations

from quam_state_manager.core import store_revs as SR
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.modifier import Modifier


def _store(tmp_path):
    import json
    (tmp_path / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"f_01": 5.0e9, "flag": True, "mode": "auto"}}}), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    return QuamStore(str(tmp_path))


def test_column_class():
    assert [SR.column_class(v) for v in (None, True, 0, 1.5, "2.5", "auto")] == \
        ["none", "bool", "num", "num", "num", "text"]


def test_a_value_class_flip_moves_the_column_token(tmp_path):
    st = _store(tmp_path)
    m = Modifier(st)
    t0 = SR.column_token(st)
    m.set_value("qubits.q1.f_01", 5.1e9, coerce=False, enforce=False)
    t1 = SR.column_token(st)
    assert t1 == t0, "a number for a number leaves the column model alone"
    m.set_value("qubits.q1.f_01", "auto", coerce=False, enforce=False)
    t2 = SR.column_token(st)
    assert t2 != t1, "a number -> text write moves the column model (no unit)"
    m.set_value("qubits.q1.mode", "fast", coerce=False, enforce=False)
    assert SR.column_token(st) == t2, "text for text leaves it alone"
    m.set_value("qubits.q1.mode", "3", coerce=False, enforce=False)
    t3 = SR.column_token(st)
    assert t3 != t2, "text -> a number written as text moves it (the unit comes back)"
