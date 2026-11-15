"""Allowed time of flight values and the writers that move them."""
import copy
import json

import pytest

from quam_state_manager.core import output_timing, regenerate
from tests.test_regen_mw_band_shift import _chip, _write
from tests.test_run_build_delay import _chip as _machine, _load_helpers


@pytest.mark.parametrize("value, nearest", [(24, 28), (26, 28), (30, 32), (46, 48),
                                          (50, 52), (28, 28), (0, 28), (29, 28)])
def test_nearest_allowed(value, nearest):
    ok, actual, reason = output_timing.tof_on_grid(value)
    assert actual == nearest
    assert ok == (value == nearest)
    assert reason == ("" if ok else "the controller takes multiples of 4 ns, at least 28 ns")


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, None])
def test_invalid_inputs_are_refused(value):
    with pytest.raises(ValueError):
        output_timing.tof_on_grid(value)


def test_build_moves_six_ns_and_names_original_sum():
    mod = _load_helpers()
    m = _machine([2, 2], ro_band=3, tof=28)
    ro = m.qubits["q1"].resonator.opx_output
    ro.delay = 14
    import types

    class RecordedTof(types.SimpleNamespace):
        def __setattr__(self, name, value):
            if name == "time_of_flight" and hasattr(self, "writes"):
                self.writes.append(value)
            super().__setattr__(name, value)

    m.qubits["q1"].resonator = RecordedTof(opx_output=ro, time_of_flight=28, writes=[])
    moves = {}
    notes = mod._align_output_timing(m, tof_moves=moves)
    assert moves["q1"]["sum"] == 34 and moves["q1"]["from"] == 28
    assert ro.delay == 20
    assert m.qubits["q1"].resonator.writes == [36]
    assert [q.resonator.time_of_flight for q in m.qubits.values()] == [36, 36]
    assert any("time of flight 34 ns -> 36 ns: the controller takes multiples of 4 ns, at least 28 ns" in n for n in notes)
    assert mod._align_output_timing(m) == []


@pytest.mark.parametrize("delay", [0, 20])
def test_typed_off_grid_is_kept_and_reviewed(delay):
    mod = _load_helpers()
    m = _machine([2, 2], ro_band=3, tof=46)
    m.qubits["q1"].resonator.opx_output.delay = delay
    spec = {"populate": {"options": {"tof_defaults": {}},
                         "resonator": {"q1": {"time_of_flight": 46},
                                       "q2": {"time_of_flight": 46}}}}
    notes = mod._align_output_timing(m, spec)
    assert [q.resonator.time_of_flight for q in m.qubits.values()] == [46, 46]
    assert any("46 ns -> 48 ns: the controller takes multiples of 4 ns, at least 28 ns" in n
               and "Kept as you set it" in n for n in notes)
    state, wiring = _chip(tof=46)
    timing = output_timing.summary(state, wiring, tof_typed={"q1", "q2"})
    assert all("46 ns -> 48 ns" in t["grid_note"] for t in timing["tof"])


def test_default_writer_uses_the_rule_and_reports_original(monkeypatch):
    mod = _load_helpers()
    m = _machine([2, 2], ro_band=2, tof=28)
    pop = {"options": {"tof_defaults": {"q1": 26}},
           "resonator": {"q1": {"time_of_flight": 26}}}
    monkeypatch.setattr(mod, "_apply_pulses", lambda *args: None)
    mod.apply_populate(m, pop)
    assert m.qubits["q1"].resonator.time_of_flight == 28
    assert pop["resonator"]["q1"]["time_of_flight"] == 26
    assert any("26 ns -> 28 ns" in n for n in mod._align_output_timing(m, {"populate": pop}))


@pytest.mark.parametrize("tof, expected, raw", [(26, 48, 46), (28, 48, 48)])
def test_band_shift_plan_and_writer(tmp_path, tof, expected, raw):
    state, wiring = _chip(ro_band=3, tof=tof)
    original = copy.deepcopy(state)
    plan = regenerate.mw_band_shift_plan(state, wiring)
    assert state == original
    assert all(t["new"] == expected and t["sum"] == raw for t in plan["tof"])
    assert all(("grid_note" in t) == (raw != expected) for t in plan["tof"])
    if raw != expected:
        assert all("46 ns -> 48 ns: the controller takes multiples of 4 ns, at least 28 ns" in t["grid_note"] for t in plan["tof"])
    _write(tmp_path, state, wiring)
    regenerate.apply_mw_band_shift(tmp_path)
    written = json.loads((tmp_path / "state.json").read_text())
    assert {q["resonator"]["time_of_flight"] for q in written["qubits"].values()} == {expected}
    timing = output_timing.summary(written, wiring, tof_moved={q: tof for q in state["qubits"]},
                                   source=(state, wiring))
    assert all(t["sum"] == raw for t in timing["tof"])
    assert all(("grid_note" in t) == (raw != expected) for t in timing["tof"])


def test_no_move_keeps_stored_bytes_and_adds_no_tof_line(tmp_path):
    state, wiring = _chip(xy_bands=(2, 2), ro_band=2, tof=26)
    _write(tmp_path, state, wiring)
    before = (tmp_path / "state.json").read_bytes()
    assert regenerate.apply_mw_band_shift(tmp_path) is None
    assert (tmp_path / "state.json").read_bytes() == before
    assert output_timing.summary(state, wiring, tof_moved=[], source=(state, wiring))["tof"] == []


def test_result_annotation_keeps_the_build_sum():
    state, wiring = _chip(ro_band=3, tof=36)
    state["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 20
    note = "time of flight 34 ns -> 36 ns: the controller takes multiples of 4 ns, at least 28 ns"
    outcome = {"result": {"tof_moves": {"q1": {"from": 28, "sum": 34, "grid_note": note}}}}
    output_timing.annotate(outcome, state, wiring)
    row = outcome["result"]["output_timing"]["tof"][0]
    assert row["from"] == 28 and row["sum"] == 34 and row["grid_note"] == note


def test_emitted_build_contains_and_executes_the_shared_rule():
    from quam_state_manager.core import script_emitter
    scope = {}
    exec(script_emitter._runtime_block(), scope)
    assert scope["tof_on_grid"](30)[1] == 32
    m = _machine([2, 2], ro_band=3, tof=28)
    m.qubits["q1"].resonator.opx_output.delay = 14
    assert scope["_align_output_timing"](m)
    assert m.qubits["q1"].resonator.time_of_flight == 36


def test_unrecorded_off_grid_is_kept_and_named():
    mod = _load_helpers()
    m = _machine([2, 2], ro_band=2, tof=46)
    spec = {"populate": {"resonator": {"q1": {"time_of_flight": 46},
                                       "q2": {"time_of_flight": 46}}}}
    notes = mod._align_output_timing(m, spec)
    assert [q.resonator.time_of_flight for q in m.qubits.values()] == [46, 46]
    assert any("46 ns -> 48 ns" in n and "Kept as you set it" in n for n in notes)


def test_a_value_the_rule_cannot_place_is_left_alone():
    mod = _load_helpers()
    m = _machine([2, 2], ro_band=2, tof=28)
    m.qubits["q1"].resonator.time_of_flight = -4
    mod._align_output_timing(m)
    assert m.qubits["q1"].resonator.time_of_flight == -4
