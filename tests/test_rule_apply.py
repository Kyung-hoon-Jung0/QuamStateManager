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
