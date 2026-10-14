"""Markers keep the face and edge colours the lab gave them (hollow rings, black-edged stars)."""
import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def _view(draw):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure  # a bare Figure needs no GUI backend
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure(figsize=(6, 4))
    ax = fig.add_subplot()
    draw(ax)
    view = mpljson_to_plotly(extract_figure(fig, "markers"))
    return {t["name"]: t["marker"] for t in view["data"] if t.get("name")}


def test_line_markers_keep_face_edge_and_hollow():
    def draw(ax):
        ax.plot([0, 1], [0, 1], "o", mfc="none", mec="#00ffff", mew=2, ms=12, label="shortlist")
        ax.plot([0.5], [0.5], "*", mfc="#ff7f0e", mec="#000000", mew=1.5, ms=16, label="proposed")
        ax.plot([0, 1], [1, 0], "s", label="default")
    markers = _view(draw)
    ring, star, plain = markers["shortlist"], markers["proposed"], markers["default"]
    assert ring["color"] == "rgba(0,0,0,0)" and ring["line"] == {"color": "#00ffff", "width": 2.0}
    assert star["color"] == "#ff7f0e" and star["line"]["color"] == "#000000" and star["line"]["width"] == 1.5
    assert plain["color"] == plain["line"]["color"]  # an unstyled marker keeps one colour


def test_hollow_scatter_points_keep_their_edge():
    def draw(ax):
        ax.scatter([0, 1, 2], [0, 1, 2], facecolors="none", edgecolors="#d62728", linewidths=1.5, label="rings")
        ax.scatter([0, 1], [2, 1], color="#2ca02c", label="filled")
    markers = _view(draw)
    assert markers["rings"]["color"] == "rgba(0,0,0,0)" and markers["rings"]["line"]["color"] == "#d62728"
    assert markers["filled"]["color"] == "#2ca02c"


def test_results_extracted_before_marker_colours_keep_the_trace_colour():
    trace = {"type": "line", "x": [0, 1], "y": [0, 1], "mode": "markers", "name": "old", "color": "#123456",
             "marker_symbol": "circle", "marker_size": 6}
    view = mpljson_to_plotly({"axes": [{"traces": [trace]}]})
    marker = next(t["marker"] for t in view["data"] if t.get("name") == "old")
    assert marker["color"] == "#123456" and "line" not in marker
