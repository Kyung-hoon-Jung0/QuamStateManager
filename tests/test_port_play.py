"""Wizard-only scope checks; no chip, real QM, or machine-wide state."""
import copy
import os
from pathlib import Path
import subprocess
import sys

import pytest
from quam_state_manager.core import port_test as pt
from quam_state_manager.generator.run_build import apply_port_offsets
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]

@pytest.fixture
def spec():
    return {'network': {'host': 'scope-host', 'cluster_name': 'scope-cluster', 'port': 80},
            'instruments': {'controllers': [
                {'con': c, 'fems': [{'slot': 1, 'fem': 'mw'}, {'slot': 3, 'fem': 'lf'}]}
                for c in range(1, 4)]}, 'qubits': []}


def test_wizard_config(spec):
    baseline = copy.deepcopy(spec)
    cfg, plan = pt.build_wizard_dc(spec, ['con2', 3, 5], .12, 'amplified')
    assert spec == baseline
    expected = pt.controllers_from_spec(spec)
    expected['con2']['fems']['3']['analog_outputs']['5'].update(offset=.12, output_mode='amplified')
    assert cfg['controllers'] == expected
    assert set(cfg['controllers']) == {'con1', 'con2', 'con3'}
    assert cfg['controllers']['con1']['fems']['1'] == {'type': 'MW'}
    assert len(cfg['controllers']['con3']['fems']['3']['analog_outputs']) == 8
    assert cfg['controllers']['con3']['fems']['3']['analog_outputs']['1']['upsampling_mode'] == 'pulse'
    assert cfg['elements'] == {'sm_port_play': {'singleInput': {'port': ('con2', 3, 5)},
        'intermediate_frequency': 0, 'operations': {'dc': 'sm_port_play.pulse'}}}
    assert cfg['waveforms'] == {'sm_port_play.zero': {'type': 'constant', 'sample': 0.0}}
    script = pt.render_script(cfg, plan)
    assert 'with infinite_loop_():' in script
    assert 'play(OPERATION * amp(AMP_SCALE), ELEMENT)' in script
    assert plan['basis'] == 'port' and plan['test'] == 'dc'
    compile(script, '<scope>', 'exec')


@pytest.mark.parametrize('mode,limit', [('direct', .5), ('amplified', 2.5)])
def test_ranges(spec, mode, limit):
    for value in [limit, -limit]:
        pt.build_wizard_dc(spec, ['con2', 3, 5], value, mode)
    for value in [limit + .001, -limit - .001, float('nan'), float('inf')]:
        with pytest.raises(pt.PortTestError, match='requires'):
            pt.build_wizard_dc(spec, ['con2', 3, 5], value, mode)
    with pytest.raises(pt.PortTestError):
        pt.build_wizard_dc(spec, ['con2', 1, 5], 0, mode)


def test_apply_saved(spec):
    spec['port_offsets'] = {'con2/3/5': {'offset': .3, 'output_mode': 'amplified'},
                            'con3/3/8': {'offset': -.2, 'output_mode': 'direct'}}
    state = {'ports': {'analog_outputs': {'con2': {'3': {'5': {'delay': 17}, '6': {'offset': .1}}}}}}
    apply_port_offsets(state, spec)
    target = state['ports']['analog_outputs']
    assert target['con2']['3']['5'] == {'delay': 17, 'offset': .3, 'output_mode': 'amplified'}
    assert target['con2']['3']['6'] == {'offset': .1}
    assert target['con3']['3']['8']['offset'] == -.2
    assert target['con3']['3']['8']['output_mode'] == 'direct'
    assert target['con3']['3']['8']['port_id'] == 8
    source = (ROOT / 'quam_state_manager/generator/run_build.py').read_text(encoding='utf-8')
    assert 'apply_port_offsets(_saved_state, spec)' in source
    spec['port_offsets']['con2/3/5']['offset'] = 3
    with pytest.raises(ValueError):
        apply_port_offsets({}, spec)


def test_route_uses_wizard_env_and_network(spec, tmp_path, monkeypatch):
    from quam_state_manager.web import port_test_routes as routes
    from quam_state_manager.core import config_generator
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    manager = routes.ptr.Runs(tmp_path / 'runs', resume=False)
    app.config['port_test_runs'] = manager
    monkeypatch.setattr(config_generator, 'known_interpreter', lambda p, _: p == sys.executable)
    captured = {}
    def start(python, script, request, meta):
        captured.update(python=python, script=script, request=request, meta=meta)
        return {'id': 'mock'}
    monkeypatch.setattr(manager, 'start', start)
    body = dict(spec=spec, python=sys.executable, con='con2', slot=3, port=5, offset=.1, max_s=12, hardware_ok=True)
    assert app.test_client().post('/port-play/run', json=dict(body, hardware_ok=False)).status_code == 400
    client = app.test_client()
    response = client.post('/port-play/run', json=body)
    assert response.status_code == 200, response.json
    assert captured['python'] == sys.executable
    assert captured['request']['target'] == {'host': 'scope-host', 'cluster': 'scope-cluster', 'port': 80}
    assert captured['request']['max_s'] == 12
    monkeypatch.setattr(manager, 'status', lambda: {'state': 'running', 'id': 'old'})
    stopped = []
    monkeypatch.setattr(manager, 'stop', lambda *args: stopped.append(args))
    assert client.post('/port-play/run', json=body).status_code == 409
    assert stopped == [('old', 'Play again')]


def test_stop_cancels_preparation(tmp_path):
    from quam_state_manager.core.port_test_runs import Runs
    manager = Runs(tmp_path, resume=False)
    generation = manager.port_play_generation
    manager.stop()
    with pytest.raises(RuntimeError, match='stopped'):
        manager.start(sys.executable, '', {'_port_play_generation': generation}, {},
                      popen=lambda *a, **k: pytest.fail('Stop must prevent process creation'))


def test_jsdom():
    env = dict(os.environ, NODE_PATH='D:/work_laptop/statemanager-cfb/node_modules')
    result = subprocess.run(['node', str(ROOT / 'tests/port_play_selfcheck.cjs')],
                            capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
