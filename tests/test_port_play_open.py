"""Open QM / Close QM in the Port play popup: a QM held open with no program."""
import sys
import time
import types

import pytest
from quam_state_manager.core import port_test as pt
from quam_state_manager.web.app import create_app


@pytest.fixture
def spec():
    return {'network': {'host': 'scope-host', 'cluster_name': 'scope-cluster', 'port': 80},
            'instruments': {'controllers': [
                {'con': c, 'fems': [{'slot': 1, 'fem': 'mw'}, {'slot': 3, 'fem': 'lf'}]}
                for c in range(1, 4)]}, 'qubits': []}


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


def _script_namespace(script, monkeypatch):
    qm = types.ModuleType('qm'); qua = types.ModuleType('qm.qua')
    qm.QuantumMachinesManager = qm.SimulationConfig = object
    for name in ('program', 'play', 'measure', 'wait', 'infinite_loop_', 'for_', 'declare',
                 'declare_stream', 'stream_processing', 'amp'):
        setattr(qua, name, lambda *a, **k: None)
    monkeypatch.setitem(sys.modules, 'qm', qm); monkeypatch.setitem(sys.modules, 'qm.qua', qua)
    ns = {'__name__': 'port_script'}
    exec(compile(script, '<port>', 'exec'), ns)
    return ns


class _QM:
    def __init__(self, log): self.id, self.log = 'qm1', log
    def execute(self, prog): self.log.append('execute'); raise AssertionError('no program on Open QM')
    def close(self): self.log.append('close')


class _QMM:
    def __init__(self): self.log = []
    def list_open_qms(self): return []
    def open_qm(self, cfg, close_other_machines=False): self.log.append('open'); return _QM(self.log)


def test_open_script_holds_the_qm_with_no_program(spec, monkeypatch):
    cfg, plan = pt.build_wizard_dc(spec, ['con2', 3, 5], .12, 'direct')
    assert 'RUN_PROGRAM = True' in pt.render_script(cfg, plan)
    ns = _script_namespace(pt.render_script(cfg, plan, run_program=False), monkeypatch)
    assert ns['RUN_PROGRAM'] is False
    assert ns['CONFIG']['controllers']['con2']['fems']['3']['analog_outputs']['5']['offset'] == .12
    # exits only on Stop ...
    qmm, flag, phases = _QMM(), {'stop': False}, []
    def stopper():
        time.sleep(.5); flag['stop'] = True
    import threading
    threading.Thread(target=stopper, daemon=True).start()
    t0 = time.monotonic()
    ns['run_on_hardware'](qmm, should_stop=lambda: flag['stop'], max_s=30, log=lambda *a: None,
                          record=lambda **k: phases.append(k))
    assert .4 < time.monotonic() - t0 < 5
    assert qmm.log == ['open', 'close']
    assert {'phase': 'QM open, no program running'} in phases
    # ... or on the max time
    qmm = _QMM(); t0 = time.monotonic()
    ns['run_on_hardware'](qmm, max_s=.6, log=lambda *a: None)
    assert .5 < time.monotonic() - t0 < 5 and qmm.log == ['open', 'close']


def test_open_token_is_bound_to_its_action(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    captured = []
    monkeypatch.setattr(manager, 'start', lambda py, script, req, meta: captured.append((script, meta)) or {'id': 'r'})
    body = dict(spec=spec, python=sys.executable, con='con2', slot=3, port=5, offset=.1, max_s=12)
    client = app.test_client()
    opened = _confirmed(client, dict(body, action='open'))
    assert client.post('/port-play/run', json=dict(opened, action='play')).status_code == 400
    played = _confirmed(client, body)
    assert client.post('/port-play/run', json=dict(played, action='open')).status_code == 400
    assert captured == []
    r = client.post('/port-play/run', json=_confirmed(client, dict(body, action='open')))
    assert r.status_code == 200, r.json
    script, meta = captured[-1]
    assert 'RUN_PROGRAM = False' in script and meta['plan']['open_only'] is True
    assert client.post('/port-play/run', json=_confirmed(client, body)).status_code == 200
    script, meta = captured[-1]
    assert 'RUN_PROGRAM = True' in script and meta['plan']['open_only'] is False


def test_open_respects_busy_and_only_closes_its_own(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    body = dict(spec=spec, python=sys.executable, con='con2', slot=3, port=5, offset=.1, max_s=12, action='open')
    client = app.test_client()
    with client.session_transaction() as s:
        s['port_test_owner'] = 'me'
    monkeypatch.setattr(manager, 'status', lambda *a: {'state': 'running', 'id': 'old'})
    stopped = []
    monkeypatch.setattr(manager, 'stop', lambda *args: stopped.append(args) or True)
    manager.current = {'id': 'old', 'play_owner': 'someone-else'}
    r = client.post('/port-play/run', json=_confirmed(client, body))
    assert r.status_code == 409 and r.json.get('busy') and stopped == []
    assert client.post('/port-play/stop', json={'id': 'old'}).status_code == 409 and stopped == []
    manager.current = {'id': 'old', 'play_owner': 'me'}
    r = client.post('/port-play/run', json=_confirmed(client, body))
    assert r.status_code == 409 and r.json['stopping'] and stopped == [('old', 'Open QM again')]
