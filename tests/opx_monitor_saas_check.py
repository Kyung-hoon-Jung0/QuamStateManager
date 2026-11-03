"""Explicit SaaS verification, using credentials already sourced by the caller.
Creates a fresh simulator, closes only QMs created here, and emits no endpoints or ids.
The caller chooses the SDK interpreter used for both this driver and the monitor worker.
"""
import contextlib, io, logging, os, sys, tempfile, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
logging.disable(logging.CRITICAL)
result = {}
try:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        from qm import QuantumMachinesManager
        from qm_saas import QmSaas, QOPVersion, ClusterConfig
        from quam_state_manager.core import opx_monitor as om
        from quam_state_manager.web.app import create_app
        client = QmSaas(email=os.environ['SAAS_EMAIL'], password=os.environ['SAAS_PASSWORD'], host=os.environ['SAAS_HOST'])
        cluster = ClusterConfig()
        cluster.controller().lf_fems(1, 2)
        with client.simulator(QOPVersion('v3_7_3'), cluster) as inst:
            if hasattr(type(inst), 'qmm_connection_params'):
                params = inst.qmm_connection_params
            else:
                import ssl
                params = dict(host=inst.host, port=inst.port, connection_headers=inst.default_connection_headers,
                              credentials=ssl.create_default_context(), follow_gateway_redirections=False)
            assert params['host'] not in ('127.0.0.1', 'localhost', '::1')
            qmm = QuantumMachinesManager(**params)
            def config(slot):
                return {'version': 1, 'controllers': {'con1': {'type': 'opx1000', 'fems': {slot: {'type': 'LF', 'analog_outputs': {1: {'offset': 0.0, 'output_mode': 'direct', 'sampling_rate': 1e9}}}}}}}
            own = []
            with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as scratch:
                app = create_app(testing=True, instance_path=scratch)
                target = {'kind': 'qmm', 'host': params['host'], 'port': params['port'], 'tls': True}
                om.save_settings(scratch, {'target': target, 'python': sys.executable, 'idle_minutes': .01, 'interval_s': 5})
                om.save_secret_headers(scratch, om.target_key(target), params.get('connection_headers', {}))
                monitor = om.Monitor(scratch)
                monitor._start_thread = lambda: None
                app.config['opx_monitor'] = monitor
                try:
                    qm1 = qmm.open_qm(config(1), close_other_machines=False)
                    own.append(str(qm1.id))
                    monitor.poll_once()
                    assert monitor.last_answer is not None
                    time.sleep(1)
                    monitor.poll_once()
                    c = app.test_client()
                    html = c.get('/opx-monitor/body?tab=cleanup').get_data(as_text=True)
                    assert 'Close idle QMs (1)' in html
                    response = c.post('/opx-monitor/close', data={'idle': '1', 'qm_ids': own[0]}).get_json()
                    assert response['ok'] and response['closed'] == [own[0]], response
                    result['idle_close'] = 'one owned QM closed through Clean up route'
                    qm2 = qmm.open_qm(config(2), close_other_machines=False)
                    own.append(str(qm2.id))
                    monitor.poll_once()
                    response = c.post('/opx-monitor/close', data={'all': '1', 'qm_ids': own[1], 'confirm_count': '1'}).get_json()
                    assert response['ok'] and response['closed'] == [own[1]], response
                    assert not set(own) & set(map(str, qmm.list_open_qms()))
                    result['confirmed_close'] = 'one owned confirmed id closed through Clean up route'
                    result['history'] = len(monitor.history().recent_actions())
                    result['result'] = 'PASS'
                finally:
                    if monitor._worker:
                        monitor._worker.kill()
                    for qid in own:
                        if qid in set(map(str, qmm.list_open_qms())):
                            qmm.get_qm(qid).close()
except Exception as exc:
    result = {'result': 'FAIL', 'error_type': type(exc).__name__, 'frames': [(f.name, f.lineno) for f in __import__('traceback').extract_tb(exc.__traceback__)]}
print(__import__('json').dumps(result))
sys.exit(0 if result.get('result') == 'PASS' else 1)
