"""A lab figure's layout-engine placement and its multi-line heading survive the reproduction."""
import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly

_HEADING = "Line one of the verdict\nLine two\nLine three\nLine four\nLine five\nLine six"


def _figure(layout=None, heading=_HEADING):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure  # a bare Figure needs no GUI backend
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure(figsize=(10, 4), layout=layout)
    for i, ax in enumerate(fig.subplots(1, 2)):
        ax.plot([0, 1], [i, i + 1])
        ax.set_title(f"panel {i}")
    fig.suptitle(heading, color="#dc143c", fontsize=11)
    return extract_figure(fig, "verdict")


def test_constrained_layout_gives_the_heading_room_as_in_the_saved_png():
    plain = _figure()
    placed = _figure("constrained")
    top = lambda f: max(a["position"][1] + a["position"][3] for a in f["axes"])
    assert top(plain) == pytest.approx(0.88)   # the default subplot box, which the heading overlaps
    assert top(placed) < 0.75                  # the engine moved the panels below a six-line heading


def test_a_multi_line_heading_hangs_from_its_top_as_a_note():
    view = mpljson_to_plotly(_figure("constrained"))
    assert view["layout"]["title"]["text"] == ""
    note = next(n for n in view["layout"]["annotations"] if n["text"].startswith("Line one"))
    assert (note["xref"], note["yref"], note["yanchor"]) == ("paper", "paper", "top")
    assert note["font"]["size"] == pytest.approx(11 * 1.2 / 1.3, abs=1e-3)  # Plotly's 1.3 em lines fill 1.2 em
    assert note["font"]["color"] == "#dc143c"


def test_a_single_line_heading_stays_the_plot_title():
    view = mpljson_to_plotly(_figure("constrained", heading="One line"))
    assert view["layout"]["title"]["text"] == "One line"
    assert not any(n["text"] == "One line" for n in view["layout"].get("annotations", []))
