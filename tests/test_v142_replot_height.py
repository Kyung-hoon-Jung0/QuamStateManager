"""A reproduced lab figure keeps its own aspect at the pane's real width."""
import subprocess
from pathlib import Path

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly

_AXES = [{"xlabel": "x", "ylabel": "y", "traces": [{"type": "scatter", "x": [0, 1], "y": [0, 1]}]}]


def test_server_sends_the_source_aspect_with_its_nominal_height():
    view = mpljson_to_plotly({"axes": _AXES, "size_inches": [15, 9]})
    assert view["source_aspect"] == 0.6
    assert view["layout"]["height"] == 720  # 1200 px nominal width, the fallback


def test_no_or_bad_size_sends_no_aspect():
    assert mpljson_to_plotly({"axes": _AXES})["source_aspect"] is None
    assert mpljson_to_plotly({"axes": _AXES, "size_inches": [0, 9]})["source_aspect"] is None
    assert mpljson_to_plotly({"axes": _AXES, "size_inches": [15, float("nan")]})["source_aspect"] is None


def test_replot_height_selfcheck():
    proc = subprocess.run(["node", str(Path(__file__).with_name("replot_height_selfcheck.cjs"))],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "11 assertions passed" in proc.stdout
