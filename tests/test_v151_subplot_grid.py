"""v1.5.1 (D3 class G): every panel of a reproduced lab figure draws the house grid.

Drives tests/subplot_grid_theme_selfcheck.cjs (the real plot-theme.js under jsdom) with a
converted multi-panel lab figure: panels 2..N used to keep Plotly's near-white default grid.
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


def test_every_panel_draws_the_house_grid(tmp_path):
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure
    from quam_state_manager.generator.iplot_extract import extract_figure
    env = _node_env()
    if shutil.which("node") is None or env is None:
        pytest.skip("node + jsdom not installed")
    fig = Figure(figsize=(12, 3))
    for i, ax in enumerate(fig.subplots(1, 4)):
        ax.plot([0, 1, 2], [i, i + 1, i])
        ax.grid(True)
        ax.set_title(f"q{i + 1}")
    view = mpljson_to_plotly(extract_figure(fig, "panels"))
    assert {"xaxis4", "yaxis4"} <= set(view["layout"])
    out = tmp_path / "converted.json"
    out.write_text(json.dumps(view), encoding="utf-8")
    r = subprocess.run(["node", str(ROOT / "tests" / "subplot_grid_theme_selfcheck.cjs"), str(out)],
                       capture_output=True, text=True, env=env, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "assertions passed" in r.stdout
    count = int(r.stdout.split("subplot_grid_theme_selfcheck: ")[1].split()[0])
    assert count >= 30, r.stdout   # 24 fixed checks + one per converted axis
