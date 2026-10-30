"""The rule apply door is a person's; expand stays open to agents."""
import pytest

from quam_state_manager.web import callers
from quam_state_manager.web.app import create_app
from tests.test_add_verdict_wording import rule, spec


@pytest.fixture
def proving(tmp_path):
    a = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    a.config["PROVE_CALLERS"] = True
    return a


def _person(app, agent=False):
    cl = app.test_client()
    cl.set_cookie("sm_person", app.config["SM_PERSON_SECRET"], domain="localhost")
    return cl, ({"X-SM-Agent": "claude"} if agent else {})


def test_registered_as_person_only():
    assert "main.generate_rules_apply" in callers.PERSON_ONLY
    assert "main.generate_rules_expand" not in callers.PERSON_ONLY


def test_agent_header_refused_on_apply_but_expand_is_open(proving):
    cl, h = _person(proving, agent=True)
    body = {"spec": spec(), "rule": rule(), "rule_sig": "x", "pairs": []}
    assert cl.post("/generate/additions/rules/apply", json=body, headers=h).status_code == 403
    assert cl.post("/generate/additions/rules/expand", json=body, headers=h).status_code == 200


def test_no_proof_refused_and_a_person_gets_409_for_a_stale_sig(proving):
    body = {"spec": spec(), "rule": rule(), "rule_sig": "stale", "pairs": ["q1-2"]}
    assert proving.test_client().post("/generate/additions/rules/apply", json=body).status_code == 403
    cl, h = _person(proving)
    r = cl.post("/generate/additions/rules/apply", json=body, headers=h)
    assert r.status_code == 409
    j = r.get_json()
    assert j["ok"] is False and j["rule_sig"] and "check again" in j["error"]


def test_a_person_applies_with_the_current_sig(proving):
    cl, h = _person(proving)
    body = {"spec": spec(), "rule": rule(), "pairs": ["q1-2"]}
    sig = cl.post("/generate/additions/rules/expand", json=body, headers=h).get_json()["rule_sig"]
    r = cl.post("/generate/additions/rules/apply", json=dict(body, rule_sig=sig), headers=h)
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["rule"]["id"] == "r1"
