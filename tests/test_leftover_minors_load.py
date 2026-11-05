import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest
from werkzeug.serving import make_server

from quam_state_manager.web import routes
from quam_state_manager.web.app import create_app


def chip(folder):
    folder.mkdir()
    for name, value in [('state', {'qubits': {'q1': {'f_01': 5e9}}}),
                        ('wiring', {'wiring': {}})]:
        (folder / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')
    return folder


@pytest.mark.parametrize('transport', ['client', 'http'])
@pytest.mark.parametrize('mode', ['atomic', 'inplace', 'rename'])
def test_load_during_writes(tmp_path, transport, mode):
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    count = int(os.environ.get('LEFTOVER_LOAD_RUNS', '3'))
    stop = threading.Event()
    active = [None]
    errors = []

    def writer():
        while not stop.is_set():
            folder = active[0]
            if folder is None:
                stop.wait(.001)
                continue
            for name in ['state.json', 'wiring.json']:
                path = folder / name
                try:
                    data = {'qubits': {'q1': {'f_01': 5e9}}} if name == 'state.json' else {'wiring': {}}
                    if mode == 'atomic':
                        pending = path.with_suffix('.pending')
                        pending.write_text(json.dumps(data), encoding='utf-8')
                        os.replace(pending, path)
                    elif mode == 'inplace':
                        with path.open('w', encoding='utf-8') as stream:
                            stop.wait(.015)
                            json.dump(data, stream)
                    else:
                        away = path.with_suffix('.away')
                        os.replace(path, away)
                        try:
                            stop.wait(.015)
                        finally:
                            os.replace(away, path)
                except OSError as exc:
                    errors.append(type(exc).__name__)
            stop.wait(.15)

    server = make_server('127.0.0.1', 0, app, threaded=True) if transport == 'http' else None
    server_thread = threading.Thread(target=server.serve_forever) if server else None
    thread = threading.Thread(target=writer)
    failures = []
    try:
        if server_thread:
            server_thread.start()
        thread.start()
        for i in range(count):
            folder = chip(tmp_path / f'chip{i}')
            active[0] = folder
            time.sleep(.003)
            if server:
                request = Request(f'http://127.0.0.1:{server.server_port}/load',
                                  data=urlencode({'folder': str(folder)}).encode(),
                                  headers={'HX-Request': 'true'})
                try:
                    with urlopen(request, timeout=30) as response:
                        status, body = response.status, response.read().decode()
                except HTTPError as exc:
                    status, body = exc.code, exc.read().decode()
            else:
                with app.test_client() as client:
                    response = client.post('/load', data={'folder': str(folder)}, headers={'HX-Request': 'true'})
                    status, body = response.status_code, response.get_data(as_text=True)
            assert status in (200, 400), body
            if status == 400:
                failures.append(body)
    finally:
        stop.set()
        thread.join(10)
        if server:
            server.shutdown()
            server.server_close()
            server_thread.join(10)
        assert not thread.is_alive()
    print(f'{transport} {mode}: {len(failures)}/{count} HTTP 400; writer errors: {errors[:5]}')
    if failures:
        print('first error:', failures[0])
    if mode == 'atomic':
        assert failures == []


def test_overlapping_loads(tmp_path):
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    folders = [chip(tmp_path / f'chip{i}') for i in range(2)]

    def load(folder):
        with app.test_client() as client:
            return client.post('/load', data={'folder': str(folder)},
                               headers={'HX-Request': 'true'}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(load, folders * 10)) == [200] * 20


def test_load_failure_logged(tmp_path, monkeypatch, caplog):
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))

    def fail(*args, **kwargs):
        raise OSError('temporary read error')
    monkeypatch.setattr(routes, '_activate_quam', fail)
    with caplog.at_level(logging.WARNING):
        response = app.test_client().post('/load', data={'folder': str(tmp_path)})
    assert response.status_code == 400
    assert any('OSError' in record.message and str(tmp_path) in record.message
               for record in caplog.records)
