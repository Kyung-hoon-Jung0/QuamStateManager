"""v1.5.1 (D3 class K): dark-theme contrast of reproduced lab artists.

Text on a heatmap cell keeps the lab colour (a confusion matrix's black diagonal text was
mapped to the light foreground on yellow cells); IQ clouds keep the lab's dot size and
per-dot alpha. Drives tests/dark_artist_contrast_selfcheck.cjs (real plot-theme.js, app.js,
map-tools.js under jsdom) with a converted lab confusion matrix.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly

ROOT = Path(__file__).resolve().parents[1]


def _node_env():
    env = dict(os.environ)
    for cand in (ROOT / "node_modules", ROOT.parent / "statemanager" / "node_modules"):
        if (cand / "jsdom").is_dir():
            env["NODE_PATH"] = str(cand)
            return env
    return None


def _extract(build):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure(figsize=(6, 4))
    build(fig.subplots())
    return extract_figure(fig, "fig")


def _traces(fig_json):
    return [t for a in fig_json["axes"] for t in a.get("traces", [])]


def test_a_point_marker_is_half_the_size_of_a_circle():
    def build(ax):
        ax.plot([0, 1], [0, 1], ".", markersize=8, alpha=0.2, label="dots")
        ax.plot([0, 1], [1, 0], "o", markersize=8, label="circles")
        ax.scatter([0, 1], [0.5, 0.5], s=16, marker=".")
        ax.scatter([0, 1], [0.2, 0.2], s=16)
    lines = [t for t in _traces(_extract(build)) if t["type"] == "line"]
    scatters = [t for t in _traces(_extract(build)) if t["type"] == "scatter"]
    assert [t["marker_size"] for t in lines] == [4.0, 8.0]
    assert [t["marker_size"] for t in scatters] == [2.0, 4.0]


def test_a_translucent_cloud_fades_each_dot_not_the_layer():
    def build(ax):
        ax.plot([0, 1, 2], [0, 1, 0], ".", alpha=0.2)
        ax.plot([0, 1, 2], [1, 0, 1], "o-", alpha=0.5)
    data = mpljson_to_plotly(_extract(build))["data"]
    cloud, line = data[0], data[1]
    assert cloud["marker"].get("opacity") == pytest.approx(0.2) and "opacity" not in cloud
    assert line["opacity"] == pytest.approx(0.5) and "opacity" not in line["marker"]


def test_dark_artist_contrast_selfcheck(tmp_path):
    env = _node_env()
    if shutil.which("node") is None or env is None:
        pytest.skip("node + jsdom not installed")

    def build(ax):
        ax.imshow([[0.92, 0.08], [0.17, 0.83]], vmin=0, vmax=1)
        for (i, j), text, color in (((0, 0), "92.0%", "k"), ((1, 0), "8.0%", "w"),
                                    ((0, 1), "17.0%", "w"), ((1, 1), "83.0%", "k")):
            ax.text(i, j, text, ha="center", va="center", color=color)
    view = mpljson_to_plotly(_extract(build))
    out = tmp_path / "confusion.json"
    out.write_text(json.dumps(view), encoding="utf-8")
    r = subprocess.run(["node", str(ROOT / "tests" / "dark_artist_contrast_selfcheck.cjs"), str(out)],
                       capture_output=True, text=True, env=env, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "39 assertions passed" in r.stdout, r.stdout
