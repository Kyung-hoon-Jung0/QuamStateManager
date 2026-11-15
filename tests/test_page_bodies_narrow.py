"""Pin shipped wrapping rules on rendered pages; actual geometry needs Chrome."""
import json
import re
import subprocess
from pathlib import Path

from tests.css_pin import run_css_cases
from tests.test_cz_plan_page import _client, _chip
from tests.test_ui_contract import _blocks, _strip_comments, measure_unruled_classes

ROOT = Path(__file__).resolve().parents[1]


def _check(sel, prop, value, all=False):
    return {"sel": sel, "prop": prop, "eq": value, "all": all}


def test_pulses_header_wraps_without_squeezing_search(tmp_path):
    html = _client(tmp_path).get('/pulses').get_data(as_text=True)
    assert '+ New pulse' in html
    # jsdom does not evaluate media queries. Flatten the shipped width queries
    # in cascade order so both the desktop width and responsive stretch are pinned.
    cases = []
    for width in (400, 700, 1000, 1100, 1101, 1440):
        css = []
        for name in ('pico.min.css', 'style.css', 'ui-kit.css'):
            source = (ROOT / 'quam_state_manager/web/static' / name).read_text(encoding='utf-8')
            for sel, body, stack in _blocks(_strip_comments(source)):
                if sel.startswith('@'):
                    continue
                applies = True
                for query in stack:
                    match = re.fullmatch(r'@media\s*\((max|min)-width:\s*(\d+)px\)', query)
                    if not match:
                        applies = False
                        break
                    kind, bound = match.groups()
                    applies &= width <= int(bound) if kind == 'max' else width >= int(bound)
                if applies:
                    css.append(f'{sel} {{{body}}}')
        cases.append({"name": f"pulse header at {width}px",
                      "html": '<style>' + '\n'.join(css) + '</style>' + html, "checks": [
            _check('.ui-pulses-head', 'flex-wrap', 'wrap'),
            _check('.ui-pulses-head .table-filter', 'width', '100%' if width <= 1100 else 'auto'),
            _check('.ui-pulses-head .table-filter', 'flex-wrap', 'wrap' if width <= 1100 else 'nowrap'),
            _check('.ui-pulses-head .ui-search', 'min-width', '12rem' if width <= 1100 else 'auto'),
            _check('.ui-pulses-head .ui-search', 'width', 'auto' if width <= 1100 else '180px'),
            _check('.ui-pulses-head .ui-search', 'flex', '1 1 14rem' if width <= 1100 else ''),
        ]})
    run_css_cases(tmp_path, cases)


def test_pair_name_values_and_pointer_chips_wrap_inside_the_pane(tmp_path):
    state, wiring = _chip()
    name = 'pair_' + 'long_name_' * 20
    target = 'target_' + 'long_token' * 20
    pair = state['qubit_pairs'].pop('q1-2')
    pair['id'] = name
    pair['qubit_target'] = '#/qubits/' + target
    state['qubits'][target] = state['qubits']['q2']
    state['qubit_pairs'][name] = pair
    folder = tmp_path / 'chip'
    folder.mkdir()
    (folder / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    (folder / 'wiring.json').write_text(json.dumps(wiring), encoding='utf-8')
    html = _client(tmp_path, folder).get('/pair/' + name,
        headers={'HX-Request': 'true'}).get_data(as_text=True)
    assert f'<strong>{name}</strong>' in html
    assert '#/qubits/' + target in html and 'CZ gate plan' in html
    run_css_cases(tmp_path, [{"name": "pair inspector", "html": html, "checks": [
        _check('.inspector-header-pair', 'flex-wrap', 'wrap'),
        _check('.ui-pair-title', 'flex-wrap', 'wrap'),
        _check('.ui-pair-title strong', 'white-space', 'normal'),
        _check('.ui-pair-title strong', 'overflow', 'visible'),
        _check('.ui-pair-title strong', 'overflow-wrap', 'anywhere'),
        _check('.inspector-actions', 'flex-wrap', 'wrap'),
        _check('.inspector-czplan-link', 'white-space', 'nowrap'),
        _check('.inspector-czplan-link', 'flex', '0 0 auto'),
        _check('table.ui-pair-props', 'table-layout', 'fixed', True),
        _check('.ui-pair-props .col-prop', 'min-width', '0px', True),
        _check('.ui-pair-props .col-prop', 'width', 'min(15rem, 40%)', True),
        _check('.ui-pair-props .col-val', 'width', 'auto', True),
        _check('.ui-pair-props .col-val', 'overflow-wrap', 'anywhere', True),
        _check('.ui-pair-props code', 'white-space', 'normal', True),
        _check('.ui-pair-props .pointer-badge', 'white-space', 'normal', True),
        _check('.ui-pair-props .pointer-badge', 'overflow-wrap', 'anywhere', True),
    ]}])


def test_wide_layout_contract_and_no_clipping_workaround():
    css = (ROOT / 'quam_state_manager/web/static/ui-kit.css').read_text(encoding='utf-8')
    css += (ROOT / 'quam_state_manager/web/templates/workbench.html').read_text(encoding='utf-8')
    css = re.sub(r'/\*.*?\*/', '', css, flags=re.S)
    # Intrinsic wrapping preserves single rows when the content fits; the pair
    # key column caps at its original 15rem width rather than scaling on desktop.
    for sel in ('.ui-pulses-head.ui-head--data', '.inspector-header-pair',
                '.ui-workbench #wb-bar', '.ui-workbench #wb-link'):
        body = re.search(re.escape(sel) + r'\s*\{([^}]+)\}', css).group(1)
        assert 'flex-wrap: wrap' in body
        assert not re.search(r'overflow[^:]*:\s*(hidden|clip)', body, re.I)
    assert 'width: min(15rem, 40%)' in css
    assert '.ui-workbench[data-narrow] #panes' in css


def test_inspector_unruled_class_baseline_has_no_slack():
    baseline = json.loads((ROOT / 'tests/ui_contract_baseline.json').read_text(encoding='utf-8'))
    key = 'templates/_inspector_header.html'
    assert baseline['unruled_classes'][key] == measure_unruled_classes()[key] == 1


def test_workbench_keeps_its_standalone_style_scope(tmp_path):
    html = _client(tmp_path).get('/workbench').get_data(as_text=True)
    assert not re.search(r'<link\b[^>]*\brel=[\"\']stylesheet[\"\']', html, re.I)
    assert '@import' not in _strip_comments(html)
    # The added layout rules cannot introduce global controls, focus or palette.
    style = re.search(r'<style>(.*?)</style>', html, re.S).group(1)
    assert ':focus-visible' not in _strip_comments(style)
    assert ':root' not in _strip_comments(style)
    for sel, body, stack in _blocks(_strip_comments(style)):
        if '.ui-workbench' in sel:
            assert not stack
            assert all(part.strip().startswith('.ui-workbench') for part in sel.split(','))
            assert not re.search(r'color|background|outline|shadow|font|border', body, re.I)


def test_workbench_selfcheck_names_the_missing_argument():
    result = subprocess.run(['node', str(ROOT / 'tests/page_bodies_narrow_selfcheck.cjs')],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode != 0
    assert 'Usage: node tests/page_bodies_narrow_selfcheck.cjs <rendered-page.html>' in result.stderr
    assert 'missing rendered-page.html argument' in result.stderr
    assert 'TypeError' not in result.stderr


def test_workbench_narrow_script_and_styles(tmp_path):
    page = tmp_path / 'workbench.html'
    page.write_text(_client(tmp_path).get('/workbench').get_data(as_text=True), encoding='utf-8')
    result = subprocess.run(['node', str(ROOT / 'tests/page_bodies_narrow_selfcheck.cjs'), str(page)],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'assertions)' in result.stdout
