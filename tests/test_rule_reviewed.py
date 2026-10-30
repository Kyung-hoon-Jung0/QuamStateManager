from copy import deepcopy
from quam_state_manager.core import addition_rules as rules
from quam_state_manager.core.add_names import unordered_key
from tests.test_add_verdict_wording import manifest, rule, spec


def installed():
    s, r = spec(), rule()
    s['qubits'] += ['q4', 'q9']
    s['qubit_pairs'] = [['q3', 'q4']]
    s['lines'] += [{'element': 'q4', 'line': 'flux'}]
    s['populate'] = {'qubit': {'q3': {'RF_freq': 6e9}, 'q4': {'RF_freq': 5e9}}}
    r['rev'] = 1
    r['items'][0].update(role='high', on_choice='auto')
    s['additions'] = rules.expand(s, r, manifest())['pairs'][0]['additions']
    return s, r


def test_review_is_local_and_revision_sensitive():
    s, r = installed()
    s['populate']['qubit']['q4']['RF_freq'] = 7e9
    row = rules.expand(s, r, manifest())['pairs'][0]
    assert row['status'] == 'Needs a look'
    r['reviewed'] = {unordered_key(['q3', 'q4']): row['instance_sig']}
    s['populate']['qubit']['q9'] = {'RF_freq': 9e9}
    assert rules.expand(s, r, manifest())['pairs'][0]['status'] == 'Already has it'
    s['populate']['qubit']['q4']['RF_freq'] = 8e9
    row2 = rules.expand(s, r, manifest())['pairs'][0]
    assert row2['status'] == 'Needs a look'
    r['reviewed'][row2['key']] = row2['instance_sig']
    r['rev'] += 1
    assert rules.expand(s, r, manifest())['pairs'][0]['status'] == 'Needs a look'


def test_expand_is_read_only_and_signs_stale_inputs():
    s, r = installed()
    before = deepcopy(s)
    old = rules.expand(s, r, manifest())
    assert s == before
    s['populate']['qubit']['q9'] = {'RF_freq': 1}
    new = rules.expand(s, r, manifest())
    assert old['rule_sig'] != new['rule_sig']
    assert old['pairs'][0]['instance_sig'] == new['pairs'][0]['instance_sig']
