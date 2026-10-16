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


def test_without_a_renderer_the_location_code_places_it_in_that_panel():
    figure, layout = _view([None, "lower right"], with_renderer=False)
    assert figure["axes"][1]["legend"]["box"] is None
    legend = layout["legend"]
    x0, y0, w, _h = figure["axes"][1]["position"]
    assert legend["x"] == pytest.approx(x0 + w) and legend["y"] == pytest.approx(y0)
    assert (legend["xanchor"], legend["yanchor"]) == ("right", "bottom")


def test_several_legends_each_stay_in_their_panel():
    # v1.4.1: one footer -> panel legends; retain panel values and avoid axis-title overlap.
    figure, layout = _view(["upper left", "lower right"], with_renderer=True)
    for name, axes in (("legend", figure["axes"][0]), ("legend2", figure["axes"][1])):
        box = axes["legend"]["box"]
        legend = layout[name]
        assert legend["orientation"] == "v" and (legend["x"], legend["y"]) == pytest.approx((box[0], box[3]))
        assert legend.get("borderwidth") == 1   # the lab's legend frame
