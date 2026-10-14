"""One legend entry per artist kind in a reproduced lab figure; contour levels stay off the legend."""
from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def _panel(col, traces):
    return {"grid": {"row": 0, "col": col, "nrows": 1, "ncols": 3}, "traces": traces}


def _line(name, color, y0=0):
    return {"type": "line", "x": [0, 1], "y": [y0, y0 + 1], "name": name, "color": color, "mode": "lines"}


def _legend(view):
    return [(t.get("name"), t.get("legendgroup")) for t in view["data"] if t.get("showlegend")]


def test_a_label_repeated_in_every_panel_is_one_entry_for_all_panels():
    view = mpljson_to_plotly({"axes": [_panel(c, [_line("edge model", "#000000", c)]) for c in range(3)]})
    fits = [t for t in view["data"] if t.get("name") == "edge model"]
    assert len(fits) == 3
    assert _legend(view) == [("edge model", fits[0]["legendgroup"])]
    assert {t["legendgroup"] for t in fits} == {fits[0]["legendgroup"]}   # one click toggles every panel


def test_same_name_different_look_stays_two_entries():
    view = mpljson_to_plotly({"axes": [_panel(0, [_line("fit", "#ff0000")]), _panel(1, [_line("fit", "#0000ff")]),
                                       _panel(2, [])]})
    names = _legend(view)
    assert [n for n, _ in names] == ["fit", "fit"] and names[0][1] != names[1][1]


def test_contour_levels_name_the_line_but_not_the_legend():
    contour = {"type": "contour", "level": 0.5, "levels": [0.5], "x": [0, 1, 1], "y": [0, 0, 1],
               "mode": "lines", "color": "#00aa00", "width": 1}
    view = mpljson_to_plotly({"axes": [_panel(0, [contour]), _panel(1, [dict(contour)]), _panel(2, [])]})
    levels = [t for t in view["data"] if t.get("name") == "level 0.5"]
    assert len(levels) == 2 and not any(t.get("showlegend") for t in levels)
