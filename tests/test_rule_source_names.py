from quam_state_manager.core import addition_rules as rules
from tests.test_rule_reviewed import installed
from tests.test_add_verdict_wording import manifest


def test_source_names_are_preserved_without_edits():
    s, r = installed()
    r['source'] = 'q3-4'
    r['source_names'] = {'high': 'cz_bm_high'}
    pulse, gate = s['additions']
    gate['fields']['flux_pulse_high'] = gate['fields']['flux_pulse_high'].replace(pulse['name'], 'cz_bm_high')
    pulse['name'] = 'cz_bm_high'
    for a in s['additions']:
        a.pop('rule_id')
    row = rules.expand(s, r, manifest())['pairs'][0]
    assert row['status'] == 'Already has it'
    assert not row['diff_vs_current'] and not row['edited']
    s['qubit_pairs'].append(['q1', 'q2'])
    s['populate']['qubit'].update(q1={'RF_freq': 6e9}, q2={'RF_freq': 5e9})
    other = rules.expand(s, r, manifest())['pairs'][1]
    assert other['additions'][0]['name'] == 'cz_bm_high_q1_q2'
