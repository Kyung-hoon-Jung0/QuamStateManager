"""Version, replacement, stale-view and alias regression pins."""
from pathlib import Path

from quam_state_manager.core import pulse_class_drafts as D
from quam_state_manager.core import pulse_class_watch as W
from quam_state_manager.core import slot_gate_template as S
from tests.test_cz_gate_slot_routes import env, SLOT_ROUTE, NAME
from tests.test_gate_status_routes import rig, _ctx, _merged, _ma, _del_list, ALIAS


def test_slot_file_uses_the_publication_import_fact(env, tmp_path):
    env.make()
    instance = env.a.app.instance_path
    original = D.load_draft(instance, env.scope, NAME)
    why = D.import_check(instance, env.scope, NAME, original)
    assert 'Save to quam_config' in why and 'preview' not in why
    env.save()
    assert D.import_check(instance, env.scope, NAME, original) is None
    assert W.save_to_target(instance, env.scope, NAME, str(tmp_path / 'export.py'))['ok']
    changed = S.render(S.SlotSpec(NAME, ['control'], False)).encode()
    D.save_draft(instance, env.scope, NAME, changed)
    assert D.import_check(instance, env.scope, NAME, changed) is not None


def test_slot_replace_is_a_question_until_yes_and_identical_is_noop(env):
    env.make()
    env.save()
    path = D.draft_path(env.a.app.instance_path, env.scope, NAME)
    before = path.read_bytes()
    body = {'class_name': NAME, 'slots': ['control'], 'spectators': False,
            'before': [{'kind': 'wait', 'length': 16}]}
    r = env.c.post(SLOT_ROUTE, json=body)
    assert r.status_code == 409 and r.json['confirm_replace']
    assert 'replaces its steps' in r.json['error']
    assert path.read_bytes() == before
    assert env.c.post(SLOT_ROUTE, json={**body, 'confirm_replace': True}).status_code == 200
    after = path.read_bytes()
    stamp = path.stat().st_mtime_ns
    assert after != before
    assert env.c.post(SLOT_ROUTE, json=body).status_code == 200
    assert path.read_bytes() == after and path.stat().st_mtime_ns == stamp


def test_activation_delete_restores_alias_and_undo_restores_the_group(rig):
    previous = _merged(rig)['qubit_pairs']['q1-q2']['macros']['cz']
    assert _ma(rig).status_code == 200
    before_delete = _ctx(rig)['store'].get_value('qubit_pairs.q1-q2.macros.cz_sq')
    result = _del_list(rig, 'cz_sq', confirm_active='1')
    assert 'previous gate alias was restored' in result.get_data(as_text=True)
    assert _ctx(rig)['store'].get_value(ALIAS) == previous
    assert rig.post('/undo').status_code == 200
    assert _ctx(rig)['store'].get_value(ALIAS) == '#./cz_sq'
    assert _ctx(rig)['store'].get_value('qubit_pairs.q1-q2.macros.cz_sq') == before_delete


def test_delete_leaves_an_alias_changed_elsewhere(rig):
    assert _ma(rig).status_code == 200
    _ctx(rig)['modifier'].set_value(ALIAS, '#./cz_two')
    assert _del_list(rig, 'cz_sq').status_code == 200
    assert _ctx(rig)['store'].get_value(ALIAS) == '#./cz_two'


def test_delete_without_activation_history_names_the_unset_alias(rig):
    result = _del_list(rig, 'cz_two', confirm_active='1')
    assert 'gate alias is now unset' in result.get_data(as_text=True)


def test_delete_does_not_restore_an_alias_to_a_gate_already_deleted(rig):
    assert _merged(rig)['qubit_pairs']['q1-q2']['macros']['cz'] == '#./cz_two'
    assert _ma(rig).status_code == 200
    assert _del_list(rig, 'cz_two').status_code == 200
    result = _del_list(rig, 'cz_sq', confirm_active='1')
    text = result.get_data(as_text=True)
    assert 'gate alias is now unset' in text and 'previous gate alias was restored' not in text
    assert 'cz' not in _merged(rig)['qubit_pairs']['q1-q2']['macros']


def test_redo_after_an_alias_restoring_delete_is_the_state_after_the_delete(rig):
    import json
    assert _ma(rig).status_code == 200
    assert _del_list(rig, 'cz_sq', confirm_active='1').status_code == 200
    after_delete = json.dumps(_ctx(rig)['store'].state, sort_keys=True)
    assert rig.post('/undo').status_code == 200
    assert json.dumps(_ctx(rig)['store'].state, sort_keys=True) != after_delete
    assert rig.post('/redo').status_code == 200
    assert json.dumps(_ctx(rig)['store'].state, sort_keys=True) == after_delete


def test_missing_pulse_is_a_readable_successful_view(rig):
    path = 'qubit_pairs.q1-q2.macros.cz_sq.flux_pulse_qubit'
    assert _del_list(rig, 'cz_sq').status_code == 200
    result = rig.get('/pulse/detail', query_string={'path': path}, headers={'HX-Request': 'true'})
    assert result.status_code == 200 and result.headers['X-Pulse-Missing'] == '1' and result.headers['HX-Trigger'] == 'pulses-changed'
    assert 'This pulse no longer exists.' in result.get_data(as_text=True)


def test_navigation_controls_use_the_kit_ring_and_names():
    root = Path(__file__).resolve().parents[1] / 'quam_state_manager/web'
    css = (root / 'static/ui-kit.css').read_text(encoding='utf-8')
    html = (root / 'templates/base.html').read_text(encoding='utf-8')
    assert '.ui-btn-icon:focus-visible' in css
    assert 'class="sidebar-toggle ui-btn-icon" aria-label="Collapse navigation"' in html
    assert 'id="pageheader-toggle" class="pageheader-toggle ui-btn-icon"' in html
