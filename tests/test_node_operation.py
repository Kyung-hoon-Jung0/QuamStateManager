"""The node's operation name: ``arb_operation`` when set, else ``operation``,
read by one function in the four places that fill an ``{operation}`` slot."""
from __future__ import annotations

import inspect

from quam_state_manager.core import fit_targets
from quam_state_manager.core.autofit import families, gates
from quam_state_manager.core.fit_targets import node_operation


def test_node_operation():
    assert node_operation({"arb_operation": "cz_x", "operation": "cz"}) == "cz_x"
    assert node_operation({"operation": "cz"}) == "cz"
    assert node_operation({"arb_operation": None, "operation": "cz"}) == "cz"
    assert node_operation({"arb_operation": "", "operation": "cz"}) == "cz"
    assert node_operation(None) is None
    assert node_operation("x") is None


def test_the_four_readers_ask_the_one_function():
    for fn in (fit_targets.resolve_fit_targets, families.trend_path_for,
               families.resolve_updates, gates.evaluate_target):
        src = inspect.getsource(fn)
        assert "node_operation(" in src, fn.__name__
        assert '.get("operation")' not in src, fn.__name__


NEW = {"arb_operation": "cz_x", "operation": "cz"}
OLD = {"operation": "cz"}
FAM = "cz_conditional_phase"


def _run(params):
    return {"experiment_name": "2Q_20b_cz_conditional_phase_error_amp",
            "fit_results": {"q1-q2": {"optimal_amplitude": 0.3, "success": True}},
            "outcomes": {"q1-q2": "successful"}, "parameters": params}


def test_fit_targets_reader():
    for params, op in ((NEW, "cz_x"), (OLD, "cz")):
        out = fit_targets.resolve_fit_targets(_run(params))
        assert out["q1-q2"]["optimal_amplitude"]["path"] == (
            f"qubit_pairs.q1-q2.macros.{op}.flux_pulse_qubit.amplitude")


def test_trend_path_reader():
    fam = families.FAMILIES[FAM]
    for params, op in ((NEW, "cz_x"), (OLD, "cz")):
        path = families.trend_path_for(fam, "optimal_amplitude", "q1-q2", params)
        assert path == f"qubit_pairs.q1-q2.macros.{op}.flux_pulse_qubit.amplitude"


def test_resolve_updates_reader():
    fam = families.FAMILIES[FAM]
    for params, op in ((NEW, "cz_x"), (OLD, "cz")):
        rows = families.resolve_updates(fam, "q1-q2", {"optimal_amplitude": 0.3},
                                        params, lambda p: 0.29)
        assert [r["path"] for r in rows] == [
            f"qubit_pairs.q1-q2.macros.{op}.flux_pulse_qubit.amplitude"]


def test_evaluate_target_reader():
    fam = families.FAMILIES[FAM]
    for params, op in ((NEW, "cz_x"), (OLD, "cz")):
        asked = []

        def value_of(p):
            asked.append(p)
            return 0.29
        gates.evaluate_target(_run(params), fam, "q1-q2", current_value_of=value_of,
                              pre_update_value_of=value_of)
        assert f"qubit_pairs.q1-q2.macros.{op}.flux_pulse_qubit.amplitude" in asked, asked
