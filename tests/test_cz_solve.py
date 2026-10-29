"""Inverse proposal contracts: S2-Q, S2-M, S2-N30, S2-PATH, S2-EXPOSE."""
from copy import deepcopy
import json
import math
from html.parser import HTMLParser
import re
from pathlib import Path
import subprocess

import numpy as np
import pytest

from quam_state_manager.core import cz_flux_math as M, cz_gate_plan as C
from tests.test_cz_plan_page import _chip, _client


def state():
    doc, _ = _chip()
    return doc


def solve(doc=None, **kw):
    return C.solve(doc or state(), "q1-2", "cz_unipolar", **kw)


def test_quad_inverse_and_direction():
    assert M.quad_inverse(-800e6, -8e9, -1) == math.sqrt(800e6 / 8e9)
    assert M.quad_inverse(-800e6, -8e9, -1, -0.1) < 0
    with pytest.raises(ValueError, match="official node 30 would refuse"):
        M.quad_inverse(-800e6, 8e9, -1)
    with pytest.raises(ValueError, match="needs q1 to move up but its sweet spot only moves it down"):
        M.quad_inverse(800e6, -8e9, -1, qubit="q1")
    with pytest.raises(ValueError, match="official node 30 would refuse"):
        M.quad_inverse(800e6, -8e9, 1)


def model():
    return dict(f_max_hz=6e9, ec_hz=200e6, d=.3, phi0_v=.8, phi_park=0,
                amp_min_v=-.4, amp_max_v=.4, f_01_at_fit_hz=6e9, model_r2=1, a_half_v=.4)


def test_model_roundtrip_and_window():
    m = model()
    for anchor in (0, .12, .5):
        for a in np.linspace(-.19, .19, 41):
            target = 5e9 + float(M.arc_frequency(m, anchor + a / m['phi0_v']) - M.arc_frequency(m, anchor))
            actual, _ = M.model_inverse(m, anchor, 5e9, target, a or 1)
            result = 5e9 + float(M.arc_frequency(m, anchor + actual / m['phi0_v']) - M.arc_frequency(m, anchor))
            assert abs(result - target) <= 1e-3
    target = float(M.arc_frequency(m, .25))
    m.update(amp_min_v=-.01, amp_max_v=.01)
    with pytest.raises(ValueError, match="outside the measured flux range"):
        M.model_inverse(m, 0, 6e9, target)


@pytest.mark.parametrize('fc,ft,ac,at,role,shift', [
    (5e9, 4e9, -200e6, -310e6, 'control', -800e6),
    (4e9, 5e9, -310e6, -200e6, 'target', -800e6),
    (5e9, 4.9e9, -200e6, -310e6, 'target', -100e6),
    (4.9e9, 5e9, -310e6, -200e6, 'control', -100e6),
    (5e9, 4.8e9, -200e6, -310e6, 'target', 0),
])
def test_signed_target(fc, ft, ac, at, role, shift):
    result = M.node30_target(fc, ft, ac, at)
    assert result['mover'] == role
    assert result['delta_hz'] == shift


@pytest.mark.parametrize('layout', ['holder', 'embedded', 'chain'])
def test_path_is_played_row_leaf(layout):
    doc = state()
    macro = doc['qubit_pairs']['q1-2']['macros']['cz_unipolar']
    expected = 'qubits.q1.z.operations.cz_flux.amplitude'
    if layout == 'embedded':
        macro['flux_pulse_qubit'] = doc['qubits']['q1']['z']['operations'].pop('cz_flux')
        macro['flux_pulse_qubit']['id'] = 'cz_flux'
        doc['qubits']['q1']['z']['operations']['cz_flux'] = '#/qubit_pairs/q1-2/macros/cz_unipolar/flux_pulse_qubit'
        expected = 'qubit_pairs.q1-2.macros.cz_unipolar.flux_pulse_qubit.amplitude'
    elif layout == 'chain':
        doc['extras'] = {'scale': .12}
        doc['qubits']['q1']['z']['operations']['cz_flux']['amplitude'] = '#/extras/scale'
        expected = 'extras.scale'
    before = deepcopy(doc)
    rows, _ = C.operations(doc)
    row = next(r for r in rows if r['pair'] == 'q1-2' and r['operation'] == 'cz_unipolar')
    result = solve(doc)
    assert result['ok'], result
    assert result['path'] == row['lines'][0]['amplitude_path'] == expected
    assert result['amplitude_v'] == pytest.approx(math.sqrt(.1))
    assert doc == before


def test_dac_bound_uses_unit_peak(monkeypatch):
    from quam_state_manager.core import physical_units
    doc = state()
    doc['ports'] = {'analog_outputs': {'a': {'1': {'1': {'output_mode': 'direct'}}}}}
    doc['qubits']['q1']['z']['opx_output'] = '#/ports/analog_outputs/a/1/1'
    monkeypatch.setattr(physical_units, 'pulse_peak', lambda *a: {'ratio': 2, 'real': True, 'sign': 1, 'reads': [], 'why': None})
    result = solve(doc)
    assert result['reason'] == '0.316228 V exceeds the direct LF output range (±0.5 V)'
    doc['ports']['analog_outputs']['a']['1']['1']['output_mode'] = 'amplified'
    assert solve(doc)['ok']
    doc['qubits']['q1']['freq_vs_flux_01_quad_term'] = -1e9
    result = solve(doc, tier='direct', amp=1.3)
    assert result['reason'] == '1.3 V exceeds the amplified LF output range (±2.5 V)'


@pytest.mark.parametrize('field,value,expected', [
    ('f_01', None, 'f_01 of q1 is not set'),
    ('anharmonicity', None, 'anharmonicity of q1 is not set'),
    ('freq_vs_flux_01_quad_term', None, 'no flux-frequency model: freq_vs_flux_01_quad_term is missing'),
    ('freq_vs_flux_01_quad_term', 0, 'freq_vs_flux_01_quad_term of q1 is 0: run the Ramsey-vs-flux calibration first'),
    ('freq_vs_flux_01_quad_term', 8e9, 'official node 30 would refuse'),
])
def test_exact_scalar_errors(field, value, expected):
    doc = state()
    doc['qubits']['q1'][field] = value
    assert solve(doc)['reason'] == expected


def test_exact_frame_and_direction_errors():
    doc = state()
    q = doc['qubits']['q1']
    q['extras'] = {'lower_sweet_spot_frequency': 5e9}
    assert solve(doc)['reason'] == 'flux direction unknown: sign convention unclear'
    q['extras'] = {'at_sweet_spot': False}
    assert solve(doc)['reason'] == 'quad term expands about a sweet spot; the qubit is not at one (frame: extras.at_sweet_spot is false: no sweet spot to expand about)'
    q['extras'] = {}
    q['z']['flux_point'] = 'min'
    q['freq_vs_flux_01_quad_term'] = 8e9
    assert solve(doc)['reason'] == 'the target needs q1 to move down but its sweet spot only moves it up'
    q['z']['flux_point'] = None
    q['freq_vs_flux_01_quad_term'] = -8e9
    assert 'node-30 convention, no sweet-spot evidence' in solve(doc)['notes']


def test_exact_model_errors_and_polarity():
    m = model()
    with pytest.raises(ValueError) as e:
        M.model_inverse(m, 0, 6e9, 7e9)
    assert str(e.value) == 'target 7 GHz is outside the fitted arc (3.19588..6 GHz)'
    m.update(amp_min_v=-.01, amp_max_v=.01)
    with pytest.raises(ValueError) as e:
        M.model_inverse(m, 0, 6e9, float(M.arc_frequency(m, .25)))
    assert str(e.value) == 'target needs -0.2 V, outside the measured flux range [-0.01, 0.01] V'
    m.update(amp_min_v=-.4, amp_max_v=0)
    a, opposite = M.model_inverse(m, 0, 6e9, 5e9)
    assert a < 0 and opposite


def test_routing_and_unplayable_errors():
    doc = state()
    pair = doc['qubit_pairs']['q1-2']
    pair['moving_qubit'] = 'invalid'
    assert solve(doc)['reason'] == 'qubit_pair.moving_qubit is missing or not control/target'
    pair['moving_qubit'] = 'control'
    pair['qubit_target'] = pair['qubit_control']
    assert solve(doc)['reason'] == 'pair roles resolve to the same qubit'
    pair['qubit_target'] = '#/qubits/q2'
    doc['qubits']['q1']['z']['operations']['cz_flux']['id'] = 'absent'
    assert solve(doc)['reason'] == 'not playable: absent missing on q1'


def test_direct_consistency_and_mover_note():
    doc = state()
    q = doc['qubits']['q1']
    q['f_01'] = 6e9
    q['extras'] = {'lffem_flux_model': model()}
    r = solve(doc)
    assert r['ok'] and r['tier'] == 'model' and r['rel_diff'] is not None
    d = solve(doc, tier='direct', amp=.1)
    assert d['frequency_hz'] == C.Conversion(C.qubit_at(doc, 'qubits.q1')).point(.1)['frequency_hz']
    q['f_01'] = 4.1e9
    q['extras'] = {}
    q['xy'] = {'RF_frequency': 4.11e9}
    r = solve(doc)
    assert 'official node 30 would move q2 and rewrite moving_qubit' in r['notes']
    assert 'xy.RF_frequency differs from f_01 by 10 MHz; the official node would aim -10 MHz elsewhere' in r['notes']


def test_two_flux_keeps_partner_operating_frequency():
    doc = state()
    r = C.solve(doc, 'q1-2', 'cz_two', line='control')
    assert r['ok'], r
    rows, _ = C.operations(doc)
    row = next(r for r in rows if r['pair'] == 'q1-2' and r['operation'] == 'cz_two')
    assert r['target_hz'] == row['lines'][1]['point']['frequency_hz'] + 200e6


def test_off_spot_model_anchor_refuses_only_quad():
    doc = state()
    q = doc['qubits']['q1']
    q['f_01'] = 6e9
    m = model()
    m['phi_park'] = .12
    q['extras'] = {'lffem_flux_model': m}
    assert solve(doc, tier='quad')['reason'] == 'quad term expands about a sweet spot; the qubit is not at one (frame: upper sweet spot: flux moves it down)'
    assert solve(doc, tier='model')['ok']


def test_stage_uses_review_chip_gate_and_one_undo_group(tmp_path):
    from quam_state_manager.web import routes
    client = _client(tmp_path)
    with client:
        proposal = client.get('/cz-plan/solve?pair=q1-2&op=cz_unipolar').json
        store = routes._store()
        before = deepcopy(store.state)
        live = tmp_path / 'chips' / 'live' / 'state.json'
        disk = live.read_bytes()
        batch = {'updates': [{'dot_path': proposal['path'], 'value': proposal['amplitude_v']}],
                 'expect_chip': 'wrong', 'group': 'new'}
        assert client.post('/field/edit-batch', json=batch).status_code == 409
        assert store.state == before and not store.change_log
        batch['expect_chip'] = proposal['chip']
        response = client.post('/field/edit-batch', json=batch)
        assert response.status_code == 200 and response.json['ok'], response.data
        assert C.current(store.merged, proposal['path']) == proposal['amplitude_v']
        assert len({entry.group_id for entry in store.change_log}) == 1
        assert live.read_bytes() == disk
        client.post('/undo')
        assert store.state == before


def test_route_readonly_domain_200_and_exposure(tmp_path):
    client = _client(tmp_path)
    before = client.get('/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar').get_json()
    r = client.get('/cz-plan/solve?pair=q1-2&op=cz_unipolar')
    assert r.status_code == 200 and r.json['ok'] and r.json['chip']
    assert client.get('/cz-plan/solve?pair=q1-2&op=absent').status_code == 200
    assert not client.get('/cz-plan/solve?pair=q1-2&op=absent').json['ok']
    after = client.get('/cz-plan/data?op=qubit_pairs.q1-2.macros.cz_unipolar').get_json()
    assert before['row'] == after['row']
    html = client.get('/cz-plan').text
    points = json.loads(re.search(r'id="cz-operating-points">(.*?)</script>', html, re.S)[1])
    class Cells(HTMLParser):
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'span' and 'data-line' in attrs:
                self.cells[(attrs['data-op'], attrs['data-line'])] = attrs
    parser = Cells()
    parser.cells = {}
    parser.feed(html)
    for p in points:
        cell = parser.cells[(p['op'], p['line'])]
        assert float(cell['data-freq-hz']) == p['frequency_hz'] if p['frequency_hz'] is not None else cell['data-freq-hz'] == 'None'
        assert cell['data-method'] == p['method']
        assert cell['data-path'] == str(p['path'])


def test_client_table_marker_blob_and_stage(tmp_path):
    client = _client(tmp_path)
    html = client.get('/cz-plan', headers={'HX-Request': 'true'}).text
    points = json.loads(re.search(r'id="cz-operating-points">(.*?)</script>', html, re.S)[1])
    ops = sorted({p['op'] for p in points})
    data = {op: client.get('/cz-plan/data', query_string={'op': op}).json for op in ops}
    solves = {op: client.get('/cz-plan/solve', query_string={'pair': data[op]['row']['pair'], 'op': op}).json for op in ops}
    fixture = tmp_path / 'fixture.json'
    fixture.write_text(json.dumps(dict(html=html, data=data, solves=solves,
                                      map=client.get('/cz-plan/map').json)), encoding='utf-8')
    result = subprocess.run(['node', str(Path(__file__).with_name('cz_solve_selfcheck.cjs')), str(fixture)],
                            capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Operating point and Stage checks passed' in result.stdout


def test_route_memo_and_no_state_mutation(tmp_path, monkeypatch):
    from quam_state_manager.core.loader import QuamStore
    from quam_state_manager.core import store_revs
    from quam_state_manager.web import routes
    from quam_state_manager.web.app import create_app
    doc, wiring = _chip()
    store = QuamStore.from_dicts(doc, wiring)
    monkeypatch.setattr(routes, '_store', lambda: store)
    monkeypatch.setattr(routes, '_active_chip_token', lambda: 'fixture-token')
    client = create_app(testing=True, instance_path=str(tmp_path / 'instance')).test_client()
    calls = []
    real_solve = C.solve
    def counted(*a, **kw):
        calls.append(1)
        return real_solve(*a, **kw)
    monkeypatch.setattr(C, 'solve', counted)
    before = deepcopy(store.merged)
    token = store_revs.seq_token(store)
    for _ in range(2):
        response = client.get('/cz-plan/solve?pair=q1-2&op=cz_unipolar')
        assert response.status_code == 200 and response.json['ok']
    assert len(calls) == 1
    assert store.merged == before
    assert store_revs.seq_token(store) == token
    # Replacing the merged document moves the same token used by _cz_plan_ops.
    store.merged = deepcopy(store.merged)
    store.merged['qubits']['q1']['f_01'] = 5.1e9
    response = client.get('/cz-plan/solve?pair=q1-2&op=cz_unipolar')
    assert len(calls) == 2 and response.json['delta_hz'] == -900e6
