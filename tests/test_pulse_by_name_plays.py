"""A pair gate that plays a channel op BY NAME (gl-0mj).

quam_builder's CZGate plays ``moving_qubit.z.play(get_pulse_name(pulse))``,
and ``get_pulse_name`` returns ``pulse.id`` when it is set. An INLINE slot
pulse whose ``id`` names a z operation therefore plays that operation -- no
pointer names it, so the pointer-only ``used_by`` saw nothing, and a rename
or delete left the gate playing a name that no longer exists.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from quam_state_manager.core.gate_pulse_ids import (
    BY_NAME_LABEL, named_slot_players, named_slot_plays)
from quam_state_manager.web.app import create_app

QC = "quam.components.pulses."
GATE = ("quam_builder.architecture.superconducting.custom_gates."
        "flux_tunable_transmon_pair.two_qubit_gates.CZGate")
OP = "qubits.q1.z.operations.cz_flat_q2"
HOLDER = "qubit_pairs.q1-q2.macros.cz_flat.flux_pulse_qubit.id"


def _state() -> dict:
    flat = {"__class__": QC + "SquarePulse", "length": 40, "amplitude": 0.2}
    return {
        "qubits": {
            "q1": {"id": "q1",
                   "z": {"operations": {"cz_flat_q2": dict(flat),
                                        "cz_other": dict(flat)}},
                   "xy": {"operations": {}}},
            "q2": {"id": "q2",
                   # same NAME on another qubit's line: not played by the slot
                   "z": {"operations": {"cz_flat_q2": dict(flat)}},
                   "xy": {"operations": {}}},
        },
        "qubit_pairs": {
            "q1-q2": {
                "qubit_control": "#/qubits/q1",
                "qubit_target": "#/qubits/q2",
                "moving_qubit": "control",
                "coupler": None,
                "macros": {
                    "cz_flat": {
                        "__class__": GATE,
                        "flux_pulse_qubit": {**flat, "id": "cz_flat_q2"},
                        "coupler_flux_pulse": None,
                    },
                    # a POINTER slot keeps the pointer re-pointing path
                    "cz_ptr": {
                        "__class__": GATE,
                        "flux_pulse_qubit": "#/qubits/q1/z/operations/cz_other",
                        "coupler_flux_pulse": None,
                    },
                },
            },
        },
    }


def _wiring() -> dict:
    return {"wiring": {"qubits": {"q1": {"z": {"opx_output": "LF/1/1"}}}},
            "network": {"host": "127.0.0.1"}}


class TestTheScan:
    def test_the_inline_id_on_the_moving_qubit_z_is_counted(self):
        assert named_slot_players(_state(), OP) == [HOLDER]

    def test_the_same_name_on_another_qubit_is_not_counted(self):
        assert named_slot_players(_state(), "qubits.q2.z.operations.cz_flat_q2") == []

    def test_a_target_moving_pair_plays_on_the_target(self):
        s = _state()
        s["qubit_pairs"]["q1-q2"]["moving_qubit"] = "target"
        assert named_slot_players(s, OP) == []
        assert named_slot_players(s, "qubits.q2.z.operations.cz_flat_q2") == [HOLDER]

    def test_a_pointer_slot_is_not_a_by_name_play(self):
        # the slot points at an op on the moving qubit's own line, whose
        # name the pointer's last segment spells: still a pointer user only
        ops = {op for op, _h, _s in named_slot_plays(_state())}
        assert ops == {OP}

    def test_an_op_with_no_by_name_user_has_none(self):
        assert named_slot_players(_state(), "qubits.q1.z.operations.cz_other") == []


@pytest.fixture
def client(tmp_path: Path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps(_wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "inst"))
    c = app.test_client()
    assert c.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    c._app = app
    return c


def _store(c):
    return next(iter(c._app.config["contexts"].values()))["store"]


class TestTheDetailSaysSo:
    def test_the_rename_step_counts_and_labels_it(self, client):
        html = client.get(f"/pulse/detail?path={OP}").data.decode()
        assert HOLDER in html
        assert "plays by name" in html
        assert "1 reference(s)" in html

    def test_another_qubits_op_of_the_same_name_shows_no_user(self, client):
        html = client.get(
            "/pulse/detail?path=qubits.q2.z.operations.cz_flat_q2").data.decode()
        assert HOLDER not in html


class TestRename:
    def test_the_slot_id_follows_the_rename_in_one_undo_group(self, client):
        r = client.post("/api/pulse/rename", data={
            "path": OP, "new_name": "cz_flat_v2", "retarget": "1"})
        assert r.status_code == 200, r.data
        st = _store(client)
        assert st.get_value(HOLDER) == "cz_flat_v2"
        assert st.get_value("qubits.q1.z.operations.cz_flat_v2.length") == 40
        assert "1 reference(s) re-pointed" in r.data.decode()
        # the other qubit's op of the old name is untouched
        assert st.get_value("qubits.q2.z.operations.cz_flat_q2.length") == 40
        # ONE Ctrl+Z restores both the op and the slot's name
        assert client.post("/undo").status_code == 200
        st = _store(client)
        assert st.get_value(HOLDER) == "cz_flat_q2"
        assert st.get_value(f"{OP}.length") == 40
        with pytest.raises(Exception):
            st.get_value("qubits.q1.z.operations.cz_flat_v2")

    def test_without_retarget_the_slot_is_counted_as_dangling(self, client):
        r = client.post("/api/pulse/rename", data={
            "path": OP, "new_name": "cz_flat_v2", "retarget": "0"})
        assert r.status_code == 200
        assert "1 reference(s) now dangle" in r.data.decode()
        assert _store(client).get_value(HOLDER) == "cz_flat_q2"

    def test_an_op_with_no_by_name_user_renames_as_before(self, client):
        r = client.post("/api/pulse/rename", data={
            "path": "qubits.q2.z.operations.cz_flat_q2", "new_name": "cz_q2b",
            "retarget": "1"})
        assert r.status_code == 200
        assert "re-pointed" not in r.data.decode()
        assert _store(client).get_value(HOLDER) == "cz_flat_q2"

    def test_a_pointer_slot_is_re_pointed_as_before(self, client):
        r = client.post("/api/pulse/rename", data={
            "path": "qubits.q1.z.operations.cz_other", "new_name": "cz_other2",
            "retarget": "1"})
        assert r.status_code == 200
        raw = _store(client).merged["qubit_pairs"]["q1-q2"]["macros"][
            "cz_ptr"]["flux_pulse_qubit"]
        assert raw == "#/qubits/q1/z/operations/cz_other2"


class TestDelete:
    def test_delete_is_refused_naming_the_gate(self, client):
        r = client.post("/api/pulse/delete", data={"path": OP})
        assert r.status_code == 409
        body = r.data.decode()
        assert HOLDER in body and BY_NAME_LABEL in body
        assert _store(client).get_value(f"{OP}.length") == 40

    def test_a_forced_delete_says_the_gate_now_plays_a_missing_name(self, client):
        r = client.post("/api/pulse/delete", data={"path": OP, "force": "1"})
        assert r.status_code == 200
        assert "no longer exists" in r.data.decode()

    def test_the_other_qubits_op_deletes_without_refusal(self, client):
        r = client.post("/api/pulse/delete",
                        data={"path": "qubits.q2.z.operations.cz_flat_q2"})
        assert r.status_code == 200

    def test_the_delete_step_warns(self, client):
        html = client.get(f"/pulse/detail?path={OP}").data.decode()
        assert "leaves the gate playing a name that no longer exists" in " ".join(html.split())
