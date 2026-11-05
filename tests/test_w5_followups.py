import json
import pytest
import quam_state_manager.web.routes as routes
from tests.test_autosync_merge import env, _arm, _edit, _ctx, _sess, _state, _write_chip
from tests.test_autosync_lost_applied import _apply_name, _outside_save_and_pull

@pytest.mark.parametrize('automatic', [True, False])
def test_new_node_value_is_adopted_with_backup_and_one_shared_notice(env, automatic):
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
    notice = env['client'].get('/state/drift').get_json()['auto_restore_notice']
    assert notice and notice['message'] == 'a node replaced a value you applied: qA1.T1 3.3e-05 -> 4.4e-05'
    assert notice['bring_back']
    assert ctx['take_live_backup']['snapshot']
    assert ctx['store'].merged['qubits']['qA1']['T1'] == 4.4e-5
    assert json.loads((env['live'] / 'state.json').read_text())['qubits']['qA1']['T1'] == 4.4e-5
    assert bool(_sess(env)) == automatic
    assert env['client'].get('/state/drift').get_json()['auto_restore_notice'] == notice
    r = env['client'].post('/state/take-live/restore')
    assert r.status_code == 200
    assert ctx['store'].merged['qubits']['qA1']['T1'] == 3.3e-5

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
    notice = env['client'].get('/state/drift').get_json()['auto_restore_notice']
    assert 'Auto-Sync recovered it' in notice['message']
    assert not notice.get('bring_back')
    assert not _ctx(env).get('take_live_backup')


def test_one_notice_covers_new_measurement_and_recovered_predecessor(env):
    _arm(env)
    _apply_name(env)
    _edit(env)
    env['client'].post('/state/apply-to-live')
    r = _outside_save_and_pull(env, _state(t1=4.4e-5))
    notice = env['client'].get('/state/drift').get_json()['auto_restore_notice']
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
