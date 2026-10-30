"""Lab additions, phase 6: knowledge cards, one lint, agent proposals.

Covers the card pack (load-time lint, hash, additive-only overlay), a lab
class inheriting its base's card through the MRO, a class with no card, the
cards replacing the hard-coded CZ / readout rules (same answers as before),
``lint_proposal`` giving the wizard and an agent the same verdict, the
calibration-number refusal for an agent, the pending / accept / dismiss flow,
the MCP tool shapes, the landing line and the agent-only button, and the
re-generate carrying ``cz_variant``.
"""
from __future__ import annotations

import json

import pytest

from quam_state_manager.core import addition_cards, addition_proposals, additions, regen_spec

from tests.test_lab_additions_p3 import CZ, FLAT, MAN, MEAS, TWO, _cz, _f, _pulse, _spec

RO = "quam.components.pulses.SquareReadoutPulse"
LAB_RO = "lab.readout.WeightsReadoutPulse"
LAB_MAC = "lab.macros.Odd"
MAN6 = json.loads(json.dumps(MAN))
MAN6["classes"].update({
    RO: {"role": "pulse", "origin": "official", "abstract": False,
         "mro": ["quam.components.pulses.ReadoutPulse", "quam.components.pulses.Pulse"],
         "fields": {"length": _f("int"), "amplitude": _f("float")}},
    LAB_RO: {"role": "pulse", "origin": "lab", "abstract": False,
             "mro": [RO, "quam.components.pulses.ReadoutPulse", "quam.components.pulses.Pulse"],
             "fields": {"length": _f("int"), "amplitude": _f("float"),
                        "weights_real": _f("list[float]", True, default_repr="[]")}},
    LAB_MAC: {"role": "macro_qubit", "origin": "lab", "abstract": False, "mro": [],
              "fields": {"note": _f("str", True, default_repr="''")}},
})
MAN6["classes"][FLAT]["mro"] = ["quam.components.pulses.Pulse"]
MAN6["classes"][CZ]["mro"] = ["quam.components.macro.qubit_pair_macros.QubitPairMacro"]
MAN6["classes"][TWO]["mro"] = [CZ, "quam.components.macro.qubit_pair_macros.QubitPairMacro"]


# --------------------------------------------------------------- the cards

class TestCards:
    def test_the_pack_loads_lints_and_hashes(self):
        d = addition_cards.load_cards()
        ids = [c["id"] for c in d["cards"]]
        for want in ("cz_gate", "cz_two_flux", "cz_both_movers", "cz_coupler", "readout_pulse",
                     "measure_macro", "pulsed_twpa", "qdac_z_refusal"):
            assert want in ids
        assert d["dropped"] == [] and len(d["hash"]) == 16 and d["version"] == "additions-v1"

    @pytest.mark.parametrize("bad", [
        {"id": "Bad", "role": "pulse", "match": {"mro_any": ["Pulse"]}},
        {"id": "x", "role": "nope", "match": {"mro_any": ["Pulse"]}},
        {"id": "x", "role": "pulse", "match": {"whatever": 1}},
        {"id": "x", "role": "macro_pair", "match": {"fields_all": ["a"]}, "pointers": {"a": "elsewhere"}},
        {"id": "x", "role": "pulse", "match": {"mro_any": ["Pulse"]}, "placement": {"target": "<chip>.ops"}},
        {"id": "x", "role": "pulse", "match": {"mro_any": ["Pulse"]}, "naming": {"strip": "("}},
    ])
    def test_a_bad_card_is_refused_by_the_lint(self, bad):
        assert addition_cards._lint_card(bad)

    def test_a_lab_class_inherits_its_bases_card_by_mro(self):
        c = additions.card_of(MAN6, LAB_RO)
        assert c["id"] == "readout_pulse"
        assert additions.card_of(MAN6, TWO)["id"] == "cz_two_flux"
        assert additions.card_of(MAN6, CZ)["id"] == "cz_gate"

    def test_a_class_with_no_card(self):
        assert additions.card_of(MAN6, LAB_MAC) is None

    def test_default_names_match_the_old_hard_coded_ones(self):
        c = addition_cards.card_by_id
        assert addition_cards.default_name(c("cz_both_movers"), "lab.g.CZGateBothMovers") == "cz_bothmovers"
        assert addition_cards.default_name(c("readout_pulse"), "lab.r.ComplexWeightsReadoutPulse") \
            == "readout_complexweights"
        assert addition_cards.default_name(c("readout_pulse"), "lab.r.ReadoutPulse") == "readout"
        assert addition_cards.default_name(c("measure_macro"), MEAS) == "measure"

    def test_cz_recipes_come_from_the_cards(self):
        assert additions.cz_recipe(MAN6["classes"][CZ]) == [
            {"field": "flux_pulse_qubit", "on": "moving", "required": True},
            {"field": "coupler_flux_pulse", "on": "coupler", "required": False}]
        assert [i["on"] for i in additions.cz_recipe(MAN6["classes"][TWO])] == ["control", "target", "coupler"]
        both = MAN6["classes"]["lab.gates.Both"]
        assert [i["on"] for i in additions.cz_recipe(both)] == ["high", "low"]
        assert additions.cz_recipe({"role": "macro_pair", "fields": {"coupler_flux_pulse": {}}}) is None


class TestOverlay:
    def _write(self, tmp_path, cards):
        addition_cards.overlay_path(tmp_path).write_text(json.dumps({"cards": cards}), encoding="utf-8")

    def test_a_new_card_is_added_and_tried_first(self, tmp_path):
        self._write(tmp_path, [{"id": "lab_odd", "role": "macro_qubit", "match": {"fields_all": ["note"]},
                                "placement": {"target": "qubits.<qubit>.macros"}}])
        d = addition_cards.load_cards(instance_path=tmp_path)
        assert d["cards"][0]["id"] == "lab_odd" and d["cards"][0]["origin"] == "overlay"
        assert addition_cards.card_for(dict(MAN6["classes"][LAB_MAC], path=LAB_MAC), deck=d)["id"] == "lab_odd"

    def test_an_overlay_never_replaces_a_shipped_card(self, tmp_path):
        self._write(tmp_path, [{"id": "cz_gate", "role": "macro_pair", "match": {"fields_all": ["x"]}}])
        d = addition_cards.load_cards(instance_path=tmp_path)
        assert [c for c in d["cards"] if c["id"] == "cz_gate"][0]["origin"] == "shipped"
        assert d["refused_overlay"] and "only ADD" in d["refused_overlay"][0][1]
        assert addition_cards.overlay_card_reason(
            {"id": "cz_gate", "role": "macro_pair", "match": {"fields_all": ["x"]}}, tmp_path)

    def test_the_hash_moves_with_the_overlay(self, tmp_path):
        h0 = addition_cards.load_cards(instance_path=tmp_path)["hash"]
        self._write(tmp_path, [{"id": "lab_odd", "role": "macro_qubit", "match": {"fields_all": ["note"]}}])
        assert addition_cards.load_cards(instance_path=tmp_path)["hash"] != h0


# --------------------------------------------------------------- the lint

class TestOneLint:
    def test_wizard_and_agent_get_the_same_verdict(self):
        spec = _spec()
        for add in (_cz(flux_pulse_qubit="#/qubits/q1/z/operations/const"),
                    _cz(cls="nope.Missing"), _cz(pair="q9-9"),
                    {"kind": "macro", "target": "qubits.q1.macros", "name": "m", "class": CZ, "fields": {}}):
            p = additions.lint_proposal(spec, add, MAN6, author="person")
            a = additions.lint_proposal(spec, add, MAN6, author="by_claude")
            assert p["errors"] == a["errors"] and p["warnings"] == a["warnings"] and p["card"] == a["card"]

    def test_precheck_reads_the_same_lint(self):
        add = _cz(cls="nope.Missing")
        spec = _spec([add])
        pre = additions.precheck(spec, MAN6)[0]
        assert pre["reason"] == additions.lint_proposal(_spec(), add, MAN6)["errors"][0]

    def test_a_structural_error_is_reported(self):
        r = additions.lint_proposal(_spec(), {"kind": "pulse", "target": "x", "name": "a b", "class": "C"}, MAN6)
        assert not r["ok"] and any("this addition" in e for e in r["errors"])

    def test_a_name_clash_with_the_builder_is_refused(self):
        spec = dict(_spec(), pair_gate="cz_tunable")
        r = additions.lint_proposal(spec, _cz(name="cz_unipolar", flux_pulse_qubit="#/a"), MAN6)
        assert not r["ok"] and "already makes a gate named cz_unipolar" in r["errors"][0]

    def test_card_placement_and_naming_are_warnings(self):
        add = {"kind": "pulse", "target": "qubits.q1.xy.operations", "name": "ro", "class": LAB_RO,
               "fields": {"length": 1, "amplitude": 0.1}}
        r = additions.lint_proposal(_spec(), add, MAN6)
        assert r["ok"] and r["card"] == "readout_pulse"
        assert any("resonator" in w for w in r["warnings"]) and any("readout_" in w for w in r["warnings"])

    def test_the_qdac_rule_comes_from_its_card(self):
        spec = dict(_spec(), qdac={"qubits": {"q1": {"channel": 1}}})
        r = additions.lint_proposal(spec, _pulse("q1", ch="z"), MAN6)
        assert not r["ok"] and "QdacBiasLine" in r["errors"][0]


class TestNumbersAreNotTheAgents:
    def test_an_agent_may_not_set_an_amplitude(self):
        r = additions.lint_proposal(_spec(), _pulse(), MAN6, author="by_claude")
        assert not r["ok"] and any("calibration value" in e for e in r["errors"])
        assert additions.lint_proposal(_spec(), _pulse(), MAN6, author="person")["ok"]

    def test_left_out_calibration_fields_are_the_persons(self):
        add = dict(_pulse(), fields={})
        r = additions.lint_proposal(_spec(), add, MAN6, author="by_codex")
        assert r["ok"] and set(r["needs_person"]) == {"amplitude", "length", "flat_length"}

    def test_a_field_the_person_typed_passes(self):
        r = additions.lint_proposal(_spec(), _pulse(), MAN6, author="by_claude",
                                    typed={"amplitude", "length", "flat_length"})
        assert r["ok"]

    def test_restating_a_class_default_is_not_a_number(self):
        add = _cz(flux_pulse_qubit="#/qubits/q1/z/operations/const", phase_shift_control=0.0)
        assert additions.lint_proposal(_spec(), add, MAN6, author="by_claude")["ok"]
        add2 = _cz(flux_pulse_qubit="#/qubits/q1/z/operations/const", phase_shift_control=0.3)
        assert not additions.lint_proposal(_spec(), add2, MAN6, author="by_claude")["ok"]

    def test_a_pointer_is_not_a_number(self):
        add = _cz(flux_pulse_qubit="#/qubits/q1/z/operations/const",
                  duration_qubit="#/qubits/q1/z/operations/const/length")
        assert additions.lint_proposal(_spec(), add, MAN6, author="by_claude")["ok"]

    def test_a_field_addition_of_a_frequency(self):
        add = {"kind": "field", "target": "qubits.q1.f_01", "value": 5.1e9}
        assert not additions.lint_proposal(_spec(), add, MAN6, author="by_claude")["ok"]


# --------------------------------------------------------------- pending

class TestPendingFlow:
    def test_propose_accept_dismiss(self, tmp_path):
        spec = _spec()
        res = addition_proposals.propose(tmp_path, [
            {"addition": dict(_pulse(), fields={}), "reason": "a flat pulse for the CZ"},
            {"addition": _pulse(name="cz_p2"), "reason": "with numbers"},
            {"addition": _cz(cls="nope.Missing"), "reason": "unknown class"},
            {"addition": _cz(flux_pulse_qubit="#/a"), "reason": ""},
        ], "by_claude", MAN6, spec=spec)
        assert [r["status"] for r in res] == ["pending", "rejected", "rejected", "rejected"]
        assert "calibration value" in res[1]["errors"][0] and "reason is required" in res[3]["errors"][0]
        pend = addition_proposals.pending(tmp_path)
        assert len(pend) == 1 and pend[0]["author"] == "by_claude"
        pid = pend[0]["id"]
        bad = addition_proposals.accept(tmp_path, pid, spec, {}, MAN6, who="human")
        assert not bad["ok"] and "required" in bad["errors"][0]
        ok = addition_proposals.accept(tmp_path, pid, spec, {"length": 40, "amplitude": 0.1, "flat_length": 24},
                                       MAN6, who="human")
        assert ok["ok"] and ok["addition"]["fields"]["amplitude"] == 0.1
        assert ok["addition"]["proposed_by"] == "by_claude"
        assert addition_proposals.pending(tmp_path) == []
        assert not addition_proposals.accept(tmp_path, pid, spec, {}, MAN6, who="human")["ok"]

    def test_two_proposals_in_one_batch_may_not_clash(self, tmp_path):
        a = _cz(flux_pulse_qubit="#/qubits/q1/z/operations/const")
        res = addition_proposals.propose(tmp_path, [{"addition": a, "reason": "r"},
                                                    {"addition": a, "reason": "r"}], "by_claude", MAN6,
                                         spec=_spec())
        assert [r["status"] for r in res] == ["pending", "rejected"]

    def test_dismiss(self, tmp_path):
        r = addition_proposals.propose(tmp_path, [{"addition": dict(_pulse(), fields={}), "reason": "r"}],
                                       "by_claude", MAN6, spec=_spec())
        assert addition_proposals.decide(tmp_path, r[0]["id"], "dismissed", "human")
        assert addition_proposals.pending(tmp_path) == []

    def test_a_card_proposal_waits_for_a_person(self, tmp_path):
        card = {"id": "lab_odd", "role": "macro_qubit", "match": {"fields_all": ["note"]}}
        r = addition_proposals.propose_card(tmp_path, card, "covers the odd macro", "by_claude")
        assert r["status"] == "pending"
        assert not addition_cards.overlay_path(tmp_path).exists()
        assert addition_proposals.accept(tmp_path, r["id"], {}, {}, None, who="human")["ok"]
        assert json.loads(addition_cards.overlay_path(tmp_path).read_text())["cards"][0]["id"] == "lab_odd"
        assert addition_proposals.propose_card(tmp_path, dict(card), "again", "by_claude")["status"] == "rejected"


# --------------------------------------------------------------- web + MCP

@pytest.fixture
def client(tmp_path, monkeypatch):
    from quam_state_manager.web.app import create_app
    return create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()


_H = {"Origin": "http://localhost", "Host": "localhost"}


class TestRoutes:
    def test_wizard_context_and_agent_propose(self, client, tmp_path, monkeypatch):
        from quam_state_manager.web import routes
        monkeypatch.setattr(routes, "_proposal_manifest", lambda spec, adds: MAN6)
        r = client.post("/api/agent/generate/propose", json={"items": [{"addition": _pulse(), "reason": "x"}]},
                        headers=_H)
        assert r.status_code == 409          # no draft recorded yet
        assert client.post("/generate/proposals/context", json={"spec": _spec()}, headers=_H).get_json()["ok"]
        r = client.post("/api/agent/generate/propose", headers=dict(_H, **{"X-SM-Agent": "claude"}),
                        json={"items": [{"addition": dict(_pulse(), fields={}), "reason": "x"},
                                        {"addition": _pulse(), "reason": "x"}]}).get_json()
        assert [x["status"] for x in r["results"]] == ["pending", "rejected"]
        items = client.get("/generate/proposals").get_json()["items"]
        assert items[0]["author"] == "by_claude"
        acc = client.post(f"/generate/proposals/{items[0]['id']}/accept", headers=_H,
                          json={"spec": _spec(), "fields": {"length": 40, "amplitude": 0.1, "flat_length": 8}})
        assert acc.status_code == 200 and acc.get_json()["addition"]["name"] == "cz_p"

    def test_agent_roles_lists_cards(self, client):
        d = client.get("/api/agent/generate/roles").get_json()
        assert d["ok"] and any(c["id"] == "cz_both_movers" for c in d["cards"]["cards"])
        assert any("calibration" in r for r in d["rules"])

    def test_the_landing_line(self):
        from pathlib import Path
        t = (Path(additions.__file__).parents[1] / "web" / "templates" / "_landing_projects.html").read_text("utf-8")
        assert 'href="/agent/setup">Connect an agent (optional)</a>' in t

    def test_the_ask_button_only_with_a_connected_agent(self):
        from pathlib import Path
        js = (Path(additions.__file__).parents[1] / "web" / "static" / "gen-additions.js").read_text("utf-8")
        assert "roles.ok && roles.agent_connected" in js


class TestMcpTools:
    def test_tool_shapes(self):
        from quam_state_manager import mcp
        for n in ("generate_roles", "propose_additions", "propose_card"):
            assert n in mcp.TOOLS and n not in mcp.WRITE_TOOLS
        d = mcp.TOOLS["propose_additions"][0]
        assert d["inputSchema"]["required"] == ["items"]
        assert "NEVER set a calibration value" in d["description"]
        assert mcp.TOOLS["propose_card"][0]["inputSchema"]["required"] == ["card", "reason"]

    def test_the_read_only_session_may_propose(self):
        from quam_state_manager.core import agent_backend
        assert {"generate_roles", "propose_additions", "propose_card"} <= set(agent_backend.READ_TOOLS)


# --------------------------------------------------------------- regen

class TestRegenCarriesCzVariant:
    def _state(self, macros):
        return {"qubits": {"q1": {}, "q2": {}},
                "qubit_pairs": {"q1-2": {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                                         "macros": macros}}}

    def _variant(self, macros):
        pop = regen_spec._extract_populate(self._state(macros), self._state(macros))
        return next(iter((pop.get("pairs") or {}).values()), {}).get("cz_variant")

    def test_one_variant_and_an_added_default(self):
        m = {"cz_unipolar": {"__class__": CZ, "flux_pulse_qubit": {"length": 40}},
             "cz_mine": {"__class__": CZ}, "cz": "#./cz_mine"}
        assert self._variant(m) == "unipolar"

    def test_all_five_read_as_all(self):
        m = {f"cz_{v}": {"__class__": CZ} for v in ("unipolar", "flattop", "bipolar", "SNZ", "flattop_erf")}
        assert self._variant(m) == "all"

    def test_the_old_shape_unchanged(self):
        assert self._variant({"cz_flattop": {"__class__": CZ}, "cz": "#./cz_flattop"}) == "flattop"
