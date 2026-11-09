"""Loopback offset calibration inside the Port play popup (BRIEF3 E-1..E-4).

No QM, no hardware: the run manager's start is mocked or fed a fake process.
"""
import json
import sys
from pathlib import Path

import pytest

from quam_state_manager.core import port_test as pt
from quam_state_manager.core import port_test_runs as ptr
from quam_state_manager.generator.run_build import apply_port_offsets

from tests.test_port_play import _app, spec  # noqa: F401 - fixture reuse


def _body(spec, **kw):
    body = dict(spec=spec, python=sys.executable, con='con2', slot=3, port=5, offset=.01,
                output_mode='direct', ai=1, kind='output')
    body.update(kw)
    return body


def _confirm(client, body):
    r = client.post('/port-play/loopcal/confirm', json=dict(body, hardware_ok=True))
    assert r.status_code == 200, r.json
    return dict(body, confirm_token=r.json['token'])


def _capture(monkeypatch, manager):
    captured = {}

    def start(python, script, request, meta, job=None):
        captured.update(python=python, request=request, meta=meta, job=job)
        return {'id': 'cal'}
    monkeypatch.setattr(manager, 'start', start)
    return captured


def test_confirm_needs_the_safety_box(spec, tmp_path, monkeypatch):
    app, _ = _app(tmp_path, monkeypatch)
    client = app.test_client()
    assert client.post('/port-play/loopcal/confirm', json=_body(spec)).status_code == 400
    assert client.post('/port-play/loopcal/confirm',
                       json=_body(spec, hardware_ok=True, kind='x')).status_code == 400
    assert client.post('/port-play/loopcal/confirm',
                       json=_body(spec, hardware_ok=True, ai=3)).status_code == 400


def test_run_refuses_without_token_or_for_another_target(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    captured = _capture(monkeypatch, manager)
    client = app.test_client()
    spec['port_offsets'] = {'con2/3/ai1': {'offset': .002, 'gain_db': 0}}
    assert client.post('/port-play/loopcal/run', json=_body(spec)).status_code == 400
    token = _confirm(client, _body(spec, ai=2))['confirm_token']
    assert client.post('/port-play/loopcal/run', json=_body(spec, confirm_token=token)).status_code == 400
    # a play token is not a calibration token
    play = client.post('/port-play/confirm', json=_body(spec, hardware_ok=True)).json['token']
    assert client.post('/port-play/loopcal/run', json=_body(spec, confirm_token=play)).status_code == 400
    assert not captured


def test_output_calibration_needs_a_saved_ai_offset(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    captured = _capture(monkeypatch, manager)
    client = app.test_client()
    r = client.post('/port-play/loopcal/run', json=_confirm(client, _body(spec)))
    assert r.status_code == 400 and 'Calibrate AI 1 of FEM 3' in r.json['error']
    # an offset for the OTHER AI, or at another gain, does not count
    spec['port_offsets'] = {'con2/3/ai2': {'offset': .002, 'gain_db': 0},
                            'con2/3/ai1': {'offset': .002, 'gain_db': 6}}
    assert client.post('/port-play/loopcal/run', json=_confirm(client, _body(spec))).status_code == 400
    assert not captured
    spec['port_offsets']['con2/3/ai1'] = {'offset': .002, 'gain_db': 0}
    r = client.post('/port-play/loopcal/run', json=_confirm(client, _body(spec)))
    assert r.status_code == 200, r.json
    job = captured['job']
    assert job['kind'] == 'output' and job['ai_offset'] == .002 and job['ao_offset'] == .01
    assert (job['con'], job['fem'], job['ao'], job['ai']) == ('con2', 3, 5, 1)
    assert job['host'] == 'scope-host' and job['cluster_name'] == 'scope-cluster' and job['port'] == 80
    # controllers come from the wizard spec, with the saved input offset applied
    assert job['controllers'] == pt.controllers_from_spec(spec)
    assert job['controllers']['con2']['fems']['3']['analog_inputs']['1']['offset'] == .002
    assert set(job['controllers']) == {'con1', 'con2', 'con3'}
    assert captured['python'] == sys.executable
    assert captured['request']['_play_owner'] and captured['meta']['loopcal']['kind'] == 'output'


def test_input_calibration_runs_without_saved_offset(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    captured = _capture(monkeypatch, manager)
    client = app.test_client()
    r = client.post('/port-play/loopcal/run', json=_confirm(client, _body(spec, kind='input', ai=2)))
    assert r.status_code == 200, r.json
    assert captured['job']['kind'] == 'input' and captured['job']['ai'] == 2
    assert captured['job']['ai_offset'] == 0.0


def test_run_refuses_an_mw_slot_and_busy(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    _capture(monkeypatch, manager)
    client = app.test_client()
    r = client.post('/port-play/loopcal/run', json=_confirm(client, _body(spec, kind='input', slot=1)))
    assert r.status_code == 400
    with client.session_transaction() as s:
        s['port_test_owner'] = 'me'
    stopped = []
    monkeypatch.setattr(manager, 'status', lambda *a: {'state': 'running', 'id': 'old'})
    monkeypatch.setattr(manager, 'stop', lambda *a: stopped.append(a) or True)
    manager.current = {'id': 'old', 'play_owner': 'other'}
    r = client.post('/port-play/loopcal/run', json=_confirm(client, _body(spec, kind='input')))
    assert r.status_code == 409 and r.json['busy'] and stopped == []
    manager.current = {'id': 'old', 'play_owner': 'me'}
    r = client.post('/port-play/loopcal/run', json=_confirm(client, _body(spec, kind='input')))
    assert r.status_code == 409 and r.json['stopping'] and stopped == [('old', 'Calibration started')]


class _Done:
    pid, returncode, stdin = None, 0, None

    def poll(self):
        return 0


def _finished(manager, tmp_path, result, owner='me', kind='input'):
    work = tmp_path / 'w'
    work.mkdir(exist_ok=True)
    (work / '_result.json').write_text(json.dumps(result), encoding='utf-8')
    (work / 'stdout.log').write_text(
        ptr.LOOPCAL_PREFIX + json.dumps({'event': 'progress', 'message': 'Iter 1: offset +0.0 V -> DC +1 mV'})
        + '\nnoise\n', encoding='utf-8')
    manager.current = {'id': 'cal', 'play_owner': owner, 'work': work, 'proc': _Done(),
                       'started': 0, 'stop_at': None, 'meta': {'loopcal': {
                           'con': 'con2', 'fem': 3, 'ao': 5, 'ai': 1, 'kind': kind,
                           'output_mode': 'direct', 'ai_offset': .002, 'gain_db': 0}}}


def test_save_only_when_converged(tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    client = app.test_client()
    with client.session_transaction() as s:
        s['port_test_owner'] = 'me'
    _finished(manager, tmp_path, {'ok': True, 'converged': False, 'final_offset': .01, 'iters': 6})
    assert client.get('/port-test/status?id=cal').json['progress'] == ['Iter 1: offset +0.0 V -> DC +1 mV']
    r = client.post('/port-play/loopcal/save', json={'id': 'cal'})
    assert r.status_code == 409 and 'Not converged' in r.json['error']
    _finished(manager, tmp_path, {'ok': True, 'converged': True, 'stopped': True, 'final_offset': .01})
    assert client.post('/port-play/loopcal/save', json={'id': 'cal'}).status_code == 409
    _finished(manager, tmp_path, {'ok': False, 'error': 'x'})
    assert client.post('/port-play/loopcal/save', json={'id': 'cal'}).status_code == 409
    _finished(manager, tmp_path, {'ok': True, 'converged': True, 'final_offset': .0012, 'iters': 2})
    assert client.post('/port-play/loopcal/save', json={'id': 'other'}).status_code == 409
    r = client.post('/port-play/loopcal/save', json={'id': 'cal'})
    assert r.status_code == 200 and r.json['pin'] == 'con2/3/ai1'
    assert r.json['entry'] == {'offset': .0012, 'gain_db': 0, 'source': 'loopback'}
    _finished(manager, tmp_path, {'ok': True, 'converged': True, 'final_offset': -.03, 'iters': 3}, kind='output')
    r = client.post('/port-play/loopcal/save', json={'id': 'cal'})
    assert r.json['pin'] == 'con2/3/5'
    assert r.json['entry']['offset'] == -.03 and r.json['entry']['output_mode'] == 'direct'
    assert r.json['entry']['source'] == 'loopback'
    _finished(manager, tmp_path, {'ok': True, 'converged': True, 'final_offset': .1}, owner='someone')
    assert client.post('/port-play/loopcal/save', json={'id': 'cal'}).status_code == 409


def test_saved_entries_feed_play_and_build(spec):
    spec['port_offsets'] = {'con2/3/ai1': {'offset': .0012, 'gain_db': 0, 'source': 'loopback'},
                            'con2/3/5': {'offset': -.03, 'output_mode': 'direct', 'source': 'loopback'}}
    ctrl = pt.controllers_from_spec(spec)['con2']['fems']['3']
    assert ctrl['analog_inputs']['1']['offset'] == .0012 and ctrl['analog_inputs']['2']['offset'] == 0
    assert ctrl['analog_outputs']['5']['offset'] == -.03
    cfg, _ = pt.build_wizard_dc(spec, ['con2', 3, 5], -.03, 'direct')  # Play again uses the new value
    assert cfg['controllers']['con2']['fems']['3']['analog_outputs']['5']['offset'] == -.03
    state = {}
    assert sorted(apply_port_offsets(state, spec)) == ['con2/3/5', 'con2/3/ai1']
    inp = state['ports']['analog_inputs']['con2']['3']['1']
    assert inp['offset'] == .0012 and inp['port_id'] == 1 and inp['__class__'].endswith('LFFEMAnalogInputPort')
    spec['port_offsets']['con2/3/ai1']['gain_db'] = 6
    with pytest.raises(ValueError):
        apply_port_offsets({}, spec)


def test_export_script_prefilled(spec, tmp_path, monkeypatch):
    app, _ = _app(tmp_path, monkeypatch)
    client = app.test_client()
    spec['port_offsets'] = {'con2/3/ai2': {'offset': .004, 'gain_db': 0},
                            'con1/3/7': {'offset': .2, 'output_mode': 'amplified'}}
    r = client.post('/port-play/loopcal/script.py', json=_body(spec, ai=2, offset=.02, output_mode='amplified'))
    assert r.status_code == 200, r.data
    assert r.headers['Content-Type'].startswith('text/x-python')
    assert 'filename="lf_offset_con2_fem3_ao5.py"' in r.headers['Content-Disposition']
    text = r.data.decode()
    compile(text, 'x.py', 'exec')
    for line in ('QOP_IP = \'scope-host\'', 'CLUSTER_NAME = \'scope-cluster\'', 'QOP_PORT = 80',
                 'CON = \'con2\'', 'FEM = 3', 'AO_PORT = 5', 'AI_PORT = 2', "OUTPUT_MODE = 'amplified'",
                 'AO_OFFSET_START = 0.02', 'AI_OFFSET_START = 0.004'):
        assert line in text, line
    assert "'con1'" in text and "'con3'" in text  # other controllers from the spec
    assert client.post('/port-play/loopcal/script.py', json=_body(spec, ai=5)).status_code == 400


def test_runs_start_spawns_the_loopcal_runner_with_a_job(tmp_path):
    manager = ptr.Runs(tmp_path, resume=False)
    seen = {}

    class P:
        pid, stdin = None, None

        def poll(self):
            return 0

    def popen(argv, **kw):
        seen['argv'] = argv
        return P()
    out = manager.start(sys.executable, '', {'mode': 'opx', 'target': {'host': 'h'}}, {},
                        popen=popen, job={'kind': 'input', 'ai': 1})
    work = manager.current['work']
    assert seen['argv'][1] == str(ptr.LOOPCAL_RUNNER) and seen['argv'][2] == str(work / 'job.json')
    job = json.loads((work / 'job.json').read_text(encoding='utf-8'))
    # Stop writes the 'stop' file; status reads '_result.json': the runner must use both.
    assert job['stop_file'] == 'stop' and job['result_file'] == '_result.json' and job['kind'] == 'input'
    assert out['id'] == manager.current['id']
