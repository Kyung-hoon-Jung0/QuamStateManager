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



class TestOneSentenceOnBothSurfaces:
    """Customer, on-site (2026-09-07): 'This is correct' did nothing visible on
    a real chip. The table button stores the COMPOSED sentence it shows (raw
    detail + fix hint + '[env: ...]'), but to_diag_findings compared the
    stored sentence against the RAW rec['detail'] -- equal only in fixtures
    with neither, so on a real chip every acknowledgement lapsed silently and
    the finding came straight back. Now finding_detail() is the one sentence:
    sent by the table row AND the Types & values card, compared by the
    server; and the card / chip-open alarm drop acknowledged rows from their
    COUNT (SM stops asking) while the table row stays, marked and revocable."""

    _LABEL = "quam 0.6.0 quam_builder 0.4.0"
    _REC = {"kind": "unknown_field", "class": "quam_config.my_quam.Quam",
            "field": "twpa_ext", "code": "", "severity": "error",
            "detail": "'twpa_ext' is not a field of the selected env's Quam",
            "fix_hint": "select that env or migrate the state",
            "count": 1, "example_paths": ["twpa_ext"]}

    def _ack(self, tmp_path, rec):
        env_ack.acknowledge(tmp_path, _ENV, kind=rec["kind"],
                            class_path=rec["class"], field=rec["field"],
                            code=rec["code"],
                            detail=state_env_validate.finding_detail(rec, self._LABEL))
        return env_ack.resolve(tmp_path, _ENV)

    def test_the_composed_sentence_is_what_the_row_shows(self):
        f = state_env_validate.to_diag_findings(
            {"findings": [self._REC]}, env_label=self._LABEL)[0]
        assert f.detail == state_env_validate.finding_detail(self._REC, self._LABEL)
        assert "[env: quam 0.6.0" in f.detail and "migrate the state" in f.detail

    def test_an_ack_stored_with_the_shown_sentence_applies(self, tmp_path):
        acks = self._ack(tmp_path, self._REC)          # what the button sends
        acked = state_env_validate.to_diag_findings(
            {"findings": [self._REC]}, env_label=self._LABEL, acknowledged=acks)
        assert acked[0].acknowledged, "the acknowledgement lapsed against its own sentence"
        assert diagnostics.summarize(acked)["issues"] == 0

    def test_is_acknowledged_mirrors_to_diag_findings(self, tmp_path):
        acks = self._ack(tmp_path, self._REC)
        assert state_env_validate.is_acknowledged(self._REC, acks, self._LABEL)
        changed = {**self._REC, "detail": "'twpa_ext' is not a field (2 places)"}
        assert not state_env_validate.is_acknowledged(changed, acks, self._LABEL)

    def test_the_card_drops_acknowledged_rows_and_stamps_the_rest(self, tmp_path):
        other = {**self._REC, "field": "flux_crosstalk_max_v",
                 "detail": "'flux_crosstalk_max_v' is not a field"}
        acks = self._ack(tmp_path, self._REC)
        kept, n = state_env_validate.unacknowledged_rows(
            [self._REC, other], acks, self._LABEL)
        assert n == 1 and [r["field"] for r in kept] == ["flux_crosstalk_max_v"]
        assert kept[0]["ack_key"] == state_env_validate.ack_key_of(other)
        assert kept[0]["ack_detail"] == state_env_validate.finding_detail(other, self._LABEL)
        from quam_state_manager.core import type_fix
        items = type_fix.env_items(kept)
        assert items[0]["ack_key"] == kept[0]["ack_key"]
        assert items[0]["detail"] == kept[0]["ack_detail"]

    def test_nothing_acknowledged_keeps_every_row_and_stamps_it(self):
        kept, n = state_env_validate.unacknowledged_rows(
            [self._REC], {}, self._LABEL)
        assert n == 0 and len(kept) == 1 and kept[0]["ack_key"]



class TestTheKeySurvivesARestart:
    """Customer, on-site (2026-09-07): every acknowledgement vanished after SM
    restarted. env_key() hashes quam_builder_commit, and the two manifest
    objects a store carries disagree on it (pristine: None; warm cache: the
    hash), so the key flipped between the click and the next process and the
    records were orphaned. The ack identity is now the package VERSIONS only,
    a lookup also reaches legacy commit-bearing keys for the same versions,
    and Un-acknowledge reaches a legacy record too."""

    V_NONE = {"quam": "0.6.0", "quam_builder": "0.4.0", "qualang_tools": "0.23.0",
              "qm": "1.4.0", "quam_builder_commit": None}
    V_HASH = {**V_NONE, "quam_builder_commit": "71fffe7f309df61eb184560f752f7d29db80aae3"}

    def test_the_commit_does_not_change_the_ack_key(self):
        from quam_state_manager.core import state_env_baseline as seb
        assert seb.ack_env_key(self.V_NONE) == seb.ack_env_key(self.V_HASH)
        assert seb.env_key(self.V_NONE) != seb.env_key(self.V_HASH)  # baselines still differ
        assert seb.ack_env_key(self.V_NONE) == seb.env_key(self.V_NONE)  # commit-less records resolve as-is

    def test_legacy_commit_keyed_acks_still_resolve(self, tmp_path):
        from quam_state_manager.core import state_env_baseline as seb
        legacy = seb.env_key(self.V_HASH)                   # what a warm process used to write
        env_ack.acknowledge(tmp_path, legacy, kind="unknown_field", class_path="Quam",
                            field="twpa_ext", code="unknown_field", detail="s")
        got = env_ack.resolve_for_versions(tmp_path, self.V_NONE)   # a fresh process
        assert "unknown_field|Quam|twpa_ext|unknown_field" in got

    def test_stable_wins_and_other_versions_never_leak(self, tmp_path):
        from quam_state_manager.core import state_env_baseline as seb
        env_ack.acknowledge(tmp_path, seb.ack_env_key(self.V_NONE), kind="k",
                            class_path="C", field="f", code="", detail="stable")
        env_ack.acknowledge(tmp_path, seb.env_key(self.V_HASH), kind="k",
                            class_path="C", field="f", code="", detail="legacy")
        other = {**self.V_NONE, "quam": "0.5.0"}
        env_ack.acknowledge(tmp_path, seb.ack_env_key(other), kind="k",
                            class_path="C", field="g", code="", detail="other")
        got = env_ack.resolve_for_versions(tmp_path, self.V_HASH)
        assert got["k|C|f|"]["detail_at_decision"] == "stable"
        assert "k|C|g|" not in got

    def test_revoke_reaches_a_legacy_record(self, tmp_path):
        from quam_state_manager.core import state_env_baseline as seb
        env_ack.acknowledge(tmp_path, seb.env_key(self.V_HASH), kind="k",
                            class_path="C", field="f", code="", detail="legacy")
        assert env_ack.revoke_for_versions(tmp_path, self.V_NONE, "k|C|f|")
        assert not env_ack.resolve_for_versions(tmp_path, self.V_NONE)
        assert not env_ack.revoke_for_versions(tmp_path, self.V_NONE, "k|C|f|")
