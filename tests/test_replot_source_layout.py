"""Source canvas margins and typography remain source-relative at both widths."""
import os
from pathlib import Path
import subprocess
import pytest

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def test_source_layout_selfcheck():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/replot_source_layout_selfcheck.cjs')],
                            cwd=root, env=dict(os.environ), capture_output=True,
                            text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'all passed' in result.stdout


def test_source_positions_retain_outer_header_and_label_space():
    view = mpljson_to_plotly(dict(size_inches=[10, 8], suptitle='Summary',
        texts=[dict(text='Summary', x=.5, y=.98, size=9, color='#dc143c')],
        axes=[dict(position=[.12, .14, .7, .6], title='Panel', xlabel='Sweep', ylabel='Signal',
                   traces=[dict(type='line', x=[0, 1], y=[1, 2])])]))
    layout = view['layout']
    assert layout['xaxis']['domain'] == pytest.approx([.12, .82])
    assert layout['yaxis']['domain'] == pytest.approx([.14, .74])
    assert layout['margin'] == dict(l=0, r=0, t=0, b=0)
    assert layout['title']['font']['size'] == 9
    assert layout['title']['font'].get('color') == '#dc143c'
    assert layout['title']['y'] <= .98
    assert layout['xaxis']['title']['standoff'] == 0
    assert layout['yaxis']['title']['standoff'] >= 4
    assert view.get('source_size_inches') == [10, 8]


def test_positioned_panel_titles_fit_between_rows():
    view = mpljson_to_plotly(dict(size_inches=[10, 8], axes=[
        dict(position=[.1, .6, .8, .3], title='Upper', annotations=[dict(text='Note')]),
        dict(position=[.1, .1, .8, .3], title='Lower', annotations=[dict(text='Note')])]))
    titles = [note for note in view['layout']['annotations'] if note['text'] in ('Upper', 'Lower')]
    assert len(titles) == 2
    assert all(note['yshift'] == 6 and note['font']['size'] == 10 for note in titles)
