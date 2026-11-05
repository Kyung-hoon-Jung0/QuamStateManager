import copy
import json
import subprocess
from pathlib import Path

import pytest

from quam_state_manager.core import regenerate


@pytest.mark.parametrize('edited', [False, True])
def test_append_preserves_resolved_calibration(tmp_path, monkeypatch, edited):
    pulse = {'length': 120, 'amplitude': .21}
    old = {'qubits': {'q1': {'f_01': 5e9, 'resonator': {'operations': {
        'readout': '#./readout_square', 'readout_square': {'length': 1120, 'amplitude': .02}}}},
        'q2': {'z': {'operations': {}}}},
        'qubit_pairs': {'q2-q1': {'qubit_control': '#/qubits/q2', 'qubit_target': '#/qubits/q1',
                                    'macros': {'cz': {'flux_pulse_qubit': pulse}}}}}
    fresh = copy.deepcopy(old)
    if edited:
        fresh['qubits']['q1']['f_01'] = 5.1e9
    fresh['qubits']['q3'] = {}
    fresh['qubits']['q1']['resonator']['operations']['readout'] = {'length': 2000, 'amplitude': .01}
    fresh['qubits']['q2']['z']['operations']['flux'] = {'length': 100, 'amplitude': .1}
    fresh['qubit_pairs']['q2-q1']['macros']['cz']['flux_pulse_qubit'] = '#/qubits/q2/z/operations/flux'
    fresh['qubit_pairs']['q3-2'] = {'qubit_control': '#/qubits/q3', 'qubit_target': '#/qubits/q2', 'macros': {}}
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'state.json').write_text(json.dumps(old), encoding='utf-8')
    (source / 'wiring.json').write_text('{"wiring": {}}', encoding='utf-8')

    def build(python_path, mode, spec, out_dir, **kwargs):
        out_dir.mkdir()
        (out_dir / 'state.json').write_text(json.dumps(fresh), encoding='utf-8')
        (out_dir / 'wiring.json').write_text('{"wiring": {}}', encoding='utf-8')
        return {'ok': True, 'result': {}}

    monkeypatch.setattr(regenerate.config_generator, 'run_generator', build)
    spec = {'qubits':['q1','q2','q3']}
    if edited:
        spec['populate'] = {'qubit': {'q1': {'RF_freq': 5.1e9}}}
    result = regenerate.run_regenerate('py', source, spec, tmp_path / 'output',
                                       populate_baseline={'qubit': {'q1': {'RF_freq': 5e9}}},
                                       source_probe=lambda *a, **k: {'ok': False})
    assert result['ok']
    report = result['merge']
    print('append calibration_changed_total =', report['calibration_changed_total'])
    assert report['calibration_changed_total'] == 0
    assert report['existing_entities_changed'] == int(edited)


def test_calibration_wording():
    r = subprocess.run(['node', str(Path(__file__).with_name('leftover_minors_calibration_selfcheck.cjs'))],
                       capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 0, r.stdout + r.stderr
