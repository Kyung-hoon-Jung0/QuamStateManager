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


def test_single_axes_caption_is_distinct_from_figure_heading():
    fig = Figure()
    ax = fig.subplots()
    ax.plot([0, 1], [1, 2])
    ax.set_title('Pair = a-b')
    fig.suptitle('Calibration')
    view = mpljson_to_plotly(extract_figure(fig, 'response'))
    assert any(n['text'] == 'Pair = a-b' for n in view['layout'].get('annotations', []))
    assert view['layout']['title']['text'] == 'Calibration'


def test_subfigure_verdicts_and_axes_use_root_coordinates():
    fig = Figure(figsize=(10, 6))
    header, body = fig.subfigures(2, 1, height_ratios=[1, 4])
    header.text(.5, .5, 'Diagnostic heading')
    body.text(.01, .98, 'VERDICT: PREP_ERROR', va='top')
    body.text(.01, .88, 'floor = 2.5%; sigma = 0.2%', va='top')
    ax = body.subplots()
    body.subplots_adjust(top=.7)
    ax.plot([0, 1], [1, 2])
    source = extract_figure(fig, 'diagnosis')
    notes = {t['text']: t for t in source['texts']}
    assert 'VERDICT: PREP_ERROR' in notes
    assert notes['Diagnostic heading']['y'] == pytest.approx(.9)
    assert notes['VERDICT: PREP_ERROR']['y'] == pytest.approx(.784)
    assert source['axes'][0]['position'][3] == pytest.approx(ax.get_position().height * .8)
    view = mpljson_to_plotly(source)
    assert len(view['layout']['annotations']) == 3
    assert view['layout']['yaxis']['domain'][1] == pytest.approx(.56)


def test_colorbar_uses_active_box_aspect_without_drawing():
    fig = Figure(figsize=(10, 6))
    ax = fig.subplots()
    points = ax.scatter([0, 1], [1, 2], c=[0, 1])
    cb = fig.colorbar(points, ax=ax, aspect=20)
    source = extract_figure(fig, 'scale')
    bar = source['axes'][0]['traces'][0]['colorbar']['position']
    assert bar[2] == pytest.approx(bar[3] * 6 / 10 / 20)
    assert cb.ax.get_position().width == pytest.approx(bar[2])


@pytest.mark.parametrize('key,title', [('operation', None), ('operation_map', 'operation map')])
def test_axes_caption_does_not_overlap_a_synthetic_key_heading(key, title):
    fig = Figure()
    ax = fig.subplots()
    ax.plot([0, 1], [1, 2])
    ax.set_title('Device\nGain = 13.4')
    view = mpljson_to_plotly(extract_figure(fig, key, title=title))
    assert view['layout']['title']['text'] == ''
    assert view['layout']['annotations'][0]['text'] == 'Device<br>Gain = 13.4'


def test_shared_subfigure_axes_choose_the_overlaid_twin():
    fig = Figure()
    upper, lower = fig.subfigures(2, 1)
    top, bottom = upper.subplots(), lower.subplots()
    bottom.sharex(top)
    twin = top.twinx()
    for ax in (top, bottom, twin):
        ax.plot([0, 1], [1, 2])
    source = extract_figure(fig, 'panels')
    assert source['axes'][0]['shares_x_with'] == 2
    assert source['axes'][1]['shares_x_with'] is None
    view = mpljson_to_plotly(source)
    assert view['layout']['yaxis']['domain'] != view['layout']['yaxis2']['domain']



def test_restored_legend_uses_the_source_footer():
    fig = Figure()
    ax = fig.subplots()
    ax.axvline(.5, label='Center = 0.5')
    view = mpljson_to_plotly(extract_figure(fig, 'reference'))
    assert view['layout']['legend']['y'] == 0
    assert view['layout']['legend'].get('yanchor') == 'bottom'



def test_source_legend_stays_inside_a_single_axes():
    fig = Figure()
    ax = fig.subplots()
    ax.axvline(.5, label='Center = 0.5')
    ax.legend(loc='upper left', fontsize=8)
    view = mpljson_to_plotly(extract_figure(fig, 'reference'))
    legend = view['layout']['legend']
    assert legend['orientation'] == 'v'
    assert legend['x'] == pytest.approx(ax.get_position().x0)
    assert legend['y'] == pytest.approx(ax.get_position().y1)
    assert legend['font']['size'] == 8



def test_fixed_aspect_uses_the_active_box_and_data_scale():
    fig = Figure(figsize=(10, 6))
    ax = fig.subplots()
    ax.plot([0, 4], [0, 1])
    ax.set_aspect('equal')
    view = mpljson_to_plotly(extract_figure(fig, 'circle'))
    box = ax.get_position()
    assert box.width * 10 / (box.height * 6) == pytest.approx(4)
    assert view['layout']['yaxis'].get('scaleanchor') == 'x'
    assert view['layout']['yaxis'].get('scaleratio') == 1

