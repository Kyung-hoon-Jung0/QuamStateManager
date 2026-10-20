"""Recovery counts and provenance follow the ordered staging plan."""
import json

from quam_state_manager.core import pending_tray
from quam_state_manager.core.loader import ChangeEntry, QuamStore
from tests.test_v151_r6_recovery_held import _chip, _open, _ctx, _checkpoint, _row, _box


def test_reversing_rows_are_both_pending_in_the_ordered_plan():
    store = QuamStore.from_dicts({'x': 0}, {})
    rows = [ChangeEntry(dot_path='x', old_value=0, new_value=1, source_file='state'),
            ChangeEntry(dot_path='x', old_value=1, new_value=0, source_file='state')]
    assert [p['status'] for p in pending_tray.restore_plan(store, rows)] == ['ok', 'ok']
    assert pending_tray.held_rows(store, rows) == [None, None]
    listing = pending_tray.with_held(store, [{'restorable': True, 'rows': [
        {'path': 'x', 'old': 0, 'new': 1}, {'path': 'x', 'old': 1, 'new': 0}]}])[0]
    assert listing['pending'] == 2 and not listing['settled']


def test_ancestor_deletion_is_held_in_the_tray_not_on_the_live_chip(tmp_path):
    instance, live = _chip(tmp_path)
    app, client = _open(instance, live)
    _ctx(app)['modifier'].delete_subtree('qubits.q1.z')
    row = _row('qubits.q1.z.joint_offset', .08, None)
    row['deleted'] = True
    file_id = _checkpoint(app, [row])
    body, _acts = _box(client.get('/state/review').get_data(as_text=True), file_id)
    assert 'already in the tray' in body
    response = client.post('/state/recovery/restore', json={'id': file_id})
    assert 'already in the tray' in response.get_json()['message']
    assert 'live chip already holds' not in response.get_json()['message']
    assert json.loads((live / 'state.json').read_text())['qubits']['q1']['z']['joint_offset'] == .08


def test_same_after_a_recovered_creation_does_not_claim_live_provenance(tmp_path):
    instance, live = _chip(tmp_path)
    app, client = _open(instance, live)
    rows = [_row('qubits.q1.T2echo', None, 1e-5), _row('qubits.q1.T2echo', 1e-5, 1e-5)]
    rows[0]['created'] = True
    file_id = _checkpoint(app, rows)
    body, _acts = _box(client.get('/state/review').get_data(as_text=True), file_id)
    assert 'already on the chip' not in body
    assert '1 staged edit not recovered' in body
    response = client.post('/state/recovery/restore', json={'id': file_id})
    message = response.get_json()['message']
    assert 'Restored 1 edit' in message
    assert 'already on the live chip' not in message
    assert 'already in the tray' not in message
    assert '1 matched an earlier restored edit' in message
    assert 'T2echo' not in json.loads((live / 'state.json').read_text())['qubits']['q1']
