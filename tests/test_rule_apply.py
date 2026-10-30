"""Rule apply: only ticked, checked pairs are written; a stale check writes nothing."""
from copy import deepcopy

import pytest

from quam_state_manager.core import addition_rules as rules
from quam_state_manager.core.add_names import unordered_key
from tests.test_add_verdict_wording import manifest, rule, spec


def _setup(**numbers):
    s, r = spec(), rule()
    s["populate"] = {"qubit": {q: {"RF_freq": 5e9 + i * 1e8} for i, q in enumerate(("q1", "q2", "q3"))}}
    r["numbers"].update(numbers)
    return s, r


def _plan(s, r):
    return rules.expand(s, r, manifest())


def test_apply_adds_exactly_the_ticked_ready_pairs():
    s, r = _setup()
    plan = _plan(s, r)
    assert [p["status"] for p in plan["pairs"]] == ["Ready", "Ready"]
    before = deepcopy(s)
    out = rules.apply(s, r, plan["rule_sig"], ["q1-2"], manifest())
    assert s == before
    assert out["added"] == ["q1-2"] and out["skipped"] == []
    adds = out["spec"]["additions"]
    assert adds and all(a["rule_id"] == "r1" for a in adds)
    assert all("q1" in a["target"] or "q2" in a["target"] for a in adds)
    assert not any("q2-3" in a["target"] or "q3" in a["target"] for a in adds)
    assert out["spec"]["addition_rules"][0]["id"] == "r1"


def test_stale_signature_raises_and_changes_nothing():
    s, r = _setup()
    plan = _plan(s, r)
    s["populate"]["qubit"]["q3"] = {"RF_freq": 1}
    before = deepcopy(s)
    with pytest.raises(rules.StaleRule) as e:
        rules.apply(s, r, plan["rule_sig"], ["q1-2"], manifest())
    assert e.value.rule_sig != plan["rule_sig"]
    assert s == before


def test_cant_pair_is_skipped_with_a_reason():
    s, r = _setup()
    r["excluded"] = [unordered_key(["q2", "q3"])]
    plan = _plan(s, r)
    out = rules.apply(s, r, plan["rule_sig"], ["q1-2", "q2-3", "nope"], manifest())
    assert out["added"] == ["q1-2"]
    assert {x["pair"]: x["reason"] for x in out["skipped"]}["q2-3"] == "excluded from this rule"
    assert [x["pair"] for x in out["skipped"]] == ["q2-3", "nope"]


@pytest.mark.parametrize("val", [40, 40.0, 1e-9])
def test_numbers_pass_through_as_given(val):
    s, r = _setup(**{"moving.length": val})
    plan = _plan(s, r)
    out = rules.apply(s, r, plan["rule_sig"], ["q1-2"], manifest())
    got = [a["fields"]["length"] for a in out["spec"]["additions"] if "length" in (a.get("fields") or {})]
    assert got == [val] and type(got[0]) is type(val)
    assert out["rule"]["numbers"]["moving.length"] == val
    assert type(out["rule"]["numbers"]["moving.length"]) is type(val)


def test_rule_is_stored_with_id_and_rev_and_replaced_by_id():
    s, r = _setup()
    r.pop("id")
    r["id"] = "r9"
    s["addition_rules"] = [{"id": "r9", "rev": 3, "gate": {}}, {"id": "other"}]
    plan = _plan(s, r)
    out = rules.apply(s, r, plan["rule_sig"], [], manifest())
    stored = out["spec"]["addition_rules"]
    assert [x["id"] for x in stored] == ["other", "r9"]
    assert stored[1]["rev"] == 1


def test_a_new_rule_without_an_id_applies_with_the_signature_its_check_gave():
    """The Set-all path sends a rule that has no id yet; the check and the
    apply must sign the same rule, and the written instances carry the id the
    apply assigned."""
    s, r = spec(), rule()
    r.pop("id", None)
    plan = rules.expand(s, r, manifest())
    out = rules.apply(s, r, plan["rule_sig"], ["q1-2"], manifest())
    assert out["added"] == ["q1-2"]
    rid = out["rule"]["id"]
    assert rid and all(a["rule_id"] == rid for a in out["spec"]["additions"])


def test_listing_a_pair_the_rule_already_covers_replaces_its_instance():
    """Reset to rule / Apply change send the pair again: the rule's instance
    there is replaced by the re-planned one, never duplicated or skipped."""
    s, r = spec(), rule()
    first = rules.apply(s, r, rules.expand(s, r, manifest())["rule_sig"], ["q1-2"], manifest())
    s2, r2 = first["spec"], first["rule"]
    n = len(s2["additions"])
    r2["numbers"] = dict(r2["numbers"], **{"moving.length": 48})
    again = rules.apply(s2, r2, rules.expand(s2, r2, manifest())["rule_sig"], ["q1-2"], manifest())
    assert again["added"] == ["q1-2"] and len(again["spec"]["additions"]) == n
    pulse = next(a for a in again["spec"]["additions"] if a["kind"] == "pulse")
    assert pulse["fields"]["length"] == 48


def test_new_rule_does_not_adopt_an_unrelated_hand_gate():
    s, r = _setup()
    s['additions'] = deepcopy(_plan(s, r)['pairs'][0]['additions'])
    for a in s['additions']:
        a.pop('rule_id', None)
    r.pop('id')
    row = _plan(s, r)['pairs'][0]
    assert row['status'] == "Can't" and 'already exists' in row['reason']


def test_apply_keeps_per_pair_choice_and_writes_moving_and_default_once():
    s, r = _setup()
    r['items'][0]['on_choice'] = 'auto'
    r['gate']['as_default'] = True
    choices = {'q1-2': 'target'}
    plan = rules.expand(s, r, manifest(), choices=choices)
    out = rules.apply(s, r, plan['rule_sig'], ['q1-2', 'q1-2'], manifest(), choices=choices)
    assert out['added'] == ['q1-2']
    after = out['spec']
    assert after['populate']['pairs']['q1-q2']['moving_qubit'] == 'target'
    default = [a for a in after['additions'] if a['kind'] == 'default_gate']
    assert len(default) == 1 and default[0]['to'] == 'cz_bm'
    row = _plan(after, out['rule'])['pairs'][0]
    assert row['status'] == 'Already has it' and not row['errors']
    assert all(a['choice_here'] == 'target' for a in row['additions'])
    # A stored high/low choice must survive an expand after the dialog closes too.
    s, r = _setup()
    r['items'][0].update(role='high', on_choice='auto')
    r['numbers'] = {'high.amplitude': .12}
    s['populate']['qubit'] = {}
    plan = rules.expand(s, r, manifest(), choices=choices)
    out = rules.apply(s, r, plan['rule_sig'], ['q1-2'], manifest(), choices=choices)
    assert _plan(out['spec'], out['rule'])['pairs'][0]['status'] == 'Already has it'


def test_source_gate_is_adopted_without_changing_saved_names_or_values():
    s, r = _setup()
    s['additions'] = deepcopy(_plan(s, r)['pairs'][0]['additions'])
    pulse, gate = s['additions']
    gate['fields']['flux_pulse_qubit'] = gate['fields']['flux_pulse_qubit'].replace(pulse['name'], 'stored_pulse')
    pulse['name'] = 'stored_pulse'
    for a in s['additions']:
        a.pop('rule_id', None)
        a.pop('copied_value', None)
    before = deepcopy(s)
    r.update(source='q1-2', source_names={'moving': 'stored_pulse'})
    r['numbers'] = {}
    out = rules.apply(s, r, _plan(s, r)['rule_sig'], ['q2-3'], manifest())
    source = rules.bundle(out['spec'], out['rule'], 'q1-2')
    assert all(a['rule_id'] == r['id'] for a in source)
    assert source[0]['name'] == 'stored_pulse' and source[0]['fields'] == before['additions'][0]['fields']
    assert source[0]['copied_value'] == {'length': 40, 'amplitude': .05}
    assert out['rule']['numbers'] == r['numbers'] | {'moving.length': 40, 'moving.amplitude': .05}
    assert _plan(out['spec'], out['rule'])['pairs'][0]['status'] == 'Already has it'
