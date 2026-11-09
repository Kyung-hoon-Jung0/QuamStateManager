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


def _app(tmp_path, monkeypatch):
    from quam_state_manager.web import port_test_routes as routes
    from quam_state_manager.core import config_generator
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    manager = routes.ptr.Runs(tmp_path / 'runs', resume=False)
    app.config['port_test_runs'] = manager
    monkeypatch.setattr(config_generator, 'known_interpreter', lambda p, _: p == sys.executable)
    return app, manager


def _confirmed(client, body):
    r = client.post('/port-play/confirm', json=dict(body, hardware_ok=True))
    assert r.status_code == 200, r.json
    return dict(body, confirm_token=r.json['token'])


def test_route_uses_wizard_env_and_network(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    captured = {}
    def start(python, script, request, meta):
        captured.update(python=python, script=script, request=request, meta=meta)
        return {'id': 'mock'}
    monkeypatch.setattr(manager, 'start', start)
    body = dict(spec=spec, python=sys.executable, con='con2', slot=3, port=5, offset=.1, max_s=12)
    client = app.test_client()
    # no token, a token without the safety box, a token for another value: all refused
    assert client.post('/port-play/run', json=body).status_code == 400
    assert client.post('/port-play/confirm', json=body).status_code == 400
    assert client.post('/port-play/run', json=_confirmed(client, dict(body, offset=.2)) | {'offset': .1}).status_code == 400
    token_body = _confirmed(client, body)
    response = client.post('/port-play/run', json=token_body)
    assert response.status_code == 200, response.json
    assert client.post('/port-play/run', json=token_body).status_code == 400  # one-time
    assert captured['python'] == sys.executable
    assert captured['request']['target'] == {'host': 'scope-host', 'cluster': 'scope-cluster', 'port': 80}
    assert captured['request']['max_s'] == 12
    assert captured['request']['_play_owner']


def test_play_again_stops_only_our_own_play(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    body = dict(spec=spec, python=sys.executable, con='con2', slot=3, port=5, offset=.1, max_s=12)
    client = app.test_client()
    with client.session_transaction() as s:
        s['port_test_owner'] = 'me'
    monkeypatch.setattr(manager, 'status', lambda *a: {'state': 'running', 'id': 'old'})
    stopped = []
    monkeypatch.setattr(manager, 'stop', lambda *args: stopped.append(args) or True)
    manager.current = {'id': 'old', 'play_owner': 'someone-else'}
    r = client.post('/port-play/run', json=_confirmed(client, body))
    assert r.status_code == 409 and r.json.get('busy') and not r.json.get('stopping')
    assert stopped == []
    manager.current = {'id': 'old', 'play_owner': None}  # a plain port test
    assert client.post('/port-play/run', json=_confirmed(client, body)).json.get('busy')
    assert stopped == []
    manager.current = {'id': 'old', 'play_owner': 'me'}
    r = client.post('/port-play/run', json=_confirmed(client, body))
    assert r.status_code == 409 and r.json['stopping']
    assert stopped == [('old', 'Play again')]


def test_play_stop_needs_own_id(tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as s:
        s['port_test_owner'] = 'me'
    stopped = []
    monkeypatch.setattr(manager, 'stop', lambda *args: stopped.append(args) or True)
    manager.current = {'id': 'r1', 'play_owner': 'other'}
    assert client.post('/port-play/stop', json={}).status_code == 400
    assert client.post('/port-play/stop', json={'id': 'r1'}).status_code == 409
    manager.current = {'id': 'r1', 'play_owner': 'me'}
    assert client.post('/port-play/stop', json={'id': 'r2'}).status_code == 409
    assert stopped == []
    assert client.post('/port-play/stop?left=1&id=r1').json['stopping']
    assert stopped == [('r1', 'the page was left')]


def test_other_ports_keep_saved_offsets(spec):
    spec['port_offsets'] = {'con2/3/6': {'offset': .2, 'output_mode': 'amplified'},
                            'con2/3/5': {'offset': .4, 'output_mode': 'direct'}}
    cfg, _ = pt.build_wizard_dc(spec, ['con2', 3, 5], .1, 'direct')
    outs = cfg['controllers']['con2']['fems']['3']['analog_outputs']
    assert outs['6']['offset'] == .2 and outs['6']['output_mode'] == 'amplified'
    assert outs['5']['offset'] == .1  # the typed value wins on the played port
    assert outs['7']['offset'] == 0.0


@pytest.mark.parametrize('con', [2, '2', 'con2'])
def test_con_spelling(spec, con):
    spec['instruments']['controllers'][1]['con'] = con
    spec['port_offsets'] = {'con2/3/5': {'offset': .3, 'output_mode': 'direct'}}
    assert pt.controllers_from_spec(spec)['con2']['fems']['3']['analog_outputs']['5']['offset'] == .3
    state = {}
    assert apply_port_offsets(state, spec) == ['con2/3/5']
    assert state['ports']['analog_outputs']['con2']['3']['5']['offset'] == .3


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


def test_saas_cluster_declares_one_controller_with_every_fem_slot():
    """QM-SaaS above QOP 3.3 refuses a cluster with more than one controller
    (HTTP 400, found by a real SaaS run of a 3-chassis Port play)."""
    import ast, types
    spec = {"instruments": {"controllers": [
        {"con": c, "fems": [{"slot": 1, "fem": "mw"}, {"slot": 2, "fem": "lf"}, {"slot": 3, "fem": "lf"}]}
        for c in (1, 2, 3)]}}
    cfg, plan = pt.build_wizard_dc(spec, ["con2", 3, 5], 0.0123)
    tree = ast.parse(pt.render_script(cfg, plan))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "saas_qmm")
    calls = {"controller": 0, "mw": [], "lf": []}

    class Ctl:
        def mw_fems(self, *s): calls["mw"].extend(s)
        def lf_fems(self, *s): calls["lf"].extend(s)

    class Cluster:
        def controller(self):
            calls["controller"] += 1
            return Ctl()

    class Inst:
        qmm_connection_params = {}
        def spawn(self): pass

    class Client:
        def latest_version(self): return "v"
        def simulator(self, v, c): return Inst()

    fake = types.ModuleType("qm_saas"); fake.ClusterConfig = Cluster
    ns = {"CONFIG": cfg, "QuantumMachinesManager": lambda **k: None}
    sys.modules["qm_saas"], saved = fake, sys.modules.get("qm_saas")
    try:
        exec(compile(ast.Module([fn], []), "saas", "exec"), ns)
        ns["saas_qmm"](Client())
    finally:
        if saved is None:
            sys.modules.pop("qm_saas", None)
        else:
            sys.modules["qm_saas"] = saved
    assert calls["controller"] == 1
    assert sorted(calls["mw"]) == [1] and sorted(calls["lf"]) == [2, 3]
