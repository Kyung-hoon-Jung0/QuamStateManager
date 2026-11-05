import json
import pytest
import quam_state_manager.web.routes as routes
from tests.test_autosync_merge import env, _arm, _edit, _ctx, _sess, _state, _write_chip
from tests.test_autosync_lost_applied import _apply_name, _outside_save_and_pull


def _notice(response):
    return json.loads(response.headers['HX-Trigger'])['autoSyncRestored']


def test_unknown_string_refusal_never_claims_live_changed(env, monkeypatch):
    _arm(env)
    _edit(env)
    monkeypatch.setattr(routes.working_copy, 'apply_to_live',
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError('synthetic refusal')))
    response = env['client'].post('/state/apply-to-live', data={'auto_flush': '1'})
    import re
    chip = re.search(r'<span class="sync-control-text">(.*?)</span>', response.text).group(1)
    assert 'unknown failure' in chip
    assert 'live changed' not in chip
    with env['app'].test_request_context():
        assert routes._merge_refusal_cause('sharing violation', '') == 'retrying: file locked'
        assert routes._merge_refusal_cause('opaque refusal', '') == 'unknown failure'
        assert routes._merge_refusal_cause('changed', 'stale_live') == 'live changed'


def test_real_live_change_is_named(env):
    _arm(env)
    _edit(env)
    _write_chip(env['live'], _state(f01=7.7e9))
    response = env['client'].post('/state/apply-to-live', data={'auto_flush': '1'})
    import re
    chip = re.search(r'<span class="sync-control-text">(.*?)</span>', response.text).group(1)
    assert 'live changed' in chip
    assert 'unknown failure' not in chip


def _window(env, win_id):
    client = env['app'].test_client()
    client.environ_base['HTTP_X_SM_WINDOW_ID'] = win_id
    return client


def _replacement(env, value=4.4e-5):
    _arm(env)
    _apply_name(env)
    return _notice(_outside_save_and_pull(env, _state(t1=value)))


@pytest.mark.parametrize('value', [4.4e-5, 2e-5])
def test_open_windows_each_receive_notice_once(env, value):
    second = _window(env, 'second')
    second.get('/state/drift')
    notice = _replacement(env, value)
    assert env['client'].get('/state/drift').get_json()['auto_restore_notice'] is None
    assert second.get('/state/drift').get_json()['auto_restore_notice'] == notice
    assert second.get('/state/drift').get_json()['auto_restore_notice'] is None
    assert _ctx(env)['_auto_restore_notice'] == notice


def test_later_window_receives_no_old_notice(env):
    second = _window(env, 'second')
    second.get('/state/drift')
    notice = _replacement(env)
    assert _window(env, 'third').get('/state/drift').get_json()['auto_restore_notice'] is None
    assert second.get('/state/drift').get_json()['auto_restore_notice'] == notice
    assert _ctx(env)['_auto_restore_notice'] == notice


def test_reload_receives_no_old_notice(env):
    second = _window(env, 'before-reload')
    second.get('/state/drift')
    notice = _replacement(env)
    assert _window(env, 'after-reload').get('/state/drift').get_json()['auto_restore_notice'] is None
    assert _ctx(env)['_auto_restore_notice'] == notice


def test_notice_expires_before_delivery(env, monkeypatch):
    second = _window(env, 'second')
    second.get('/state/drift')
    notice = _replacement(env)
    monkeypatch.setattr(routes.time, 'time', lambda: notice['at'] + 60)
    assert second.get('/state/drift').get_json()['auto_restore_notice'] is None
    assert not _ctx(env).get('_auto_restore_notice')


def test_inactive_window_is_not_a_recipient(env):
    stale = _window(env, 'stale')
    stale.get('/state/drift')
    _ctx(env)['_notice_windows']['stale'] -= 31
    _replacement(env)
    assert stale.get('/state/drift').get_json()['auto_restore_notice'] is None


def test_notice_backups_are_bounded(env):
    ctx = _ctx(env)
    ctx['_notice_backups'] = {str(i): {'snapshot': str(i)} for i in range(55)}
    _replacement(env)
    assert len(ctx['_notice_backups']) == 50
    assert '0' not in ctx['_notice_backups']


def test_notice_backup_is_independent_and_stale_restore_names_newer_live(env):
    _arm(env)
    _apply_name(env)
    first = _notice(_outside_save_and_pull(env, _state(t1=4.4e-5)))
    _edit(env, path='qubits.qA1.T1', value='5.5e-5')
    env['client'].post('/state/apply-to-live')
    second = _notice(_outside_save_and_pull(env, _state(t1=6.6e-5)))
    response = env['client'].post('/state/take-live/restore', data={'notice_id': first['event']})
    assert response.status_code == 409
    assert '6.6e-05' in response.text
    assert _ctx(env)['store'].merged['qubits']['qA1']['T1'] == 6.6e-5
    # Even a node write not yet pulled must defeat the second notice's CAS.
    _write_chip(env['live'], _state(t1=7.7e-5))
    response = env['client'].post('/state/take-live/restore', data={'notice_id': second['event']})
    assert response.status_code == 409
    assert '7.7e-05' in response.text
    assert json.loads((env['live'] / 'state.json').read_text())['qubits']['qA1']['T1'] == 7.7e-5


def test_value_without_predecessor_is_announced_and_adopted(env, monkeypatch):
    _arm(env)
    _apply_name(env)
    held = routes._HeldApplied({'qubits.qA1.T1': 3.3e-5})
    monkeypatch.setattr(routes, '_applied_values_held', lambda *a, **k: held)
    response = _outside_save_and_pull(env, _state(t1=4.4e-5))
    assert 'a node replaced' in _notice(response)['message']
    assert json.loads((env['live'] / 'state.json').read_text())['qubits']['qA1']['T1'] == 4.4e-5

@pytest.mark.parametrize('automatic', [True, False])
def test_new_node_value_is_adopted_with_backup_and_one_shared_notice(env, monkeypatch, automatic):
    _arm(env)
    _apply_name(env)
    if not automatic:
        env['client'].post('/auto-sync/set', data={'pull': '0', 'push': '0'})
    newer = _state(t1=4.4e-5)
    if automatic:
        r = _outside_save_and_pull(env, newer)
    else:
        _write_chip(env['live'], newer)
        r = env['client'].post('/state/sync', data={'mode': 'discard'})
    assert r.status_code == 200
    ctx = _ctx(env)
    notice = _notice(r)
    assert notice and notice['message'] == 'a node replaced a value you applied: qA1.T1 3.3e-05 -> 4.4e-05'
    assert notice['bring_back']
    assert ctx['_notice_backups'][notice['event']]['snapshot']
    assert ctx['store'].merged['qubits']['qA1']['T1'] == 4.4e-5
    assert json.loads((env['live'] / 'state.json').read_text())['qubits']['qA1']['T1'] == 4.4e-5
    assert bool(_sess(env)) == automatic
    assert env['client'].get('/state/drift').get_json()['auto_restore_notice'] is None
    writes = []
    apply = routes.working_copy.apply_to_live
    def write(*args, **kwargs):
        writes.append(True)
        return apply(*args, **kwargs)
    monkeypatch.setattr(routes.working_copy, 'apply_to_live', write)
    r = env['client'].post('/state/take-live/restore', data={'notice_id': notice['event']})
    assert r.status_code == 200
    assert ctx['store'].merged['qubits']['qA1']['T1'] == 3.3e-5
    assert json.loads((env['live'] / 'state.json').read_text())['qubits']['qA1']['T1'] == (3.3e-5 if automatic else 4.4e-5)
    assert bool(ctx['store'].change_log) == (not automatic)
    assert not (_sess(env) or {}).get('hold')
    assert len(writes) == int(automatic)
    assert ('to live' if automatic else 'as unapplied edits') in json.loads(r.headers['HX-Trigger'])['syncToast']['message']


def test_bring_back_resolves_notice_for_two_windows(env):
    second = _window(env, 'second')
    second.get('/state/drift')
    notice = _replacement(env)
    assert second.get('/state/drift').get_json()['auto_restore_notice'] == notice
    response = env['client'].post('/state/take-live/restore', data={'notice_id': notice['event']})
    assert response.status_code == 200
    for client in (env['client'], second):
        poll = client.get('/state/drift').get_json()
        assert notice['event'] in poll['auto_restore_resolved']
        assert poll['auto_restore_notice'] is None
    assert second.post('/state/take-live/restore', data={'notice_id': notice['event']}).status_code == 409

@pytest.mark.parametrize('readonly', [False, True])
def test_first_push_refusal_names_cause_and_transient_has_no_error_response(env, monkeypatch, readonly):
    _arm(env)
    _edit(env)
    def fail(*a, **kw):
        if readonly:
            raise PermissionError('read-only synthetic folder')
        exc = OSError('sharing violation on replace')
        exc.winerror = 32
        raise exc
    monkeypatch.setattr(routes.working_copy, 'apply_to_live', fail)
    r = env['client'].post('/state/apply-to-live', data={'auto_flush': '1'})
    html = r.get_data(as_text=True)
    if readonly:
        assert 'read-only' in html
    else:
        assert r.status_code == 200
        assert not r.headers.get('X-SM-Error-Code')
        assert 'UNVERIFIED' not in html
        assert 'auto-apply-off' not in html and 'auto-apply-on' in html
        assert 'retrying (1)' in html
        assert 'retrying: file locked' in html
    import re
    chip = re.search(r'<span class="sync-control-text">(.*?)</span>', html).group(1)
    assert ('read-only' if readonly else 'retrying: file locked') in chip
    assert 'live changed' not in chip

@pytest.mark.parametrize('predecessor', [2e-5, 3.3e-5])
def test_predecessors_are_recovered_without_replacement_backup(env, predecessor):
    _arm(env)
    _apply_name(env)
    _edit(env, path='qubits.qA1.T1', value='3.8e-5')
    env['client'].post('/state/apply-to-live')
    r = _outside_save_and_pull(env, _state(t1=predecessor))
    notice = _notice(r)
    assert 'Auto-Sync recovered it' in notice['message']
    assert not notice.get('bring_back')
    assert not _ctx(env).get('take_live_backup')


def test_one_notice_covers_new_measurement_and_recovered_predecessor(env):
    _arm(env)
    _apply_name(env)
    _edit(env)
    env['client'].post('/state/apply-to-live')
    r = _outside_save_and_pull(env, _state(t1=4.4e-5))
    notice = _notice(r)
    assert notice == json.loads(r.headers['HX-Trigger'])['autoSyncRestored']
    assert 'a node replaced a value you applied: qA1.T1' in notice['message']
    assert 'Auto-Sync recovered it' in notice['message']
    assert notice['bring_back']
    assert _ctx(env)['store'].merged['qubits']['qA1'] == _state(f01=5.1e9, t1=4.4e-5)['qubits']['qA1']


@pytest.mark.parametrize('readonly', [False, True])
def test_manual_write_refusal_also_names_the_cause(env, monkeypatch, readonly):
    env['client'].post('/auto-sync/set', data={'pull': '0', 'push': '0'})
    _edit(env)
    def fail(*a, **kw):
        if readonly:
            raise PermissionError('read-only synthetic folder')
        exc = OSError('sharing violation')
        exc.winerror = 32
        raise exc
    monkeypatch.setattr(routes.working_copy, 'apply_to_live', fail)
    r = env['client'].post('/state/apply-to-live')
    import re
    html = r.get_data(as_text=True)
    chip = re.search(r'<span class="sync-control-text">(.*?)</span>', html).group(1)
    assert ('read-only' if readonly else 'file locked') in chip
    assert 'live changed' not in chip


def test_notice_window_client_identity():
    import shutil
    import subprocess
    from pathlib import Path
    if shutil.which('node') is None:
        pytest.skip('node not installed')
    driver = Path(__file__).with_name('notice_window_selfcheck.cjs')
    result = subprocess.run(['node', str(driver)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '8 assertions passed' in result.stdout


def test_unidentified_request_cannot_consume_notice(env):
    second = _window(env, 'second')
    second.get('/state/drift')
    notice = _replacement(env)
    assert env['app'].test_client().get('/state/drift').get_json()['auto_restore_notice'] is None
    assert second.get('/state/drift').get_json()['auto_restore_notice'] == notice


def test_new_event_delivers_again_to_each_open_window(env):
    second = _window(env, 'second')
    second.get('/state/drift')
    first = _replacement(env)
    assert second.get('/state/drift').get_json()['auto_restore_notice'] == first
    _edit(env, path='qubits.qA1.T1', value='5.5e-5')
    env['client'].post('/state/apply-to-live')
    next_notice = _notice(_outside_save_and_pull(env, _state(t1=6.6e-5)))
    assert next_notice['event'] != first['event']
    assert second.get('/state/drift').get_json()['auto_restore_notice'] == next_notice
    assert second.get('/state/drift').get_json()['auto_restore_notice'] is None


@pytest.mark.parametrize('stale_event', [False, True])
def test_buffered_response_cannot_deliver_twice_or_consume_another_event(env, stale_event):
    from flask import jsonify
    second = _window(env, 'second')
    second.get('/state/drift')
    notice = _replacement(env)
    ctx = _ctx(env)
    with env['app'].test_request_context(headers={'X-SM-Window-ID': 'second'}):
        def buffered():
            response = jsonify(auto_restore_notice=notice)
            response.headers['HX-Trigger'] = json.dumps({'autoSyncRestored': notice, 'other': {}})
            return response
        response = buffered()
        if stale_event:
            ctx['_auto_restore_notice'] = dict(notice, event='next-event')
        else:
            routes._finish_auto_restore_notice(buffered())
        response = routes._finish_auto_restore_notice(response)
        assert response.get_json()['auto_restore_notice'] is None
        assert json.loads(response.headers['HX-Trigger']) == {'other': {}}
        if stale_event:
            assert 'second' not in ctx['_notice_delivered']
