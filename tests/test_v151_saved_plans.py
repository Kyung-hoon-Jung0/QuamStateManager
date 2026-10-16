"""v1.5.1 (D3 class A): a saved namespace stand-in is attribute-readable at every depth, and a
plot it still cannot feed names the missing input instead of an internal Python error.

The planner replay raised ``'dict' object has no attribute 'control'``: data.json held the plans
as dicts nested in the saved mapping, and only the top-level mapping was wrapped.
"""
import ast
import sys
from types import ModuleType

import pytest

from quam_state_manager.core.interactive_plots import replot_revision
from quam_state_manager.generator import run_interactive_replot as runner

_NODE = """
def draw(node):
    plans = node.namespace['plans']
    {fn}(plans)
"""


@pytest.fixture
def plots(monkeypatch):
    module = ModuleType("synthetic_planner_plots")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    exec("""
def plot_pair_plans(plans):
    out = {}
    for name, plan in plans.items():
        scores = [c.score for c in plan.candidates]           # dicts inside a list
        corners = [c.edge.lo for c in plan.corners]             # dicts inside a tuple-like list
        out[name] = (plan.control.name, plan.target.f_01_hz, scores, corners)
    return out

def plot_settings(plans):
    return [plan.settings.mode for plan in plans.values()]     # not in the saved summary

def plot_broken(plans):
    raise ValueError('a bug of the plot itself')

def plot_plan_list(plans):
    return [plan.control.name for plan in plans]                # a LIST of saved plan dicts

def plot_tolerant(plans):
    return [getattr(plan, 'settings', 'default') for plan in plans.values()]
""", module.__dict__)
    return module


_SAVED = {"q1-2": {"pair": "q1-2", "control": {"name": "q1", "f_01_hz": 4.9e9},
                   "target": {"name": "q2", "f_01_hz": 3.4e9},
                   "candidates": [{"score": 1.0}, {"score": 0.5}],
                   "corners": ({"edge": {"lo": 1}}, {"edge": {"lo": 2}})}}


def _call(fn, values):
    """The plot's result, or the error it raised as text (so a regression fails an assert)."""
    try:
        return fn(**values)
    except Exception as error:   # noqa: BLE001 -- any error is a wrong result here
        return f"{type(error).__name__}: {error}"


def _bind(fn, report, saved=_SAVED):
    return runner._bind_args(fn, ds_raw=None, ds_fit=None, qubits=[], params={}, results={"plans": saved},
                             node_tree=ast.parse(_NODE.format(fn=fn.__name__)), bind_report=report,
                             plot_imports={fn.__name__: ("calibration_utils.synthetic", fn.__name__)})


def test_saved_plans_are_readable_at_every_depth(plots):
    report = {}
    values = _bind(plots.plot_pair_plans, report)
    assert report["plans"]["source"] == "data.plans via node.namespace.plans"
    assert _call(plots.plot_pair_plans, values) == {"q1-2": ("q1", 3.4e9, [1.0, 0.5], [1, 2])}
    assert values["plans"] == _SAVED and isinstance(values["plans"]["q1-2"]["corners"], tuple)


def test_a_plot_the_summary_cannot_feed_names_the_missing_input(plots):
    report = {}
    values = _bind(plots.plot_settings, report)
    try:
        plots.plot_settings(**values)
    except Exception as error:   # noqa: BLE001 -- the runner's own except clause
        trace = runner._plot_error_trace(error, report)
    shown = replot_revision.reasons({"errors": [{"stage": "plot:settings", "trace": trace}]})[-1]
    assert shown == ("missing input 'plans': the plot needs node.namespace['plans'] as the node's analysis "
                     "built it; data.json saves only a summary (no plans['q1-2'].settings)")
    assert "AttributeError" not in shown and "Error" not in shown


def test_an_error_of_the_plot_itself_is_not_blamed_on_the_summary(plots):
    report = {}
    values = _bind(plots.plot_broken, report)
    try:
        plots.plot_broken(**values)
    except Exception as error:   # noqa: BLE001
        trace = runner._plot_error_trace(error, report)
    assert trace.strip().splitlines()[-1] == "ValueError: a bug of the plot itself"


def test_a_tolerant_plot_still_gets_its_default(plots):
    assert _call(plots.plot_tolerant, _bind(plots.plot_tolerant, {})) == ["default"]


def test_a_saved_list_of_plans_is_readable(plots):
    saved = [{"control": {"name": "q1"}}, {"control": {"name": "q3"}}]
    assert _call(plots.plot_plan_list, _bind(plots.plot_plan_list, {}, saved)) == ["q1", "q3"]
