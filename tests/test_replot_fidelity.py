"""Bare figures pin artist fidelity through extraction and conversion."""
from matplotlib.figure import Figure
import pytest

from quam_state_manager.generator.iplot_extract import extract_figure
from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def test_shared_subplots_keep_twins_in_their_own_panels():
    fig = Figure()
    upper, lower = fig.subplots(2, 1, sharex=True)
    twin = upper.twinx()
    for ax in (upper, lower, twin):
        ax.plot([0, 1], [1, 2])
    source = extract_figure(fig, 'stability')
    assert source['axes'][0]['shares_x_with'] == 2
    assert source['axes'][1]['shares_x_with'] is None
    view = mpljson_to_plotly(source)
    assert len([k for k in view['layout'] if k.startswith('yaxis')]) == 3
    assert view['layout']['yaxis']['domain'] != view['layout']['yaxis2']['domain']
    assert view['layout']['yaxis101']['overlaying'] == 'y'
    # Older extracted relationships must also respect physical subplot bounds.
    source['axes'][0]['shares_x_with'] = 1
    source['axes'][2]['shares_x_with'] = 0
    assert len(mpljson_to_plotly(source)['data']) == 3
    assert 'yaxis2' in mpljson_to_plotly(source)['layout']
