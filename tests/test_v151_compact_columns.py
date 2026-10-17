"""The two interactive figure menus use a compact column-count toolbar."""
from pathlib import Path
import re

import pytest
from jinja2 import Environment, FileSystemLoader

WEB = Path(__file__).resolve().parents[1] / 'quam_state_manager/web'


@pytest.mark.parametrize('template', ['_dataset_interactive.html', '_dataset_replot.html'])
def test_columns_avoid_the_full_width_pico_group(template):
    env = Environment(loader=FileSystemLoader(WEB / 'templates'))
    html = env.get_template(template).render(available=True, figures=[dict(key='a', title='Plot', available=True)],
                                              outcome=dict(state='ok'), errors=[], uid='1', run_id=1)
    control = re.search(r'<div class="ds-interactive-colbtns"([^>]*)>(.*?)</div>', html, re.S)
    assert control is not None
    assert 'role="toolbar"' in control[1]
    assert re.findall(r'data-cols="(\d)"', control[2]) == ['1', '2', '3']
    css = (WEB / 'static/style.css').read_text(encoding='utf8')
    group = re.search(r'^\.ds-interactive-colbtns\s*\{([^}]*)\}', css, re.M)[1]
    button = css.split('.ds-col-btn {', 1)[1].split('}', 1)[0]
    assert 'width: auto' in group and 'flex: 0 0 auto' in group
    assert 'width: auto' in button and 'flex: 0 0 auto' in button
