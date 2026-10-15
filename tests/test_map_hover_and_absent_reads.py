"""Reproduced maps: hover names values by their axis titles, a dashed crosshair follows the
pointer, and a run file that was never written costs no retry wait (bug report 2026-10-15)."""
import json

import pytest

from quam_state_manager.core import safe_io
from quam_state_manager.core.interactive_plots import learn_click
from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def _figure():
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure(figsize=(8, 4))
    left, right = fig.subplots(1, 2)
    mesh = left.pcolormesh([[0, 1, 2], [3, 4, 5], [6, 7, 8]])
    left.set_xlabel("Drive power [dBm]")
    left.set_ylabel("Frequency [GHz]")
    fig.colorbar(mesh, ax=left, label="Amplitude [mV]")
    right.plot([0, 1, 2], [1, 4, 9], label="fit")
    right.plot([0, 1, 2], [2, 3, 4])
    right.set_xlabel("Time [ns]")
    right.set_ylabel("Population")
    return mpljson_to_plotly(extract_figure(fig, "hover"))


def test_hover_uses_axis_titles_not_xyz():
    out = _figure()
    heat = next(t for t in out["data"] if t["type"] == "heatmap")
    assert heat["hovertemplate"].startswith("Drive power [dBm]: %{x}<br>Frequency [GHz]: %{y}<br>")
    assert "Amplitude [mV]: %{z}" in heat["hovertemplate"]
    lines = [t for t in out["data"] if t["type"] in ("scatter", "scattergl")]
    named = next(t for t in lines if t.get("name") == "fit")
    unnamed = next(t for t in lines if not t.get("name"))
    assert named["hovertemplate"] == "Time [ns]: %{x}<br>Population: %{y}"          # the name shows beside it
    assert unnamed["hovertemplate"].endswith("<extra></extra>")                     # never "trace 3"


def test_every_visible_axis_draws_a_dashed_crosshair_through_the_pointer():
    layout = _figure()["layout"]
    axes = [v for k, v in layout.items() if k[1:5] == "axis" and k[0] in "xy" and v.get("visible", True)]
    assert len(axes) >= 4
    for axis in axes:
        assert (axis["showspikes"], axis["spikemode"], axis["spikesnap"], axis["spikedash"]) == (
            True, "across", "cursor", "dash")
    assert layout["spikedistance"] == -1 and layout["hovermode"] == "closest"


def test_a_run_file_never_written_costs_no_retry_wait(tmp_path, monkeypatch):
    (tmp_path / "node.json").write_text(json.dumps({"patches": []}))     # no patches -> proposed tier
    slept = []
    monkeypatch.setattr(safe_io.time, "sleep", lambda s: slept.append(s))
    assert learn_click.evidence(tmp_path) == []
    assert slept == []          # state_updates.json is absent: one look, no 0.9 s ladder


def test_a_present_file_still_gets_the_retry_ladder(tmp_path, monkeypatch):
    path = tmp_path / "node.json"
    path.write_text("{")                        # caught mid-write: retried, then given up
    slept = []
    monkeypatch.setattr(safe_io.time, "sleep", lambda s: slept.append(s))
    assert learn_click._read(tmp_path, "node.json") == {}
    assert slept                                # the live-file ladder is kept for files that exist


def test_a_shared_label_drawn_once_names_every_panel_of_its_row():
    pytest.importorskip("matplotlib")
    from matplotlib.figure import Figure
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure(figsize=(9, 3))
    panels = fig.subplots(1, 3, sharey=True)
    for i, ax in enumerate(panels):
        ax.pcolormesh([[0, 1, 2], [3, 4, 5], [6, 7, 8]])
        ax.set_xlabel("RF frequency [GHz]")
    panels[0].set_ylabel("Power (dBm)")                  # the lab labels the row once
    out = mpljson_to_plotly(extract_figure(fig, "shared"))
    heats = [t for t in out["data"] if t["type"] == "heatmap"]
    assert len(heats) == 3
    assert all("Power (dBm): %{y}" in t["hovertemplate"] for t in heats)
