"""Live Edit stress regressions; all instrument writes use a temporary copy."""
import json
import re
from html import unescape

import pytest

from quam_state_manager.web.app import create_app


@pytest.fixture
def rig(tmp_path):
    chip = tmp_path / "chip"
    chip.mkdir()
    state = {"qubits": {"q1": {"id": "q1", "f_01": 5e9,
                                "xy": {"RF_frequency": 5e9}}},
             "active_qubit_names": ["q1"], "qubit_pairs": {}}
    (chip / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (chip / "wiring.json").write_text('{}', encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    c = app.test_client()
    c.post('/load', data={"folder": str(chip)})
    return c, app, chip


def values(c):
    return c.get('/field/peek?dot_path=qubits.q1.f_01&dot_path=qubits.q1.xy.RF_frequency').get_json()['values']


def disk(chip):
    q = json.loads((chip / 'state.json').read_text(encoding='utf-8'))['qubits']['q1']
    return [q['f_01'], q['xy']['RF_frequency']]


def test_cold_partner_staged_applied_and_undone_as_one_gesture(rig):
    c, app, chip = rig
    r = c.post('/field/edit-batch', json={"freq_sync": True, "updates": [
        {"dot_path": "qubits.q1.f_01", "value": "5100000000"}]})
    assert r.status_code == 200 and r.get_json()['ok']
    assert list(values(c).values()) == [5.1e9, 5.1e9]
    ctx = next(iter(app.config['contexts'].values()))
    assert len(ctx['store'].change_log) == 2
    assert len({e.group_id for e in ctx['store'].change_log}) == 1
    assert disk(chip) == [5e9, 5e9]
    assert c.post('/state/apply-to-live').status_code == 200
    assert disk(chip) == [5.1e9, 5.1e9]
    assert c.post('/undo').status_code == 200
    assert list(values(c).values()) == [5e9, 5e9]
    assert disk(chip) == [5e9, 5e9]


def test_unseen_applied_journal_is_refused_with_path(rig):
    c, app, chip = rig
    seen = unescape(re.search(r'data-edit-seq="([^"]*)"', c.get('/state/tray').get_data(as_text=True))[1])
    c.post('/field/edit-batch', json={"updates": [
        {"dot_path": "qubits.q1.f_01", "value": "5100000000"}]})
    c.post('/state/apply-to-live')
    before = values(c)
    r = c.post('/undo', data={"seen_edit_seq": seen})
    assert r.status_code == 409
    assert b'qubits.q1.f_01' in r.data
    assert values(c) == before
    assert disk(chip) == [5.1e9, 5e9]


def test_refused_mirror_rolls_back_primary(rig, monkeypatch):
    c, app, chip = rig
    modifier = next(iter(app.config['contexts'].values()))['modifier']
    real = modifier.set_value
    def refused(path, value, **kwargs):
        if path.endswith('.RF_frequency'):
            raise ValueError('mirror refused')
        return real(path, value, **kwargs)
    monkeypatch.setattr(modifier, 'set_value', refused)
    r = c.post('/field/edit-batch', json={'freq_sync': True, 'updates': [
        {'dot_path': 'qubits.q1.f_01', 'value': '5100000000'}]})
    assert not r.get_json()['ok']
    assert list(values(c).values()) == [5e9, 5e9]
    assert disk(chip) == [5e9, 5e9]


def test_readability_parts_and_labels(rig):
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / 'quam_state_manager/web'
    bulk = (root / 'static/bulk-edit.js').read_text(encoding='utf-8')
    assert 'typed, not staged' in bulk
    assert 'Comparison and range operators work only in the sidebar search.' in bulk
    assert 'staged, not applied' in (root / 'templates/_sync_control.html').read_text(encoding='utf-8')
    assert '{{ _as.flushes }} landed' in (root / 'templates/_auto_sync_pill.html').read_text(encoding='utf-8')
    css = (root / 'static/ui-kit.css').read_text(encoding='utf-8')
    assert re.search(r'\.ui-live-tray > \.auto-sync-wrap\s*\{\s*order: -1;', css)
    assert re.search(r'\.ui-live-cell\s*\{\s*min-width: calc\(var\(--ui-icon-btn\) \* 3\)', css)
    assert re.search(r'\.ui-live-pin\s*\{\s*overflow: hidden;', css)
    assert "'bulk-col-pinned', 'ui-live-pin'" in bulk
    assert 'coldPins.every' in bulk
