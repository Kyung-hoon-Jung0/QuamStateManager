"""A reproduced legend sits where the lab drew it: inside the one panel that carries it."""
import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def _view(legends, with_renderer):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure  # a bare Figure needs no GUI backend
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure(figsize=(10, 4))
    if with_renderer:
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        FigureCanvasAgg(fig)
    axes = fig.subplots(1, 2)
    for i, ax in enumerate(axes):
        ax.plot([0, 1, 2], [0, 1, 4], "o-", label=f"data {i}")
        ax.plot([0, 2], [3.5, 3.5], label=f"limit {i}")
        if legends[i]:
            ax.legend(loc=legends[i])
    figure = extract_figure(fig, "legend")
    return figure, mpljson_to_plotly(figure)["layout"]


def test_one_legend_panel_keeps_its_drawn_box():
    figure, layout = _view([None, "lower right"], with_renderer=True)
    box = figure["axes"][1]["legend"]["box"]
    right = figure["axes"][1]["position"]
    assert box and right[0] < box[0] < box[2] <= right[0] + right[2] + 1e-6   # inside the right panel
    legend = layout["legend"]
    assert (legend["x"], legend["y"]) == pytest.approx((box[0], box[3]))
    assert (legend["xanchor"], legend["yanchor"], legend["orientation"]) == ("left", "top", "v")


def test_a_bare_figure_is_measured_too():
    # v1.4.1: a bare Figure had no renderer, so its legend box was None -> the extractor
    # measures it (most lab plots build a bare Figure; the fixed fallback misplaced them).
    figure, layout = _view([None, "lower right"], with_renderer=False)
    box = figure["axes"][1]["legend"]["box"]
    assert box is not None
    assert (layout["legend"]["x"], layout["legend"]["y"]) == pytest.approx((box[0], box[3]))


@pytest.mark.parametrize("loc,anchors", [(4, ("right", "bottom")), (8, ("center", "bottom")),
                                         (9, ("center", "top")), (6, ("left", "middle")),
                                         (10, ("center", "middle")), (2, ("left", "top"))])
def test_without_a_box_the_location_code_places_it_in_that_panel(loc, anchors):
    # v1.4.1: reached through a bare Figure -> through a removed box (bare figures are measured
    # now); the centre codes (lower/upper center, center left, center) were read as a corner.
    figure, _layout = _view([None, "lower right"], with_renderer=True)
    figure["axes"][1]["legend"].update(box=None, loc=loc)
    legend = mpljson_to_plotly(figure)["layout"]["legend"]
    x0, y0, w, h = figure["axes"][1]["position"]
    xs = {"left": x0, "center": x0 + w / 2, "right": x0 + w}
    ys = {"bottom": y0, "middle": y0 + h / 2, "top": y0 + h}
    assert (legend["xanchor"], legend["yanchor"]) == anchors
    assert (legend["x"], legend["y"]) == pytest.approx((xs[anchors[0]], ys[anchors[1]]))


def test_several_legends_each_stay_in_their_panel():
    # v1.4.1: one footer -> panel legends; retain panel values and avoid axis-title overlap.
    figure, layout = _view(["upper left", "lower right"], with_renderer=True)
    for name, axes in (("legend", figure["axes"][0]), ("legend2", figure["axes"][1])):
        box = axes["legend"]["box"]
        legend = layout[name]
        assert legend["orientation"] == "v" and (legend["x"], legend["y"]) == pytest.approx((box[0], box[3]))
        assert legend.get("borderwidth") == 1   # the lab's legend frame
