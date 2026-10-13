"""Figure labels and secondary-axis titles remain readable in Plotly."""
import json
from pathlib import Path
import pytest

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
    layout = mpljson_to_plotly({"axes": axes, "suptitle": "Power scan", "texts": [
        {"text": "Power scan", "x": .5, "y": .98, "ha": "center", "va": "top"}]})["layout"]
    assert layout["margin"]["t"] >= 90 and layout["height"] >= 768
    assert all(note["yshift"] >= 60 for note in layout["annotations"])
    assert layout["yaxis"]["domain"][1] < .91
    assert layout["title"]["text"] == "Power scan"
    assert all(note["text"] != "Power scan" for note in layout["annotations"])


def test_source_aspect_column_titles_and_secondary_ticks():
    axes = [{"xlabel": "Pulse amplitude [mV]", "ylabel": "Number of pulses", "title": f"q{i}",
             "grid": {"row": 0, "col": i, "nrows": 1, "ncols": 5},
             "traces": [{"type": "heatmap", "x": [0, 1], "y": [0, 1], "z": [[0, 1], [1, 0]]}],
             "secondary_axes": [{"axis": "x", "side": "top", "label": "amplitude prefactor",
                                  "map": {"parent": list(range(11)), "forward": [n/10 for n in range(11)]}}]}
            for i in range(5)]
    layout = mpljson_to_plotly({"axes": axes, "size_inches": [15, 9]})["layout"]
    assert layout["height"] == 720
    assert layout["yaxis"]["title"]["text"] == "Number of pulses"
    assert all(layout[f"yaxis{i}"]["title"]["text"] == "" for i in range(2, 6))
    gap = layout["xaxis2"]["domain"][0] - layout["xaxis"]["domain"][1]
    assert gap * 1080 >= 80
    for i in range(201, 206):
        assert layout[f"xaxis{i}"]["tickvals"] == [1, 5, 9]


def test_extractor_carries_source_figure_aspect():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = plt.figure(figsize=(15, 9))
    try:
        assert extract_figure(fig, "map")["size_inches"] == [15, 9]
    finally:
        plt.close(fig)


def test_serialized_warning_wraps_newlines_and_escapes_pseudo_html():
    warning = "the rotation per pair stays positive across the whole window: no zero crossing to report " * 4
    note = {"text": warning + "\nroot < lower & upper > 0", "x": .02, "y": .06,
            "xref_frac": True, "size": 8, "ha": "left", "va": "bottom", "wrap": True}
    axes = [{"xlabel": "alpha", "traces": [{"type": "line", "x": [0, 1], "y": [0, 1]}],
             "annotations": [note], "grid": {"row": 0, "col": col, "nrows": 1, "ncols": 3}}
            for col in range(3)]
    figure = {"axes": axes, "texts": [{**note, "text": "figure < warning & note >\nsecond line"}]}
    notes = mpljson_to_plotly(figure)["layout"]["annotations"]
    assert all("<br>" in n["text"] and "\n" not in n["text"] for n in notes)
    assert all("&lt;" in n["text"] and "&gt;" in n["text"] and "&amp;" in n["text"] for n in notes)
    assert max(len(line) for line in notes[0]["text"].split("<br>")[:-1]) <= 55
    assert readable("literal <br> & $x^2$\nnext") == "literal &lt;br&gt; &amp; x<sup>2</sup><br>next"


def test_extractor_retains_annotation_and_figure_wrap():
    pytest.importorskip("matplotlib")
    import matplotlib.pyplot as plt
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig, ax = plt.subplots()
    try:
        ax.annotate("warning\nroot side", (.02, .06), xycoords="axes fraction", wrap=True)
        fig.text(.1, .1, "figure note", wrap=True)
        extracted = extract_figure(fig, "warning")
        assert extracted["axes"][0]["annotations"][0]["wrap"] is True
        assert extracted["texts"][0]["wrap"] is True
    finally:
        plt.close(fig)
