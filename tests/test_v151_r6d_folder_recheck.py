"""Render rechecks have one budget and retain the last definite notice."""
import pathlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from flask import Flask

from quam_state_manager.core import folder_probe
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.web import routes


def _setup(monkeypatch, verdicts, stat):
    app = Flask(__name__)
    ctx = {'type': 'quam', 'origin': 'live',
           'store': QuamStore.from_dicts({'extras': {'data_folder': ['ready', 'offline']}}, {}),
           'extras_data_dangling': ['ready', 'offline']}
    monkeypatch.setattr(folder_probe, 'folders_present', lambda *a, **kw: verdicts)
    monkeypatch.setattr(routes, '_extras_native', lambda raw: raw)
    monkeypatch.setattr(routes, '_ws', lambda: SimpleNamespace(root_folders=[pathlib.Path('ready')]))
    monkeypatch.setattr(pathlib.Path, 'is_dir', stat)
    return app, ctx


def test_slow_adoption_is_bounded_serialized_and_publishes_atomically(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def stat(p):
        if str(p) == 'offline':
            calls.append(threading.get_ident())
            entered.set()
            release.wait(3)
            time.sleep(.6)
            return False
        return True

    app, ctx = _setup(monkeypatch, {'ready': True, 'offline': False}, stat)

    def render():
        with app.app_context():
            start = time.monotonic()
            answer = routes._recheck_extras_dangling(ctx)
            return answer, time.monotonic() - start

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(render)
            assert entered.wait(2)
            answer, elapsed = render()
            assert answer == ['ready', 'offline'], 'concurrent render saw a partial publication'
            assert elapsed < .4
            answer, elapsed = first.result(timeout=2)
            assert elapsed < .4, 'render waited for the 600 ms filesystem scan'
            assert answer == ['ready', 'offline']
            assert len(calls) == 1
    finally:
        release.set()
        # Join the background adoption before monkeypatch restores its dependencies.
        worker = ctx.get('_extras_recheck_thread')
        if worker is not None:
            worker.join(3)
    assert ctx['extras_data_dangling'] == ['offline']
    assert len(calls) == 1, 'concurrent renders queued duplicate adoptions'


@pytest.mark.parametrize('failure', ['unknown', 'exception'])
def test_unknown_or_exception_keeps_the_entire_old_list(monkeypatch, failure):
    def stat(p):
        if str(p) == 'offline':
            if failure == 'exception':
                raise PermissionError('share unavailable')
            return False
        return True

    app, ctx = _setup(monkeypatch, {'ready': True, 'offline': None if failure == 'unknown' else False}, stat)
    with app.app_context():
        assert routes._recheck_extras_dangling(ctx) == ['ready', 'offline']
        worker = ctx.get('_extras_recheck_thread')
        if worker is not None:
            worker.join(2)
        assert ctx['extras_data_dangling'] == ['ready', 'offline']
        assert routes._recheck_extras_dangling(ctx) == ['ready', 'offline']


def test_direct_get_adoption_also_uses_the_bounded_worker(monkeypatch):
    def stat(p):
        if str(p) == 'offline':
            time.sleep(.6)
            return False
        return True

    app, ctx = _setup(monkeypatch, {'ready': True, 'offline': False}, stat)
    with app.test_request_context('/'):
        started = time.monotonic()
        routes._adopt_extras_data_folders(ctx)
        elapsed = time.monotonic() - started
    try:
        assert elapsed < .4, 'GET adoption bypassed the render budget'
        assert ctx['extras_data_dangling'] == ['ready', 'offline']
    finally:
        worker = ctx.get('_extras_recheck_thread')
        if worker is not None:
            worker.join(2)
    assert ctx['extras_data_dangling'] == ['offline']
