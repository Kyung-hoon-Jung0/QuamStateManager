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


@pytest.mark.parametrize('independent', [False, True])
def test_scalar_scatter_and_independent_colorbars(independent):
    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import LogNorm
    fig = Figure()
    ax = fig.subplots()
    norm = LogNorm(1, 100)
    scatter = ax.scatter([0, 1, 2], [2, 1, 0], c=[1, 10, 100], cmap='plasma', norm=norm)
    mappable = ScalarMappable(norm=norm, cmap='plasma') if independent else scatter
    cb = fig.colorbar(mappable, ax=ax, label='Level [V]')
    view = mpljson_to_plotly(extract_figure(fig, 'operation'))
    scales = [t['marker'] for t in view['data'] if t.get('marker', {}).get('showscale')]
    assert len(scales) == 1
    bar = scales[0]
    assert bar['cmin'] == 0 and bar['cmax'] == 2
    assert bar['colorbar']['title']['text'] == 'Level [V]'
    assert bar['colorbar']['x'] == pytest.approx(cb.ax.get_position().x0)
    assert bar['colorbar']['ticktext'] == ['1', '3.16228', '10', '31.6228', '100']
    assert view['data'][0]['marker']['color'] == [0, 1, 2]


def test_explicit_ticks_and_colored_twin_ticks():
    fig = Figure()
    ax = fig.subplots()
    ax.bar([0, 1], [1, 2])
    ax.set_xticks([0, 1], ['prepared a', 'prepared b'], rotation=30)
    ax.set_yticks([1, 2], ['low', 'high'])
    right = ax.twinx()
    right.plot([0, 1], [10, 20])
    right.set_yticks([10, 20], ['slow', 'fast'])
    right.tick_params(axis='y', colors='orange')
    right.set_ylabel('Delay', color='orange')
    view = mpljson_to_plotly(extract_figure(fig, 'phase'))['layout']
    assert view['xaxis'].get('ticktext') == ['prepared a', 'prepared b']
    assert view['xaxis']['tickvals'] == [0, 1]
    assert view['xaxis']['tickangle'] == -30
    assert view['yaxis']['ticktext'] == ['low', 'high']
    assert view['yaxis101']['ticktext'] == ['slow', 'fast']
    assert view['yaxis101']['tickfont']['color'] == '#ffa500'
    assert view['yaxis101']['title']['font']['color'] == '#ffa500'


def test_reference_marker_and_proxy_legends_deduplicate():
    from matplotlib.lines import Line2D
    fig = Figure()
    for ax in fig.subplots(2, 1):
        ax.plot([0, 1], [1, 2], label='Signal')
        ax.axvline(.5, color='red', label='Center = 0.5')
        ax.axhline(1.5, color='green', label='Idle = 1.5')
        ax.scatter([.4], [1.4], label='Peak = 1.4')
        ax.plot([.2], [1.2], 'x', label='Selected = 1.2')
        handles, labels = ax.get_legend_handles_labels()
        ax.legend(handles + [Line2D([], [], marker='s', color='orange')], labels + ['Flux = 0.2'])
    view = mpljson_to_plotly(extract_figure(fig, 'references'))
    legends = [t['name'] for t in view['data'] if t.get('showlegend')]
    assert sorted(legends) == sorted(['Signal', 'Center = 0.5', 'Idle = 1.5', 'Peak = 1.4',
                                     'Selected = 1.2', 'Flux = 0.2'])
    assert len(view['layout']['shapes']) == 4
    for name in legends:
        assert len({t['legendgroup'] for t in view['data'] if t.get('name') == name}) == 1
