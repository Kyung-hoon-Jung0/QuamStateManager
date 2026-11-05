import copy

import pytest

from quam_state_manager.core.regen_merge import merge_states


def pair(c, t):
    return {'qubit_control': '#/qubits/' + c,
            'qubit_target': '#/qubits/' + t,
            'macros': {'cz_SNZ': {'length': 120}}}


def walk(node):
    if isinstance(node, dict):
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)
    elif isinstance(node, str):
        yield node


@pytest.mark.parametrize('scheme', ['long', 'short', 'mixed'])
@pytest.mark.parametrize('alias_shape', ['relative', 'absolute'])
def test_appended_pair_keys_and_references(scheme, alias_shape):
    long_source = scheme == 'long'
    old_id = 'qX2-qX1' if long_source else 'qX2-X1'
    old_pair = pair('qX2', 'qX1')
    old_pair['macros']['cz'] = ('#./cz_SNZ' if alias_shape == 'relative'
                                else f'#/qubit_pairs/{old_id}/macros/cz_SNZ')
    old = {'qubits': {'qX1': {}, 'qX2': {}}, 'qubit_pairs': {old_id: old_pair}}
    if scheme == 'mixed':
        old['qubits']['qX0'] = {}
        old['qubit_pairs']['qX1-qX0'] = pair('qX1', 'qX0')
    new = copy.deepcopy(old)
    new['qubits']['qX3'] = {}
    new['qubit_pairs']['qX3-X2'] = pair('qX3', 'qX2')
    new['qubit_pairs']['qX3-X2']['id'] = 'qX3-X2'
    new['extras'] = {'pair': '#/qubit_pairs/qX3-X2/macros/cz_SNZ',
                     'path': 'qubit_pairs.qX3-X2.macros.cz_SNZ'}
    wiring = {'wiring': {'qubit_pairs': {'qX3-X2': {'control': '#/qubits/qX3'}}}}
    before = copy.deepcopy(old)
    result = merge_states(old, new, new_wiring=wiring)
    expected = 'qX3-qX2' if long_source else 'qX3-X2'
    assert expected in result.merged['qubit_pairs']
    assert result.merged['qubit_pairs'][expected]['id'] == expected
    assert result.merged['qubit_pairs'][old_id] == before['qubit_pairs'][old_id]
    assert old == before
    assert expected in wiring['wiring']['qubit_pairs']
    if long_source:
        alias = ('#./cz_SNZ' if alias_shape == 'relative'
                 else f'#/qubit_pairs/{expected}/macros/cz_SNZ')
        assert result.merged['qubit_pairs'][expected]['macros']['cz'] == alias
    doc = dict(result.merged, **wiring)
    for value in walk(doc):
        if value.startswith('#/'):
            node = doc
            for part in value[2:].split('/'):
                node = node[part]
    assert result.merged['extras']['path'] == f'qubit_pairs.{expected}.macros.cz_SNZ'
