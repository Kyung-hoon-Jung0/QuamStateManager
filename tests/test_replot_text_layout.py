"""Figure labels and secondary-axis titles remain readable in Plotly."""
import json
from pathlib import Path

from quam_state_manager.core.interactive_plots.mathtext import readable
from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def test_real_drag_axis_renders_greek_mathtext():
    figure = json.loads((Path(__file__).parent / "fixtures/map_journeys/run-106.json").read_text())[0]
    label = mpljson_to_plotly(figure)["layout"]["xaxis"]["title"]["text"]
    assert label == "DRAG coefficient \u03b1" and "$" not in label


def test_mathtext_subscripts_superscripts_and_embedded_labels():
    assert readable(r"Frequency $f_{01}$ and $\alpha^2$ [Hz]") == "Frequency f<sub>01</sub> and \u03b1<sup>2</sup> [Hz]"
    assert readable(r"$\mathrm{T}_{1}$ ($\mu$s), $10^{-3}$") == "T<sub>1</sub> (\u03bcs), 10<sup>-3</sup>"
    assert readable("Amplitude (mV)") == "Amplitude (mV)"


def test_secondary_top_axis_reserves_title_space_in_each_grid_row():
    axes = []
    for row in range(2):
        for col in range(3):
            axes.append({"xlabel": "Amplitude (mV)", "ylabel": "Number of pulses", "title": f"q{row*3+col}",
                "grid": {"row": row, "col": col, "nrows": 2, "ncols": 3},
                "traces": [{"type": "line", "x": [0, 1], "y": [0, 1]}],
                "secondary_axes": [{"axis": "x", "side": "top", "label": "amplitude prefactor",
                                    "map": {"parent": [0, 1], "forward": [.9, 1.1]}}]})
    layout = mpljson_to_plotly({"axes": axes})["layout"]
    assert layout["margin"]["t"] >= 90 and layout["height"] >= 768
    assert all(note["yshift"] >= 60 for note in layout["annotations"])
    assert layout["yaxis"]["domain"][1] < .91
