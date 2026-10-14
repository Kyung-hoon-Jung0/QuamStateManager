"""Synthetic text panels preserve their artists and source axes decorations."""
from matplotlib.figure import Figure
import pytest

from quam_state_manager.generator.iplot_extract import extract_figure
from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def test_extractor_records_hidden_and_drawn_axes_without_gui():
    fig = Figure()
    hidden = fig.add_subplot(211)
    hidden.set_axis_off()
    shown = fig.add_subplot(212)
    shown.spines['top'].set_visible(False)
    axes = extract_figure(fig, 'summary')['axes']
    assert axes[0].get('axison') is False
    assert axes[1].get('axison') is True
    assert axes[1]['spines_visible'] == dict(left=True, right=True, bottom=True, top=False)
    assert axes[1]['ticks_visible'] == dict(x=True, y=True)


def test_text_only_pairs_have_inert_traces_and_preserve_visibility():
    fig = Figure()
    for index in range(2):
        ax = fig.add_subplot(2, 1, index + 1)
        ax.text(.1, .8, 'Summary', transform=ax.transAxes)
        if index == 0:
            ax.set_axis_off()
    view = mpljson_to_plotly(extract_figure(fig, 'summary'))
    assert len(view['data']) == 2
    for trace, suffix in zip(view['data'], ['', '2']):
        assert trace['xaxis'] == 'x' + suffix and trace['yaxis'] == 'y' + suffix
        assert trace['x'] == [None] and trace['y'] == [None]
        assert trace['opacity'] == 0 and trace['showlegend'] is False
        assert trace['hoverinfo'] == 'skip'
    assert len(view['layout']['annotations']) == 2
    assert view['layout']['xaxis'].get('visible') is False
    assert view['layout']['yaxis'].get('visible') is False
    assert view['layout']['xaxis2'].get('visible') is True
    assert view['layout']['yaxis2'].get('visible') is True


def test_hidden_decorations_and_old_cache_visibility():
    ax = dict(annotations=[dict(text='Summary')], axison=True,
              ticks_visible=dict(x=False, y=False), spines_visible={})
    view = mpljson_to_plotly(dict(axes=[ax]))
    assert view['layout']['xaxis'].get('visible') is False
    assert view['layout']['yaxis'].get('visible') is False
    ax.pop('axison')
    assert 'visible' not in mpljson_to_plotly(dict(axes=[ax]))['layout']['xaxis']


def test_extractor_records_real_positions_and_colorbar_width():
    fig = Figure(figsize=(8, 6))
    ax = fig.add_axes([.12, .15, .6, .7])
    cbax = fig.add_axes([.8, .15, .03, .7])
    image = ax.imshow([[0, 1], [1, 0]], aspect='auto')
    fig.colorbar(image, cax=cbax)
    axes = extract_figure(fig, 'map')['axes']
    assert axes[0].get('position') == pytest.approx([.12, .15, .6, .7])
    assert axes[1].get('position') == pytest.approx([.8, .15, .03, .7])
    assert axes[0]['traces'][0]['colorbar'].get('position') == pytest.approx([.8, .15, .03, .7])
    assert axes[0]['traces'][0]['colorbar'].get('orientation') == 'vertical'


def test_source_positions_preserve_unequal_rows_gaps_and_colorbar_extent():
    import pytest
    axes = [dict(position=[.1, .8, .6, .1], axison=False,
                 annotations=[dict(text='Summary')]),
            dict(position=[.1, .1, .6, .6], xlabel='Sweep', ylabel='Signal',
                 traces=[dict(type='heatmap', x=[0, 1], y=[0, 1], z=[[0, 1], [1, 0]],
                              colorbar=dict(visible=True, position=[.8, .1, .03, .6]))]),
            dict(is_colorbar=True, position=[.8, .1, .03, .6])]
    view = mpljson_to_plotly(dict(axes=axes, suptitle='Scan'))
    layout = view['layout']
    assert layout['yaxis']['domain'] == pytest.approx([.875, 1])
    assert layout['yaxis2']['domain'] == pytest.approx([0, .75])
    assert layout['xaxis2']['domain'] == pytest.approx([0, .6 / .73])
    assert layout['xaxis2']['title']['text'] == 'Sweep'
    cb = view['data'][1]['colorbar']
    assert cb['x'] == pytest.approx(.7 / .73)
    assert cb['len'] == pytest.approx(.75)
    assert cb['thickness'] == pytest.approx(.03 / .73)
    assert layout['margin']['t'] >= 44


def test_missing_or_invalid_position_keeps_cached_grid_rule():
    for position in (None, [0, 0, 0, 1], [0, 0, float('nan'), 1]):
        axes = [dict(position=position, annotations=[dict(text='Summary')]),
                dict(position=[.1, .1, .8, .6], traces=[dict(type='line', x=[0, 1], y=[0, 1])])]
        layout = mpljson_to_plotly(dict(axes=axes))['layout']
        assert layout['xaxis']['domain'] == [.06, .94]
        assert layout['yaxis']['domain'] == [.55, .95]


def test_positioned_spanning_panel_twins_and_horizontal_colorbar():
    axes = [dict(position=[.1, .55, .8, .35], title='Wide panel',
                 traces=[dict(type='line', x=[0, 1], y=[0, 1], name='Signal')]),
            dict(position=[.1, .55, .8, .35], shares_x_with=0, y_side='right', ylabel='Ratio',
                 traces=[dict(type='line', x=[0, 1], y=[1, 2])]),
            dict(position=[.1, .15, .3, .25], xlabel='Sweep',
                 traces=[dict(type='heatmap', x=[0, 1], y=[0, 1], z=[[0, 1], [1, 0]],
                              colorbar=dict(visible=True, position=[.1, .05, .3, .02],
                                            orientation='horizontal'))]),
            dict(is_colorbar=True, position=[.1, .05, .3, .02])]
    view = mpljson_to_plotly(dict(axes=axes, suptitle='Scan'))
    layout = view['layout']
    assert layout['xaxis']['domain'] == pytest.approx([0, 1])
    assert layout['xaxis2']['domain'] == pytest.approx([0, .375])
    assert layout['yaxis101']['domain'] == layout['yaxis']['domain']
    assert layout['yaxis101']['title']['text'] == 'Ratio'
    assert layout['yaxis101']['overlaying'] == 'y'
    assert layout['showlegend'] is True and view['data'][0]['name'] == 'Signal'
    assert layout['margin']['b'] >= 50
    cb = view['data'][2]['colorbar']
    assert cb['orientation'] == 'h'
    assert cb['x'] == pytest.approx(.1875) and cb['y'] == pytest.approx(0)
    assert cb['len'] == pytest.approx(.375) and cb['thickness'] == pytest.approx(.02 / .85)

