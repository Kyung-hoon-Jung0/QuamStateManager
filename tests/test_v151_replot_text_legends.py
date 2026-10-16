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


@pytest.mark.parametrize("va,anchor,y", [("top", "top", 0.995), ("center", "middle", 0.95),
                                         ("bottom", "bottom", 0.95), ("baseline", "bottom", 0.95)])
def test_the_heading_hangs_at_the_lab_y(va, anchor, y):
    def build(fig):
        fig.subplots().plot([0, 1], [0, 1])
        fig.suptitle("Heading", y=y, va=va)
    title = mpljson_to_plotly(_figure(build))["layout"]["title"]
    assert title["text"] == "Heading"
    assert title["y"] == pytest.approx(y) and title["yanchor"] == anchor


@pytest.mark.parametrize("canvas", [True, False])
def test_a_heading_wider_than_the_figure_fits_its_width(canvas):
    # the lab saves with a tight box, so its PNG grows to hold the heading; the reproduced
    # figure has the lab's width and cut one long line off at both sides
    heading = ("GEF readout: amplitude cut through the chosen detuning "
               "(the outlier fraction is the over-power gate)")
    def build(fig):
        fig.subplots().plot([0, 1], [0, 1])
        fig.suptitle(heading)
    title = mpljson_to_plotly(_figure(build, size=(6, 4.6), canvas=canvas))["layout"]["title"]
    # one line, on the line the lab reserved for it, shrunk to the figure width
    assert title["text"] == heading
    size = title["font"]["size"]
    assert size == pytest.approx(6 * 72 * 0.98 / (len(heading) * 0.54), rel=1e-3)   # just enough
    assert 12 * 0.6 <= size < 12

    def short(fig):
        fig.subplots().plot([0, 1], [0, 1])
        fig.suptitle("Short heading")
    title = mpljson_to_plotly(_figure(short, size=(6, 4.6)))["layout"]["title"]
    assert title["text"] == "Short heading" and title["font"]["size"] == 12

    def very_long(fig):
        fig.subplots().plot([0, 1], [0, 1])
        fig.suptitle(heading + " " + heading)
    view = mpljson_to_plotly(_figure(very_long, size=(6, 4.6)))
    assert view["layout"]["title"]["text"] == ""
    note = next(n for n in view["layout"]["annotations"] if n["text"].startswith("GEF readout"))
    lines = note["text"].split("<br>")
    assert len(lines) >= 2 and " ".join(lines) == heading + " " + heading
    assert all(len(line) * 12 * 0.6 * 0.54 <= 6 * 72 * 0.98 for line in lines)
    assert (note["xanchor"], note["yanchor"]) == ("center", "top")
    assert note["font"]["size"] == pytest.approx(12 * 0.6 * 1.2 / 1.3, abs=1e-3)   # at the floor size


def test_a_heading_keeps_its_colour_without_source_positions():
    def build(fig):
        fig.subplots().plot([0, 1], [0, 1])
        fig.suptitle("Heading", color="crimson")
    fig_json = _figure(build)
    for axes in fig_json["axes"]:
        axes.pop("position", None)     # an older extraction: no panel positions
    title = mpljson_to_plotly(fig_json)["layout"]["title"]
    assert title["text"] == "Heading" and title.get("font", {}).get("color") == "#dc143c"


def test_lab_legend_font_sizes_are_kept():
    def panels(fig):
        for i, ax in enumerate(fig.subplots(1, 2)):
            ax.plot([0, 1], [i, i + 1], label=f"data {i}")
            ax.legend(fontsize=7)
    layout = mpljson_to_plotly(_figure(panels, size=(12, 4)))["layout"]
    assert layout["legend"]["font"]["size"] == pytest.approx(7)
    assert layout["legend2"]["font"]["size"] == pytest.approx(7)

    def figure_legend(fig):
        line, = fig.subplots().plot([0, 1], [0, 1])
        fig.legend([line], ["data"], fontsize=7, loc="lower center")
    assert mpljson_to_plotly(_figure(figure_legend))["layout"]["legend"]["font"]["size"] == pytest.approx(7)


@pytest.mark.parametrize("canvas", [True, False])   # most lab plots build a bare Figure
def test_a_title_over_a_top_axis_sits_where_matplotlib_drew_it(canvas):
    def build(fig):
        ax = fig.subplots()
        ax.plot([0, 1], [0, 1])
        top = ax.twiny()
        top.set_xlim(-20, 20)
        top.set_xlabel("Detuning [MHz]")
        ax.set_title("qubit = q5")
        fig.suptitle("Chevron")
    fig_json = _figure(build, layout="constrained", canvas=canvas)
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


@pytest.mark.parametrize("fmt,mode,symbol,size", [
    ("s-", "lines+markers", "square", 6.0), ("-", "lines", None, None), ("D", "markers", "diamond", 6.0),
    (".-", "lines+markers", "circle", 3.0), ("^--", "lines+markers", "triangle-up", 6.0)])
def test_an_errorbar_entry_draws_the_lab_marker_and_no_phantom_one(fmt, mode, symbol, size):
    # The legend draws an errorbar series as a line with marker "none" plus a marker-only
    # line: "none" read as a marker gave every entry a circle (a square, a diamond, a "."
    # dot all became full circles, and a plain line grew one).
    def build(fig):
        ax = fig.subplots()
        series = ax.errorbar([0, 1, 2], [1, 2, 1.5], yerr=.1, fmt=fmt, capsize=3)
        ax.legend([series], ["T2*"])
    entry = next(t for t in mpljson_to_plotly(_figure(build))["data"] if t.get("name") == "T2*")
    assert entry["mode"] == mode
    if symbol is None:
        assert "marker" not in entry
    else:
        assert entry["marker"]["symbol"] == symbol and entry["marker"]["size"] == pytest.approx(size)
    assert entry["line"]["dash"] == ("dash" if "--" in fmt else "solid")


def test_a_line_drawn_with_marker_none_has_no_markers():
    def build(fig):
        fig.subplots().plot([0, 1, 2], [0, 1, 0], marker="none", label="fit")
    trace = next(t for t in mpljson_to_plotly(_figure(build))["data"] if t.get("name") == "fit")
    assert trace["mode"] == "lines" and "marker" not in trace


@pytest.mark.parametrize("ncol,orientation", [(3, "h"), (1, "v")])
def test_a_multi_column_axes_legend_reads_across(ncol, orientation):
    def build(fig):
        ax = fig.subplots()
        for i in range(3):
            ax.plot([0, 1], [i, i + 1], label=f"series {i}")
        ax.legend(loc="lower center", ncol=ncol)
    fig_json = _figure(build, canvas=False)
    legend = mpljson_to_plotly(fig_json)["layout"]["legend"]
    box = fig_json["axes"][0]["legend"]["box"]
    assert box is not None, "a bare Figure is measured too"
    assert legend["orientation"] == orientation
    # a lower-centre legend is held by its bottom centre
    assert (legend["x"], legend["y"]) == pytest.approx(((box[0] + box[2]) / 2, box[1]))
    assert (legend["xanchor"], legend["yanchor"]) == ("center", "bottom")


@pytest.mark.parametrize("kwargs,anchors,corner", [
    ({"loc": "upper left", "bbox_to_anchor": (1.02, 1.0)}, ("left", "top"), (0, 3)),     # outside right
    ({"loc": "lower left"}, ("left", "bottom"), (0, 1)),
    ({"loc": "upper right"}, ("right", "top"), (2, 3)),
    ({"loc": "upper center"}, ("center", "top"), (None, 3)),
    ({"loc": "center right"}, ("right", "middle"), (2, None)),
    ({"loc": "lower center", "bbox_to_anchor": (0.5, -0.25)}, ("center", "top"), (None, 3))])  # below the axes
def test_a_legend_is_held_by_the_edge_facing_its_panel_border(kwargs, anchors, corner):
    def build(fig):
        ax = fig.subplots()
        ax.plot([0, 1], [0, 1], label="data")
        ax.plot([0, 1], [1, 0], label="fit")
        ax.legend(**kwargs)
    fig_json = _figure(build, size=(10, 6), canvas=False)
    box = fig_json["axes"][0]["legend"]["box"]
    legend = mpljson_to_plotly(fig_json)["layout"]["legend"]
    assert (legend["xanchor"], legend["yanchor"]) == anchors
    x = (box[0] + box[2]) / 2 if corner[0] is None else box[corner[0]]
    y = (box[1] + box[3]) / 2 if corner[1] is None else box[corner[1]]
    assert (legend["x"], legend["y"]) == pytest.approx((x, y))


def test_a_legend_with_enlarged_symbols_keeps_them_readable():
    def build(fig):
        ax = fig.subplots()
        ax.scatter([0, 1, 2], [0, 1, 0], s=1, label="shots")
        ax.legend(markerscale=6)
    legend = mpljson_to_plotly(_figure(build))["layout"]["legend"]
    assert legend.get("itemsizing") == "constant"

    def plain(fig):
        ax = fig.subplots()
        ax.scatter([0, 1, 2], [0, 1, 0], s=1, label="shots")
        ax.legend()
    assert "itemsizing" not in mpljson_to_plotly(_figure(plain))["layout"]["legend"]


def test_a_log_axis_over_decades_is_labelled_at_the_decades_only():
    def build(fig):
        wide, narrow = fig.subplots(1, 2)
        wide.plot([1, 2, 3], [1e-3, 1e-2, 1e-1])
        wide.set_yscale("log")
        narrow.plot([1, 2, 3], [2, 3, 5])
        narrow.set_yscale("log")
        narrow.set_ylim(2, 6)
    layout = mpljson_to_plotly(_figure(build, size=(10, 4)))["layout"]
    assert layout["yaxis"]["type"] == "log"
    assert layout["yaxis"].get("dtick") == 1 and layout["yaxis"].get("exponentformat") == "power"
    assert "dtick" not in layout["yaxis2"]          # under a decade: Plotly's own labels stay


def test_the_lab_tick_and_axis_title_sizes_are_kept():
    def build(fig):
        lab, plain = fig.subplots(1, 2)
        lab.plot([0, 1], [0, 1])
        lab.tick_params(labelsize=14)
        lab.set_xlabel("detuning", fontsize=13)
        lab.set_ylabel("signal")
        plain.plot([0, 1], [1, 0])
        plain.set_xlabel("time")
    layout = mpljson_to_plotly(_figure(build, size=(12, 4), canvas=False))["layout"]
    assert layout["xaxis"]["tickfont"]["size"] == pytest.approx(14)
    assert layout["yaxis"]["tickfont"]["size"] == pytest.approx(14)
    assert layout["xaxis"]["title"]["font"]["size"] == pytest.approx(13)
    assert layout["yaxis"]["title"]["font"]["size"] == pytest.approx(10)    # the default label size
    assert layout["xaxis2"]["tickfont"]["size"] == pytest.approx(10)        # a default panel is unchanged
    assert layout["xaxis2"]["title"]["font"]["size"] == pytest.approx(10)


def test_an_unfilled_patch_is_drawn_as_its_outline():
    # A step histogram (histtype="step") is one unfilled patch: only its edge is drawn.
    def build(fig):
        ax = fig.subplots()
        ax.hist([0, 1, 1, 2, 2, 2], bins=3, histtype="step", color="grey", lw=1.5, label="without")
        ax.hist([0, 0, 1], bins=3, color="tab:blue", alpha=.5, edgecolor="black", label="prepared")
        ax.add_patch(__import__("matplotlib.patches", fromlist=["Rectangle"]).Rectangle(
            (0.2, 0.2), 0.5, 1, fill=False, edgecolor="red", linestyle="--"))
        ax.axvspan(1.5, 1.8, facecolor="none", edgecolor="green")
    view = mpljson_to_plotly(_figure(build))
    polys = [t for t in view["data"] if t.get("line", {}).get("width")]
    step = next((t for t in polys if t["line"]["color"] == "#808080"), None)
    assert step is not None, "the step histogram is drawn as its outline"
    assert step["fill"] == "none" and step.get("opacity", 1) == 1 and step["line"]["width"] == 1.5
    box = next((t for t in polys if t["line"]["color"] == "#ff0000"), None)
    assert box is not None, "an unfilled box is drawn as its outline"
    assert box["fill"] == "none" and box["line"]["dash"] == "dash"
    bars = [t for t in polys if t["line"]["color"] == "rgba(0,0,0,0.5)"]   # alpha reaches the edge too
    assert bars and all(t["fill"] == "toself" and t["fillcolor"] == "rgba(31,119,180,0.5)"
                        and t.get("opacity", 1) == 1 for t in bars)   # the fill keeps its alpha, the edge none
    span = next((sh for sh in view["layout"]["shapes"] if sh.get("line", {}).get("color") == "#008000"), None)
    assert span is not None, "an unfilled span is drawn as its outline"
    assert span["fillcolor"] == "rgba(0,0,0,0)" and span["line"]["width"] == 1


def test_a_secondary_axis_keeps_the_ticks_matplotlib_drew():
    # a detuning axis over an RF axis: matplotlib draws round detuning ticks; samples at the
    # axis ends gave bin-edge labels (-20.25 ... 19.75)
    def build(fig):
        ax = fig.subplots()
        ax.plot([5000.0 - 20.25, 5000.0 + 19.75], [0, 1])
        ax.set_xlim(5000.0 - 20.25, 5000.0 + 19.75)
        ax.secondary_xaxis("top", functions=(lambda f: f - 5000.0, lambda d: d + 5000.0)).set_xlabel("detuning")
    fig_json = _figure(build, canvas=False)
    top = mpljson_to_plotly(fig_json)["layout"]["xaxis201"]
    detuning = [round(v - 5000.0, 6) for v in top["tickvals"]]
    assert len(detuning) >= 3 and 0 in detuning and all(float(d).is_integer() for d in detuning)
    assert [t.replace("\u2212", "-") for t in top["ticktext"]] == [f"{d:g}" for d in detuning]


def test_a_text_keeps_the_box_the_lab_drew_behind_it():
    def build(fig):
        ax = fig.subplots()
        ax.plot([0, 1], [0, 1])
        ax.text(.5, .5, "T1 = 21.8 us", fontsize=10, bbox={"facecolor": "white", "alpha": .5})
        ax.text(.2, .8, "plain", fontsize=10)
        fig.text(.5, .02, "summary", fontsize=8, bbox={"facecolor": "yellow", "edgecolor": "red"})
    notes = mpljson_to_plotly(_figure(build))["layout"]["annotations"]
    boxed = next(n for n in notes if n["text"] == "T1 = 21.8 us")
    assert boxed.get("bgcolor") == "rgba(255,255,255,0.5)" and boxed.get("bordercolor") == "rgba(0,0,0,0.5)"
    assert boxed["borderwidth"] == 1 and boxed["borderpad"] == pytest.approx(4)   # matplotlib: 4 pt
    plain = next(n for n in notes if n["text"] == "plain")
    assert "bgcolor" not in plain and "bordercolor" not in plain
    summary = next(n for n in notes if n["text"] == "summary")
    assert (summary.get("bgcolor"), summary.get("bordercolor")) == ("#ffff00", "#ff0000")


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
            # distinct colours: the third panel (no legend of its own) lists nothing, and
            # de-duplication cannot hide its entry if it leaked
            ax.plot([0, 1], [i, i + 1], label="data", color=f"C{i}")
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
