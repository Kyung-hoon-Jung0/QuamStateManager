"""A wide lab figure, and a run's only figure, take a whole row of the Interactive grid."""
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from quam_state_manager.core.interactive_plots import replot

_ROOT = Path(__file__).resolve().parents[1] / "quam_state_manager" / "web"


def _panel(row, col, nrows, ncols, **extra):
    return {"grid": {"row": row, "col": col, "nrows": nrows, "ncols": ncols}, "traces": [], **extra}


def _span(fig):
    return replot.replot_menu({"figures": [dict(fig, key="f")]})[0]["span"]


def test_several_panel_columns_span_the_row():
    assert _span({"axes": [_panel(0, 0, 2, 2), _panel(0, 1, 2, 2), _panel(1, 0, 2, 2), _panel(1, 1, 2, 2)],
                  "size_inches": [8, 6]})


def test_a_wide_single_panel_canvas_spans_the_row():
    assert _span({"axes": [_panel(0, 0, 1, 1)], "size_inches": [15, 9]})


def test_a_small_single_panel_keeps_its_column():
    assert not _span({"axes": [_panel(0, 0, 1, 1)], "size_inches": [7.77, 5.87]})
    assert not _span({"axes": [_panel(0, 0, 1, 1)]})                     # no size recorded
    assert not _span({"axes": [_panel(0, 0, 1, 1)], "size_inches": [float("nan"), 5]})


def test_a_colorbar_is_not_a_panel_column():
    colorbar = _panel(0, 1, 1, 2, is_colorbar=True)
    assert not _span({"axes": [_panel(0, 0, 1, 1), colorbar], "size_inches": [7, 5]})


def test_template_marks_spanning_tiles():
    menu = replot.replot_menu({"figures": [
        {"key": "grid", "axes": [_panel(0, 0, 1, 2), _panel(0, 1, 1, 2)], "size_inches": [15, 9]},
        {"key": "single", "axes": [_panel(0, 0, 1, 1)], "size_inches": [7, 5]}]})
    body = Environment(loader=FileSystemLoader(_ROOT / "templates")).get_template("_dataset_replot.html").render(
        available=True, figures=menu, errors=[], util="synthetic", uid="0", run_id="0")
    tiles = re.findall(r'<div class="(ds-interactive-fig[^"]*)" data-fig="(\w+)"', body)
    assert tiles == [("ds-interactive-fig ds-fig-span", "grid"), ("ds-interactive-fig", "single")]


def test_css_spans_wide_and_lone_tiles():
    css = (_ROOT / "static" / "style.css").read_text(encoding="utf-8")
    rule = re.search(r"\.ds-interactive-list > \.ds-fig-span,\s*"
                     r"\.ds-interactive-list > \.ds-interactive-fig:only-child\s*\{([^}]*)\}", css)
    assert rule and re.search(r"grid-column:\s*1\s*/\s*-1", rule.group(1))
