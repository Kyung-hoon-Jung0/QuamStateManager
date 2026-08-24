"""Chip profile + closure rules (docs/135, round ② of the A/B/C doctrine).

The profile is enumerated cases, never a customer's rules: the case space is
code-curated in ``chip_profile.FIELDS``, the chip's ANSWERS travel in its own
state.json (``extras.sm_profile``), knowledge rules gate on fields via
``requires_profile``, and an unanswered gating field deactivates the rule and
queues the question — the conservative branch is silence, not a default.
"""
from __future__ import annotations

import itertools
import json
import os
import time
from pathlib import Path

import pytest

from quam_state_manager.core.autofit import chip_profile as cp
from quam_state_manager.core.autofit import knowledge
from quam_state_manager.core.autofit import replaybench as RB


# ---------------------------------------------------------------------------
# chip_profile: parsing, hash, questions, contradictions
# ---------------------------------------------------------------------------

class TestReadProfile:
    def test_no_state_no_extras_no_profile_all_missing(self):
        for state in (None, {}, {"extras": None}, {"extras": {}},
                      {"extras": {"sm_profile": "not-a-dict"}}):
            r = cp.read_profile(state)
            assert r.answers == {}
            assert r.missing == [f.key for f in cp.FIELDS]

    def test_valid_invalid_unknown_split_honestly(self):
        state = {"extras": {"sm_profile": {
            "two_dip_identity": "purcell_companion",     # valid
            "coupler_position": "on_the_moon",           # not a listed case
            "res_vs_flux_parking": "unknown",            # explicit ask-later
            "not_a_field": "whatever",                   # preserved, flagged
        }}}
        r = cp.read_profile(state)
        assert r.answers == {"two_dip_identity": "purcell_companion"}
        assert r.invalid == {"coupler_position": "on_the_moon"}
        assert "res_vs_flux_parking" in r.missing   # unknown == unanswered
        assert r.unknown_keys == ["not_a_field"]

    def test_hash_none_when_empty_and_order_free(self):
        assert cp.profile_hash(None) is None
        assert cp.profile_hash({}) is None
        a = cp.profile_hash({"x": "1", "y": "2"})
        b = cp.profile_hash({"y": "2", "x": "1"})
        assert a == b and len(a) == 16

    def test_questions_filter_by_consuming_family(self):
        r = cp.read_profile(None)
        allq = cp.questions(r)
        assert [q["key"] for q in allq] == [f.key for f in cp.FIELDS]
        only = cp.questions(r, families=["resonator_spectroscopy"])
        assert [q["key"] for q in only] == ["two_dip_identity"]

    def test_answered_field_is_not_asked_again(self):
        r = cp.read_profile({"extras": {"sm_profile": {
            "two_dip_identity": "rival_neighbor"}}})
        assert "two_dip_identity" not in [q["key"] for q in cp.questions(r)]

    def test_contradiction_fires_only_on_declared_case(self):
        ans = {"res_vs_coupler_response": "weak_flat_normal"}
        hit = cp.contradiction("resonator_spectroscopy_vs_coupler_flux",
                               "curve_full_swing", ans)
        assert hit and "weak_flat_normal" in hit
        # other family, other signal, other/absent answer: silent
        assert cp.contradiction("resonator_spectroscopy",
                                "curve_full_swing", ans) is None
        assert cp.contradiction("resonator_spectroscopy_vs_coupler_flux",
                                "curve_flat_no_response", ans) is None
        assert cp.contradiction("resonator_spectroscopy_vs_coupler_flux",
                                "curve_full_swing", {}) is None


# ---------------------------------------------------------------------------
# knowledge: closure lint, gates, signal resolution
# ---------------------------------------------------------------------------

def _mini_pack(tmp_path, closure_rules=None, cases=None, signal_map=None):
    fam = "resonator_spectroscopy"
    d = tmp_path / "know" / "v1" / fam
    d.mkdir(parents=True, exist_ok=True)
    pack = {"schema": "smknow/v1", "family": fam,
            "cases": cases if cases is not None else
            [{"id": "S1", "geometry": "one resolved dip", "prescription": "adopt"}]}
    if closure_rules is not None:
        pack["closure_rules"] = closure_rules
    if signal_map is not None:
        pack["signal_map"] = signal_map
    (d / "cases.json").write_text(json.dumps(pack), encoding="utf-8")
    return fam, tmp_path / "know"


class TestClosureLint:
    def _load(self, monkeypatch, tmp_path, **kw):
        fam, root = _mini_pack(tmp_path, **kw)
        monkeypatch.setattr(knowledge, "_ROOT", root)
        return knowledge.load_family(fam)

    def test_absolute_scale_closure_rule_is_dropped_never_taught(
            self, monkeypatch, tmp_path):
        pack = self._load(monkeypatch, tmp_path, closure_rules=[
            {"id": "CL-BAD", "trigger": "shift beyond 5 MHz", "text": "x"},
            {"id": "CL-OK", "trigger": "a bounded try-set exhausted",
             "text": "escalate"}])
        assert [r["id"] for r in pack["closure_rules"]] == ["CL-OK"]
        assert pack["closure_lint_dropped"] == ["CL-BAD"]

    def test_gate_on_unknown_field_or_case_is_dropped(
            self, monkeypatch, tmp_path):
        pack = self._load(monkeypatch, tmp_path, closure_rules=[
            {"id": "CL-F", "trigger": "t", "text": "x",
             "requires_profile": {"no_such_field": ["a"]}},
            {"id": "CL-C", "trigger": "t", "text": "x",
             "requires_profile": {"two_dip_identity": ["no_such_case"]}}])
        assert pack["closure_rules"] == []
        assert set(pack["closure_lint_dropped"]) == {"CL-F", "CL-C"}

    def test_closure_rules_move_the_manual_hash(self, monkeypatch, tmp_path):
        a = self._load(monkeypatch, tmp_path, closure_rules=[])
        h1 = a["manual_hash"]
        b = self._load(monkeypatch, tmp_path, closure_rules=[
            {"id": "CL-1", "trigger": "t", "text": "x"}])
        assert b["manual_hash"] != h1, \
            "closure rules change judgment — they must move the context hash"


class TestActiveView:
    RULES = [
        {"id": "CL-A", "trigger": "t", "text": "x"},
        {"id": "CL-B", "trigger": "t", "text": "x",
         "requires_profile": {"two_dip_identity": ["purcell_companion"]}},
        {"id": "CL-C2", "trigger": "t", "text": "x",
         "requires_profile": {"two_dip_identity": ["rival_neighbor"]}},
    ]

    def _pack(self, monkeypatch, tmp_path):
        fam, root = _mini_pack(tmp_path, closure_rules=self.RULES)
        monkeypatch.setattr(knowledge, "_ROOT", root)
        return knowledge.load_family(fam)

    def test_answered_field_splits_active_inactive(
            self, monkeypatch, tmp_path):
        pack = self._pack(monkeypatch, tmp_path)
        av = knowledge.active_view(
            pack, {"two_dip_identity": "purcell_companion"})
        assert [r["id"] for r in av["closure_rules"]] == ["CL-A", "CL-B"]
        assert av["inactive_closure_ids"] == ["CL-C2"]
        assert av["pending_questions"] == []

    def test_unanswered_field_deactivates_and_queues_the_question(
            self, monkeypatch, tmp_path):
        pack = self._pack(monkeypatch, tmp_path)
        av = knowledge.active_view(pack, {})
        assert [r["id"] for r in av["closure_rules"]] == ["CL-A"], \
            "the conservative branch is silence, not a default case"
        assert av["pending_questions"] == ["two_dip_identity"]


class TestResolveSignal:
    def test_plain_string_entry_is_byte_identical(self, monkeypatch, tmp_path):
        fam, root = _mini_pack(tmp_path, signal_map={"line_clean": "S1"})
        monkeypatch.setattr(knowledge, "_ROOT", root)
        pack = knowledge.load_family(fam)
        assert knowledge.resolve_signal(pack, "line_clean", None) == ("S1", None)
        assert knowledge.resolve_signal(pack, "absent", None) == (None, None)

    def test_branched_entry_resolves_by_answer_or_defaults_with_question(
            self, monkeypatch, tmp_path):
        smap = {"line_multi_feature": {
            "default": "S4",
            "by_profile": {"two_dip_identity": {
                "purcell_companion": "S4", "rival_neighbor": "S4"}}}}
        fam, root = _mini_pack(tmp_path, signal_map=smap)
        monkeypatch.setattr(knowledge, "_ROOT", root)
        pack = knowledge.load_family(fam)
        got = knowledge.resolve_signal(
            pack, "line_multi_feature", {"two_dip_identity": "rival_neighbor"})
        assert got == ("S4", None)
        got = knowledge.resolve_signal(pack, "line_multi_feature", {})
        assert got == ("S4", "two_dip_identity"), \
            "unanswered gate: conservative default + the question, never a guess"


class TestShippedPacks:
    """The four packs that gained closure rules load them clean."""

    EXPECT = {
        "resonator_spectroscopy": ["CL-RIVAL", "CL-COMPANION", "CL-NOCAND",
                                   "CL-CLUSTER", "CL-R2", "CL-EDGE",
                                   "CL-MAXPROM"],
        "qubit_spectroscopy": ["CL-NOCAND", "CL-CLUSTER", "CL-R2", "CL-EDGE"],
        "resonator_spectroscopy_vs_coupler_flux": ["CL-FLATOK", "CL-FLATQ"],
        "resonator_spectroscopy_vs_flux": ["CL-PARK-MAX", "CL-PARK-MIN"],
    }

    @pytest.mark.parametrize("fam", sorted(EXPECT))
    def test_pack_ships_its_closure_rules(self, fam):
        pack = knowledge.load_family(fam)
        assert pack is not None
        assert [r["id"] for r in pack["closure_rules"]] == self.EXPECT[fam]
        assert pack["closure_lint_dropped"] == []

    def test_park_rules_are_mutually_exclusive_by_profile(self):
        pack = knowledge.load_family("resonator_spectroscopy_vs_flux")
        for case in ("resonator_freq_maxima", "resonator_freq_minima"):
            av = knowledge.active_view(pack, {"res_vs_flux_parking": case})
            active = [r["id"] for r in av["closure_rules"]]
            assert len(active) == 1, active
        av = knowledge.active_view(pack, {})
        assert av["closure_rules"] == []
        assert av["pending_questions"] == ["res_vs_flux_parking"]


# ---------------------------------------------------------------------------
# verification context: the profile is part of a verdict's warrant
# ---------------------------------------------------------------------------

class TestProfileInContext:
    def test_profile_hash_splits_comparability(self):
        from quam_state_manager.core.autofit.verification import (
            VerificationContext)
        a = VerificationContext(analysis_rev="r1", run_generation="g1")
        b = VerificationContext(analysis_rev="r1", run_generation="g1")
        assert a.key() == b.key(), "both None — old verdicts stay comparable"
        b.profile_hash = "abc"
        assert a.key() != b.key(), \
            "a judgment branched on one answer set is not comparable to " \
            "a judgment branched on another"
        assert "profile_hash" in a.as_dict()


# ---------------------------------------------------------------------------
# two-tier scoring: tags produce the rows, no tags is byte-identical
# ---------------------------------------------------------------------------

class TestTwoTierScoring:
    def _result(self):
        from quam_state_manager.core.autofit.replaybench import Result, Step
        steps = [
            Step(index=0, run_id="#10_x", signal="s", case="S1", flags=[],
                 action="hold", adopted={}, refused=[], next_params={},
                 proposal_matched=None, prescription="", reasons=[]),
            Step(index=1, run_id="#11_x", signal="s", case="S2", flags=[],
                 action="adopt", adopted={}, refused=[], next_params={},
                 proposal_matched=None, prescription="", reasons=[]),
        ]
        return Result(family="resonator_spectroscopy", session_id="s",
                      target="q1", steps=steps, final_state={},
                      first_value={}, first_value_at=None,
                      runs_to_first_value=0, runs_consumed=2,
                      unresolved=True, revisions=[], unscoreable_proposals=0)

    def _key(self, tags=False):
        path = [{"run": "#10_x", "expected_case": "S1"},
                {"run": "#11_x", "expected_case": "S9"}]
        if tags:
            path[0]["step_class"] = "B"
            path[1]["step_class"] = "C"
        return {"termination": {"unresolved": True}, "ideal_path": path}

    def test_untagged_key_scores_byte_identically(self):
        from quam_state_manager.core.autofit import replaybench as rb
        out = rb.score(self._result(), self._key(tags=False))
        assert "b_direction_agreement" not in out
        assert "c_points" not in out
        assert out["case_agreement"] == "1/2"

    def test_tagged_key_scores_b_direction_and_names_c_points(self):
        from quam_state_manager.core.autofit import replaybench as rb
        out = rb.score(self._result(), self._key(tags=True))
        # decision-class matcher (docs/136): a B step means "this run does
        # not decide" — the replay agrees by also taking no value from it
        # (these keys describe cases in prose, so a text matcher is vacuous)
        assert out["b_direction_agreement"] == "1/1"
        assert out["b_proposal_matched"] == "n/a"
        assert out["c_points"] == ["#11"]
        assert out["terminal_class"] == "C"
        assert out["conclusion_scored_at"] == "termination"

    def test_b_disagreement_is_an_adoption_at_a_b_step(self):
        from quam_state_manager.core.autofit import replaybench as rb
        res = self._result()
        res.steps[0].action = "adopt"      # took a value where the key says
        out = rb.score(res, self._key(tags=True))   # "this run does not decide"
        assert out["b_direction_agreement"] == "0/1"


# ---------------------------------------------------------------------------
# routes: the GUI form stages through the working copy, never live
# ---------------------------------------------------------------------------

_MTIME_TICK = itertools.count(1)


def _write(folder: Path, state: dict, wiring: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(wiring), encoding="utf-8")
    t = time.time() + 2 * next(_MTIME_TICK)
    for f in ("state.json", "wiring.json"):
        os.utime(folder / f, (t, t))
    return folder


class TestProfileRoutes:
    def _env(self, tmp_path, state=None):
        from quam_state_manager.web.app import create_app
        live = _write(tmp_path / "chips" / "live",
                      state or {"qubits": {"qA1": {"id": "qA1", "f_01": 5e9}},
                                "qubit_pairs": {}},
                      {"wiring": {}, "network": {"host": "10.0.0.1",
                                                 "cluster_name": "C"}})
        app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
        c = app.test_client()
        r = c.post("/load", data={"folder": str(live)})
        assert r.status_code in (200, 302)
        return app, c, live

    def test_form_renders_every_field(self, tmp_path):
        _app, c, _live = self._env(tmp_path)
        html = c.get("/chip-profile").data.decode()
        for f in cp.FIELDS:
            assert f'name="{f.key}"' in html
        assert "0 of 6 answered" in html

    def test_set_stages_into_working_copy_only(self, tmp_path):
        app, c, live = self._env(tmp_path)
        live_bytes = (live / "state.json").read_bytes()
        r = c.post("/chip-profile/set", data={
            "two_dip_identity": "purcell_companion",
            "res_vs_flux_parking": "resonator_freq_maxima"})
        assert r.status_code == 200, r.data.decode()
        ctx = next(iter(app.config["contexts"].values()))
        prof = ctx["store"].state["extras"]["sm_profile"]
        assert prof == {"two_dip_identity": "purcell_companion",
                        "res_vs_flux_parking": "resonator_freq_maxima"}
        assert (live / "state.json").read_bytes() == live_bytes, \
            "never a direct live write — Apply is the only path"
        # answers render back
        html = c.get("/chip-profile").data.decode()
        assert "2 of 6 answered" in html

    def test_unlisted_case_is_refused_at_the_door(self, tmp_path):
        _app, c, _live = self._env(tmp_path)
        r = c.post("/chip-profile/set",
                   data={"two_dip_identity": "on_the_moon"})
        assert r.status_code == 400

    def test_unchanged_answers_stage_nothing(self, tmp_path):
        app, c, _live = self._env(tmp_path, state={
            "qubits": {"qA1": {"id": "qA1", "f_01": 5e9}}, "qubit_pairs": {},
            "extras": {"sm_profile": {"two_dip_identity": "rival_neighbor"}}})
        r = c.post("/chip-profile/set",
                   data={"two_dip_identity": "rival_neighbor"})
        assert r.status_code == 200
        assert b"nothing staged" in r.data.lower()
        ctx = next(iter(app.config["contexts"].values()))
        assert not ctx["store"].change_log, "no-op must not dirty the tray"

# ---------------------------------------------------------------------------
# C2: cross-family closure (docs/137)
# ---------------------------------------------------------------------------

class TestCrossClosure:
    def test_shipped_cross_rules_load_clean(self):
        doc = knowledge.load_cross()
        assert doc is not None
        assert [r["id"] for r in doc["cross_closure_rules"]] == [
            "X-JOINT-QSPEC", "X-TWO-DIP-POWER", "X-PARKING-AGREE",
            "X-COUPLER-DECISION"]
        assert doc["cross_lint_dropped"] == []
        assert len(doc["cross_hash"]) == 16

    def test_profile_gate_and_family_filter(self):
        doc = knowledge.load_cross()
        fam = "resonator_spectroscopy_vs_coupler_flux"
        with_p = knowledge.cross_rules_for(
            doc, fam, {"res_vs_coupler_response": "weak_flat_normal"})
        assert [r["id"] for r in with_p] == ["X-COUPLER-DECISION"]
        assert knowledge.cross_rules_for(doc, fam, {}) == [], \
            "an unanswered gating field means silence, not a default"
        both = knowledge.cross_rules_for(doc, "qubit_spectroscopy", {})
        assert [r["id"] for r in both] == ["X-JOINT-QSPEC"]

    def test_lint_drops_bad_cross_rules(self, monkeypatch, tmp_path):
        d = tmp_path / "v1" / "_cross"
        d.mkdir(parents=True)
        (d / "closure.json").write_text(json.dumps({"cross_closure_rules": [
            {"id": "X-ABS", "families": ["a", "b"],
             "trigger": "shift beyond 5 MHz", "text": "x"},
            {"id": "X-ONE", "families": ["a"], "trigger": "t", "text": "x"},
            {"id": "X-OK", "families": ["a", "b"], "trigger": "t",
             "text": "x"}]}), encoding="utf-8")
        monkeypatch.setattr(knowledge, "_ROOT", tmp_path)
        doc = knowledge.load_cross()
        assert [r["id"] for r in doc["cross_closure_rules"]] == ["X-OK"]
        assert set(doc["cross_lint_dropped"]) == {"X-ABS", "X-ONE"}


# ---------------------------------------------------------------------------
# C2 executed: cross_close (docs/138)
# ---------------------------------------------------------------------------

class TestCrossExecution:
    """The three data-only C2 rules, executed by ``replaybench.cross_close``.

    Each pin carries the shape that motivated it; the byte-identity pins are
    the doctrine (knowledge strictly optional, unanswered gate = silence)."""

    @staticmethod
    def _res(family, session_id, final_state, reads=None, notes=None):
        return RB.Result(
            family=family, session_id=session_id, target="q1",
            steps=[], final_state=dict(final_state), first_value={},
            first_value_at="#1", runs_to_first_value=1, runs_consumed=1,
            unresolved=False, revisions=[], unscoreable_proposals=0,
            closure_notes=list(notes or []),
            value_reads={k: list(v) for k, v in (reads or {}).items()})

    _PROFILE = {"res_vs_coupler_response": "weak_flat_normal"}

    def test_no_doc_is_byte_identical(self):
        r = self._res("resonator_spectroscopy_vs_coupler_flux",
                      "f__lab__2026-08-14", {"idle_offset": 0.1})
        assert RB.cross_close([r], doc=None, profile=self._PROFILE) == []
        assert r.final_state == {"idle_offset": 0.1}

    def test_coupler_decision_gated_on_profile(self):
        doc = knowledge.load_cross()
        r = self._res("resonator_spectroscopy_vs_coupler_flux",
                      "f__lab__2026-08-14",
                      {"resonator_frequency": 5.0e9, "idle_offset": 0.1})
        # unanswered gating field: silence, never a default
        assert RB.cross_close([r], doc=doc, profile={}) == []
        assert "idle_offset" in r.final_state
        notes = RB.cross_close([r], doc=doc, profile=self._PROFILE)
        assert "idle_offset" not in r.final_state
        # the verification half (the frequency reading) is untouched
        assert r.final_state["resonator_frequency"] == 5.0e9
        assert any("X-COUPLER-DECISION" in n for n in notes)
        assert r.closure_notes == notes

    def test_parking_disagreement_drops_both_and_names_the_staler_map(self):
        doc = knowledge.load_cross()
        p = {"min_flux_offset_in_v": -0.2, "max_flux_offset_in_v": 0.2}
        ra = self._res("resonator_spectroscopy_vs_flux",
                       "a__lab__2026-08-14",
                       {"resonator_frequency": 5e9, "idle_offset": 0.10},
                       reads={"idle_offset": [(0.10, p, "#3")]})
        rq = self._res("qubit_spectroscopy_vs_flux",
                       "b__lab__2026-08-13",
                       {"qubit_frequency": 4e9, "idle_offset": 0.02},
                       reads={"idle_offset": [(0.02, p, "#5")]})
        notes = RB.cross_close([ra, rq], doc=doc)
        assert "idle_offset" not in ra.final_state
        assert "idle_offset" not in rq.final_state
        # never averaged, and the direction names the STALER map
        assert any("2026-08-13" in n and "re-measure" in n for n in notes)
        assert ra.final_state["resonator_frequency"] == 5e9
        assert rq.final_state["qubit_frequency"] == 4e9

    def test_parking_agreement_within_the_window_changes_nothing(self):
        doc = knowledge.load_cross()
        p = {"min_flux_offset_in_v": -0.2, "max_flux_offset_in_v": 0.2}
        ra = self._res("resonator_spectroscopy_vs_flux", "a__lab__2026-08-14",
                       {"idle_offset": 0.10},
                       reads={"idle_offset": [(0.10, p, "#3")]})
        rq = self._res("qubit_spectroscopy_vs_flux", "b__lab__2026-08-13",
                       {"idle_offset": 0.11},
                       reads={"idle_offset": [(0.11, p, "#5")]})
        assert RB.cross_close([ra, rq], doc=doc) == []
        assert ra.final_state["idle_offset"] == 0.10

    def test_parking_silent_without_a_recoverable_window(self):
        # the comparison scale is the maps' OWN swept window; with none
        # recoverable the rule stays silent rather than inventing volts
        doc = knowledge.load_cross()
        ra = self._res("resonator_spectroscopy_vs_flux", "a__lab__2026-08-14",
                       {"idle_offset": 0.10},
                       reads={"idle_offset": [(0.10, {}, "#3")]})
        rq = self._res("qubit_spectroscopy_vs_flux", "b__lab__2026-08-13",
                       {"idle_offset": 0.02},
                       reads={"idle_offset": [(0.02, {}, "#5")]})
        assert RB.cross_close([ra, rq], doc=doc) == []
        assert ra.final_state["idle_offset"] == 0.10

    def test_two_dip_power_closes_a_contested_1d_window(self):
        doc = knowledge.load_cross()
        rs = self._res("resonator_spectroscopy", "s__lab__2026-08-13", {},
                       notes=["CL-CLUSTER: frequency is CONTESTED - 2 "
                              "disagreeing readings"])
        rs.unresolved = True
        rp = self._res("resonator_spectroscopy_vs_power",
                       "p__lab__2026-08-13",
                       {"resonator_frequency": 5.1e9})
        notes = RB.cross_close([rs, rp], doc=doc)
        assert rs.final_state["frequency"] == 5.1e9
        # the conclusion now exists and the flag says so (a lie in a
        # variable is a lie everywhere)
        assert rs.unresolved is False
        assert any("X-TWO-DIP-POWER" in n for n in notes)

    def test_two_dip_power_needs_the_contested_stamp(self):
        # a merely-absent frequency (the walk never adopted one) is NOT a
        # contested one - the rule closes a contest, not an absence
        doc = knowledge.load_cross()
        rs = self._res("resonator_spectroscopy", "s__lab__2026-08-13", {})
        rp = self._res("resonator_spectroscopy_vs_power",
                       "p__lab__2026-08-13",
                       {"resonator_frequency": 5.1e9})
        assert RB.cross_close([rs, rp], doc=doc) == []
        assert "frequency" not in rs.final_state
