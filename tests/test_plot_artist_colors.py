"""Synthetic house-theme artists and mounting preserve source palettes."""
import os
from pathlib import Path
import subprocess

from quam_state_manager.core.interactive_plots.replot import mpljson_to_plotly


def test_house_artist_colors():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', str(root / 'tests/plot_artist_colors_selfcheck.cjs')],
                            cwd=root, env=dict(os.environ), capture_output=True,
                            text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'all passed' in result.stdout


def test_converter_keeps_black_text_for_light_mode():
    figure = dict(axes=[dict(annotations=[dict(text='Summary', color='#000000')])])
    view = mpljson_to_plotly(figure)
    assert view['layout']['annotations'][0]['font'].get('color') == '#000000'
