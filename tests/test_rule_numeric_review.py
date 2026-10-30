"""Rule edits propose numeric deltas; role changes preserve pair calibrations."""
from copy import deepcopy

from quam_state_manager.core import addition_rules as rules
from tests.test_add_verdict_wording import manifest, rule, spec


def install_high():
    s, r = spec(), rule()
    r['rev'] = 1
    r['items'][0].update(role='high', on_choice='auto')
    r['numbers'] = {'high.amplitude': .12, 'high.length': 40}
    s['populate'] = {'qubit': {'q1': {'RF_freq': 6e9}, 'q2': {'RF_freq': 5e9}, 'q3': {'RF_freq': 4e9}}}
    out = rules.apply(s, r, rules.expand(s, r, manifest())['rule_sig'], ['q1-2'], manifest())
    return out['spec'], out['rule']


def test_rule_number_edit_is_an_explicit_old_to_new_proposal():
    s, r = install_high()
    before = deepcopy(s)
    r = deepcopy(r)
    r['rev'] += 1
    r['numbers']['high.length'] = 48
    row = rules.expand(s, r, manifest())['pairs'][0]
    assert s == before
    assert row['status'] == 'Needs a look'
    numeric = [d for d in row['diff_vs_current'] if d['field'] == 'length']
    assert len(numeric) == 1 and numeric[0]['old'] == 40 and numeric[0]['new'] == 48


def test_role_change_moves_the_pairs_calibration_and_preserves_its_copy_baseline():
    s, r = install_high()
    pulse = next(a for a in s['additions'] if a['kind'] == 'pulse')
    pulse['fields']['amplitude'] = .19
    s['populate']['qubit']['q2']['RF_freq'] = 7e9
    plan = rules.expand(s, r, manifest())
    row = plan['pairs'][0]
    planned = next(a for a in row['additions'] if a['kind'] == 'pulse')
    assert planned['target'] == 'qubits.q2.z.operations'
    assert planned['fields']['amplitude'] == .19
    assert planned['copied_value']['amplitude'] == .12
    assert not any(d['field'] == 'amplitude' for d in row['diff_vs_current'])
    assert not row['edited'], 'a role move is not a local class or structure edit'
    out = rules.apply(s, r, plan['rule_sig'], ['q1-2'], manifest())
    pulse = next(a for a in out['spec']['additions'] if a['kind'] == 'pulse')
    assert pulse['fields']['amplitude'] == .19 and pulse['copied_value']['amplitude'] == .12


def test_recalibrated_source_only_supplies_numbers_to_new_instances():
    s, r = install_high()
    r['source'] = 'q1-2'
    source = next(a for a in s['additions'] if a['kind'] == 'pulse')
    r['source_names'] = {'high': source['name']}
    source['fields']['amplitude'] = .31
    plan = rules.expand(s, r, manifest())
    out = rules.apply(s, r, plan['rule_sig'], ['q2-3'], manifest())
    assert out['rule']['numbers']['high.amplitude'] == .12
    other = rules.bundle(out['spec'], out['rule'], 'q2-3')[0]
    assert other['fields']['amplitude'] == .31 and other['copied_value']['amplitude'] == .31
    # The original instance and its calibration remain untouched after a rule review.
    row = rules.expand(out['spec'], out['rule'], manifest())['pairs'][1]
    assert row['status'] == 'Already has it' and not row['diff_vs_current']


def test_numeric_gate_fields_have_review_deltas_too():
    s, r = install_high()
    gate = next(a for a in s['additions'] if a['kind'] == 'macro')
    gate['fields']['phase_shift'] = .1
    r = deepcopy(r)
    r['rev'] += 1
    r['fields'] = {'phase_shift': .2}
    row = rules.expand(s, r, manifest())['pairs'][0]
    delta = [d for d in row['diff_vs_current'] if d['field'] == 'phase_shift']
    assert len(delta) == 1 and delta[0]['old'] == .1 and delta[0]['new'] == .2
