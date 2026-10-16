"""v1.5.1 (D3 classes T and P, the systematic causes): headings, titles and legends of a
reproduced lab figure sit and read as the lab drew them.

* the figure heading hangs at the lab's own y (capped at 0.98 it sat on the first row's titles);
* an axes title sits where matplotlib drew it (a fixed 64 px lift over a top axis put it on the
  heading) and keeps its colour and size (a crimson warning title lost its colour);
* a label-only legend entry (a fit value: ``ax.plot([], [], " ", label=...)``) gets a point, so
  the legend keeps it;
* several panels that each drew a legend keep one legend each, where the lab drew it (one footer
  legend merged per-panel numbers and sat on the bottom axis titles).
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly

ROOT = Path(__file__).resolve().parents[1]


def _figure(build, size=(10, 6), canvas=True, layout=None):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure(figsize=size, layout=layout)
    if canvas:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)
    build(fig)
    return extract_figure(fig, "fig")


def _note(view, text):
    return next(n for n in view["layout"]["annotations"] if n["text"].replace("<br>", "\n") == text)


def test_the_heading_hangs_at_the_lab_y():
    def build(fig):
        fig.subplots().plot([0, 1], [0, 1])
        fig.suptitle("Heading", y=0.995)
    title = mpljson_to_plotly(_figure(build))["layout"]["title"]
    assert title["text"] == "Heading"
    assert title["y"] == pytest.approx(0.995) and title["yanchor"] == "top"


def test_a_title_over_a_top_axis_sits_where_matplotlib_drew_it():
    def build(fig):
        ax = fig.subplots()
        ax.plot([0, 1], [0, 1])
        top = ax.twiny()
        top.set_xlim(-20, 20)
        top.set_xlabel("Detuning [MHz]")
        ax.set_title("qubit = q5")
        fig.suptitle("Chevron")
    fig_json = _figure(build, layout="constrained")
    box = next(a.get("title_box") for a in fig_json["axes"] if a.get("title"))
    assert box is not None and len(box) == 4, "the extractor records where the title was drawn"
    note =_note(mpljson_to_plotly(fig_json), "qubit = q5")
    assert note["y"] == pytest.approx(box[1]) and note.get("yshift", 0) == 0
    top_axes = max(a["position"][1] + a["position"][3] for a in fig_json["axes"])
    assert note["y"] > top_axes + 0.05            # above the top axis labels, as drawn ...
    heading = next(t for t in fig_json["texts"] if t["text"] == "Chevron")
    assert box[3] < heading["y"]                   # ... and below the heading


def test_a_warning_title_keeps_its_colour_and_size():
    def build(fig):
        ax = fig.subplots()
        ax.plot([0, 1], [0, 1])
        ax.set_title("WARNING: not qualified\nsecond line", color="crimson", fontsize=8)
    note = _note(mpljson_to_plotly(_figure(build)), "WARNING: not qualified\nsecond line")
    assert note["font"].get("color") == "#dc143c"
    assert note["font"]["size"] == pytest.approx(8 * 1.2 / 1.3, abs=1e-3)   # two lines: matplotlib spacing


def test_a_label_only_legend_entry_has_a_point():
    def build(fig):
        ax = fig.subplots()
        ax.plot([0, 1, 2], [0, 1, 0], ".", label="data")
        ax.plot([], [], " ", label="T2* = 1.16 us")
        ax.plot([], label="EPC = 1.1e-03")
        ax.legend()
    data = mpljson_to_plotly(_figure(build))["data"]
    entry = next(t for t in data if t.get("name") == "T2* = 1.16 us")
    assert entry["x"] == [None] and entry["showlegend"] is True
    assert entry["mode"] == "markers"                                   # no symbol, as drawn:
    assert entry["marker"]["color"] == entry["marker"]["line"]["color"] == "rgba(0,0,0,0)"
    line = next(t for t in data if t.get("name") == "EPC = 1.1e-03")
    assert line["x"] == [None] and line["mode"] == "lines"


def test_errorbar_bar_band_and_span_entries_stay_in_the_legend():
    def build(fig):
        ax = fig.subplots()
        series = ax.errorbar([0, 1, 2], [1, 2, 1.5], yerr=[.1, .2, .1], fmt=".-", color="C0")
        bars = ax.bar([0, 1], [1, 2], color="C1")
        band = ax.fill_between([0, 2], [0, 0], [.5, .5], color="C2", alpha=.3)
        span = ax.axvspan(.2, .4, color="C3", alpha=.2)
        line = ax.axvline(1, color="red", linestyle="--")
        ax.legend([series, bars, band, span, line], ["T2*", "made by x180", "candidate window", "ADC range", "Flux offset"])
    data = mpljson_to_plotly(_figure(build))["data"]
    shown = {t["name"]: t for t in data if t.get("showlegend")}
    assert set(shown) == {"T2*", "made by x180", "candidate window", "ADC range", "Flux offset"}
    assert shown["T2*"]["mode"] == "lines+markers"                 # the series line and its dots
    assert shown["candidate window"]["marker"]["symbol"] == "square"   # a swatch for a filled area


def test_a_figure_legend_sits_where_the_lab_drew_it():
    def build(fig):
        for i, ax in enumerate(fig.subplots(1, 3)):
            ax.plot([0, 1], [i, i + 1], ".", label="Ground")
            ax.plot([0, 1], [i + 1, i], ".", label="Excited")
        handles, labels = ax.get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=2)
    fig_json = _figure(build, size=(12, 4))
    box = fig_json["figure_legend"]["box"]
    assert box and fig_json["figure_legend"]["ncol"] == 2
    legend = mpljson_to_plotly(fig_json)["layout"]["legend"]
    assert legend["x"] == pytest.approx((box[0] + box[2]) / 2) and legend["xanchor"] == "center"
    assert legend["y"] == pytest.approx(box[1]) and legend["yanchor"] == "bottom"
    assert legend["orientation"] == "h" and legend.get("borderwidth") == 1
    view = mpljson_to_plotly(fig_json)
    for label in ("Ground", "Excited"):
        traces = [trace for trace in view["data"] if trace.get("name") == label]
        assert len(traces) == 4
        assert len({trace.get("legendgroup") for trace in traces}) == 1


def test_legend_packing_order_caps_scatter_and_hollow_swatches():
    def build(fig):
        from matplotlib.patches import Patch
        ax = fig.subplots()
        error = ax.errorbar([0, 1], [0, 1], yerr=.1, fmt="o-", capsize=4)
        cloud = ax.scatter([0, 1], [1, 0], s=16, color="purple")
        ax.legend([error, cloud, Patch(facecolor="none", edgecolor="red")],
                  ["errors", "cloud", "outline"], markerfirst=False, ncol=2)
    shown = {t["name"]: t for t in mpljson_to_plotly(_figure(build))["data"] if t.get("showlegend")}
    assert set(shown) == {"errors", "cloud", "outline"}
    assert shown["errors"].get("marker", {}).get("symbol") == "circle"
    assert shown["cloud"]["marker"]["color"] == "#800080"
    assert shown["outline"].get("opacity", 1) * shown["outline"]["marker"].get("opacity", 1) == 1
    assert shown["outline"]["marker"]["color"] == "rgba(0,0,0,0)"
    assert shown["outline"]["marker"]["line"]["color"] == "#ff0000"


@pytest.mark.parametrize("legacy,packing", [(False, "changed"), (True, "missing"), (False, "empty"), (False, "throws")])
def test_legend_public_fallback_survives_missing_or_changed_private_boxes(legacy, packing):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure
    from quam_state_manager.generator.iplot_extract import _legend_proxies
    ax = Figure().subplots()
    error = ax.errorbar([0, 1], [0, 1], yerr=.1, alpha=.4)
    ax.legend([error], ["errors"])
    real = ax.get_legend()
    class PublicLegend:
        def get_texts(self):
            return real.get_texts()
    leg = PublicLegend()
    setattr(leg, "legendHandles" if legacy else "legend_handles", real.legend_handles)
    if not legacy:
        from types import SimpleNamespace
        def children():
            if packing == "throws":
                raise RuntimeError("unavailable private packing")
            return []
        leg._legend_handle_box = object() if packing == "changed" else SimpleNamespace(get_children=children)
    try:
        entries = _legend_proxies(leg)
    except (AttributeError, TypeError, RuntimeError) as error:
        entries = [(str(error), None)]
    assert len(entries) == 1 and entries[0][0] == "errors"
    assert entries[0][1] and entries[0][1]["mode"] == "lines"
    assert entries[0][1].get("opacity", 1.0) == pytest.approx(.4)


def test_figure_custom_legends_and_axes_legends_coexist():
    def build(fig):
        from matplotlib.patches import Patch
        ax = fig.subplots()
        ax.plot([0, 1], [0, 1], label="data")
        ax.legend()
        fig.legend([Patch(color="orange")], ["window"], loc="lower center")
        fig.legend([Patch(color="blue")], ["reference"], loc="upper center")
    view = mpljson_to_plotly(_figure(build))
    shown = [(t.get("legend", "legend"), t["name"]) for t in view["data"] if t.get("showlegend")]
    assert sorted(shown) == [("legend", "data"), ("legend2", "window"), ("legend3", "reference")]


def test_errorbars_without_a_data_line_and_independent_swatch_alpha():
    def build(fig):
        from matplotlib.patches import Patch
        ax = fig.subplots()
        bars = ax.errorbar([0, 1], [0, 1], yerr=.1, fmt="none", capsize=4, alpha=.25)
        swatch = Patch(facecolor=(1, 0, 0, .25), edgecolor=(0, 0, 1, .75))
        ax.legend([bars, swatch], ["uncertainty", "area"])
    shown = {t["name"]: t for t in mpljson_to_plotly(_figure(build))["data"] if t.get("showlegend")}
    assert shown["uncertainty"]["mode"] == "lines"
    assert shown["uncertainty"].get("visible") is not False
    assert shown["uncertainty"].get("opacity", 1.0) == pytest.approx(.25)
    assert shown["area"]["marker"]["color"] == "rgba(255,0,0,0.25)"
    assert shown["area"]["marker"]["line"]["color"] == "rgba(0,0,255,0.75)"


def test_figure_legend_keeps_only_its_explicit_entries():
    def build(fig):
        ax = fig.subplots()
        line, = ax.plot([0, 1], [0, 1], label="internal label")
        ax.plot([0, 1], [1, 0], label="unlisted")
        fig.legend([line], ["public label"])
    view = mpljson_to_plotly(_figure(build))
    assert [t["name"] for t in view["data"] if t.get("showlegend")] == ["public label"]


def test_every_panel_keeps_its_own_legend(tmp_path):
    def build(fig):
        for i, ax in enumerate(fig.subplots(1, 3)):
            ax.plot([0, 1], [i, i + 1], label="data")
            if i < 2:
                ax.plot([], [], " ", label=f"T2* = {i + 1}.0 us")
                ax.legend(loc="upper left")
    fig_json = _figure(build, size=(12, 4))
    view = mpljson_to_plotly(fig_json)
    shown = [(t.get("legend", "legend"), t["name"]) for t in view["data"] if t.get("showlegend")]
    assert sorted(shown) == [("legend", "T2* = 1.0 us"), ("legend", "data"),
                             ("legend2", "T2* = 2.0 us"), ("legend2", "data")]
    for name, axes in (("legend", fig_json["axes"][0]), ("legend2", fig_json["axes"][1])):
        box = axes["legend"]["box"]
        assert (view["layout"][name]["x"], view["layout"][name]["y"]) == pytest.approx((box[0], box[3]))
        assert view["layout"][name].get("borderwidth") == 1
    out = tmp_path / "legends.json"
    out.write_text(json.dumps(view), encoding="utf-8")
    env = dict(os.environ)
    for cand in (ROOT / "node_modules", ROOT.parent / "statemanager" / "node_modules"):
        if (cand / "jsdom").is_dir():
            env["NODE_PATH"] = str(cand)
            break
    else:
        pytest.skip("node + jsdom not installed")
    if shutil.which("node") is None:
        pytest.skip("node not installed")
    r = subprocess.run(["node", str(ROOT / "tests" / "replot_legends_selfcheck.cjs"), str(out)],
                       capture_output=True, text=True, env=env, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "replot_legends_selfcheck: 8 assertions passed" in r.stdout, r.stdout
