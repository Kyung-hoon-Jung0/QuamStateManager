"""Synthetic text panels preserve their artists and source axes decorations."""
from matplotlib.figure import Figure

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

