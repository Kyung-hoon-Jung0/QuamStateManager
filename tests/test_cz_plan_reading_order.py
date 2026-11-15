"""CZ plan reading order: summary strip, state pills, zones and amplitude tiles, driven by the shipped client."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_cz_plan_page import _client
from tests.test_cz_plan_structure import _amplitude_state


def _fixture(tmp_path):
    state, wiring = _amplitude_state('refused')
    macros = next(iter(state['qubit_pairs'].values()))['macros']
    played = macros['cz_two']
    played['flux_pulse_high'] = played.pop('flux_pulse_qubit')
    played['flux_pulse_low'] = played.pop('flux_pulse_target')
    played['duration'] = 12
    macros['outside'] = {
        '__class__': 'x.CZGate', 'flux_pulse_qubit': {
            '__class__': 'quam.components.pulses.SquarePulse', 'length': 40, 'amplitude': .8}}
    folder = tmp_path / 'state'
    folder.mkdir()
    (folder / 'state.json').write_text(json.dumps(state), encoding='utf-8')
    (folder / 'wiring.json').write_text(json.dumps(wiring), encoding='utf-8')
    client = _client(tmp_path, folder)
    html = client.get('/cz-plan', headers={'HX-Request': 'true'}).get_data(as_text=True)
    first = re.search(r'<tr[^>]*class="[^"]*czp-row[^"]*"[^>]*data-plan-state="in-range"[^>]*>', html)[0]
    op = re.search(r'data-op="([^"]+)"', first)[1]
    pair = re.search(r'data-pair="([^"]+)"', first)[1]
    data = client.get('/cz-plan/data?op=' + op).get_json()
    solve = client.get(f'/cz-plan/solve?pair={pair}&op={op}&line=control').get_json()
    history = client.get('/cz-plan/history').get_json()
    return html, dict(html=html, data=data, solve=solve, history=history)


def test_states_are_counted_from_the_rendered_rows(tmp_path):
    html, _ = _fixture(tmp_path)
    states = re.findall(r'data-plan-state="([^"]+)"', html)
    assert 0 < states.count('in-range') < len(states)
    assert states.count('not-playable') > 0
    assert states.count('outside') == 1
    assert states.count('unknown') > 0
    assert 'data-plan-summary' in html and html.count('data-plan-tile=') == 4
    assert html.count('class="ui-zone"') == 2


def test_reading_order_client(tmp_path):
    _, fx = _fixture(tmp_path)
    assert fx['solve'].get('ok'), fx['solve']
    path = tmp_path / 'ro.json'
    path.write_text(json.dumps(fx), encoding='utf8')
    root = Path(__file__).resolve().parents[1]
    r = subprocess.run(['node', 'tests/cz_plan_reading_order_selfcheck.cjs', str(path)], cwd=root,
                       env=os.environ.copy(), capture_output=True, text=True, encoding='utf8', timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    assert re.search(r'\((\d+) assertions\)', r.stdout), r.stdout


@pytest.mark.parametrize('qubits', [{}, {'q1': {'id': 'q1', 'z': {'operations': {}}}}])
def test_empty_plan_has_a_neutral_sentence(tmp_path, qubits):
    folder = tmp_path / 'empty'
    folder.mkdir()
    (folder / 'state.json').write_text(json.dumps({'qubits': qubits, 'qubit_pairs': {}}), encoding='utf8')
    (folder / 'wiring.json').write_text('{}', encoding='utf8')
    html = _client(tmp_path, folder).get('/cz-plan', headers={'HX-Request': 'true'}).get_data(as_text=True)
    assert 'czp-row' not in html
    assert re.search(r'class="ui-verdict ui-verdict--neutral ui-verdict--strong" role="status" data-plan-verdict', html)
    assert 'data-plan-key>No operation is shown.</strong>' in html
    assert '0 of 0 operations in range' not in html


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), -float('inf'), '', [],
                                   [5000000], {}, 'abc', '5000000', True, 'missing'],
                         ids=['null', 'nan', 'positive-infinity', 'negative-infinity', 'empty-string',
                              'empty-list', 'list', 'object', 'text', 'numeric-string', 'boolean', 'undefined'])
def test_template_rejects_invalid_detuning(tmp_path, monkeypatch, value):
    from quam_state_manager.web import routes
    table = routes._cz_plan_table

    def invalid(rows):
        result = table(rows)
        for row in result:
            row['nearest'] = dict(row.get('nearest') or {}, name='nearest', words='nearest')
            row['nearest']['distance_hz'] = value
            if value == 'missing':
                row['nearest'].pop('distance_hz', None)
                row.pop('detuning_hz', None)
            else:
                row['detuning_hz'] = value
        return result

    monkeypatch.setattr(routes, '_cz_plan_table', invalid)
    html, _ = _fixture(tmp_path)
    rendered = re.findall(r'<tr[^>]*class="[^"]*czp-row[^>]*>.*?</tr>', html, re.S)
    assert rendered
    for row in rendered:
        assert 'data-s-delta=""' in row
        assert 'data-s-near=""' in row
        cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)
        assert cells[5] == chr(8212)
        assert 'data-near-words' not in cells[6]


def test_reading_order_test_files_are_ascii():
    for name in ('test_cz_plan_reading_order.py', 'cz_plan_reading_order_selfcheck.cjs'):
        assert Path(__file__).with_name(name).read_bytes().isascii(), name


if __name__ == '__main__':
    import contextlib
    import tempfile

    if len(sys.argv) > 1:
        with contextlib.redirect_stdout(sys.stderr):
            _, fixture = _fixture(Path(sys.argv[1]))
        print(json.dumps(fixture))
    else:
        # The child exits and releases SQLite handles before the parent removes its folder.
        with tempfile.TemporaryDirectory(prefix='cz_plan_fixture_') as folder:
            result = subprocess.run([sys.executable, __file__, folder], check=True,
                                    capture_output=True, text=True, encoding='utf8', timeout=45)
            print(result.stdout)
