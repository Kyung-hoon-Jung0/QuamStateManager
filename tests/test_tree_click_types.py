import json
import pytest
from quam_state_manager.web.app import create_app
from quam_state_manager.core import type_policy


def kind(value):
    if value is None:
        return 'null'
    return {list: 'list', dict: 'dict', str: 'string', float: 'float', int: 'int'}[type(value)]


@pytest.fixture
def env(tmp_path, old):
    live = tmp_path / 'live'
    live.mkdir()
    (live / 'state.json').write_text(json.dumps({'qubits': {'q1': {'extras': {'value': old}}}}))
    (live / 'wiring.json').write_text('{}')
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    expr = {'null': 'str', 'string': 'str', 'float': 'real'}.get(kind(old), kind(old))
    type_policy.save_assignment(app.instance_path, live, 'qubits.q1.extras.value', {'type': expr, 'override_env': True})
    c = app.test_client()
    assert c.post('/load', data={'folder': str(live)}).status_code in (200, 302)
    ctx = next(iter(app.config['contexts'].values()))
    return c, ctx


@pytest.mark.parametrize('old,new', [
    (None, [1, 2]), (None, 1.5), (1.5, None), ('abc', 3), (3, '3'),
    (None, 3), (None, 'abc'), ([1], 3), ({'a': 1}, 3), (3, {'a': 1}),
])
@pytest.mark.parametrize('door', ['field', 'json'])
def test_tree_type_change_and_undo(env, old, new, door):
    c, ctx = env
    path = 'qubits.q1.extras.value'
    if door == 'field':
        r = c.post('/field/edit', data={'dot_path': path, 'value': json.dumps(new), 'tree_kind_change': '1'})
    else:
        opened = c.get('/tree/json-edit/source', query_string={'path': path}).get_json()
        r = c.post('/tree/json-edit/save', json={'path': path, 'tab': 'state', 'text': json.dumps(new), 'base_seq': opened['mutation_seq'], 'tree_kind_change': True})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()['ok'], r.get_json()
    if door == 'field':
        assert r.get_json()['type_change'] == kind(old) + ' \u2192 ' + kind(new)
    stored = ctx['store'].get_value(path)
    assert type(stored) is type(new) and stored == new
    peek = c.get('/field/peek', query_string={'dot_path': path}).get_json()['values'][path]
    assert type(peek) is type(new) and peek == new
    assert c.post('/undo').status_code == 200
    restored = ctx['store'].get_value(path)
    assert type(restored) is type(old) and restored == old


@pytest.fixture
def plain(tmp_path):
    live = tmp_path / 'live'
    live.mkdir()
    (live / 'state.json').write_text(json.dumps({'qubits': {'q1': {'f': 4.5, 'label': 'abc'}}}))
    (live / 'wiring.json').write_text('{}')
    app = create_app(testing=True, instance_path=str(tmp_path / 'instance'))
    c = app.test_client()
    assert c.post('/load', data={'folder': str(live)}).status_code in (200, 302)
    return c, next(iter(app.config['contexts'].values())), app



@pytest.mark.parametrize('door', ['field', 'json'])
def test_an_integral_number_over_a_float_stays_a_float(plain, door):
    c, ctx, app = plain
    path = 'qubits.q1.f'
    if door == 'field':
        r = c.post('/field/edit', data={'dot_path': path, 'value': '5', 'tree_kind_change': '1'})
    else:
        opened = c.get('/tree/json-edit/source', query_string={'path': path}).get_json()
        r = c.post('/tree/json-edit/save', json={'path': path, 'tab': 'state', 'text': '5', 'base_seq': opened['mutation_seq'], 'tree_kind_change': True})
    assert r.status_code == 200 and r.get_json()['ok'], r.get_json()
    assert 'type_change' not in r.get_json()
    v = ctx['store'].get_value(path)
    assert type(v) is float and v == 5.0


def test_unquoted_text_over_a_number_is_refused_not_a_string(plain):
    c, ctx, app = plain
    r = c.post('/field/edit', data={'dot_path': 'qubits.q1.f', 'value': '12abc', 'tree_kind_change': '1'})
    assert r.status_code >= 400 or not r.get_json().get('ok')
    assert ctx['store'].get_value('qubits.q1.f') == 4.5


def test_an_env_typed_kind_change_says_load_may_refuse(plain, monkeypatch):
    c, ctx, app = plain
    pol = ctx['store'].type_policy
    cls = type(pol)
    orig = cls.expected_for

    def fake(self, merged, dot_path, current_value=None, *, infer=True):
        a = self.assignments.get(dot_path) or {}
        if dot_path == 'qubits.q1.label' and not a.get('override_env'):
            return type_policy.Expected(spec=type_policy.parse_type('str'), source='env', class_path='X', field='label')
        return orig(self, merged, dot_path, current_value, infer=infer)
    monkeypatch.setattr(cls, 'expected_for', fake)
    r = c.post('/field/edit', data={'dot_path': 'qubits.q1.label', 'value': '[1, 2]', 'tree_kind_change': '1'})
    assert r.status_code == 200, r.get_json()
    assert 'Quam.load() may refuse' in r.get_json()['type_change']
