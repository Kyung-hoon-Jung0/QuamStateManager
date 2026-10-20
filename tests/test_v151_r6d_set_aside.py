"""Set aside revalidates settled recovery rows, separately from Discard."""
import pathlib

from tests.test_v151_r6_recovery_held import _staged_twice, _box, _ctx


def test_set_aside_has_its_own_intent_and_refreshes_stale_choices(tmp_path):
    _live, app, client, file_id = _staged_twice(tmp_path)
    _body, acts = _box(client.get('/state/review').get_data(as_text=True), file_id)
    assert "'set-aside'" in acts and "'discard'" not in acts
    client.post('/field/edit', data={'dot_path': 'qubits.q1.T1', 'value': '1.4e-05'})
    response = client.post('/state/recovery/set-aside', json={'id': file_id})
    assert response.status_code == 409
    assert response.get_json()['error_kind'] == 'recovery_changed'
    _body, acts = _box(client.get('/state/review').get_data(as_text=True), file_id)
    assert 'sp-unrec-restore' in acts and '>Discard<' in acts
    assert client.post('/state/recovery/restore', json={'id': file_id}).status_code == 409


def test_set_aside_archives_only_while_holding_the_store_lock(tmp_path, monkeypatch):
    _live, app, client, file_id = _staged_twice(tmp_path)
    from quam_state_manager.core import pending_tray
    real = pending_tray.archive_unrecovered
    locked = []

    def archive(*args):
        locked.append(_ctx(app)['store']._lock._is_owned())
        return real(*args)

    monkeypatch.setattr(pending_tray, 'archive_unrecovered', archive)
    response = client.post('/state/recovery/set-aside', json={'id': file_id})
    assert response.status_code == 200
    assert locked == [True]
    assert pathlib.Path(response.get_json()['kept']).exists()


def test_plain_discard_remains_explicit_even_when_values_are_not_held(tmp_path):
    _live, _app, client, file_id = _staged_twice(tmp_path)
    client.post('/field/edit', data={'dot_path': 'qubits.q1.T1', 'value': '1.4e-05'})
    response = client.post('/state/recovery/discard', json={'id': file_id})
    assert response.status_code == 200
    assert response.get_json()['message'].startswith('Discarded.')


def test_stale_set_aside_refreshes_the_open_panel():
    import subprocess
    source = pathlib.Path(__file__).resolve().parents[1] / 'quam_state_manager/web/static/app.js'
    probe = r"""
const {JSDOM} = require('jsdom'), fs = require('fs');
const w = new JSDOM('<div id="state-review-host"><div class="sp-unrecovered" data-recovery-id="abc"><button>Set aside</button><div class="sp-unrec-msg"></div></div></div>', {runScripts:'outside-only'}).window;
w._syncPanelOpen = () => true; w._placeSyncPanel = () => {};
const calls = [];
w.fetch = (url) => { calls.push(url); return Promise.resolve(url === '/state/review'
    ? {text:()=>Promise.resolve('<button class="sp-unrec-restore">Restore</button>')}
    : {json:()=>Promise.resolve({ok:false,error_kind:'recovery_changed',error:'Review Restore'})}); };
const text = fs.readFileSync(process.argv[1], 'utf8');
const start = text.indexOf('window.SyncPanel = (function () {');
w.eval(text.slice(start, text.indexOf('})();', start) + 5));
const btn = w.document.querySelector('button');
w.SyncPanel.recovery(btn, 'set-aside');
if (calls.length) throw Error('first press must only arm');
btn._armedAt = Date.now()-1000;
w.SyncPanel.recovery(btn, 'set-aside');
setTimeout(() => {
    const refreshed = calls.includes('/state/review') && w.document.querySelector('.sp-unrec-restore');
    console.log(JSON.stringify({refreshed:!!refreshed,calls})); w.close(); process.exit(0);
}, 50);
"""
    result = subprocess.run(['node', '-e', probe, str(source)], capture_output=True,
                            text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    import json
    assert json.loads(result.stdout)['refreshed'], result.stdout
