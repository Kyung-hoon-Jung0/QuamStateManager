"""v1.5.1 (r6, D4a P2-1): a colour-bar title longer than its bar fits the bar.

Plotly centres a vertical colour-bar title on the bar and draws it 1.2x the figure font, so a
long lab label ran past both ends of the bar: off the canvas edge (clipped to "eld-out ...") and
into the panel heading above. The converter now shrinks such a title to the bar, down to the
figure-UX font floor (60 % of the lab label size), and wraps it at word boundaries below that;
the browser scales the fitted size with the canvas like every other point size.
"""
import os
import subprocess
from pathlib import Path

import pytest
from matplotlib.figure import Figure

from quam_state_manager.core.interactive_plots import replot
from quam_state_manager.core.interactive_plots.mathtext import readable
from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly
from quam_state_manager.generator.iplot_extract import extract_figure

ROOT = Path(__file__).resolve().parents[1]
LONG = "held-out assignment fidelity (min over selector qubits)"


def _figure(label, size=(13.0, 5.4), bar=(.83, .12, .015, .6), orientation="vertical"):
    return dict(size_inches=list(size), axes=[
        dict(position=[.05, .12, .7, .6], xlabel="offset [MHz]", ylabel="amplitude",
             traces=[dict(type="heatmap", x=[0, 1], y=[0, 1], z=[[0, 1], [1, 0]],
                          colorbar=dict(visible=True, label=label, position=list(bar),
                                        orientation=orientation))]),
        dict(is_colorbar=True, position=list(bar))])


def _title(view):
    return view["data"][0]["colorbar"]["title"]


def _size(title):
    """The drawn size in figure points: a fitted size, else Plotly's own (1.2 x the 10 pt font)."""
    return (title.get("font") or {}).get("size", 12.0)


def _drawn_em(text):
    return max(replot._text_em(line) for line in text.split("<br>"))


def test_a_title_longer_than_its_bar_shrinks_to_the_bar():
    view = mpljson_to_plotly(_figure(LONG))
    title = _title(view)
    bar_pt = .6 * 5.4 * 72
    size = _size(title)
    assert title["text"] == LONG, "a shrink within the floor keeps one line"
    assert size < replot._COLORBAR_TITLE_PT, "the title is drawn smaller than Plotly's default"
    assert size >= replot._LAB_LABEL_PT * replot._HEADING_MIN_SCALE
    assert _drawn_em(title["text"]) * size <= bar_pt, "the drawn title fits its bar"
    assert title["side"] == "right"


def test_a_title_that_fits_keeps_plotly_size():
    title = _title(mpljson_to_plotly(_figure("fidelity [%]")))
    assert title["text"] == "fidelity [%]"
    assert "font" not in title, "a title that fits is left exactly as before"


def test_below_the_floor_the_title_wraps_at_words_only():
    label = "worst state fidelity of the $\\Delta f$ sweep, minimum over every selector qubit of the chip"
    view = mpljson_to_plotly(_figure(label, bar=(.83, .45, .015, .2)))
    title = _title(view)
    floor = replot._LAB_LABEL_PT * replot._HEADING_MIN_SCALE
    assert _size(title) == pytest.approx(floor), "it shrinks no further than the floor"
    lines = title["text"].split("<br>")
    assert len(lines) > 1, "a title too long for the floor wraps"
    assert title["text"].replace("<br>", " ") == readable(label), "lines break only between words"
    delta = readable("$\\Delta$")
    assert any(delta + " f" in line for line in lines), "a $...$ span stays on one line"
    bar_pt = .2 * 5.4 * 72
    assert all(replot._text_em(line) * floor <= bar_pt for line in lines if delta not in line)


def test_a_horizontal_colour_bar_is_untouched():
    view = mpljson_to_plotly(_figure(LONG, bar=(.05, .02, .3, .02), orientation="horizontal"))
    assert "font" not in _title(view)


def test_matplotlib_round_trip_fits_the_drawn_bar():
    fig = Figure(figsize=(13, 5.4))
    ax = fig.add_axes([.05, .12, .7, .6])
    image = ax.pcolormesh([[0, 1], [1, 0]])
    cax = fig.add_axes([.83, .12, .015, .6])
    fig.colorbar(image, cax=cax, label=LONG)
    view = mpljson_to_plotly(extract_figure(fig, "fidelity_map"))
    bar = view["data"][0]["colorbar"]
    title = bar["title"]
    assert title["text"] == LONG
    assert _drawn_em(title["text"]) * _size(title) <= bar["len"] * 5.4 * 72


def test_the_browser_scales_the_fitted_size_with_the_canvas():
    result = subprocess.run(["node", str(ROOT / "tests" / "replot_colorbar_title_selfcheck.cjs")],
                            cwd=ROOT, env=dict(os.environ), capture_output=True,
                            text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "all passed" in result.stdout
