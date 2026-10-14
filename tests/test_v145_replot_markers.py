"""Matplotlib marker shapes survive the reproduction: a tick-like '_' marker is a short stroke, not a dot."""
import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def _converted(*specs):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure  # a bare Figure needs no GUI backend
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure(figsize=(6, 4))
    ax = fig.add_subplot()
    for marker, label in specs:
        ax.plot([0, 1, 2], [1, 2, 3], linestyle="none", marker=marker, color="#000000", markersize=12, label=label)
    view = mpljson_to_plotly(extract_figure(fig, "markers"))
    return {t["name"]: t["marker"] for t in view["data"] if t.get("name")}


def test_tick_markers_are_strokes_drawn_with_the_line_colour():
    markers = _converted(("_", "expected level"), ("|", "boundary"))
    assert markers["expected level"]["symbol"] == "line-ew"
    assert markers["boundary"]["symbol"] == "line-ns"
    for marker in markers.values():
        assert marker["line"]["color"] == "#000000" and marker["line"]["width"] >= 1.5


def test_line_markers_and_filled_markers_keep_their_kind():
    markers = _converted(("x", "thin x"), ("X", "filled x"), ("+", "plus"), ("p", "pentagon"), ("o", "dot"))
    assert markers["thin x"]["symbol"] == "x-thin" and "line" in markers["thin x"]
    # v1.4.7: every marker now carries the lab's edge; a filled one has face and edge alike.
    assert markers["filled x"]["symbol"] == "x" and markers["filled x"]["line"]["color"] == markers["filled x"]["color"]
    assert markers["plus"]["symbol"] == "cross-thin"
    assert markers["pentagon"]["symbol"] == "pentagon"
    assert markers["dot"]["symbol"] == "circle" and markers["dot"]["line"]["color"] == markers["dot"]["color"]
