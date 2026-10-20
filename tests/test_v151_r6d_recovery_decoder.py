"""Listing and Restore accept exactly the same recovery row encoding."""
import pytest

from quam_state_manager.core import pending_tray
from quam_state_manager.web import routes
from tests.test_v151_r6_recovery_held import _chip, _open, _ctx, _checkpoint, _row, _box


@pytest.mark.parametrize('source', ['not_a_file', None, 3])
def test_invalid_source_cannot_settle_a_matching_checkpoint(tmp_path, source):
    instance, live = _chip(tmp_path)
    app, client = _open(instance, live)
    row = _row('qubits.q1.T1', 0, 1e-5)
    row['source_file'] = source
    file_id = _checkpoint(app, [row])
    with app.test_request_context():
        listing = next(d for d in routes._tray_unrecovered() if d['id'] == file_id)
    assert listing['restorable'] is False
    assert listing['settled'] is False and listing['pending'] == 1
    assert 'row names no source file' in listing['reason']
    panel = client.get('/state/review').get_data(as_text=True)
    body, acts = _box(panel, file_id)
    assert '1 staged edit not recovered' in body and 'row names no source file' in body
    assert '>Set aside<' not in acts
    assert 'sync-marker-unrecovered' in client.get('/state/tray').get_data(as_text=True)
    assert client.post('/state/recovery/restore', json={'id': file_id}).status_code == 409
    assert client.post('/state/recovery/set-aside', json={'id': file_id}).status_code == 409
    assert pending_tray.unrecovered_file(_ctx(app)['working_copy'], file_id) is not None


def test_listing_accepts_metadata_that_restore_normalizes(tmp_path):
    instance, live = _chip(tmp_path)
    app, _client = _open(instance, live)
    row = _row('qubits.q1.T1', 0, 1e-5)
    row.update(actor=3, group_id=4, origin='invalid', future_metadata=True)
    file_id = _checkpoint(app, [row])
    wc = _ctx(app)['working_copy']
    decoded = pending_tray.read_unrecovered_rows(wc, file_id)
    assert decoded[0].actor == 'human' and decoded[0].group_id is None
    assert decoded[0].origin is None
    with app.test_request_context():
        listing = next(d for d in routes._tray_unrecovered() if d['id'] == file_id)
    assert listing['restorable'] is True and listing['settled'] is True
