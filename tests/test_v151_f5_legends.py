"""Legend ownership, balanced packing and unambiguous click groups."""
import pytest
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from quam_state_manager.generator.iplot_extract import extract_figure
from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def view(fig):
    return mpljson_to_plotly(extract_figure(fig, "figure"))


@pytest.mark.parametrize("panels", [1, 3])
@pytest.mark.parametrize("styles", [
    ({"color": "red", "ls": "--"}, {"color": "blue", "ls": "-"}),
    ({"color": "red", "ls": "--"}, {"color": "red", "ls": "-"}),
    ({"color": "red", "lw": 1}, {"color": "red", "lw": 4}),
    ({"color": "red", "marker": "*"}, {"color": "red", "marker": "s"}),
])
def test_duplicate_labels_link_only_the_same_style(panels, styles):
    fig = Figure(figsize=(12, 6))
    handles = []
    for ax in fig.subplots(1, panels, squeeze=False)[0]:
        handles = [ax.plot([i * 2, i * 2 + 1], [0, 1], label="data", **style)[0]
                   for i, style in enumerate(styles)]
    fig.legend(handles, ["data", "data"])
    data = view(fig)["data"]
    shown = [t for t in data if t.get("showlegend")]
    assert len(shown) == 2
    for t in data:
        if t.get("x") != [None]:
            expected = shown[0 if t["x"][0] == 0 else 1]
            assert t.get("legendgroup") == expected.get("legendgroup")


def test_keys_of_different_look_cannot_share_a_measurement():
    # an axes key the lab restyled (opaque) and a figure key of the cloud's own look both stand
    # for the one cloud: keys that disagree on the look claim it ambiguously, so neither does
    fig = Figure()
    ax = fig.subplots()
    cloud = ax.scatter([0, 1], [0, 1], color="red", alpha=.2, label="cloud")
    for handle in ax.legend().legend_handles:
        handle.set_alpha(1)
    fig.legend([cloud], ["cloud"], loc="lower center")
    data = view(fig)["data"]
    trace = next(t for t in data if t.get("x") != [None] and t.get("name") == "cloud")
    keys = [t for t in data if t.get("showlegend")]
    assert len(keys) == 2 and len({k["legendgroup"] for k in keys}) == 2
    assert not trace.get("legendgroup")


def test_identical_keys_drawn_twice_are_one_key():
    # two identical keys (a lab that draws the same figure legend twice, one over the
    # other) -> one group with their measurement (the first F5 draft left it to neither)
    fig = Figure()
    ax = fig.subplots()
    ax.plot([0, 1], [0, 1], color="red", label="data")
    handles = [Line2D([], [], color="red") for _ in range(2)]
    fig.legend(handles, ["data", "data"])
    data = view(fig)["data"]
    trace = next(t for t in data if t.get("x") != [None])
    keys = [t for t in data if t.get("showlegend")]
    assert len(keys) == 2 and {k.get("legendgroup") for k in keys} == {trace.get("legendgroup")}
    assert trace.get("legendgroup")


@pytest.mark.parametrize("panels", [1, 4])
def test_a_figure_legend_drawn_twice_still_controls_every_panel(panels):
    fig = Figure(figsize=(12, 6))
    for ax in fig.subplots(1, panels, squeeze=False)[0]:
        ax.plot([0, 1], [0, 1], ".", alpha=.2, label="Ground")
        ax.axvline(.5, color="r", ls="--", lw=.5, label="Threshold")
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=2)
    fig.legend(handles, labels, loc="lower center", ncol=2)
    data = view(fig)["data"]
    keys = {}
    for t in data:
        if t.get("showlegend"):
            keys.setdefault(t["name"], set()).add(t.get("legendgroup"))
    assert sorted(keys) == ["Ground", "Threshold"] and all(len(g) == 1 for g in keys.values())
    measured = [t for t in data if t.get("name") in keys and not t.get("showlegend")]
    assert len(measured) == 2 * panels
    assert all({t.get("legendgroup")} == keys[t["name"]] for t in measured)


@pytest.mark.parametrize("count,cols", [(7, 3), (6, 4), (5, 3), (3, 2)])
@pytest.mark.parametrize("owner", ["figure", "axes"])
def test_balanced_columns_keep_drawn_rows(count, cols, owner):
    fig = Figure(figsize=(12, 6))
    ax = fig.subplots()
    lines = [ax.plot([0, 1], [i, i + 1], label=str(i), color="red")[0] for i in range(count)]
    # Repeated labels and an explicitly empty label still occupy their drawn seats.
    labels = ["same" if i in (0, 1) else "" if i == 2 else str(i) for i in range(count)]
    leg = (fig if owner == "figure" else ax).legend(lines, labels, ncol=cols)
    source = extract_figure(fig, "figure")
    columns = [[next(c.get_text() for c in row.get_children() if callable(getattr(c, "get_text", None)))
                for row in column.get_children()] for column in leg._legend_handle_box.get_children()]
    expected = [[col[r] for col in columns if r < len(col)] for r in range(max(map(len, columns)))]
    result = mpljson_to_plotly(source)
    keys = sorted({t.get("legend", "legend") for t in result["data"] if t.get("showlegend")},
                  key=lambda k: result["layout"][k]["y"], reverse=True)
    actual = [[t["name"] for t in sorted(result["data"], key=lambda t: t.get("legendrank", 1000))
               if t.get("showlegend") and t.get("legend", "legend") == key] for key in keys]
    assert actual == expected


def test_twin_legends_keep_their_own_entries_and_position():
    fig = Figure(figsize=(10, 6))
    ax = fig.subplots()
    twin = ax.twinx()
    ax.plot([0, 1], [0, 1], color="red", label="left")
    ax.legend(loc="upper left")
    twin.plot([0, 1], [2, 4], color="blue", label="right")
    twin.legend(loc="upper right")
    result = view(fig)
    shown = {t["name"]: t.get("legend", "legend") for t in result["data"] if t.get("showlegend")}
    assert set(shown) == {"left", "right"} and shown["left"] != shown["right"]
    assert result["layout"][shown["left"]]["x"] < result["layout"][shown["right"]]["x"]


@pytest.mark.parametrize("hidden", [False, True])
@pytest.mark.parametrize("nested", [False, True])
def test_subfigure_and_hidden_legends(hidden, nested):
    fig = Figure(figsize=(10, 6))
    owner = fig.subfigures(2, 1)[1] if nested else fig
    line = owner.subplots().plot([0, 1], [0, 1], label="source data")[0]
    leg = owner.legend([line], ["subfigure data"], loc="lower center")
    leg.set_visible(not hidden)
    result = view(fig)
    assert [t["name"] for t in result["data"] if t.get("showlegend")] == ([] if hidden else ["subfigure data"])
    if nested and not hidden:
        assert result["layout"]["legend"]["y"] < .2


def test_framed_split_legend_has_one_backing_over_data():
    fig = Figure(figsize=(10, 6))
    ax = fig.subplots()
    ax.imshow([[1, 1], [1, 1]], cmap="gray", vmin=0, vmax=1)
    ax.legend([Line2D([], [], color="red") for _ in range(4)], list("abcd"),
              ncol=2, frameon=True, facecolor="white", framealpha=1)
    layout = view(fig)["layout"]
    backs = [s for s in layout.get("shapes", []) if s.get("name") == "legend-backing"]
    assert len(backs) == 1 and backs[0].get("fillcolor") and backs[0]["line"]["width"] > 0
    assert backs[0]["layer"] == "above" and backs[0]["xref"] == backs[0]["yref"] == "paper"


def test_hidden_axes_legend_has_no_entries():
    fig = Figure()
    ax = fig.subplots()
    ax.plot([0, 1], [0, 1], label="data")
    ax.legend().set_visible(False)
    assert not any(t.get("showlegend") for t in view(fig)["data"])


def test_subfigure_legend_cannot_claim_a_matching_sibling():
    fig = Figure()
    upper, lower = fig.subfigures(2, 1)
    line = upper.subplots().plot([0, 1], [0, 1], label="data", color="red")[0]
    lower.subplots().plot([0, 1], [2, 3], label="data", color="red")
    upper.legend([line], ["data"])
    data = view(fig)["data"]
    proxy = next(t for t in data if t.get("showlegend"))
    upper_line = next(t for t in data if t.get("y") == [0, 1])
    lower_line = next(t for t in data if t.get("y") == [2, 3])
    assert upper_line.get("legendgroup") == proxy.get("legendgroup")
    assert not lower_line.get("legendgroup")


def test_same_label_and_paint_keep_line_and_scatter_ownership():
    fig = Figure()
    ax = fig.subplots()
    line = ax.plot([0], [0], marker="o", ls="none", color="red", label="data")[0]
    cloud = ax.scatter([2], [0], color="red", label="data")
    fig.legend([line, cloud], ["data", "data"])
    data = view(fig)["data"]
    shown = [t for t in data if t.get("showlegend")]
    assert len(shown) == 2
    for trace in data:
        if trace.get("x") != [None]:
            assert trace.get("legendgroup") == shown[0 if trace["x"][0] == 0 else 1].get("legendgroup")


@pytest.mark.parametrize("marker", ["axhline", "axvline", "axvspan", "axhspan"])
def test_an_unlabelled_line_or_span_keeps_the_entry_before_it(marker):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([0, 1], [0, 1], color="red", label="data")
    getattr(ax, marker)(*((.5,) if marker in ("axhline", "axvline") else (.2, .4)), color="black")
    ax.legend()
    data = view(fig)["data"]
    assert [(t.get("name"), t.get("x")) for t in data if t.get("showlegend")] == [("data", [0.0, 1.0])]


def test_one_panel_legend_still_controls_the_same_kind_in_every_panel():
    fig = Figure(figsize=(10, 4))
    axes = fig.subplots(1, 3)
    for ax in axes:
        ax.plot([0, 1], [0, 1], color="red", label="data")
        ax.plot([0, 1], [1, 0], color="blue", ls="--", label="fit")
    axes[2].plot([0, 1], [.5, .5], color="green", label="data")   # same name, another look
    axes[0].legend()
    data = view(fig)["data"]
    shown = {t["name"]: t["legendgroup"] for t in data if t.get("showlegend")}
    assert set(shown) == {"data", "fit"}
    for t in data:
        if t.get("showlegend") or not t.get("name"):
            continue
        colour = t["line"]["color"]
        want = shown["data"] if colour == "#ff0000" else shown["fit"] if colour == "#0000ff" else None
        assert t.get("legendgroup") == want, (t["name"], colour)


def test_a_combined_twin_legend_controls_the_twin_measurement():
    fig = Figure()
    ax = fig.subplots()
    twin = ax.twinx()
    left = ax.plot([0, 1], [0, 1], color="red", label="left")[0]
    right = twin.plot([0, 1], [3, 2], color="blue", label="right")[0]
    ax.legend([left, right], ["left", "right"])
    data = view(fig)["data"]
    key = next(t for t in data if t.get("showlegend") and t["name"] == "right")
    measured = next(t for t in data if t.get("name") == "right" and t.get("x") != [None])
    assert key.get("x") == [None] and measured.get("legendgroup") == key["legendgroup"]


def test_a_restyled_key_controls_its_one_measurement_only():
    fig = Figure()
    ax = fig.subplots()
    ax.scatter([0, 1], [0, 1], color="red", alpha=.2, label="cloud")
    ax.scatter([2, 3], [0, 1], color="blue", alpha=.2, label="other")
    leg = ax.legend()
    for handle in leg.legend_handles:
        handle.set_alpha(1)   # the common lab idiom: an opaque key for a translucent cloud
    data = view(fig)["data"]
    keys = {t["name"]: t for t in data if t.get("showlegend")}
    assert all(k.get("x") == [None] and k["marker"]["opacity"] == 1 for k in keys.values())
    for t in data:
        if t.get("x") != [None] and t.get("name"):
            assert t.get("legendgroup") == keys[t["name"]]["legendgroup"]
    assert keys["cloud"]["legendgroup"] != keys["other"]["legendgroup"]


def test_an_undrawable_entry_keeps_its_seat_in_the_rows():
    fig = Figure(figsize=(12, 6))
    ax = fig.subplots()
    lines = [ax.plot([0, 1], [i, i + 1], color="red", label=str(i))[0] for i in range(5)]
    lines[1].set_visible(False)   # the legend draws an empty key the converter cannot draw
    ax.legend(lines, [str(i) for i in range(5)], ncol=2)
    result = view(fig)
    rows = {}
    for t in result["data"]:
        if t.get("showlegend"):
            rows.setdefault(result["layout"][t.get("legend", "legend")]["y"], []).append(t["name"])
    # matplotlib: column 0 = 0, 1, 2 and column 1 = 3, 4; key 1 is drawn empty in row 1
    assert [sorted(names) for _, names in sorted(rows.items(), reverse=True)] == [["0", "3"], ["4"], ["2"]]


def test_each_panel_legend_keeps_its_own_drawn_rows():
    fig = Figure(figsize=(14, 6))
    axes = fig.subplots(1, 2)
    expected = []
    for ax, count in zip(axes, (7, 5)):
        lines = [ax.plot([0, 1], [i, i + 1], color="red", label=f"{count}:{i}")[0] for i in range(count)]
        leg = ax.legend(lines, [line.get_label() for line in lines], ncol=3)
        columns = [[next(c.get_text() for c in row.get_children() if callable(getattr(c, "get_text", None)))
                    for row in column.get_children()] for column in leg._legend_handle_box.get_children()]
        expected.append([[col[r] for col in columns if r < len(col)] for r in range(max(map(len, columns)))])
    result = view(fig)
    for count, rows in zip((7, 5), expected):
        keys = sorted({t.get("legend", "legend") for t in result["data"]
                       if t.get("showlegend") and t["name"].startswith(f"{count}:")},
                      key=lambda k: result["layout"][k]["y"], reverse=True)
        actual = [[t["name"] for t in sorted(result["data"], key=lambda t: t.get("legendrank", 1000))
                   if t.get("showlegend") and t.get("legend", "legend") == key] for key in keys]
        assert actual == rows


def test_a_figure_legend_that_lists_every_panel_keeps_its_rows_and_order():
    # fig.legend() with no handles lists every panel's artists: the same key twice per panel pair
    fig = Figure(figsize=(15, 9))
    for ax in fig.subplots(1, 2):
        ax.plot([0, 1], [0, 1], color="b", label="I")
        ax.plot([0, 1], [1, 0], color="r", label="Q")
        ax.axvline(.5, color="k", ls="--", label="TOF")
        ax.fill_between([0, 1], -.5, .5, color="grey", alpha=.2, label="ADC Range")
    leg = fig.legend(loc="upper right", ncols=4, bbox_to_anchor=(.5, 1.0))
    columns = [[next(c.get_text() for c in row.get_children() if callable(getattr(c, "get_text", None)))
                for row in column.get_children()] for column in leg._legend_handle_box.get_children()]
    expected = [[col[r] for col in columns if r < len(col)] for r in range(max(map(len, columns)))]
    result = view(fig)
    keys = sorted({t.get("legend", "legend") for t in result["data"] if t.get("showlegend")},
                  key=lambda k: result["layout"][k]["y"], reverse=True)
    actual = [[t["name"] for t in sorted(result["data"], key=lambda t: t.get("legendrank", 1000))
               if t.get("showlegend") and t.get("legend", "legend") == key] for key in keys]
    assert actual == expected
    # the same key twice is one group, and every row keeps the lab's order instead of
    # stacking a group's keys into a block (Plotly's default "grouped" order)
    assert all(result["layout"][k].get("traceorder") == "normal" for k in keys)
    groups = {}
    for t in result["data"]:
        if t.get("showlegend"):
            groups.setdefault(t["name"], set()).add(t.get("legendgroup"))
    # (the ADC Range band is a filled region no key can show or hide: its two keys stay apart)
    assert all(len(groups[name]) == 1 for name in ("I", "Q", "TOF"))
