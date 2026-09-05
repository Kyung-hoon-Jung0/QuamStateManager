"""Telling SM an environment finding is expected (docs/168).

Customer, 2026-09-05: *"SM says the type is wrong. It cannot know that I
introduced this key on purpose — but I must be able to tell it, and after that
the check should pass it as healthy."*

Measured on their 20-qubit chip with a real probe of the `cqt` env: four
error-severity findings covering 24 places, a red banner on every page load,
and the only action offered on any of them was "Go to field".

What these pin is the SHAPE of the answer, because that is where it can go
wrong quietly:

  * an acknowledgement is keyed by the finding's OWN identity, not by
    ``class.field`` — two different findings can share a field, and one
    acknowledgement must never silence the other (this was the CRITICAL an
    adversarial review found in the first design);
  * it stops the finding being COUNTED, and changes nothing else: same
    severity, same row, same text, still in ``total``;
  * it is scoped to the environment it was made in;
  * it lapses when the finding stops saying what was acknowledged;
  * and it can be taken back.
"""

from __future__ import annotations

import json

import pytest

from quam_state_manager.core import diagnostics, env_ack, state_env_validate
from quam_state_manager.web.app import create_app

# Two DIFFERENT findings that share a class AND a field. Under a class.field
# key these collide; under the finding's own identity they do not.
_ANALYSIS = {
    "findings": [
        {"kind": "unknown_field", "class": "quam_config.my_quam.Quam",
         "field": "qdac", "code": "", "severity": "error",
         "detail": "this environment does not declare it", "count": 1,
         "example_paths": ["qdac"]},
        {"kind": "missing_required", "class": "quam_config.my_quam.Quam",
         "field": "qdac", "code": "", "severity": "error",
         "detail": "a required field is absent", "count": 1,
         "example_paths": ["qdac"]},
        # same kind, same class, same field -- only the CODE differs. The
        # analyzer aggregates on the code too, so it is part of the identity.
        {"kind": "type_mismatch", "class": "quam_config.my_quam.Quam",
         "field": "qdac", "code": "wrong_base", "severity": "warning",
         "detail": "expected dict, got str", "count": 1,
         "example_paths": ["qdac"]},
        {"kind": "type_mismatch", "class": "quam_config.my_quam.Quam",
         "field": "qdac", "code": "wrong_elem", "severity": "warning",
         "detail": "expected int elements, got str", "count": 1,
         "example_paths": ["qdac"]},
    ],
    "summary": {"errors": 2, "warnings": 2},
}
_ENV = "quam-0.6.0__qb-0.4.0__test"


def _key(kind):
    return env_ack.finding_key(kind, "quam_config.my_quam.Quam", "qdac", "")


class TestTheKeyIsTheFindingNotTheField:
    def test_two_findings_on_one_field_have_different_keys(self):
        assert _key("unknown_field") != _key("missing_required")

    def test_two_findings_differing_only_by_code_have_different_keys(self):
        """`analyze_state` aggregates on (kind, class, field, code). Two
        type_mismatch rows on one field differ ONLY by the code, so a key that
        drops it would let one acknowledgement silence the other."""
        a = env_ack.finding_key("type_mismatch", "C", "f", "wrong_base")
        b = env_ack.finding_key("type_mismatch", "C", "f", "wrong_elem")
        assert a != b

    def test_acknowledging_one_code_leaves_the_other_code_alone(self, tmp_path):
        env_ack.acknowledge(tmp_path, _ENV, kind="type_mismatch",
                            class_path="quam_config.my_quam.Quam", field="qdac",
                            code="wrong_base", detail="expected dict, got str")
        out = state_env_validate.to_diag_findings(
            _ANALYSIS, acknowledged=env_ack.resolve(tmp_path, _ENV))
        acked = [f for f in out if f.acknowledged]
        assert len(acked) == 1, [f.ack_key for f in acked]
        assert acked[0].ack_key.endswith("wrong_base")

    def test_acknowledging_one_leaves_the_other_alone(self, tmp_path):
        env_ack.acknowledge(tmp_path, _ENV, kind="unknown_field",
                            class_path="quam_config.my_quam.Quam", field="qdac",
                            code="", detail="this environment does not declare it")
        out = state_env_validate.to_diag_findings(
            _ANALYSIS, acknowledged=env_ack.resolve(tmp_path, _ENV))
        by_cat = {f.category: bool(f.acknowledged) for f in out}
        assert by_cat["env_unknown_field"] is True
        assert by_cat["env_missing_required"] is False


class TestWhatItChangesAndWhatItDoesNot:
    @pytest.fixture
    def acked(self, tmp_path):
        env_ack.acknowledge(tmp_path, _ENV, kind="unknown_field",
                            class_path="quam_config.my_quam.Quam", field="qdac",
                            code="", detail="this environment does not declare it")
        return state_env_validate.to_diag_findings(
            _ANALYSIS, acknowledged=env_ack.resolve(tmp_path, _ENV))

    def test_it_stops_counting_as_an_issue(self, acked):
        plain = state_env_validate.to_diag_findings(_ANALYSIS)
        before, after = diagnostics.summarize(plain), diagnostics.summarize(acked)
        assert before["issues"] == 4
        assert after["issues"] == 3
        assert after["acknowledged"] == 1

    def test_it_does_not_lower_the_severity(self, acked):
        f = next(x for x in acked if x.category == "env_unknown_field")
        assert f.severity == "error", "the fact keeps its weight; only the alarm stops"

    def test_nothing_is_hidden(self, acked):
        plain = state_env_validate.to_diag_findings(_ANALYSIS)
        assert len(acked) == len(plain)
        assert diagnostics.summarize(acked)["total"] == 4

    def test_the_row_says_so(self, acked):
        f = next(x for x in acked if x.category == "env_unknown_field")
        assert "acknowledged by you" in f.detail

    def test_it_reaches_the_client(self, acked):
        d = next(x for x in acked if x.category == "env_unknown_field").as_dict()
        assert d["acknowledged"] and d["ack_key"]


class TestItIsScopedAndRevocable:
    def test_another_environment_does_not_inherit_it(self, tmp_path):
        env_ack.acknowledge(tmp_path, _ENV, kind="unknown_field",
                            class_path="quam_config.my_quam.Quam", field="qdac",
                            code="", detail="x")
        assert env_ack.resolve(tmp_path, "quam-9.9.9__other") == {}

    def test_an_unknown_environment_resolves_to_nothing(self, tmp_path):
        env_ack.acknowledge(tmp_path, _ENV, kind="unknown_field",
                            class_path="c", field="f", code="", detail="x")
        assert env_ack.resolve(tmp_path, "") == {}

    def test_revoking_brings_it_back(self, tmp_path):
        env_ack.acknowledge(tmp_path, _ENV, kind="unknown_field",
                            class_path="quam_config.my_quam.Quam", field="qdac",
                            code="", detail="this environment does not declare it")
        assert env_ack.revoke(tmp_path, _ENV, _key("unknown_field")) is True
        out = state_env_validate.to_diag_findings(
            _ANALYSIS, acknowledged=env_ack.resolve(tmp_path, _ENV))
        assert diagnostics.summarize(out)["issues"] == 4

    def test_revoking_something_absent_says_so(self, tmp_path):
        assert env_ack.revoke(tmp_path, _ENV, "nope") is False


class TestSilenceCannotOutliveItsSubject:
    def test_a_changed_detail_lapses_it(self, tmp_path):
        env_ack.acknowledge(tmp_path, _ENV, kind="unknown_field",
                            class_path="quam_config.my_quam.Quam", field="qdac",
                            code="", detail="an OLDER sentence")
        out = state_env_validate.to_diag_findings(
            _ANALYSIS, acknowledged=env_ack.resolve(tmp_path, _ENV))
        assert not any(f.acknowledged for f in out), (
            "the user agreed to a sentence; if the finding now says something "
            "else, the agreement is about something that is no longer there")

    def test_an_unreadable_store_shows_the_findings(self, tmp_path):
        (tmp_path / env_ack.ACKS_FILENAME).write_text("{ not json",
                                                      encoding="utf-8")
        assert env_ack.load_store(tmp_path) == {}, "degrade to NOT acknowledged"

    def test_a_store_from_another_format_is_ignored(self, tmp_path):
        (tmp_path / env_ack.ACKS_FILENAME).write_text(
            json.dumps({"format": 99, "envs": {_ENV: {"k": {}}}}), encoding="utf-8")
        assert env_ack.load_store(tmp_path) == {}


class TestItTouchesNoTypeExpectation:
    def test_the_type_policy_never_reads_this_store(self):
        """`type_verdicts` answers "what type is this field". An
        acknowledgement is not a type claim, and must not become one."""
        from pathlib import Path
        root = Path(__file__).resolve().parent.parent / "quam_state_manager"
        for name in ("core/type_policy.py", "core/type_verdicts.py",
                     "core/modifier.py"):
            src = (root / name).read_text(encoding="utf-8")
            assert "env_ack" not in src, name


class TestTheDoor:
    @pytest.fixture
    def client(self, tmp_path):
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        return app.test_client()

    def test_it_refuses_without_a_chip(self, client):
        r = client.post("/env-ack", data={"kind": "unknown_field"})
        assert r.status_code == 400
        assert r.get_json()["ok"] is False

    def test_revoke_needs_a_key(self, client):
        r = client.post("/env-ack/revoke", data={})
        assert r.status_code in (400, 409)

    def test_the_routes_exist(self, client):
        app = client.application
        rules = {r.rule for r in app.url_map.iter_rules()}
        assert "/env-ack" in rules and "/env-ack/revoke" in rules

    def test_the_control_only_offers_itself_on_env_findings(self):
        from pathlib import Path
        tpl = (Path(__file__).resolve().parent.parent / "quam_state_manager"
               / "web" / "templates" / "_diagnostics_list.html").read_text(encoding="utf-8")
        assert "f.category.startswith('env_')" in tpl
        assert "envAck(this)" in tpl and "envAckRevoke(this)" in tpl

    def test_the_client_helpers_exist(self):
        from pathlib import Path
        js = (Path(__file__).resolve().parent.parent / "quam_state_manager"
              / "web" / "static" / "app.js").read_text(encoding="utf-8")
        assert "window.envAck = function" in js
        assert "window.envAckRevoke = function" in js
        # the sentence the user agreed with must travel with the press
        assert 'b.append("detail"' in js
