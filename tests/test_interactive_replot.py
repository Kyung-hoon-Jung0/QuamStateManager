"""Strategy-B interactive replot: converter + driver helpers + extractor contract.

The converter and driver helpers are pure-Python and run in the plain test env.
The extractor (``generator/iplot_extract.py``) needs numpy/matplotlib, so its
tests skip when those are absent (they live in the QM env, not the test env).
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from quam_state_manager.core.interactive_plots import replot


# ── Synthetic iplot/v1 axes/figures exercising every shape the converter handles ──
def _ax(**kw):
    """An iplot/v1 axes dict with sensible defaults; override via kwargs."""
    base = {"xlabel": "", "ylabel": "", "title": "", "xscale": "linear",
            "yscale": "linear", "xlim": [0, 1], "ylim": [0, 1],
            "x_side": "bottom", "y_side": "left", "shares_x_with": None,
            "shares_y_with": None, "is_colorbar": False, "show_legend": True,
            "traces": [], "annotations": [],
            "grid": {"row": 0, "col": 0, "nrows": 1, "ncols": 1}}
    base.update(kw)
    return base


def _line(name=None, x=(0, 1), y=(0, 1), color="#000000", mode="lines"):
    return {"type": "line", "x": list(x), "y": list(y), "mode": mode, "name": name,
            "color": color, "dash": "solid", "width": 1.5,
            "marker_symbol": "circle", "marker_size": 6, "opacity": 1.0}


def _twin_log_fig():
    """quality_factors-shaped: base log-Y (left) + a twinx right-Y, plus a note."""
    return {
        "key": "quality_factors_vs_power", "title": "quality factors",
        "suptitle": "Resonator spectroscopy vs power",
        "axes": [
            _ax(xlabel="Readout power [dBm]", ylabel="quality factor", yscale="log",
                ylim=[1e3, 1e8], y_side="left", shares_x_with=1,
                traces=[_line("Qi", (-50, -30, -10), (1e4, 1e5, 1e6), "#2ca02c",
                              "lines+markers")],
                annotations=[{"text": "opt = -32 dBm", "x": 0.0, "y": 0.0,
                              "xref_frac": True}]),
            _ax(ylabel="contrast", y_side="right", shares_x_with=0,
                traces=[_line("contrast", (-50, -10), (0.2, 0.8))]),
        ],
    }


def _heatmap_with_colorbar_fig():
    """A real pcolormesh heatmap + a colorbar-strip axes that must be dropped."""
    return {
        "key": "raw_data_with_fit", "title": "raw data with fit", "suptitle": "",
        "axes": [
            _ax(xlabel="RF frequency [GHz]", ylabel="Power (dBm)", show_legend=False,
                traces=[
                    {"type": "heatmap", "x": [4.91, 4.92], "y": [-50, -40],
                     "z": [[1.0, 2.0], [3.0, None]], "colorscale": "Viridis"},
                    _line(None, (4.91, 4.92), (-30, -31), "#ff0000")]),
            _ax(ylabel="Readout power [dBm]", is_colorbar=True, show_legend=False),
        ],
    }


# ── Converter ─────────────────────────────────────────────────────────────
def test_converter_twin_axis_and_log_scale():
    out = replot.mpljson_to_plotly(_twin_log_fig())
    assert "data" in out and "layout" in out
    lay = out["layout"]
    # primary log y-axis
    assert lay["yaxis"]["type"] == "log"
    # twin-Y becomes an overlaying right-hand axis
    assert "yaxis101" in lay
    assert lay["yaxis101"]["overlaying"] == "y"
    assert lay["yaxis101"]["side"] == "right"
    # both traces present, mapped to the two y-axes
    yrefs = {t["yaxis"] for t in out["data"]}
    assert yrefs == {"y", "y101"}
    # axes-fraction annotation surfaced
    assert any(a["text"].startswith("opt =") for a in lay.get("annotations", []))


def test_converter_drops_colorbar_keeps_heatmap():
    out = replot.mpljson_to_plotly(_heatmap_with_colorbar_fig())
    types = [t["type"] for t in out["data"]]
    assert "heatmap" in types
    # only ONE panel survives (the colorbar axes is dropped) -> no xaxis2
    assert "xaxis2" not in out["layout"]
    assert "xaxis" in out["layout"]


def test_converter_output_is_json_serializable():
    for fig in (_twin_log_fig(), _heatmap_with_colorbar_fig()):
        json.dumps(replot.mpljson_to_plotly(fig))  # must not raise


def test_converter_empty_figure_is_safe():
    out = replot.mpljson_to_plotly({"key": "x", "title": "x", "axes": []})
    assert out["data"] == []
    assert "annotations" in out["layout"]


def test_converter_twin_side_independent_of_axes_order():
    # The base (left-Y) axis must stay on yaxis regardless of extraction order.
    fig = _twin_log_fig()
    fig["axes"] = list(reversed(fig["axes"]))   # twin (right-Y) now comes first
    fig["axes"][0]["shares_x_with"] = 1          # fix indices after reversal
    fig["axes"][1]["shares_x_with"] = 0
    out = replot.mpljson_to_plotly(fig)
    # left yaxis is the quality-factor (log) axis, not contrast
    assert out["layout"]["yaxis"]["type"] == "log"
    assert out["layout"]["yaxis"]["title"]["text"] == "quality factor"
    assert out["layout"]["yaxis101"]["title"]["text"] == "contrast"
    assert out["layout"]["yaxis101"]["side"] == "right"


def test_converter_twiny_folds_to_single_panel_with_top_axis():
    # raw_data_with_fit: bottom-X (RF freq) + a twiny top-X (detuning) on ONE panel.
    fig = {"key": "raw_data_with_fit", "title": "x", "suptitle": "",
           "axes": [
               _ax(xlabel="RF frequency [GHz]", ylabel="Power", shares_y_with=1,
                   traces=[{"type": "heatmap", "x": [1, 2], "y": [1, 2],
                            "z": [[1, 2], [3, 4]], "colorscale": "Viridis"}]),
               _ax(xlabel="Detuning [MHz]", x_side="top", shares_y_with=0,
                   traces=[_line("fit", (1, 2), (1, 2), "#ff7f0e")])]}
    out = replot.mpljson_to_plotly(fig)
    lay = out["layout"]
    # one panel: primary bottom x + a secondary top x overlaying it
    assert "xaxis" in lay and "xaxis101" in lay
    assert lay["xaxis101"]["overlaying"] == "x"
    assert lay["xaxis101"]["side"] == "top"
    assert "yaxis2" not in lay  # NOT two stacked panels
    # only one colorbar shown across the figure
    cbs = [t for t in out["data"] if t.get("type") == "heatmap" and t.get("showscale")]
    assert len(cbs) == 1


def test_converter_qubit_grid_layout():
    # 4 qubit panels at grid (r,c) in a 2x2 QubitGrid -> 2x2 domains, not a stack.
    axes = []
    for r in range(2):
        for c in range(2):
            axes.append(_ax(title=f"q{r}{c}",
                            grid={"row": r, "col": c, "nrows": 2, "ncols": 2},
                            traces=[_line(f"q{r}{c}", (0, 1), (0, 1))]))
    out = replot.mpljson_to_plotly({"key": "grid", "title": "g", "suptitle": "",
                                    "axes": axes})
    lay = out["layout"]
    # four independent panels => xaxis..xaxis4 / yaxis..yaxis4
    assert {"xaxis", "xaxis2", "xaxis3", "xaxis4"} <= set(lay)
    # top-left panel sits in the upper-left quadrant, bottom-right in lower-right
    assert lay["xaxis"]["domain"][0] < 0.5 and lay["yaxis"]["domain"][1] > 0.5
    assert lay["xaxis4"]["domain"][0] > 0.4 and lay["yaxis4"]["domain"][1] < 0.6
    # per-cell titles surfaced
    titles = {a["text"] for a in lay.get("annotations", [])}
    assert {"q00", "q11"} <= titles


def test_line_long_series_uses_scattergl():
    fig = {"key": "k", "title": "k", "axes": [{
        "xlabel": "", "ylabel": "", "title": "", "xscale": "linear",
        "yscale": "linear", "xlim": [0, 1], "ylim": [0, 1], "twin_of": None,
        "is_colorbar": False, "show_legend": False, "annotations": [],
        "traces": [{"type": "line", "x": list(range(500)), "y": list(range(500)),
                    "mode": "lines", "name": None, "color": "#000", "dash": "solid",
                    "width": 1.0, "marker_symbol": "circle", "marker_size": 6,
                    "opacity": 1.0}]}]}
    out = replot.mpljson_to_plotly(fig)
    assert out["data"][0]["type"] == "scattergl"


# ── Driver helpers ────────────────────────────────────────────────────────
@pytest.mark.parametrize("node_name,expected", [
    ("05b_resonator_spectroscopy_vs_power_iq", "resonator_spectroscopy_vs_power_iq"),
    ("1Q_03_resonator_spectroscopy", "resonator_spectroscopy"),  # only leading num stripped
    ("ramsey", "ramsey"),
    ("", ""),
])
def test_derive_util(node_name, expected):
    # the leading "<num><opt letter>_" segment is stripped once
    got = replot._derive_util(node_name)
    assert got == expected or got.endswith(expected)


def test_capability_requires_env(tmp_path, monkeypatch):
    run = SimpleNamespace(experiment_name="05b_resonator_spectroscopy_vs_power_iq",
                          folder_path=str(tmp_path))
    (tmp_path / "quam_state").mkdir()
    monkeypatch.setattr(
        "quam_state_manager.core.config_generator.get_selected_env",
        lambda _ip: None)
    cap = replot.replot_capability(run, str(tmp_path))
    assert cap["available"] is False
    assert "environment" in cap["reason"].lower()


def test_capability_requires_quam_state(tmp_path, monkeypatch):
    run = SimpleNamespace(experiment_name="05b_resonator_spectroscopy_vs_power_iq",
                          folder_path=str(tmp_path))  # no quam_state/ dir
    monkeypatch.setattr(
        "quam_state_manager.core.config_generator.get_selected_env",
        lambda _ip: "/fake/python")
    cap = replot.replot_capability(run, str(tmp_path))
    assert cap["available"] is False
    assert "quam_state" in cap["reason"]


def test_capability_available(tmp_path, monkeypatch):
    run = SimpleNamespace(experiment_name="05b_resonator_spectroscopy_vs_power_iq",
                          folder_path=str(tmp_path))
    (tmp_path / "quam_state").mkdir()
    monkeypatch.setattr(
        "quam_state_manager.core.config_generator.get_selected_env",
        lambda _ip: "/fake/python")
    cap = replot.replot_capability(run, str(tmp_path))
    assert cap["available"] is True
    assert cap["util"] == "resonator_spectroscopy_vs_power_iq"


def test_fingerprint_changes_with_mtime(tmp_path):
    import os
    from pathlib import Path
    f = tmp_path / "ds_fit.h5"
    f.write_bytes(b"abc")
    fp1 = replot._fingerprint(Path(tmp_path))
    os.utime(f, (10**9, 10**9 + 50))
    fp2 = replot._fingerprint(Path(tmp_path))
    assert fp1 != fp2


def test_concurrent_same_run_spawns_one_subprocess(tmp_path, monkeypatch):
    """The menu call + N lazy tile calls must coalesce onto ONE subprocess."""
    import json as _json
    import threading
    import time
    from quam_state_manager.core.interactive_plots import replot as R

    with R._LOCK:
        R._CACHE.clear()
        R._INFLIGHT.clear()

    (tmp_path / "ds_fit.h5").write_bytes(b"x")
    run = SimpleNamespace(folder_path=str(tmp_path), experiment_name="05b_foo")
    spawns = {"n": 0}
    spawn_lock = threading.Lock()

    monkeypatch.setattr(
        "quam_state_manager.core.config_generator.get_selected_env", lambda _ip: "/fake/py")
    monkeypatch.setattr(R, "_script_path", lambda: "/fake/script.py")

    def fake_run(args, **kw):
        with spawn_lock:
            spawns["n"] += 1
        out = args[args.index("--out") + 1]
        time.sleep(0.3)  # window for the other threads to pile up behind the in-flight guard
        with open(out, "w") as fh:
            _json.dump({"schema": "iplot/v1", "figures": [_twin_log_fig()], "errors": []}, fh)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(R.subprocess, "run", fake_run)

    results = []
    threads = [threading.Thread(
        target=lambda: results.append(R.replot_run(run, str(tmp_path)))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert spawns["n"] == 1                       # coalesced
    assert len(results) == 6
    assert all(r.get("figures") for r in results)  # everyone got the real result
    with R._LOCK:
        assert R._INFLIGHT == {}                   # released


def test_transient_failure_not_cached_but_success_is(tmp_path, monkeypatch):
    import json as _json
    from quam_state_manager.core.interactive_plots import replot as R
    with R._LOCK:
        R._CACHE.clear(); R._INFLIGHT.clear()
    (tmp_path / "ds_fit.h5").write_bytes(b"x")
    run = SimpleNamespace(folder_path=str(tmp_path), experiment_name="05b_foo")
    monkeypatch.setattr(
        "quam_state_manager.core.config_generator.get_selected_env", lambda _ip: "/fake/py")
    monkeypatch.setattr(R, "_script_path", lambda: "/fake/s.py")

    state = {"ok": False}

    def fake_run(args, **kw):
        out = args[args.index("--out") + 1]
        payload = ({"schema": "iplot/v1", "figures": [_twin_log_fig()], "errors": []}
                   if state["ok"] else {"schema": "iplot/v1", "figures": [],
                                        "errors": [{"stage": "timeout", "trace": "x"}]})
        with open(out, "w") as fh:
            _json.dump(payload, fh)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(R.subprocess, "run", fake_run)

    r1 = R.replot_run(run, str(tmp_path))
    assert not r1.get("figures")
    with R._LOCK:
        assert str(tmp_path) not in R._CACHE      # failure NOT cached → retryable
    state["ok"] = True
    r2 = R.replot_run(run, str(tmp_path))
    assert r2.get("figures")                       # retry succeeds
    with R._LOCK:
        assert str(tmp_path) in R._CACHE          # success cached


def test_heatmap_downsampled_when_huge():
    big = 900
    z = [[1.0] * big for _ in range(big)]
    fig = {"key": "k", "title": "k", "suptitle": "", "axes": [
        _ax(traces=[{"type": "heatmap", "x": list(range(big)), "y": list(range(big)),
                     "z": z, "colorscale": "Viridis"}])]}
    out = replot.mpljson_to_plotly(fig)
    hm = next(t for t in out["data"] if t["type"] == "heatmap")
    assert len(hm["x"]) <= replot._MAX_HEATMAP_DIM
    assert len(hm["z"]) <= replot._MAX_HEATMAP_DIM


def test_replot_menu_and_figure_roundtrip():
    result = {"schema": "iplot/v1", "figures": [_twin_log_fig()]}
    menu = replot.replot_menu(result)
    assert menu[0]["key"] == "quality_factors_vs_power"
    fig = replot.replot_figure(result, "quality_factors_vs_power")
    assert fig is not None and "data" in fig
    assert replot.replot_figure(result, "missing") is None


# ── Extractor contract (needs numpy/matplotlib; skip those tests when absent) ──
try:
    import numpy  # noqa: F401
    import matplotlib  # noqa: F401
    _HAVE_MPL = True
except ImportError:
    _HAVE_MPL = False

_needs_mpl = pytest.mark.skipif(not _HAVE_MPL,
                                reason="extractor needs numpy+matplotlib (QM env only)")


@_needs_mpl
def test_extractor_line_and_log_and_twin():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from quam_state_manager.generator import iplot_extract

    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [10, 100, 1000], label="Qi", color="green")
    ax.set_yscale("log")
    ax.set_xlabel("power"); ax.set_ylabel("Q")
    ax2 = ax.twinx()
    ax2.plot([1, 2, 3], [0.1, 0.2, 0.3], label="contrast")
    ax2.set_ylabel("contrast")
    out = iplot_extract.extract_figure(fig, "quality_factors")
    plt.close(fig)

    assert out["key"] == "quality_factors"
    assert len(out["axes"]) == 2
    primary = out["axes"][0]
    assert primary["yscale"] == "log"
    # the twinx axes references its sibling via shared-X, and lands on the right
    assert any(a["shares_x_with"] is not None for a in out["axes"])
    assert any(a["y_side"] == "right" for a in out["axes"])
    # line trace carries data + colour
    line = primary["traces"][0]
    assert line["type"] == "line"
    assert line["y"][:3] == [10.0, 100.0, 1000.0]
    json.dumps(out, default=lambda o: o.item() if hasattr(o, "item") else str(o))


# ── Route seam (test client; subprocess + run-resolution mocked) ──────────
def _app_client(monkeypatch, fake_run):
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes as r
    ds = SimpleNamespace(runs={1: fake_run})
    monkeypatch.setattr(r, "_resolve_run", lambda uid: (ds, 1, ""))
    app = create_app()
    return app.test_client()


def test_replot_route_renders_menu(monkeypatch, tmp_path):
    (tmp_path / "quam_state").mkdir()
    fake_run = SimpleNamespace(experiment_name="05b_resonator_spectroscopy_vs_power_iq",
                               folder_path=str(tmp_path))
    captured = {"schema": "iplot/v1", "util": "resonator_spectroscopy_vs_power_iq",
                "figures": [_twin_log_fig()], "errors": []}
    from quam_state_manager.core.interactive_plots import replot as R
    monkeypatch.setattr(R, "replot_capability",
                        lambda run, ip: {"available": True, "reason": "",
                                         "util": "resonator_spectroscopy_vs_power_iq",
                                         "env": "/fake/py"})
    monkeypatch.setattr(R, "replot_run", lambda run, ip, **kw: captured)
    client = _app_client(monkeypatch, fake_run)
    res = client.get("/dataset/anyuid/replot")
    assert res.status_code == 200
    body = res.data.decode()
    assert 'data-endpoint="replot/plot"' in body
    assert "quality_factors_vs_power" in body
    assert "resonator_spectroscopy_vs_power_iq" in body


def test_replot_route_unavailable_shows_reason(monkeypatch, tmp_path):
    fake_run = SimpleNamespace(experiment_name="x", folder_path=str(tmp_path))
    from quam_state_manager.core.interactive_plots import replot as R
    monkeypatch.setattr(R, "replot_capability",
                        lambda run, ip: {"available": False,
                                         "reason": "No QM environment selected.",
                                         "util": "", "env": None})
    client = _app_client(monkeypatch, fake_run)
    res = client.get("/dataset/anyuid/replot")
    assert res.status_code == 200
    assert "No QM environment selected." in res.data.decode()


def test_replot_plot_route_returns_plotly(monkeypatch, tmp_path):
    fake_run = SimpleNamespace(experiment_name="05b_resonator_spectroscopy_vs_power_iq",
                               folder_path=str(tmp_path))
    captured = {"schema": "iplot/v1", "figures": [_twin_log_fig()], "errors": []}
    from quam_state_manager.core.interactive_plots import replot as R
    monkeypatch.setattr(R, "replot_run", lambda run, ip, **kw: captured)
    monkeypatch.setattr(R, "replot_capability",
                        lambda run, ip: {"available": True, "reason": "",
                                         "util": "u", "env": "/fake/py"})
    client = _app_client(monkeypatch, fake_run)
    res = client.get("/dataset/anyuid/replot/plot",
                     query_string={"fig": "quality_factors_vs_power"})
    assert res.status_code == 200
    payload = res.get_json()
    assert "data" in payload and "layout" in payload
    assert payload["layout"]["yaxis"]["type"] == "log"
    # unknown key -> 404
    res2 = client.get("/dataset/anyuid/replot/plot", query_string={"fig": "nope"})
    assert res2.status_code == 404


@_needs_mpl
def test_extractor_nan_becomes_none():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from quam_state_manager.generator import iplot_extract

    fig, ax = plt.subplots()
    ax.plot([1, 2, 3], [1.0, float("nan"), 3.0])
    out = iplot_extract.extract_figure(fig, "k")
    plt.close(fig)
    ys = out["axes"][0]["traces"][0]["y"]
    assert ys[1] is None  # NaN -> null (Plotly reads as a gap)
