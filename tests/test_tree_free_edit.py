"""Stage A behavior pins against shipped JS, with executable mutations."""
import os
import subprocess
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]

def run(mutation=None):
    env = dict(os.environ)
    if mutation:
        env["TREE_MUTATION"] = mutation
    return subprocess.run(["node", "tests/tree_free_edit_selfcheck.cjs"], cwd=ROOT,
                          env=env, capture_output=True, text=True, encoding="utf-8", timeout=45)

def test_tree_free_edit():
    r = run()
    assert r.returncode == 0 and "ALL OK" in r.stdout, r.stdout + r.stderr

@pytest.mark.parametrize("mutation", ["auto_blur", "auto_hidden", "auto_pagehide", "auto_nav", "pointer_layout", "pointer_cached", "draft_jump", "sync_snapshot", "suggestion_flow", "suggestion_outside", "press_mount", "pointer_hint", "pointer_toast", "pointer_flash", "idle_pointer", "draft_count", "input_width", "input_literal", "input_echo", "sync_count", "draft_style", "relink", "echo_target", "draft_storage", "unload_drafts", "refusal_clear", "lazy_resume", "nav_drafts", "one", "escape", "caret", "flight", "cache", "visible", "tab", "undo", "text_undo", "global", "brackets", "multi", "search", "off", "render", "pane", "fsp", "fsp_cancel", "refusal", "peek_retry", "tooltip", "pointer", "chip_context", "stale_token", "source", "source_key", "display_order"])
def test_tree_free_edit_mutations(mutation):
    r = run(mutation)
    assert "mutation target exists" not in r.stderr, r.stderr
    assert r.returncode != 0 and "AssertionError" in r.stderr, r.stdout + r.stderr


def run_find(mutation=None, server=None):
    env = dict(os.environ)
    if mutation:
        env['FIND_MUTATION'] = mutation
    if server:
        env['FIND_SERVER'] = server
    return subprocess.run(['node', 'tests/tree_find_selfcheck.cjs'], cwd=ROOT,
                          env=env, capture_output=True, text=True, encoding='utf-8', timeout=60)


def test_tree_find():
    result = run_find()
    assert result.returncode == 0 and 'ALL OK' in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize('mutation', [
    'find_open', 'find_mac', 'find_model', 'find_cap', 'find_case', 'find_word',
    'find_regex', 'find_keys', 'find_preview', 'find_group', 'find_one',
    'find_occurrence', 'find_skip', 'find_entity', 'find_typing', 'find_all',
    'find_meta', 'find_path', 'find_reset', 'find_key_selection', 'find_paging',
    'find_race', 'find_correct', 'find_rename', 'find_existing', 'find_inline', 'find_enter', 'find_close_query', 'find_tab', 'find_late_inline', 'find_current', 'find_search_chord', 'find_reopen', 'find_skipped_open',
    # review findings D-36..D-41 and the client half of expect_seq
    'find_debounce', 'find_worker', 'find_budget', 'find_slow_msg', 'find_cancel', 'find_phrase',
    'find_literal_rep', 'find_whole_number', 'find_review', 'find_review_more', 'find_expect_seq',
    'find_advance', 'find_label', 'chord_palette', 'chord_stop', 'chord_expiry', 'chord_other_key',
    'empty_refuse', 'empty_confirm', 'mac_code', 'multi_label', 'multi_full_old',
])
def test_tree_find_mutations(mutation):
    result = run_find(mutation)
    assert 'mutation target exists' not in result.stderr, result.stderr
    assert result.returncode != 0 and 'AssertionError' in result.stderr, result.stdout + result.stderr


def test_tree_find_server_undo(tmp_path):
    """The shipped key handler undoes the real replacement group."""
    import json
    import threading
    from werkzeug.serving import make_server
    from quam_state_manager.web.app import create_app

    folder = tmp_path / 'state'
    folder.mkdir()
    state = {
        'items': {'first': {'amplitude': 1, 'note': 'same'},
                  'second': {'amplitude': 2, 'note': 'same'},
                  'third': {'amplitude': 1, 'note': 'other'}},
        'text': 'cat scatter CAT', 'literal': 'a.b',
        'pointer': '#/items/first/amplitude', 'id': 'same', 'full_scale_power_dbm': 1,
        'samples': {f'group_{i}': {'value': 'needle'} for i in range(200)},
    }
    (folder / 'state.json').write_text(json.dumps(state))
    (folder / 'wiring.json').write_text('{}')
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    with app.test_client() as client:
        client.post('/load', data={'folder': str(folder)})
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = run_find(server=f'http://127.0.0.1:{server.server_port}')
        assert result.returncode == 0 and 'ALL OK' in result.stdout, result.stdout + result.stderr
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@pytest.mark.parametrize('mutation', [None, 'preview_pointer', 'preview_readonly', 'preview_fsp', 'preview_type', 'preview_key'])
def test_replace_preview_policy(tmp_path, mutation):
    """Refusal pins run against the real server and executable route mutations."""
    import inspect
    import json
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes

    folder = tmp_path / 'state'
    folder.mkdir()
    state = {'settings': {'real': 1.0, 'other': 2.0, 'id': 'fixed',
                          'pointer': '#/settings/real', 'full_scale_power_dbm': 0},
             'qubits': {'item': {'xy': {'operations': {'old': {'amplitude': 0.2}}}}}}
    (folder / 'state.json').write_text(json.dumps(state))
    (folder / 'wiring.json').write_text('{}')
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    client = app.test_client()
    client.post('/load', data={'folder': str(folder)})
    if mutation:
        source = inspect.getsource(routes.field_replace_preview)
        source = source[source.index('def field_replace_preview'):]
        before, after = {
            'preview_pointer': ('if isinstance(old, str) and old.startswith', 'if False and isinstance(old, str) and old.startswith'),
            'preview_readonly': ('if reason:\n                    raise ValueError(reason)', 'if False:\n                    raise ValueError(reason)'),
            'preview_key': ('if _pulse_is_renamable(path):', 'if False:'),
            'preview_fsp': ('if path.endswith("full_scale_power_dbm"):', 'if False:'),
            'preview_type': ('modifier._checked_value(path, old, parsed, coerce=True, enforce=True)', 'parsed'),
        }[mutation]
        assert before in source
        namespace = dict(vars(routes))
        exec(compile(source.replace(before, after), '<preview mutation>', 'exec'), namespace)
        app.view_functions['main.field_replace_preview'] = namespace['field_replace_preview']

    def pin():
        updates = [{'dot_path': 'settings.' + key, 'value': value}
                   for key, value in [('real', '5.0e9'), ('other', 'invalid'),
                                      ('pointer', 'replacement'), ('id', 'replacement'),
                                      ('full_scale_power_dbm', '1')]]
        updates.append({'dot_path': 'qubits.item.xy.operations.old', 'value': 'new', 'key': True})
        response = client.post('/field/replace-preview', json={'updates': updates})
        assert response.status_code == 200
        rows = {r['dot_path'].split('.')[-1]: r for r in response.json['results']}
        assert rows['old'].get('rename') == {'path': 'qubits.item.xy.operations.old', 'new_name': 'new', 'retarget': '1'}
        assert rows['real']['new_value'] == 5.0e9
        assert isinstance(rows['real']['new_value'], float), 'real remains real'
        for key, reason in [('other', 'coerce'), ('pointer', 'pointer'), ('id', 'read-only'), ('full_scale_power_dbm', 'FSP')]:
            assert reason.lower() in rows[key].get('skip', '').lower(), (key, rows[key])
        assert client.get('/field/peek?dot_path=settings.real').json['values']['settings.real'] == 1.0
        accepted = [r for r in response.json['results'] if not r.get('skip')]
        reply = client.post('/field/edit-batch', json={'group': 'new', 'expect_seq': response.json['mutation_seq'],
                            'updates': [{'dot_path': r['dot_path'], 'value': r['value']} for r in accepted]})
        assert reply.json['ok']
        assert client.get('/field/peek?dot_path=settings.real').json['values']['settings.real'] == 5.0e9
        stale = client.post('/field/edit-batch', json={'expect_seq': response.json['mutation_seq'],
                            'updates': [{'dot_path': 'settings.real', 'value': '7'}]})
        assert stale.status_code == 409 and not stale.json['ok']

    if mutation:
        with pytest.raises(AssertionError):
            pin()
    else:
        pin()


@pytest.mark.parametrize('mutation', [None, 'undo_header'])
def test_large_replacement_undo_headers(tmp_path, monkeypatch, mutation):
    import json
    from quam_state_manager.web.app import create_app
    from quam_state_manager.web import routes

    folder = tmp_path / 'state'
    folder.mkdir()
    original = {f'value_{i}': 1.0 for i in range(151)}
    (folder / 'state.json').write_text(json.dumps({'settings': original}))
    (folder / 'wiring.json').write_text('{}')
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    client = app.test_client()
    client.post('/load', data={'folder': str(folder)})
    if mutation:
        monkeypatch.setattr(routes, '_HEADER_PATCH_CAP', 1000000)

    def pin():
        updates = [{'dot_path': 'settings.' + key, 'value': '2.0'} for key in original]
        assert client.post('/field/edit-batch', json={'updates': updates, 'group': 'new'}).json['ok']
        for door, expected in [('/undo', 1.0), ('/redo', 2.0)]:
            response = client.post(door)
            trigger = json.loads(response.headers['HX-Trigger'])['cellsReverted']
            assert trigger.get('structural') and not trigger['entries'], 'large group uses bounded response'
            assert len(response.headers['HX-Trigger']) < 4096
            values = client.get('/field/peek', query_string=[('dot_path', u['dot_path']) for u in updates]).json['values']
            assert len(values) == 151 and all(v == expected for v in values.values())

    if mutation:
        with pytest.raises(AssertionError):
            pin()
    else:
        pin()


def test_pointer_server_paths(tmp_path):
    """A relink edits the file string; a plain number retains value mode."""
    import json
    from quam_state_manager.web.app import create_app

    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps({
        "qubits": {"q1": {"resonator": {"operations": {
            "readout": {"amplitude": 0.2},
            "other": {"amplitude": 0.4},
            "alias": {"amplitude": "#/qubits/q1/resonator/operations/readout/amplitude"},
            "sibling": {"amplitude": "#/qubits/q1/resonator/operations/readout/amplitude"},
        }}}}}))
    (folder / "wiring.json").write_text('{}')
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    client.post('/load', data={'folder': str(folder)})
    prefix = 'qubits.q1.resonator.operations.'
    alias, target, other = [prefix + p + '.amplitude' for p in ('alias', 'readout', 'other')]
    sharing = client.get('/field/peek', query_string={'dot_path': alias}).json['resolved'][alias]
    assert sharing['resolved_path'] == target
    assert sharing['shared_by'] == ['sibling']
    pointer = '#/' + other.replace('.', '/')
    reply = client.post('/field/edit', data={
        'dot_path': alias, 'value': pointer, 'value_quoted': '1', 'pointer_mode': 'relink'})
    assert reply.status_code == 200, reply.json
    values = client.get('/field/peek', query_string=[('dot_path', p) for p in (alias, target, other)]).json['values']
    assert values == {alias: pointer, target: 0.2, other: 0.4}
    assert reply.json['stored'] == values[alias]
    reply = client.post('/field/edit', data={'dot_path': alias, 'value': '0.6'})
    assert reply.status_code == 200, reply.json
    values = client.get('/field/peek', query_string=[('dot_path', p) for p in (alias, target, other)]).json['values']
    assert values == {alias: pointer, target: 0.2, other: 0.6}
    assert reply.json['resolved_path'] == other
    assert reply.json['raw_value'] == pointer


def test_commit_on_leave_default_is_versioned(tmp_path):
    """Auto-save defaults ON; a pre-version file does not hold a choice
    against that default, an OFF saved now is respected."""
    from quam_state_manager.web.app import create_app
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    (tmp_path / "instance").mkdir(exist_ok=True)
    (tmp_path / "instance" / "tree_edit.json").write_text('{"commit_on_leave": false}')
    assert client.get('/settings/tree-edit').json['commit_on_leave'] is True
    assert client.post('/settings/tree-edit', data={'commit_on_leave': '0'}).json['commit_on_leave'] is False
    assert client.get('/settings/tree-edit').json['commit_on_leave'] is False
    assert client.post('/settings/tree-edit', data={'commit_on_leave': '1'}).json['commit_on_leave'] is True
    assert client.get('/settings/tree-edit').json['commit_on_leave'] is True
