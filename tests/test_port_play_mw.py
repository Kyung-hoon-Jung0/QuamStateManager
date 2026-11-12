"""MW-FEM CW play in the wizard's Port play panel; no chip, no real QM."""
import copy
import sys

import pytest
from quam_state_manager.core import port_test as pt
from quam_state_manager.web.app import create_app


@pytest.fixture
def spec():
    return {'network': {'host': 'scope-host', 'cluster_name': 'scope-cluster', 'port': 80},
            'instruments': {'controllers': [
                {'con': c, 'fems': [{'slot': 1, 'fem': 'mw'}, {'slot': 2, 'fem': 'mw'}, {'slot': 3, 'fem': 'lf'}]}
                for c in range(1, 4)]},
            'qubits': [], 'port_offsets': {'con1/3/2': {'offset': 0.05, 'output_mode': 'direct'}}}


ARGS = dict(band=2, lo_hz=6e9, if_hz=50e6, fsp=1, amp=0.5)


def _mw(spec, port=('con2', 1, 4), **kw):
    a = dict(ARGS, **kw)
    return pt.build_wizard_mw(spec, list(port), a['band'], a['lo_hz'], a['if_hz'], a['fsp'], a['amp'])


def test_only_the_played_mw_port_is_configured(spec):
    baseline = copy.deepcopy(spec)
    cfg, plan = _mw(spec)
    assert spec == baseline
    ref = pt.controllers_from_spec(spec, outputs_at_zero=True)
    for con, ctrl in cfg['controllers'].items():
        for slot, fem in ctrl['fems'].items():
            if (con, slot) == ('con2', '1'):
                assert fem['analog_outputs'] == {'4': {
                    'band': 2, 'upconverter_frequency': 6e9, 'full_scale_power_dbm': 1, 'sampling_rate': 1e9}}
            else:
                assert fem == ref[con]['fems'][slot], (con, slot)
    assert cfg['controllers']['con1']['fems']['3']['analog_outputs']['2']['offset'] == 0.0  # a saved offset outside the play is not driven
    el = cfg['elements']['sm_port_play']
    assert el['MWInput']['port'] == ('con2', 1, 4) and el['intermediate_frequency'] == 50e6
    wf = cfg['pulses'][el['operations']['cw']]['waveforms']
    assert cfg['waveforms'][wf['I']]['sample'] == 0.5 and cfg['waveforms'][wf['Q']]['sample'] == 0.0
    assert plan['test'] == 'cw' and plan['kind'] == 'mw' and plan['rf'] == 6.05e9
    assert plan['expected_dbm'] == pytest.approx(1 + 20 * __import__('math').log10(0.5))
    compile(pt.render_script(cfg, plan), 'play.py', 'exec')


@pytest.mark.parametrize('kw, words', [
    (dict(band=4), 'Band'), (dict(band=0), 'Band'), (dict(band=True), 'Band'),
    (dict(band=1, lo_hz=5.6e9), 'Band 1'), (dict(band=2, lo_hz=4.4e9), 'Band 2'),
    (dict(band=3, lo_hz=11.1e9), 'Band 3'), (dict(if_hz=501e6), 'IF'), (dict(if_hz=-501e6), 'IF'),
    (dict(fsp=-12), 'Full-scale'), (dict(fsp=19), 'Full-scale'), (dict(fsp=1.5), 'integer'),
    (dict(amp=1.0), 'Amplitude'), (dict(amp=-1.0), 'Amplitude'), (dict(amp=float('nan')), 'finite'),
])
def test_range_refusals(spec, kw, words):
    with pytest.raises(pt.PortTestError, match=words):
        _mw(spec, **kw)


def test_edges_accepted_with_notes(spec):
    _, plan = _mw(spec, band=3, lo_hz=11e9, if_hz=-450e6, fsp=18, amp=-0.999)
    text = ' '.join(plan['notes'])
    assert '400 MHz' in text and '16 dBm' in text
    _mw(spec, band=1, lo_hz=0.0, if_hz=500e6, fsp=-11, amp=0.0)


def test_lf_or_input_port_refused(spec):
    with pytest.raises(pt.PortTestError, match='MW-FEM'):
        _mw(spec, port=('con2', 3, 4))
    with pytest.raises(pt.PortTestError, match='MW-FEM'):
        _mw(spec, port=('con2', 1, 9))


def _app(tmp_path, monkeypatch):
    from quam_state_manager.web import port_test_routes as routes
    from quam_state_manager.core import config_generator
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    manager = routes.ptr.Runs(tmp_path / 'runs', resume=False)
    app.config['port_test_runs'] = manager
    monkeypatch.setattr(config_generator, 'known_interpreter', lambda p, _: p == sys.executable)
    return app, manager


def test_token_bound_to_mw_params(spec, tmp_path, monkeypatch):
    app, manager = _app(tmp_path, monkeypatch)
    captured = {}
    monkeypatch.setattr(manager, 'start', lambda py, script, req, meta: captured.update(
        script=script, meta=meta) or {'id': 'mock'})
    body = dict(kind='mw', spec=spec, python=sys.executable, con='con2', slot=1, port=4, band=2,
                lo_hz=6e9, if_hz=50e6, full_scale_power_dbm=1, amp=0.5, max_s=12)
    c = app.test_client()
    tok = c.post('/port-play/confirm', json=dict(body, hardware_ok=True)).json['token']
    for k, v in (('amp', 0.6), ('full_scale_power_dbm', 2), ('band', 3), ('lo_hz', 6.1e9),
                 ('if_hz', 60e6), ('port', 5)):
        changed = dict(body, confirm_token=tok, **{k: v})
        assert c.post('/port-play/run', json=changed).status_code == 400, k
        tok = c.post('/port-play/confirm', json=dict(body, hardware_ok=True)).json['token']
    r = c.post('/port-play/run', json=dict(body, confirm_token=tok))
    assert r.status_code == 200, r.json
    assert captured['meta']['plan']['kind'] == 'mw' and "'band': 2" in captured['script']
    bad = c.post('/port-play/confirm', json=dict(body, hardware_ok=True, full_scale_power_dbm=30))
    assert bad.status_code == 400 and 'Full-scale' in bad.json['error']
