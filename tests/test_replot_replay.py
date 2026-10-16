"""Saved-input replay pins using synthetic source trees and run data."""
import sys
import ast
import json
import re
import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from types import ModuleType

import pytest

from quam_state_manager.generator import run_interactive_replot as runner


@pytest.fixture(autouse=True)
def headless_figures():
    try:
        import matplotlib
        matplotlib.use("Agg")
    except ImportError:
        pass


def test_serializer_preserves_every_line_and_fit_summary():
    import matplotlib.pyplot as plt
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig, ax = plt.subplots()
    for i in range(2):
        ax.plot([0, 1, 2], [i, i + 1, i], label="data")
        ax.plot([0, 1, 2], [i + .2, i + 1.2, i + .2], label="fit",
                linestyle=":", alpha=.3, zorder=7, drawstyle="steps-post")
        ax.axvline(i + .5, linestyle="--")
    ax.text(.1, .9, "fit summary", transform=ax.transAxes)
    ax.plot([1], [2], marker="*", linestyle="None", alpha=0)
    ax.plot([], [])
    ax.plot([0, 1], [2, 3], linestyle="None")
    out = extract_figure(fig, "fit")
    plt.close(fig)
    traces = out["axes"][0]["traces"]
    assert len(traces) == 9
    fits = [t for t in traces if t["name"] == "fit"]
    assert len(fits) == 2
    assert all(t["opacity"] == .3 and t["zorder"] == 7 and
               t["drawstyle"] == "steps-post" and t["linestyle"] == ":" for t in fits)
    assert traces[-3]["mode"] == "markers" and traces[-3]["opacity"] == 0
    assert traces[-2]["x"] == []
    assert traces[-1]["mode"] == "none"
    assert out["axes"][0]["annotations"][0]["text"] == "fit summary"


def test_explicit_saved_dataset_beats_generic_parameter_name():
    def plot_map(ds):
        return ds
    tree = ast.parse('draw(node.results["ds_fit"])')
    raw, fitted = object(), object()
    report = {}
    args = runner._bind_args(plot_map, ds_raw=raw, ds_fit=fitted, qubits=[], params={}, results={},
        node_tree=tree, plot_imports={"draw": ("calibration_utils.synthetic", "plot_map")}, bind_report=report)
    assert args["ds"] is fitted, "The node explicitly plots the saved fitted dataset"
    assert report["ds"]["source"] == "ds_fit.h5"


def test_serializer_artist_coverage():
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.collections import LineCollection
    from matplotlib.patches import Circle
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig, ax = plt.subplots()
    ax.plot([1, 2], [1, 2])
    ax.set_xscale("log")
    ax.secondary_xaxis("top", functions=(lambda x: x * 2, lambda x: x / 2)).set_xlabel("scaled")
    ax.contour([[0, 1], [1, 2]], levels=[.5, 1.5])
    ax.fill_between([1, 2], [1, 2], [2, 3], alpha=.2)
    ax.add_collection(LineCollection([[(1, 1), (1, 2)], [(2, 2), (2, 3)]]))
    ax.imshow([[1, 2], [3, 4]], extent=(1, 3, 2, 6), origin="upper")
    ax.scatter([1, 2], [2, 3], s=[16, 81], c=["red", "blue"])
    ax.axvline(1.5)
    ax.axhline(2.5)
    ax.axvspan(1.1, 1.2, alpha=.3)
    ax.annotate("pick", xy=(1, 2), xytext=(2, 3), arrowprops={"arrowstyle": "->"})
    ax.twinx().plot([1, 2], [3, 4])
    fig.text(.3, .05, "summary")
    fig.suptitle("heading")
    # A display-coordinate artist must be named rather than silently omitted.
    ax.add_patch(Circle((.1, .1), .03, transform=fig.transFigure))
    out = extract_figure(fig, "coverage")
    plt.close(fig)
    a = out["axes"][0]
    ts = a["traces"]
    assert a["xscale"] == "log" and out["axes"][1]["shares_x_with"] == 0
    assert len([t for t in ts if t["type"] == "contour"]) == 2
    assert {t["level"] for t in ts if t["type"] == "contour"} == {.5, 1.5}
    assert len([t for t in ts if t["type"] == "polygon"]) == 2
    assert len([t for t in ts if t["type"] == "line"]) == 5
    assert {t.get("role") for t in ts} >= {"vline", "hline"}
    image = next(t for t in ts if t["type"] == "heatmap")
    assert image["y_edges"] == [6, 4, 2] and image["z"] == [[1, 2], [3, 4]]
    scatter = next(t for t in ts if t["type"] == "scatter")
    assert scatter["marker_size"] == [4, 9] and scatter["marker_color"] == ["#ff0000", "#0000ff"]
    assert len(a["secondary_axes"]) == 1
    assert a["secondary_axes"][0]["map"]["forward"] == [2 * v for v in a["secondary_axes"][0]["map"]["parent"]]
    assert a["secondary_axes"][0]["map"]["inverse"] == a["secondary_axes"][0]["map"]["parent"]
    assert a["annotations"][0]["arrow"] is True
    assert {t["text"] for t in out["texts"]} == {"summary", "heading"}
    assert a["skipped"] == [{"artist": "Circle", "n": 1, "reason": "unsupported patch transform"}]


def test_map_metadata_and_full_resolution(monkeypatch):
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import LogNorm, TwoSlopeNorm
    from quam_state_manager.generator.iplot_extract import extract_figure
    from quam_state_manager.core.interactive_plots import replot
    fig, axes = plt.subplots(1, 3)
    mesh = axes[0].pcolormesh([0, 1, 4], [0, 2, 5], [[1, 10], [100, 1000]],
                              cmap="twilight_shifted", norm=LogNorm(1, 1000))
    fig.colorbar(mesh, ax=axes[0], label="phase (deg)")
    axes[1].imshow([[-2, 0], [1, 4]], norm=TwoSlopeNorm(0, -2, 4), origin="lower")
    axes[2].pcolormesh(np.arange(412), np.arange(412), np.ones((411, 411)))
    out = extract_figure(fig, "maps")
    plt.close(fig)
    t = out["axes"][0]["traces"][0]
    assert t["cmap"] == "twilight_shifted" and len(t["colorscale"]) == 257
    assert t["norm"]["log"] and t["norm"]["vmin"] == 1 and t["norm"]["vmax"] == 1000
    assert t["x_edges"] == [0, 1, 4] and t["x"] == [.5, 2.5]
    assert t["colorbar"]["label"] == "phase (deg)" and t["colorbar"]["units"] == "deg"
    assert out["axes"][1]["traces"][0]["norm"]["vcenter"] == 0
    assert out["axes"][1]["traces"][0]["norm"]["type"] == "TwoSlopeNorm"
    result = {"figures": [out]}
    full = replot.full_map(result, "maps", 2, 0)
    assert full is not None and len(full["z"]) == 411 and full["full_resolution"]
    converted = replot.mpljson_to_plotly(out)
    maps = [t for t in converted["data"] if t["type"] == "heatmap"]
    assert len(maps[2]["z"]) == 411 and maps[0]["x"] == [0, 1, 4]
    monkeypatch.setattr(replot, "_MAP_BYTE_BUDGET", 1024)
    reduced = replot.mpljson_to_plotly(out)
    m = [t for t in reduced["data"] if t["type"] == "heatmap"][2]
    assert m["meta"]["preview_reduced"] and len(m["z"]) <= 400
    assert any("full array retained" in s for s in reduced["notices"])
    assert replot.full_map(result, "maps", 2, 0)["z"] == full["z"]


def test_map_nonmonotonic_edges_reported():
    import matplotlib.pyplot as plt
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig, ax = plt.subplots()
    ax.pcolormesh([0, 2, 1], [0, 1, 2], [[1, 2], [3, 4]])
    out = extract_figure(fig, "map")
    plt.close(fig)
    assert out["axes"][0]["traces"] == []
    assert out["axes"][0]["skipped"][0]["artist"] == "QuadMesh"


def test_renderer_artist_meaning_and_notes():
    import matplotlib.pyplot as plt
    from matplotlib.colors import LogNorm, TwoSlopeNorm
    from quam_state_manager.generator.iplot_extract import extract_figure
    from quam_state_manager.core.interactive_plots import replot
    fig, axes = plt.subplots(1, 2)
    ax = axes[0]
    hm = ax.pcolormesh([1, 2, 4], [1, 2, 3], [[1, 10], [100, 1000]],
                       norm=LogNorm(1, 1000), cmap="twilight_shifted")
    fig.colorbar(hm, ax=ax, label="signal (V)")
    other = axes[1].imshow([[-2, 0], [1, 4]], norm=TwoSlopeNorm(0, -2, 4))
    fig.colorbar(other, ax=axes[1], label="offset")
    ax.set_xscale("log")
    ax.plot([1, 2, 3], [1, 2, 1], drawstyle="steps-post", alpha=.4, zorder=9, label="fit")
    ax.fill_between([1, 2], [1, 1], [2, 2], alpha=.2)
    ax.contour([[0, 1], [1, 2]], levels=[.5])
    ax.axvline(1.4, alpha=.5)
    ax.axhline(2.5)
    ax.axvspan(1.1, 1.2, alpha=.25)
    ax.secondary_xaxis("top", functions=(lambda x: x ** 2, lambda x: x ** .5)).set_xlabel("squared")
    ax.annotate("pick", (2, 2), xytext=(3, 3), arrowprops={"arrowstyle": "->"})
    ax.twinx().text(.5, .5, "twin note", transform=ax.transAxes)
    fig.text(.4, .03, "summary")
    out = extract_figure(fig, "render")
    plt.close(fig)
    out["axes"][0]["skipped"] = [{"artist": "Unknown", "n": 2, "reason": "custom transform"}]
    converted = replot.mpljson_to_plotly(out)
    data, layout = converted["data"], converted["layout"]
    assert len([t for t in data if t.get("fill") == "toself"]) == 1
    fit = next(t for t in data if t.get("name") == "fit")
    assert fit["line"]["shape"] == "hv" and fit["opacity"] == .4 and fit["zorder"] == 9
    assert any(t.get("name") == "level 0.5" for t in data)
    assert layout["xaxis"]["type"] == "log"
    assert layout["xaxis"]["domain"][1] < layout["xaxis2"]["domain"][0]
    # v1.4.1: equal grid domains -> actual bounds, imshow preserves a shorter aspect.
    for key, source_axis in (("yaxis", out["axes"][0]), ("yaxis2", out["axes"][1])):
        _, bottom, _, height = source_axis["position"]
        assert layout[key]["domain"] == pytest.approx([bottom, bottom + height])
    assert "xaxis201" in layout
    assert any(t.get("xaxis") == "x201" for t in data)
    assert layout["xaxis201"]["overlaying"] == "x" and layout["xaxis201"]["title"]["text"] == "squared"
    # v1.4.1: samples "f(v):.6g" -> the ticks matplotlib drew on the secondary axis (on this log
    # parent: 10<sup>k</sup> labels, the \mathdefault wrapper removed).
    # v1.4.1: unchecked zip -> equal nonempty lengths, so missing labels cannot pass.
    assert len(layout["xaxis201"]["tickvals"]) == len(layout["xaxis201"]["ticktext"]) >= 2
    for v, text in zip(layout["xaxis201"]["tickvals"], layout["xaxis201"]["ticktext"]):
        power = re.fullmatch(r"10<sup>(-?\d+)</sup>", text.replace("\u2212", "-"))
        assert power and 10 ** int(power[1]) == pytest.approx(v ** 2)
    assert "shapes" in layout
    assert any(s["type"] == "line" and s["yref"] == "y domain" for s in layout["shapes"])
    assert any(s["type"] == "line" and s["xref"] == "x domain" for s in layout["shapes"])
    assert any(s["type"] == "rect" and s["yref"] == "y domain" and s["opacity"] == .25 for s in layout["shapes"])
    hm, other = [t for t in data if t["type"] == "heatmap"]
    assert hm["z"] == [[0, 1], [2, 3]] and hm["zmin"] == 0 and hm["zmax"] == 3
    assert hm["customdata"] == [[1, 10], [100, 1000]]
    assert hm["colorbar"]["title"]["text"] == "signal (V)" and len(hm["colorscale"]) == 257
    assert other["z"] == [[0, .5], [.625, 1]]
    assert hm["showscale"] and other["showscale"]
    assert any(a["text"] == "summary" and a["xref"] == "paper" for a in layout["annotations"])
    # v1.4.1: omitted black -> source black, the house theme alone adapts dark mode.
    assert all(a["font"].get("color") == "#000000" for a in layout["annotations"] if a["text"] == "summary")
    assert any(a["text"] == "twin note" for a in layout["annotations"])
    assert any(a["text"] == "pick" and a["showarrow"] and a["axref"] == "x" for a in layout["annotations"])
    arrow = next(a for a in layout["annotations"] if a["text"] == "pick")
    assert arrow["x"] == math.log10(2) and arrow["ax"] == math.log10(3)
    assert len(converted["notices"]) == 1
    assert "Unknown x2" in converted["notices"][0]


def test_unsupported_figure_artist_is_reported():
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle
    from quam_state_manager.generator.iplot_extract import extract_figure
    from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly
    fig = plt.figure()
    fig.add_artist(Circle((.2, .2), .1, transform=fig.transFigure))
    extracted = extract_figure(fig, "custom")
    plt.close(fig)
    assert len(extracted["skipped"]) == 1
    converted = mpljson_to_plotly(extracted)
    assert len(converted["notices"]) == 1 and "Circle x1" in converted["notices"][0]
    fig, ax = plt.subplots()
    ax.table(cellText=[["value"]], loc="center")
    fig.lines.append(Line2D([.1, .9], [.2, .8], transform=fig.transFigure))
    extracted = extract_figure(fig, "unsupported")
    plt.close(fig)
    assert extracted["axes"][0]["skipped"] == [{"artist": "Table", "n": 1, "reason": "unsupported artist"}]
    assert extracted["skipped"] == [{"artist": "Line2D", "n": 1, "reason": "unsupported figure artist"}]
    note = mpljson_to_plotly(extracted)["notices"][0]
    assert "Table x1" in note and "Line2D x1" in note


def test_unsupported_coordinates_and_norms_are_named():
    import matplotlib.pyplot as plt
    from matplotlib.colors import SymLogNorm
    from matplotlib.transforms import IdentityTransform
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig, ax = plt.subplots()
    ax.imshow([[-1, 1], [2, 3]], norm=SymLogNorm(1))
    ax.text(1, 2, "display", transform=IdentityTransform())
    ax.annotate("offset", (1, 2), xycoords="figure pixels")
    fig.text(1, 2, "display", transform=IdentityTransform())
    out = extract_figure(fig, "unsupported")
    plt.close(fig)
    assert {s["artist"] for s in out["axes"][0]["skipped"]} == {"AxesImage", "Text", "Annotation"}
    assert len(out["skipped"]) == 1 and out["skipped"][0]["artist"] == "FigureText"
    assert out["axes"][0]["annotations"] == []


def test_replot_renderer_selfcheck():
    import subprocess
    from pathlib import Path
    proc = subprocess.run(["node", str(Path(__file__).with_name("replot_artists_selfcheck.cjs"))],
                          capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_renderer_annotation_coordinates_and_empty_mesh_notice():
    import matplotlib.pyplot as plt
    from quam_state_manager.generator.iplot_extract import extract_figure
    from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly
    fig, ax = plt.subplots()
    ax.pcolormesh([0, 2, 1], [0, 1, 2], [[1, 2], [3, 4]])
    extracted = extract_figure(fig, "mesh")
    plt.close(fig)
    assert not extracted["axes"][0]["is_colorbar"]
    assert "QuadMesh x1" in mpljson_to_plotly(extracted)["notices"][0]
    fig, ax = plt.subplots()
    ax.set_xscale("log")
    ax.set_xlim(1, 100)
    ax.annotate("fraction", (.2, .7), xycoords="axes fraction")
    ax.annotate("offset", (10, 2), xytext=(6, 9), textcoords="offset points")
    extracted = extract_figure(fig, "notes")
    plt.close(fig)
    notes = mpljson_to_plotly(extracted)["layout"]["annotations"]
    fraction = next(n for n in notes if n["text"] == "fraction")
    offset = next(n for n in notes if n["text"] == "offset")
    assert fraction["xref"] == "x domain" and fraction["x"] == .2
    assert fraction["yref"] == "y domain" and fraction["y"] == .7
    assert offset["x"] == 1 and offset["y"] == 2
    assert offset["xshift"] == 8 and offset["yshift"] == 12


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
    # v1.3.0: v1 -> v2, the payload now carries explicit artist and map metadata.
    assert result["schema"] == "iplot/v2"
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


def test_defaulted_argument_feeding_a_marker_is_reported(replay):
    import matplotlib.pyplot as plt

    def plot_data(marker_at=0.5):
        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1])
        ax.axvline(marker_at)
        return fig

    def plot_style(width=2):
        fig, ax = plt.subplots()
        ax.plot([0, 1], [0, 1], lw=width)
        return fig

    result = replay(plot_data, functions={"plot_style": plot_style})
    defaults = {f["function_key"]: f["marker_defaults"] for f in result["figures"]}
    assert defaults == {"data": ["marker_at"], "style": []}


def test_import_failure_still_reports_the_code_to_watch(replay, monkeypatch):
    monkeypatch.setattr(runner, "_resolve_node", lambda *a: ("calibration_utils.absent_module", None, {}))
    result = replay(lambda: None)
    assert result["errors"][0]["stage"] == "import"
    assert result.get("code_fingerprint") and result.get("module_files")
