"""Coupler-only CZ package, build, capability and reconstruction contracts."""
import json
import os
from pathlib import Path
import subprocess
import shutil

import pytest

from quam_state_manager.core import capabilities as cap
from quam_state_manager.core.config_generator import validate_spec
from quam_state_manager.core.regen_spec import reconstruct_spec
from quam_state_manager.generator.probe_capabilities import CATALOG, CATALOG_IDS

ROOT = Path(__file__).resolve().parents[1]
EXT = Path('C:/ProgramData/miniconda3/envs/qsm_official_ext/python.exe')
BASE = Path('C:/ProgramData/miniconda3/envs/qsm_official/python.exe')


def spec():
    return {
        'network': {'host': '127.0.0.1', 'cluster_name': 'C'},
        'instruments': {'controllers': [{'con': 1, 'fems': [
            {'slot': 1, 'fem': 'mw'}, {'slot': 5, 'fem': 'lf'}]}]},
        'qubits': ['q1', 'q2'], 'qubit_pairs': [['q1', 'q2']], 'twpas': [],
        'pair_gate': 'cz_coupler',
        'lines': [
            {'element': q, 'line': line, **({'group': 'f'} if line == 'resonator' else {})}
            for q in ['q1', 'q2'] for line in ['resonator', 'drive']
        ] + [{'element': 'q1-q2', 'line': 'coupler'}],
        'populate': {'qubit': {'q1': {'RF_freq': 5e9}, 'q2': {'RF_freq': 5.2e9}},
                     'pairs': {'q1-q2': {'cz_amplitude': 0.17, 'cz_interaction_duration': 120,
                                         'coupler_interaction_offset': 0.02}}},
    }


def run(python, code, *args):
    if not python.exists():
        pytest.skip('Selected environment absent')
    result = subprocess.run([str(python), '-c', code, *map(str, args)],
                            cwd=ROOT, capture_output=True, text=True,
                            encoding='utf8', env={**os.environ, 'PYTHONUTF8': '1'}, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_capability_and_validation():
    s = spec()
    assert validate_spec(s) == []
    req = cap.required_capabilities(s)
    assert {'pair.coupler_cz', 'pulses.square', 'pulse.cr_flattop', 'qpu.fixed_frequency'} <= req
    assert 'pair.cz_gate' not in req and 'qpu.flux_tunable' not in req
    assert set(cap.REGISTRY) == set(CATALOG_IDS)
    assert CATALOG['pair.coupler_cz'] == {'module': 'qsm_quam_ext', 'attr': 'CouplerCZGate'}
    manifest = {'capabilities': {c: {'available': c != 'pair.coupler_cz'} for c in CATALOG_IDS}}
    report = cap.assess(s, manifest)
    assert not report['buildable']
    assert [r['id'] for r in report['blockers']] == ['pair.coupler_cz']
    assert report['blockers'][0]['fix'] == ('needs the qsm_quam_ext package in this environment '
                                           '(pip install <path to extensions/qsm_quam_ext>)')
    manifest['capabilities']['pair.coupler_cz']['available'] = True
    manifest['capabilities']['pulse.cr_flattop']['available'] = False
    assert [r['id'] for r in cap.assess(s, manifest)['blockers']] == ['pulse.cr_flattop']
    s['lines'].append({'element': 'q1', 'line': 'flux'})
    assert 'cz_coupler requires no qubit z line' in validate_spec(s)
    s['lines'] = [ln for ln in s['lines'] if ln['line'] not in ('flux', 'coupler')]
    assert any('requires a coupler flux line' in e for e in validate_spec(s))


BUILD = """
import json, sys
from pathlib import Path
from quam_state_manager.generator.run_build import run_build
s = json.loads(Path(sys.argv[1]).read_text())
run_build(s, Path(sys.argv[2]))
"""


def test_live_build_roundtrip_and_apply(tmp_path):
    source = tmp_path / 'spec.json'
    source.write_text(json.dumps(spec()), encoding='utf8')
    out = tmp_path / 'built'
    run(EXT, BUILD, source, out)
    state = json.loads((out / 'state.json').read_text())
    wiring = json.loads((out / 'wiring.json').read_text())
    assert state['__class__'].endswith('.FixedFrequencyQuam')
    pair = next(iter(state['qubit_pairs'].values()))
    assert pair['__class__'] == 'qsm_quam_ext.FixedFrequencyTunableCouplerPair'
    assert all('z' not in q for q in state['qubits'].values())
    key = 'cz_coupler_pulse_q1_q2'
    assert pair['coupler']['operations'][key]['id'] == key
    assert pair['coupler']['operations'][key]['__class__'].endswith('.FlatTopGaussianPulse')
    gate = pair['macros']['cz_coupler']
    assert gate['__class__'] == 'qsm_quam_ext.CouplerCZGate'
    assert gate['coupler_flux_pulse'].endswith('/operations/' + key)
    assert 'flux_pulse_qubit' not in gate and 'moving_qubit' not in pair
    assert pair['macros']['cz'] == '#./cz_coupler'
    rebuilt = reconstruct_spec(state, wiring).spec
    assert rebuilt['pair_gate'] == 'cz_coupler'
    seeds = next(iter(rebuilt['populate']['pairs'].values()))
    assert seeds['cz_amplitude'] == 0.17
    assert seeds['cz_interaction_duration'] == 120
    assert seeds['coupler_interaction_offset'] == 0.02
    assert validate_spec(rebuilt) == []
    source.write_text(json.dumps(rebuilt), encoding='utf8')
    run(EXT, BUILD, source, tmp_path / 'rebuilt')
    run(EXT, """
import sys
from pathlib import Path
from quam.core import QuamRoot
from qm.qua import program, declare, fixed
m = QuamRoot.load(Path(sys.argv[1]))
cfg = m.generate_config()
pair = next(iter(m.qubit_pairs.values()))
gate = pair.macros['cz']
assert gate is pair.macros['cz_coupler']
with program() as p:
    phase = declare(fixed)
    gate.apply()
    gate.apply(phase_shift_control=phase + 0.1)
    gate.apply(phase_shift_target=phase + 0.2)
    gate.apply(phase_shift_control=phase, phase_shift_target=phase)
    gate.apply(amplitude_scale_coupler=0.5, duration=16)
    gate.duration = 20
    gate.phase_shift_control = 0.1
    gate.phase_shift_target = -0.1
    gate.apply()
    pair.align()
    pair.wait(8)
m.save(Path(sys.argv[2]) / 'state.json')
n = QuamRoot.load(Path(sys.argv[2]))
assert n.generate_config() == cfg
from unittest.mock import patch
import qsm_quam_ext as ext
names = {ch.name for q in (pair.qubit_control, pair.qubit_target) for ch in q.channels.values()}
names.add(pair.coupler.name)
with patch.object(ext, 'align') as aligned, patch.object(ext, 'wait') as waited:
    pair.align()
    assert set(aligned.call_args.args) == names
    pair.wait(12)
    assert waited.call_args.args[0] == 12
    assert set(waited.call_args.args[1:]) == names
    with patch.object(type(pair.coupler), 'play', autospec=True) as played, \
         patch.object(type(pair.qubit_control.xy), 'frame_rotation_2pi', autospec=True) as rotated:
        aligned.reset_mock()
        gate.apply(phase_shift_control=0.3, phase_shift_target=0.4)
        assert aligned.call_count == 3
        assert played.call_args.args[1] == 'cz_coupler_pulse_q1_q2'
        assert played.call_args.kwargs == {'validate': False, 'amplitude_scale': None, 'duration': 20}
        assert [c.args[1] for c in rotated.call_args_list] == [0.3, 0.4]
        rotated.reset_mock()
        gate.apply()
        assert [c.args[1] for c in rotated.call_args_list] == [0.1, -0.1]
""", out, tmp_path / 'saved')


def test_official_environment_refuses_before_writing(tmp_path):
    source = tmp_path / 'spec.json'
    source.write_text(json.dumps(spec()), encoding='utf8')
    out = tmp_path / 'refused'
    run(BASE, """
import importlib.util, json, sys
from pathlib import Path
from quam_state_manager.generator.run_build import run_build
assert importlib.util.find_spec('qsm_quam_ext') is None
try:
    run_build(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
except RuntimeError as exc:
    assert 'needs the qsm_quam_ext package in this environment' in str(exc)
else:
    raise AssertionError('Build must refuse without the extension')
assert not Path(sys.argv[2]).exists()
""", source, out)
    assert not out.exists()


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_wizard_coupler_selfcheck():
    r = subprocess.run(['node', str(ROOT / 'tests/generate_coupler_selfcheck.cjs')],
                       cwd=ROOT, capture_output=True, text=True, encoding='utf8', timeout=60)
    if r.returncode == 2:
        pytest.skip('jsdom absent')
    assert r.returncode == 0, r.stdout + r.stderr
    assert 'coupler selfcheck passed' in r.stdout


def test_build_route_blocks_missing_extension(monkeypatch, tmp_path):
    from quam_state_manager.core import config_generator
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    manifest = {'ok': True, 'versions': {}, 'capabilities': {
        c: {'available': c != 'pair.coupler_cz'} for c in CATALOG_IDS}}
    monkeypatch.setattr(config_generator, 'get_selected_env', lambda _: 'py')
    monkeypatch.setattr(config_generator, 'probe_capabilities', lambda *a, **k: manifest)
    def forbidden(*a, **k):
        pytest.fail('Build invoked despite the extension blocker')
    monkeypatch.setattr(config_generator, 'run_generator', forbidden)
    out = tmp_path / 'refused'
    r = app.test_client().post('/generate/build', json={
        'spec': spec(), 'output_path': str(out), 'force': True, 'ack_degrades': True})
    assert r.status_code == 400
    assert [b['id'] for b in r.get_json()['capability_blockers']] == ['pair.coupler_cz']
    assert not out.exists()
