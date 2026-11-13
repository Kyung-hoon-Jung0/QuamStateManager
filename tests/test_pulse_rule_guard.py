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


def _load(tmp_path: Path, state: dict):
    folder = tmp_path / "chip"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}, "network": {}}),
                                        encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_inst"))
    c = app.test_client()
    c.post("/load", data={"folder": str(folder)})
    return c


@pytest.fixture
def client(tmp_path: Path):
    return _load(tmp_path, _state())


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


# ---------------------------------------------------------------------------
# a config pulse length QOP refuses (not a multiple of 4 ns, or under
# the QOP minimum) -- generate_config() passes, then QOP refuses every program
# ---------------------------------------------------------------------------

def _create_square(c, length):
    return c.post("/api/pulse/create", data={
        "target_kind": "qubit", "qubit": "q1", "channel": "z",
        "op_name": "sq_new", "pulse_type": "SquarePulse",
        "amplitude": "0.05", "length": str(length)})


class TestPulseLength:
    def test_the_rule(self):
        from quam_state_manager.core.spec_constraints import pulse_length_problem
        assert "multiple of 4" in pulse_length_problem(42)
        assert pulse_length_problem(44) is None
        assert pulse_length_problem(4) is None             # QOP >= 3.3 / unknown
        assert "16 ns minimum" in pulse_length_problem(8, "3.2.1")
        assert pulse_length_problem(8, "3.3") is None
        assert "no QOP version" in pulse_length_problem(0)
        assert pulse_length_problem("#./inferred_length") is None

    def test_create_refuses_a_length_not_a_multiple_of_4(self, client):
        r = _create_square(client, 42)
        assert r.status_code == 400 and b"multiple of 4" in r.data
        assert client.get(f"/field/peek?dot_path={Z}.sq_new").get_json()[
            "values"].get(f"{Z}.sq_new") is None
        assert _create_square(client, 44).status_code == 200

    def test_an_inferred_length_is_judged_rounded(self, client):
        # 22 + 2 + 2 = 26 raw, rounded to 28 by the class: accepted
        assert _create(client, 22).status_code == 200

    def test_field_edit_refuses_a_length_not_a_multiple_of_4(self, client):
        r = client.post("/field/edit", data={"dot_path": f"{Z}.flat.length",
                                              "value": "42"})
        assert r.status_code == 400 and "multiple of 4" in r.get_json()["error"]
        assert _peek(client, f"{Z}.flat.length") == 40
        assert client.post("/field/edit", data={
            "dot_path": f"{Z}.flat.length", "value": "44"}).status_code == 200

    def test_edit_batch_refuses_a_length_not_a_multiple_of_4(self, client):
        r = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": f"{Z}.flat.length", "value": "42"}]})
        assert r.status_code == 400 and "multiple of 4" in r.get_json()["error"]

    def test_a_lab_class_with_a_stored_length_is_judged_too(self, client):
        # the rule is the QOP's, not the class's: a class SM cannot draw is
        # still refused a length QOP refuses (its own rules stay unjudged)
        r = client.post("/field/edit", data={"dot_path": f"{Z}.mystery.length",
                                              "value": "42"})
        assert r.status_code == 400

    def test_the_minimum_follows_the_chip_qop_version(self, tmp_path):
        st = _state()
        st["extras"] = {"qop_version": "3.2.0"}
        c = _load(tmp_path / "a", st)
        r = c.post("/field/edit", data={"dot_path": f"{Z}.flat.length", "value": "8"})
        assert r.status_code == 400 and "16 ns minimum" in r.get_json()["error"]
        st["extras"] = {"qop_version": "3.7.3"}
        c2 = _load(tmp_path / "b", st)
        assert c2.post("/field/edit", data={"dot_path": f"{Z}.flat.length",
                                             "value": "8"}).status_code == 200


class TestEveryBrokenPulseIsRefused:
    """An independent batch skips the rows of EVERY pulse a rule breaks,
    not only the first one found; an atomic batch writes nothing."""

    def _batch(self, client, independent):
        return client.post("/field/edit-batch", json={
            "independent": independent, "updates": [
                {"dot_path": f"{Z}.broken.amplitude", "value": "0.3"},
                {"dot_path": f"{Z}.snz.flat_length", "value": "45"},
                {"dot_path": f"{Z}.flat.length", "value": "42"}]})

    def test_independent_writes_the_valid_row_and_refuses_both(self, client):
        body = self._batch(client, True).get_json()
        res = {x["dot_path"]: x for x in body["results"]}
        assert res[f"{Z}.broken.amplitude"]["applied"] is True
        assert res[f"{Z}.snz.flat_length"].get("lab_refused") is True
        assert res[f"{Z}.flat.length"].get("lab_refused") is True
        assert _peek(client, f"{Z}.broken.amplitude") == 0.3
        assert _peek(client, f"{Z}.snz.flat_length") == 20
        assert _peek(client, f"{Z}.flat.length") == 40
        msg = res[f"{Z}.flat.length"]["error"]
        assert "2 pulses" in msg and f"{Z}.snz" in msg and f"{Z}.flat" in msg

    def test_atomic_writes_nothing_and_names_both(self, client):
        r = self._batch(client, False)
        assert r.status_code == 400
        flags = [x["lab_refused"] for x in r.get_json()["results"]]
        assert flags == [False, True, True]
        assert _peek(client, f"{Z}.broken.amplitude") == 0.1

    def test_one_broken_pulse_keeps_the_single_sentence(self, client):
        r = client.post("/field/edit-batch", json={"updates": [
            {"dot_path": f"{Z}.flat.length", "value": "42"}]})
        err = r.get_json()["error"]
        assert "pulses)" not in err and f"{Z}.flat" in err


class TestPulseLengthDiagnostics:
    def _found(self, st):
        from quam_state_manager.core import diagnostics
        from quam_state_manager.core.loader import QuamStore
        return [f for f in diagnostics.lint_state(QuamStore.from_dicts(st, {}))
                if f.category == "waveform_length"]

    def test_one_finding_per_pulse_jumping_to_the_length(self):
        st = _state()
        ops = st["qubits"]["q1"]["z"]["operations"]
        ops["flat"]["length"] = 42
        ops["mystery"]["length"] = 42
        found = self._found(st)
        assert sorted(f.jump_path for f in found) == [
            f"{Z}.flat.length", f"{Z}.mystery.length"]
        assert all(f.severity == "error" for f in found)

    def test_a_clean_chip_has_none(self):
        assert self._found(_state()) == []


# ---------------------------------------------------------------------------
# a new DRAG pulse's anharmonicity defaults to the qubit's own
# ---------------------------------------------------------------------------

def _drag_state() -> dict:
    st = _state()
    q1 = st["qubits"]["q1"]
    q1["anharmonicity"] = 210e6
    q1["anh_drive"] = 190e6
    q1["xy"] = {"operations": {
        "x180": {"__class__": QC + "DragCosinePulse", "length": 40,
                 "amplitude": 0.1, "alpha": 0.0, "axis_angle": 0.0,
                 "anharmonicity": "#/qubits/q1/anh_drive"},
        "x90": {"__class__": QC + "DragCosinePulse", "length": 40,
                "amplitude": 0.05, "alpha": 0.0, "axis_angle": 0.0,
                "anharmonicity": "#../x180/anharmonicity"}}}
    st["qubits"]["q2"] = {"id": "q2", "anharmonicity": 200e6,
                          "xy": {"operations": {}}}
    st["qubits"]["q3"] = {"id": "q3", "xy": {"operations": {}}}
    return st


class TestDragDefault:
    def test_the_form_names_each_qubits_value(self, tmp_path):
        import re
        c = _load(tmp_path, _drag_state())
        html = c.get("/pulse/new").data.decode()
        m = re.search(r'id="pulse-qubit-defaults-data"[^>]*>(.*?)</script>', html, re.S)
        got = json.loads(m.group(1))
        assert got["q1"]["anharmonicity"]["value"] == "#/qubits/q1/anh_drive"
        assert got["q2"]["anharmonicity"]["value"] == "#/qubits/q2/anharmonicity"
        assert got["q3"]["anharmonicity"]["value"] is None
        assert "placeholder" in got["q3"]["anharmonicity"]["note"]

    def test_the_default_creates_and_previews(self, tmp_path):
        c = _load(tmp_path, _drag_state())
        prev = c.post("/api/pulse/synth", json={
            "qclass": "DragCosinePulse", "params": {
                "length": "40", "amplitude": "0.1", "alpha": "0.1", "axis_angle": "0",
                "anharmonicity": "#/qubits/q2/anharmonicity"}}).get_json()
        assert prev["ok"] is True, prev
        r = c.post("/api/pulse/create", data={
            "target_kind": "qubit", "qubit": "q2", "channel": "xy",
            "op_name": "x180", "pulse_type": "DragCosinePulse", "length": "40",
            "amplitude": "0.1", "alpha": "0.1", "axis_angle": "0",
            "anharmonicity": "#/qubits/q2/anharmonicity"})
        assert r.status_code == 200, r.data[:300]
        assert _peek(c, "qubits.q2.xy.operations.x180.anharmonicity") == "#/qubits/q2/anharmonicity"

    def test_the_form_script_selfcheck(self):
        import os
        import shutil
        import subprocess
        node = shutil.which("node")
        if not node:
            pytest.skip("node not installed")
        here = Path(__file__).parent
        r = subprocess.run([node, str(here / "pulses_qubit_default_selfcheck.cjs")],
                           capture_output=True, text=True, env=dict(os.environ))
        if r.returncode == 2 and "jsdom not installed" in r.stderr:
            pytest.skip("jsdom not installed")
        assert r.returncode == 0, r.stdout + r.stderr
