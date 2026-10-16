"""Height fitting, source axis styles, marker paths, text-only keys and filled regions."""
import json
import pytest
from matplotlib.figure import Figure
from quam_state_manager.generator.iplot_extract import extract_figure, _MARKER
from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def test_heading_fits_height_above_tall_stack():
    fig = Figure(figsize=(7.5, 30))
    for i, ax in enumerate(fig.subplots(5, 1)):
        ax.plot([0, 1], [0, 1])
        ax.set_title("Panel caption", fontsize=16)
    fig.subplots_adjust(top=.965)
    fig.suptitle("Heading line one\nHeading line two", fontsize=24, y=.99)
    source = extract_figure(fig, "stack")
    result = mpljson_to_plotly(source)
    heading = next(n for n in result["layout"]["annotations"] if n["text"].startswith("Heading"))
    caption_top = max(a["title_box"][3] for a in source["axes"])
    bottom = heading["y"] - (heading["font"]["size"] * 1.3 * 2) / (30 * 72)
    assert bottom > caption_top + 2 / (30 * 72)
    assert heading["font"]["size"] < 24 * 1.2 / 1.3


def test_heading_rises_into_the_room_above_the_captions_at_the_lab_size():
    # a two-line heading hung at y=0.98 over a tall stack reaches the first captions, but the
    # room up to the figure's top edge holds it at the lab's own size
    fig = Figure(figsize=(11, 25))
    for ax in fig.subplots(5, 2).ravel():
        ax.plot([0, 1], [0, 1])
        ax.set_title("Panel caption", fontsize=10)
    fig.subplots_adjust(top=.965)
    fig.suptitle("Heading line one\nHeading line two", fontsize=12, y=.98)
    source = extract_figure(fig, "stack")
    heading = next(n for n in mpljson_to_plotly(source)["layout"]["annotations"] if n["text"].startswith("Heading"))
    caption_top = max(a["title_box"][3] for a in source["axes"]) * 25 * 72
    assert heading["font"]["size"] == pytest.approx(12 * 1.2 / 1.3, abs=1e-3)
    assert heading["y"] > .98 and heading["y"] <= 1
    assert heading["y"] * 25 * 72 - heading["font"]["size"] * 1.3 * 2 >= caption_top + 2


@pytest.mark.parametrize("kind,size", [("x", 17), ("y", 7)])
def test_secondary_axis_keeps_its_own_text_sizes(kind, size):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([0, 1], [0, 1])
    secondary = getattr(ax, "secondary_" + kind + "axis")("top" if kind == "x" else "right")
    getattr(secondary, "set_" + kind + "label")("Secondary", fontsize=size, color="orange")
    secondary.tick_params(axis=kind, labelsize=size - 1, labelcolor="red")
    result = mpljson_to_plotly(extract_figure(fig, "axis"))
    axis = result["layout"][kind + "axis201"]
    assert axis["title"]["font"] == {"size": size, "color": "#ffa500"}
    assert axis["tickfont"] == {"size": size - 1, "color": "#ff0000"}


@pytest.mark.parametrize("marker,symbol", list(_MARKER.items()))
def test_scatter_and_line_markers_keep_supported_shapes(marker, symbol):
    fig = Figure()
    ax = fig.subplots()
    ax.scatter([0], [0], marker=marker, color="gray")
    ax.plot([1], [1], marker=marker, color="gray", ls="none")
    data = mpljson_to_plotly(extract_figure(fig, "markers"))["data"]
    assert len(data) == 2
    assert all(t.get("marker", {}).get("symbol") == symbol for t in data)


def test_every_named_matplotlib_marker_has_a_plotly_symbol():
    from matplotlib.markers import MarkerStyle
    named = [m for m in MarkerStyle.markers if not (isinstance(m, str) and m.strip().lower() in ("none", ""))]
    assert sorted(map(str, set(named) - set(_MARKER))) == []


@pytest.mark.parametrize("marker,symbol", [(0, "line-ew"), (1, "line-ew"), (2, "line-ns"), (3, "line-ns"),
                                           (4, "triangle-left"), (5, "triangle-right"), (6, "triangle-up"),
                                           (7, "triangle-down"), (8, "triangle-left"), (11, "triangle-down")])
def test_tick_and_caret_markers_keep_their_shape(marker, symbol):
    fig = Figure()
    ax = fig.subplots()
    ax.scatter([0], [0], marker=marker, color="gray")
    ax.plot([1], [1], marker=marker, color="gray", ls="none")
    data = mpljson_to_plotly(extract_figure(fig, "markers"))["data"]
    assert [t["marker"]["symbol"] for t in data] == [symbol, symbol]


def test_text_only_legend_hides_symbols_and_keeps_padding():
    fig = Figure()
    ax = fig.subplots()
    ax.plot([0, 1], [0, 1], label="Score = 1.0", color="orange")
    ax.legend(handlelength=0, handletextpad=.2)
    source = extract_figure(fig, "key")
    assert source["axes"][0]["legend"]["handletextpad"] == .2
    result = mpljson_to_plotly(source)
    shown = [t for t in result["data"] if t.get("showlegend")]
    assert len(shown) == 1
    assert shown[0]["mode"] == "markers"
    assert shown[0]["marker"]["color"] == shown[0]["marker"]["line"]["color"] == "rgba(0,0,0,0)"
    assert result["layout"]["legend"]["indentation"] == -15
    line = next(t for t in result["data"] if t.get("line", {}).get("color") == "#ffa500" and t.get("x") == [0, 1])
    # the text-only key still shows and hides the line it labels
    assert line.get("legendgroup") == shown[0]["legendgroup"]


@pytest.mark.parametrize("alpha", [.3, 0])
def test_filled_contours_keep_levels_paint_and_compound_holes(alpha):
    import numpy as np
    fig = Figure()
    ax = fig.subplots()
    x = np.linspace(-2, 2, 31)
    xx, yy = np.meshgrid(x, x)
    levels = [0, .25, 1, 3, 8]
    contours = ax.contourf(x, x, xx ** 2 + yy ** 2, levels=levels, cmap="plasma", alpha=alpha)
    source = extract_figure(fig, "regions")
    regions = [t for t in source["axes"][0]["traces"] if t["type"] == "filled_contour"]
    assert len(regions) == 4 and all(t["levels"] == levels and t["cmap"] == "plasma" for t in regions)
    assert all(t["color"] == "rgba(0,0,0,0)" if alpha == 0 else t["color"].endswith(",0.3)") for t in regions)
    assert any(len(t["rings"]) > 1 for t in regions)
    result = mpljson_to_plotly(source)
    shapes = [s for s in result["layout"].get("shapes", []) if s.get("name") == "filled-contour"]
    assert len(shapes) == len(regions)
    assert all(s["fillrule"] == "evenodd" and s["fillcolor"] == t["color"] for s, t in zip(shapes, regions))
    # above the house grid, as the lab's opaque fill hides its grid; below every trace
    assert all(s["layer"] == "between" for s in shapes)
    assert all(s["path"].count("M ") == len(t["rings"]) for s, t in zip(shapes, regions))
    assert not any("filled contour" in n for n in result["notices"])
    json.dumps(result, allow_nan=False)


def test_contour_hatching_is_reported_while_fills_are_drawn():
    fig = Figure()
    ax = fig.subplots()
    ax.contourf([[0, 1], [1, 0]], levels=[0, .5, 1], hatches=["/", "\\"])
    result = mpljson_to_plotly(extract_figure(fig, "hatched"))
    assert any("contour hatching" in n for n in result["notices"])
    assert any(s.get("name") == "filled-contour" for s in result["layout"].get("shapes", []))
