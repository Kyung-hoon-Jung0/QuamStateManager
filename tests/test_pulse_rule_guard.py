"""A write that breaks a catalog pulse class's OWN rule is never staged.

An odd ``SNZPulse.flat_length`` was created and applied; quam's
``generate_config()`` then raised for the whole chip and every node failed.
SM's in-process synthesis already raises the class's own rule
(``waveform_synth.PulseRuleError``), so every editing door asks it first
(``routes._synth_write_refusal``). Only a rule violation refuses: an unknown
class, an unresolved pointer or anything SM merely cannot draw never does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core import waveform_synth
from quam_state_manager.web.app import create_app

QC = "quam.components.pulses."
QB = "quam_builder.architecture.superconducting.components.pulses."
Z = "qubits.q1.z.operations"


def _state() -> dict:
    return {
        "qubits": {
            "q1": {
                "id": "q1",
                "z": {
                    "cz_flat_length": 20,
                    "operations": {
                        "snz": {"__class__": QB + "SNZPulse", "amplitude": 0.1,
                                "flat_length": 20, "t_phi_eff": 2.0,
                                "length": 40},
                        "flat": {"__class__": QC + "SquarePulse",
                                 "amplitude": 0.1, "length": 40},
                        # its flat_length is a leaf outside every pulse (one hop)
                        "snz_mirror": {"__class__": QB + "SNZPulse",
                                       "amplitude": 0.05,
                                       "flat_length": "#/qubits/q1/z/cz_flat_length",
                                       "t_phi_eff": 2.0, "length": 40},
                        "broken": {"__class__": QB + "SNZPulse", "amplitude": 0.1,
                                   "flat_length": 45, "t_phi_eff": 2.0,
                                   "length": 60},
                        "mystery": {"__class__": "some_lab.pulses.OddPulse",
                                    "flat_length": 20, "length": 40},
                    },
                },
            },
        },
        "active_qubit_names": ["q1"],
    }


@pytest.fixture
def client(tmp_path: Path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}),
                                        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    return c


def _peek(c, path):
    return c.get(f"/field/peek?dot_path={path}").get_json()["values"][path]


def _create(c, flat_length):
    return c.post("/api/pulse/create", data={
        "target_kind": "qubit", "qubit": "q1", "channel": "z",
        "op_name": "snz_new", "pulse_type": "SNZPulse",
        "amplitude": "0.05", "flat_length": str(flat_length), "t_phi_eff": "2.0",
    })


def test_the_synthesis_marks_a_rule_violation_and_nothing_else():
    odd = waveform_synth.synthesize("SNZPulse", {
        "amplitude": 0.1, "flat_length": 45, "t_phi_eff": 2.0, "length": 60})
    assert odd["ok"] is False and odd.get("rule_violation") is True
    assert "must be even" in odd["error"]
    unknown = waveform_synth.synthesize("some_lab.pulses.OddPulse", {"length": 4})
    assert not unknown.get("rule_violation")
    missing = waveform_synth.synthesize("SNZPulse", {"flat_length": 20})
    assert missing["ok"] is False and not missing.get("rule_violation")


class TestCreate:
    def test_an_odd_flat_length_is_refused_and_nothing_is_staged(self, client):
        r = _create(client, 45)
        assert r.status_code == 400
        assert b"must be even" in r.data and b"Nothing was written" in r.data
        assert client.get(f"/field/peek?dot_path={Z}.snz_new").get_json()[
            "values"].get(f"{Z}.snz_new") is None

    def test_an_even_flat_length_is_created(self, client):
        r = _create(client, 44)
        assert r.status_code == 200, r.data[:300]
        assert _peek(client, f"{Z}.snz_new.flat_length") == 44


class TestFieldEdit:
    def test_field_edit_refuses_an_odd_flat_length(self, client):
        r = client.post("/field/edit", data={"dot_path": f"{Z}.snz.flat_length",
                                              "value": "45"})
        assert r.status_code == 400
        body = r.get_json()
        assert body["lab_refused"] is True and "must be even" in body["error"]
        assert _peek(client, f"{Z}.snz.flat_length") == 20

    def test_field_edit_stages_an_even_flat_length(self, client):
        r = client.post("/field/edit", data={"dot_path": f"{Z}.snz.flat_length",
                                              "value": "44"})
        assert r.status_code == 200, r.data[:300]
        assert _peek(client, f"{Z}.snz.flat_length") == 44

    def test_edit_batch_refuses_an_odd_flat_length(self, client):
        r = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": f"{Z}.flat.amplitude", "value": "0.2"},
            {"dot_path": f"{Z}.snz.flat_length", "value": "45"}]})
        assert r.status_code == 400
        body = r.get_json()
        assert "must be even" in body["error"]
        assert [x["lab_refused"] for x in body["results"]] == [False, True]
        assert _peek(client, f"{Z}.snz.flat_length") == 20
        assert _peek(client, f"{Z}.flat.amplitude") == 0.1

    def test_edit_batch_stages_an_even_flat_length(self, client):
        r = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": f"{Z}.snz.flat_length", "value": "44"}]})
        assert r.status_code == 200, r.data[:300]
        assert _peek(client, f"{Z}.snz.flat_length") == 44

    def test_a_pulse_pointing_at_the_written_leaf_is_judged_too(self, client):
        # the written leaf is in no pulse; a pulse's flat_length points at it
        client.get("/pulses")           # the Pulses page warms the index
        r = client.post("/field/edit", data={"dot_path": "qubits.q1.z.cz_flat_length",
                                              "value": "45"})
        assert r.status_code == 400
        assert "snz_mirror" in r.get_json()["error"]
        assert _peek(client, "qubits.q1.z.cz_flat_length") == 20
        assert client.post("/field/edit", data={
            "dot_path": "qubits.q1.z.cz_flat_length", "value": "22"}).status_code == 200

    def test_an_unknown_class_is_never_refused(self, client):
        r = client.post("/field/edit", data={
            "dot_path": f"{Z}.mystery.flat_length", "value": "45"})
        assert r.status_code == 200, r.data[:300]
        assert _peek(client, f"{Z}.mystery.flat_length") == 45

    def test_a_pulse_already_breaking_the_rule_takes_an_unrelated_edit(self, client):
        assert client.post("/field/edit", data={"dot_path": f"{Z}.broken.amplitude",
                                                 "value": "0.3"}).status_code == 200
        r = client.post("/field/edit", data={"dot_path": f"{Z}.broken.flat_length",
                                              "value": "47"})
        assert r.status_code == 400
