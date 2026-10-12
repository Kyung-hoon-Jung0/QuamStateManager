"""Saved-input replay pins using synthetic source trees and run data."""
import sys
import ast
import json
import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from types import ModuleType

import pytest

from quam_state_manager.generator import run_interactive_replot as runner


def test_module_resolution_reads_node_imports(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    nodes = tmp_path / "calibrations" / "group"
    nodes.mkdir(parents=True)
    for name, module in (("01_alias", "actual"), ("02_alias_dsp", "actual_dsp"),
                         ("03_power", "amplitude")):
        (nodes / (name + ".py")).write_text(
            f"from calibration_utils.{module} import plot_data as draw\n"
            "raise RuntimeError('acquisition must not execute')\n", encoding="utf-8")
        resolved, tree, imports = runner._resolve_node("1Q_" + name, str(tmp_path))
        assert resolved == "calibration_utils." + module
        assert tree is not None
        assert imports["draw"] == (resolved, "plot_data")
    assert str(nodes) in sys.path
    assert str(tmp_path / "calibrations") in sys.path


def test_module_resolution_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert runner._resolve_node("1Q_04_unlisted", str(tmp_path)) == (
        "calibration_utils.unlisted", None, {})


@dataclass
class Detail:
    centre: float = 0.0
    marker: float = 2.0


@dataclass
class Record:
    peak: float = 0.0
    details: dict[str, Detail] = field(default_factory=dict)


@dataclass
class IncompleteRecord:
    rate: float = float("nan")
    window: tuple[float, float] = (float("nan"), float("nan"))
    optional: float | None = None


def test_binder_restores_nonfinite_saved_numbers_and_preserves_optional_none():
    report = []
    restored = runner._restore_record(IncompleteRecord,
        {"rate": None, "window": [None, 3.0], "optional": None}, report, "pair0")
    assert isinstance(restored.rate, float) and math.isnan(restored.rate)
    assert isinstance(restored.window[0], float) and math.isnan(restored.window[0])
    assert restored.window[1] == 3.0 and restored.optional is None
    assert report[0]["restored_nonfinite"] == ["pair0.rate", "pair0.window.0"]
    assert report[0]["defaulted"] == {}
    json.dumps(report, allow_nan=False)


def plot_records(ds_fit, fits: dict[str, Record], qubit_pairs=None):
    pass


def test_binder_saved_inputs_and_keyword_only():
    def plot_inputs(qubit_pairs, pair_names, fringe_results, fit_results=None, *,
                    target_deg=180, stage=None, num_shots=None, limit=0.25, verdicts=None):
        pass
    pair = object()
    report = {}
    values = runner._bind_args(
        plot_inputs, ds_raw=None, ds_fit=None, qubits=[],
        params={"model": {"qubit_pairs": ["pair0"], "phase_target_deg": 90,
                          "stage": "scan", "num_shots": 64}},
        results={"fringe": {"q0": [1]}, "fit_results": {"pair0": {"peak": 4}},
                 "verdicts": {"pair0": "ok"}},
        machine=SimpleNamespace(qubit_pairs={"pair0": pair}), bind_report=report)
    assert values["qubit_pairs"] == [pair]
    assert values["pair_names"] == ["pair0"]
    assert values["fringe_results"] == {"q0": [1]}
    assert values["fit_results"] == {"pair0": {"peak": 4}}
    assert values["target_deg"] == 90 and values["stage"] == "scan"
    assert values["num_shots"] == 64 and values["verdicts"] == {"pair0": "ok"}
    assert "limit" not in values
    assert report["limit"] == {"status": "defaulted", "source": "signature", "value": 0.25}
    assert report["target_deg"]["source"] == "parameters.phase_target_deg"
    assert report["fringe_results"]["source"] == "data.fringe"
    assert set(report) == set(__import__("inspect").signature(plot_inputs).parameters)


def test_binder_restores_nested_records_and_reports_defaults():
    report = {}
    values = runner._bind_args(plot_records, ds_raw=None, ds_fit="dataset", qubits=[], params={},
                              results={"fit_results": {"pair0": {"peak": 4, "unused": 1,
                                        "details": {"left": {"centre": 3}}}}}, bind_report=report)
    assert isinstance(values["fits"]["pair0"], Record)
    assert values["fits"]["pair0"].details["left"].centre == 3
    assert values["fits"]["pair0"].details["left"].marker == 2
    assert report["fits"]["records"][0]["defaulted"] == {"marker": 2.0}
    assert report["fits"]["records"][1]["dropped"] == ["unused"]
    assert values["ds_fit"] == "dataset"


def test_binder_namespace_objects_with_untyped_annotation():
    def plot_untyped(ds_fit, fits: dict[str, object]):
        pass
    tree = ast.parse("plot_untyped(node.results['ds_fit'], node.namespace['fit_objects'])")
    report = {}
    values = runner._bind_args(plot_untyped, ds_raw=None, ds_fit="dataset", qubits=[], params={},
        results={"fit_results": {"pair0": {"peak": 4, "details": {"left": {"centre": 3}}}}},
        node_tree=tree, plot_imports={"plot_untyped": ("calibration_utils.synthetic", "plot_untyped")},
        util_module=SimpleNamespace(Record=Record, Detail=Detail), bind_report=report)
    assert isinstance(values["fits"]["pair0"], Record)
    assert isinstance(values["fits"]["pair0"].details["left"], Detail)
    assert values["fits"]["pair0"].details["left"].centre == 3
    assert report["fits"]["records"][-1]["class"] == "Record"


def test_binder_keeps_saved_plain_dicts_and_ambiguous_records():
    def plot_untyped(fits: dict[str, object]):
        pass
    records = {"pair0": {"peak": 4, "details": {}}}
    kwargs = dict(ds_raw=None, ds_fit=None, qubits=[], params={}, results={"fit_results": records},
                  plot_imports={"plot_untyped": ("calibration_utils.synthetic", "plot_untyped")})
    tree = ast.parse("plot_untyped(node.results['fit_results'])")
    values = runner._bind_args(plot_untyped, node_tree=tree, util_module=SimpleNamespace(Record=Record), **kwargs)
    assert values["fits"] == records
    other = __import__("dataclasses").make_dataclass("OtherRecord", [("peak", float), ("details", dict)])
    tree = ast.parse("plot_untyped(node.namespace['fit_objects'])")
    values = runner._bind_args(plot_untyped, node_tree=tree,
                              util_module=SimpleNamespace(Record=Record, OtherRecord=other), **kwargs)
    assert values["fits"] == records


def test_binder_node_call_preserves_dataset_fit_and_namespace_records():
    def plot_data(ds, fits):
        pass
    report = {}
    tree = ast.parse("plot_data(node.results['ds_raw'], node.results['ds_fit'])")
    kwargs = dict(ds_raw="raw", ds_fit="dataset", qubits=[], params={},
                  results={"fit_results": {"q0": {"peak": 1}}},
                  plot_imports={"plot_data": ("calibration_utils.synthetic", "plot_data")})
    assert runner._bind_args(plot_data, node_tree=tree, bind_report=report, **kwargs)["fits"] == "dataset"
    assert report["fits"]["source"] == "ds_fit.h5"
    tree = ast.parse("plot_data(node.results['ds_raw'], node.namespace['fit_objects'])")
    assert runner._bind_args(plot_data, node_tree=tree, **kwargs)["fits"] == {"q0": {"peak": 1}}


def test_binder_unbound_is_reported():
    def plot_data(required):
        pass
    report = {}
    with pytest.raises(ValueError, match="unbound required arg"):
        runner._bind_args(plot_data, ds_raw=None, ds_fit=None, qubits=[], params={}, bind_report=report)
    assert report["required"]["status"] == "unbound"


def test_binding_report_reaches_menu_plot_and_visible_defaults():
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path
    from quam_state_manager.core.interactive_plots import replot
    report = {"limit": {"status": "defaulted", "source": "signature", "value": 0.25},
              "fits": {"status": "bound", "source": "data.fit_results", "records": [
                  {"record": "pair0", "defaulted": {"marker": 2}, "dropped": []}]}}
    result = {"figures": [{"key": "fit", "axes": [], "bind_report": report}]}
    menu = replot.replot_menu(result)
    assert menu[0]["bind_report"] == report
    assert replot.replot_figure(result, "fit")["bind_report"] == report
    templates = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"
    body = Environment(loader=FileSystemLoader(templates)).get_template("_dataset_replot.html").render(
        available=True, figures=menu, errors=[], util="synthetic", uid="0", run_id="0")
    assert "Plot inputs and defaults" in body and "defaulted from signature (0.25)" in body
    assert "marker" in body and "2" in body


def test_dict_returns_extract_each_figure():
    plt = pytest.importorskip("matplotlib.pyplot")
    from quam_state_manager.generator.iplot_extract import extract_figure
    first, second = plt.figure(), plt.figure()
    first.add_subplot().plot([0, 1], [1, 2])
    second.add_subplot().plot([0, 1], [3, 4])
    report = {"ds": {"status": "bound", "source": "ds_fit.h5"}}
    try:
        output = runner._extract_return({"left": first, "right": second}, "diagnostics", report, extract_figure)
        assert [f["key"] for f in output] == ["diagnostics::left", "diagnostics::right"]
        assert [f["return_key"] for f in output] == ["left", "right"]
        assert all(f["axes"] and f["bind_report"] == report for f in output)
        assert output[0]["axes"][0]["traces"][0]["y"] == [1.0, 2.0]
        assert output[1]["axes"][0]["traces"][0]["y"] == [3.0, 4.0]
        assert runner._extract_return({}, "empty", {}, extract_figure) == []
        assert runner._extract_return(first, "single", {}, extract_figure)[0]["key"] == "single"
    finally:
        plt.close("all")


@pytest.fixture
def replay(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    from quam_state_manager.generator import iplot_extract
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "iplot_extract", iplot_extract)
    config = ModuleType("quam_config")
    config.Quam = SimpleNamespace(load=lambda path: SimpleNamespace(qubits={"q0": object()}))
    monkeypatch.setitem(sys.modules, "quam_config", config)
    monkeypatch.setitem(sys.modules, "xarray", ModuleType("xarray"))
    module = ModuleType("calibration_utils.synthetic")
    monkeypatch.setitem(sys.modules, "calibration_utils.synthetic", module)
    monkeypatch.setattr(runner, "_resolve_node", lambda *a: ("calibration_utils.synthetic", None, {}))
    (tmp_path / "node.json").write_text(json.dumps({"metadata": {"name": "01_synthetic"}}))
    monkeypatch.setattr(sys, "argv", ["replay", "--run", str(tmp_path)])
    captured = {}
    monkeypatch.setattr(runner, "_emit", lambda result, out: captured.update(result) or 0)

    def run(fn, tree=None, data=None, only="", functions=None, ds_fit=None):
        module.plot_data = fn
        for name, function in (functions or {}).items():
            setattr(module, name, function)
        if tree is not None:
            monkeypatch.setattr(runner, "_resolve_node", lambda *a: (
                "calibration_utils.synthetic", tree, {name: ("calibration_utils.synthetic", name)
                                                     for name in (functions or {"plot_data": fn})}))
        if ds_fit is not None:
            (tmp_path / "ds_fit.h5").touch()
            sys.modules["xarray"].open_dataset = lambda *a, **kw: ds_fit
        if data is not None:
            (tmp_path / "data.json").write_text(json.dumps(data))
        if only:
            monkeypatch.setattr(sys, "argv", ["replay", "--run", str(tmp_path), "--only", only])
        runner.main()
        return captured
    return run


def test_dict_returns_worker_envelope(replay):
    import matplotlib.pyplot as plt

    def plot_data():
        return {"left": plt.figure(), "right": plt.figure()}
    result = replay(plot_data)
    assert len(result["figures"]) == 2
    assert result["errors"] == []


def test_none_return_is_absence_in_worker_and_ui(replay):
    from quam_state_manager.core.interactive_plots import replot
    from jinja2 import Environment, FileSystemLoader
    from pathlib import Path

    def plot_data():
        return None
    result = replay(plot_data)
    assert result["errors"] == []
    assert result["figures"] == []
    assert result["not_produced"] == [{"key": "data", "reason": "no figure for this run"}]
    outcome = replot.replot_outcome(result)
    assert outcome["state"] == "no_figures"
    templates = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web" / "templates"
    body = Environment(loader=FileSystemLoader(templates)).get_template("_dataset_replot.html").render(
        available=True, figures=[], errors=[], outcome=outcome, util="synthetic")
    assert "No figure for this run" in body and "has no" not in body
    missing = []
    assert runner._extract_return({"optional": None}, "diagnostics", {}, None, missing) == []
    assert missing[0]["key"] == "diagnostics::optional"


def test_plot_plan_selects_one_mode_and_raw_function():
    tree = ast.parse("""
def plot_data(node):
    if 'ds_fit' not in node.results:
        return
    if node.results['ds_fit'].attrs.get('scan_mode') == 'resonance_line':
        figs = {'raw': plot_line_raw(node.results['ds_raw'])}
        figs.update(plot_line_fits(node.results['ds_raw']))
        node.results['figures'] = figs
        return
    figs = {'raw': plot_raw_maps(node.results['ds_raw'])}
    figs.update(plot_chevrons(node.results['ds_raw']))
    figs['hyperbola'] = plot_hyperbola_fits(node.results['ds_fit'])
    node.results['figures'] = figs
""")
    imports = {n: ("calibration_utils.synthetic", n) for n in
               ("plot_line_raw", "plot_line_fits", "plot_raw_maps", "plot_chevrons", "plot_hyperbola_fits")}
    selected, aliases = runner._plot_plan(tree, imports, SimpleNamespace(attrs={"scan_mode": "resonance_line"}))
    assert selected == {"plot_line_raw", "plot_line_fits"}
    assert aliases == {"plot_line_raw": "raw"}
    selected, aliases = runner._plot_plan(tree, imports, SimpleNamespace(attrs={"scan_mode": "map"}))
    assert selected == {"plot_raw_maps", "plot_chevrons", "plot_hyperbola_fits"}
    assert aliases == {"plot_raw_maps": "raw", "plot_hyperbola_fits": "hyperbola"}
    assert runner._plot_plan(tree, imports, None)[0] == set()


def test_archive_matching_includes_standalone_and_reports_extras(tmp_path):
    (tmp_path / "figure_l1_partner.png").write_bytes(b"synthetic")
    saved = runner._saved_figures(tmp_path, {"figures": {"amplitude": "./figures.amplitude.png"}})
    assert saved == {"amplitude": "./figures.amplitude.png", "l1_partner": "./figure_l1_partner.png"}
    figures = [
        {"key": "raw_data_with_fit", "function_key": "raw_data_with_fit", "return_key": None},
        {"key": "landscapes::l1_partner", "function_key": "landscapes", "return_key": "l1_partner"},
        {"key": "raw_data", "function_key": "raw_data", "return_key": None},
    ]
    report = runner._match_archive(figures, saved, {"plot_raw_data_with_fit": "amplitude"})
    assert report == {"matched": ["amplitude", "l1_partner"], "missing": [], "extra": ["raw_data"]}
    assert figures[0]["key"] == "amplitude" and figures[0]["source_key"] == "raw_data_with_fit"
    assert figures[1]["archive_path"] == "./figure_l1_partner.png"
    assert figures[2]["archive_key"] is None


def test_colorbar_marker_does_not_become_archive_panel():
    plt = pytest.importorskip("matplotlib.pyplot")
    from quam_state_manager.generator.iplot_extract import extract_figure
    from quam_state_manager.core.interactive_plots import replot
    fig, ax = plt.subplots()
    mesh = ax.pcolormesh([[0, 1], [2, 3]])
    colorbar = fig.colorbar(mesh, ax=ax)
    colorbar.ax.axhline(1.0, color="red")
    try:
        extracted = extract_figure(fig, "map")
        assert extracted["axes"][0]["is_colorbar"] is False
        assert extracted["axes"][1]["is_colorbar"] is True
        axes = extracted["axes"]
        assert len(replot._group_panels(axes, replot._drawable(axes))) == 1
    finally:
        plt.close(fig)


def test_archive_alias_reaches_worker_and_only_filter(replay):
    import matplotlib.pyplot as plt

    def plot_data():
        return plt.figure()
    tree = ast.parse("""
def plot_data(node):
    fig = plot_data()
    node.results['figures'] = {'amplitude': fig}
""")
    result = replay(plot_data, tree=tree, data={"figures": {"amplitude": "./figures.amplitude.png"}},
                    only="amplitude")
    assert len(result["figures"]) == 1
    assert result["figures"][0]["key"] == "amplitude"
    assert result["archive_match"] == {"matched": ["amplitude"], "missing": [], "extra": []}


def test_worker_does_not_invoke_both_modes_or_duplicate_raw(replay):
    import matplotlib.pyplot as plt
    called = []

    def draw(name):
        def function():
            called.append(name)
            return plt.figure()
        return function
    functions = {n: draw(n) for n in ("plot_line_raw", "plot_line_fits", "plot_raw_maps", "plot_chevrons")}
    tree = ast.parse("""
def plot_data(node):
    if node.results['ds_fit'].attrs.get('scan_mode') == 'resonance_line':
        figs = {'raw': plot_line_raw()}
        figs['line'] = plot_line_fits()
        node.results['figures'] = figs
        return
    figs = {'raw': plot_raw_maps()}
    figs['map'] = plot_chevrons()
    node.results['figures'] = figs
""")
    result = replay(None, tree=tree, functions=functions,
                    ds_fit=SimpleNamespace(attrs={"scan_mode": "resonance_line"}),
                    data={"figures": {"raw": "./figures.raw.png", "line": "./figures.line.png"}})
    assert called == ["plot_line_fits", "plot_line_raw"]
    assert sorted(f["key"] for f in result["figures"]) == ["line", "raw"]
    assert result["archive_match"]["extra"] == []
