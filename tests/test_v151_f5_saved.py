"""Saved records keep aliases and mutations throughout one replay."""
import ast
from quam_state_manager.generator import run_interactive_replot as runner


def test_saved_records_share_mutations_across_plot_bindings():
    def plot_first(plans):
        plans["pair"]["score"] += 1
        plans["list"].append(plans["pair"])
        return plans["pair"]["score"]
    def plot_second(plans):
        return plans.pair.score, len(plans["list"]), plans["list"][-1] is plans.pair
    shared = {"score": 0}
    results = {"plans": {"pair": shared, "alias": shared, "list": []}}
    context = {}
    tree = ast.parse("def draw(node):\n plot_first(node.namespace['plans'])\n plot_second(node.namespace['plans'])")
    def bind(fn):
        return runner._bind_args(fn, ds_raw=None, ds_fit=None, qubits=[], params={}, results=results,
                                 node_tree=tree, saved_context=context,
                                 plot_imports={name: ("calibration_utils.synthetic", name)
                                               for name in ("plot_first", "plot_second")})
    first = bind(plot_first)
    assert first["plans"].pair is first["plans"].alias
    assert plot_first(**first) == 1
    second = bind(plot_second)
    assert second["plans"] is first["plans"]
    assert plot_second(**second) == (1, 1, True)

    def plot_direct(plans):
        return plans["pair"]["score"]
    direct_tree = ast.parse("def draw(node):\n plot_direct(node.results['plans'])")
    direct = runner._bind_args(plot_direct, ds_raw=None, ds_fit=None, qubits=[], params={}, results=results,
                               node_tree=direct_tree, saved_context=context,
                               plot_imports={"plot_direct": ("calibration_utils.synthetic", "plot_direct")})
    assert direct["plans"] is first["plans"] and plot_direct(**direct) == 1


def test_shared_context_keeps_the_missing_input_path():
    def plot_missing(first, second):
        return second.required
    report = {}
    values = runner._bind_args(plot_missing, ds_raw=None, ds_fit=None, qubits=[], params={},
        results={"first": {}, "second": {}}, saved_context={}, bind_report=report,
        node_tree=ast.parse("def draw(node):\n plot_missing(node.namespace['first'], node.namespace['second'])"),
        plot_imports={"plot_missing": ("calibration_utils.synthetic", "plot_missing")})
    try:
        plot_missing(**values)
    except Exception as error:
        reason = runner._saved_input_reason(error, report)
    else:
        reason = "no missing-input error"
    assert "missing input 'second'" in reason and "second.required" in reason


def test_a_bound_saved_record_reports_as_a_mapping():
    def plot_plans(plans):
        return plans
    report = {}
    runner._bind_args(plot_plans, ds_raw=None, ds_fit=None, qubits=[], params={},
                      results={"plans": {"pair": {"score": 1}}}, saved_context={}, bind_report=report,
                      node_tree=ast.parse("def draw(node):\n plot_plans(node.namespace['plans'])"),
                      plot_imports={"plot_plans": ("calibration_utils.synthetic", "plot_plans")})
    # the tile lists the input as the mapping it is, never the adapter's class name
    assert report["plans"]["value"] == {"type": "dict"}
