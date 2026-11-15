"""Allowed time of flight values and the writers that move them."""
import copy
import json

import pytest

from quam_state_manager.core import output_timing, regenerate
from tests.test_regen_mw_band_shift import _chip, _write
from tests.test_run_build_delay import _chip as _machine, _load_helpers

REASON = "the controller takes multiples of 4 ns, at least 28 ns"
ROUNDED_34 = ("Time of flight 34 ns rounded up to 36 ns (" + REASON + "): acquisition starts "
              "2 ns after the return; measure time of flight again.")
ROUNDED_46 = ("Time of flight 46 ns rounded up to 48 ns (" + REASON + "): acquisition starts "
              "2 ns after the return; measure time of flight again.")
KEPT_46 = "Time of flight 46 ns is kept as you set it, but " + REASON + " (nearest allowed 48 ns)."


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
    assert any(n == "q1: " + ROUNDED_34 for n in notes)
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
    assert any(n == "q1: " + KEPT_46 for n in notes)
    state, wiring = _chip(tof=46)
    if delay:
        state["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = delay
    timing = output_timing.summary(state, wiring, tof_typed={"q1", "q2"})
    assert all(t["grid_note"] == KEPT_46 for t in timing["tof"])
    # nothing delayed: the row names only the grid, never "delayed by 0 ns"
    assert all(bool(t.get("grid_only")) == (delay == 0) for t in timing["tof"])


def test_default_writer_uses_the_rule_and_reports_original(monkeypatch):
    mod = _load_helpers()
    m = _machine([2, 2], ro_band=2, tof=28)
    pop = {"options": {"tof_defaults": {"q1": 26}},
           "resonator": {"q1": {"time_of_flight": 26}}}
    monkeypatch.setattr(mod, "_apply_pulses", lambda *args: None)
    mod.apply_populate(m, pop)
    assert m.qubits["q1"].resonator.time_of_flight == 28
    assert pop["resonator"]["q1"]["time_of_flight"] == 26
    assert ("q1: The default time of flight 26 ns was written as 28 ns (" + REASON + ")."
            in mod._align_output_timing(m, {"populate": pop}))


@pytest.mark.parametrize("tof, expected, raw", [(26, 48, 46), (28, 48, 48)])
def test_band_shift_plan_and_writer(tmp_path, tof, expected, raw):
    state, wiring = _chip(ro_band=3, tof=tof)
    original = copy.deepcopy(state)
    plan = regenerate.mw_band_shift_plan(state, wiring)
    assert state == original
    assert all(t["new"] == expected and t["sum"] == raw for t in plan["tof"])
    assert all(("grid_note" in t) == (raw != expected) for t in plan["tof"])
    if raw != expected:
        assert all(t["grid_note"] == ROUNDED_46 for t in plan["tof"])
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
    note = ROUNDED_34
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
    assert any(n == "q1: " + KEPT_46 for n in notes)


def test_a_value_the_rule_cannot_place_is_left_alone():
    mod = _load_helpers()
    m = _machine([2, 2], ro_band=2, tof=28)
    m.qubits["q1"].resonator.time_of_flight = -4
    mod._align_output_timing(m)
    assert m.qubits["q1"].resonator.time_of_flight == -4


def test_review_survives_a_stored_value_the_rule_cannot_place():
    state, wiring = _chip(ro_band=3, tof=-4)
    assert output_timing.summary(state, wiring, tof_typed={"q1", "q2"}) is not None


def test_rounded_note_names_the_side_of_the_return():
    assert output_timing.tof_rounded_note(33, 32, REASON) == (
        "Time of flight 33 ns rounded down to 32 ns (" + REASON + "): acquisition starts "
        "1 ns before the return; measure time of flight again.")
    assert output_timing.tof_rounded_note(20, 28, REASON).startswith(
        "Time of flight 20 ns rounded up to 28 ns")


@pytest.mark.parametrize("bad", [float("nan"), -50])
def test_band_shift_offers_the_rest_when_one_stored_value_cannot_be_placed(tmp_path, bad):
    state, wiring = _chip(ro_band=3, tof=28)
    state["qubits"]["q2"]["resonator"]["time_of_flight"] = bad
    plan = regenerate.mw_band_shift_plan(state, wiring)
    assert plan and plan["ports"]
    assert [t["qubit"] for t in plan["tof"]] == ["q1"] and plan["tof"][0]["new"] == 48
    (miss,) = plan["tof_unplaced"]
    assert miss["qubit"] == "q2"
    assert "finite non-negative number" in miss["note"] and miss["note"].startswith(
        "Time of flight of q2 is not moved")
    assert miss["note"] in regenerate.mw_band_shift_offer_text(plan)
    assert miss["note"] in regenerate.mw_band_shift_question(plan)["why"]
    _write(tmp_path, state, wiring)
    applied = regenerate.apply_mw_band_shift(tmp_path)
    assert applied and applied["tof_unplaced"]
    written = json.loads((tmp_path / "state.json").read_text())
    assert written["qubits"]["q1"]["resonator"]["time_of_flight"] == 48
    stored = written["qubits"]["q2"]["resonator"]["time_of_flight"]
    assert stored == -50 if bad == -50 else stored != stored   # left as stored


def test_fresh_build_writes_an_off_grid_value_nobody_set_on_the_grid():
    mod = _load_helpers()
    m = _machine([2, 2], ro_band=2, tof=28)
    m.qubits["q1"].resonator.time_of_flight = 46     # a builder value, not in populate
    spec = {"populate": {"options": {"tof_defaults": {}}, "resonator": {}}}
    notes = mod._align_output_timing(m, spec)
    assert m.qubits["q1"].resonator.time_of_flight == 48
    assert m.qubits["q2"].resonator.time_of_flight == 28
    assert "q1: Time of flight 46 ns was written as 48 ns (" + REASON + ")." in notes


def test_build_result_carries_the_moves_into_the_review():
    import ast
    import inspect
    mod = _load_helpers()
    tree = ast.parse(inspect.getsource(mod.run_build))
    passed = [c.args[2].id for c in ast.walk(tree) if isinstance(c, ast.Call)
              and getattr(c.func, "id", None) == "_align_output_timing" and len(c.args) == 3]
    keys = {k.value: v for d in ast.walk(tree) if isinstance(d, ast.Dict)
            for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant)}
    assert passed and isinstance(keys.get("tof_moves"), ast.Name)
    assert keys["tof_moves"].id == passed[0]
    # and the review reads exactly what the build recorded
    m = _machine([2, 2], ro_band=3, tof=28)
    m.qubits["q1"].resonator.opx_output.delay = 14
    moves = {}
    mod._align_output_timing(m, tof_moves=moves)
    state, wiring = _chip(ro_band=3, tof=36)
    state["ports"]["mw_outputs"]["con1"]["1"]["8"]["delay"] = 20
    outcome = {"result": {"tof_moves": moves}}
    output_timing.annotate(outcome, state, wiring)
    rows = outcome["result"]["output_timing"]["tof"]
    assert rows and all(r["sum"] == 34 and r["from"] == 28 and r["grid_note"] == ROUNDED_34
                        for r in rows)
