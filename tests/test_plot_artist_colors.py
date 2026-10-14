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


def test_extracted_point_colors_pass_through_the_dark_theme():
    import json
    from matplotlib.figure import Figure
    from quam_state_manager.generator.iplot_extract import extract_figure
    fig = Figure()
    fig.subplots().scatter([0, 1, 2], [2, 1, 0], c=['black', '#10202f', '#1f77b4'])
    view = mpljson_to_plotly(extract_figure(fig, 'cloud'))
    root = Path(__file__).resolve().parents[1]
    script = r"""
const assert = require('assert'), fs = require('fs'), {JSDOM} = require('jsdom');
const dom = new JSDOM('<html data-theme="dark"></html>', {runScripts:'outside-only'});
dom.window.document.documentElement.style.setProperty('--plot-axis-text', '#e6e9ef');
dom.window.eval(fs.readFileSync('quam_state_manager/web/static/plot-theme.js', 'utf8'));
const data = JSON.parse(fs.readFileSync(0, 'utf8'));
const colors = dom.window.PlotTheme.houseData(data)[0].marker.color;
assert.deepStrictEqual(Array.from(colors), ['#e6e9ef', '#e6e9ef', '#1f77b4']);
"""
    result = subprocess.run(['node', '-e', script], input=json.dumps(view['data']),
                            cwd=root, env=dict(os.environ), capture_output=True,
                            text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
