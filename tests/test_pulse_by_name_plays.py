"""A pair gate that plays a channel op BY NAME.

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

    def test_the_forced_delete_answers_a_warning_naming_the_gate(self, client):
        r = client.post("/api/pulse/delete", data={"path": OP, "force": "1"})
        body = r.data.decode()
        assert "toast-warning" in body and "toast-success" not in body
        assert HOLDER in body and "fails" in body
        assert "diagnostics-changed" in r.headers.get("HX-Trigger", "")

    def test_after_the_forced_delete_diagnostics_lists_the_missing_name(self, client):
        before = client.get("/diagnostics/summary").data.decode()
        client.post("/api/pulse/delete", data={"path": OP, "force": "1"})
        after = client.get("/diagnostics/summary").data.decode()
        assert after != before
        page = client.get("/diagnostics").data.decode()
        assert "cz_flat_q2" in page and "by name" in page

    def test_a_plain_delete_stays_a_success(self, client):
        r = client.post("/api/pulse/delete",
                        data={"path": "qubits.q2.z.operations.cz_flat_q2"})
        assert "toast-success" in r.data.decode()

    def test_the_other_qubits_op_deletes_without_refusal(self, client):
        r = client.post("/api/pulse/delete",
                        data={"path": "qubits.q2.z.operations.cz_flat_q2"})
        assert r.status_code == 200

    def test_the_delete_step_warns(self, client):
        html = client.get(f"/pulse/detail?path={OP}").data.decode()
        assert "leaves the gate playing a name that no longer exists" in " ".join(html.split())


class TestTheJsonTreeDeleteDoor:
    def test_the_confirm_count_names_the_by_name_play(self, client):
        j = client.get("/field/refs?dot_path=qubits.q1.z").get_json()
        assert j["ok"] and j["by_name"] == [HOLDER]
        j = client.get("/field/refs?dot_path=qubits.q2.z").get_json()
        assert j["by_name"] == []

    def test_a_channel_delete_that_takes_the_op_is_refused_naming_the_slot(self, client):
        r = client.post("/field/delete", data={"dot_path": "qubits.q1.z"})
        assert r.status_code == 409, r.data[:300]
        j = r.get_json()
        assert j["error_kind"] == "by_name" and j["by_name"] == [HOLDER]
        assert BY_NAME_LABEL in j["error"]
        assert _store(client).get_value(f"{OP}.length") == 40

    def test_forced_it_deletes_and_says_so(self, client):
        r = client.post("/field/delete", data={"dot_path": "qubits.q1.z", "force": "1"})
        assert r.status_code == 200, r.data[:300]
        assert "no longer exists" in r.get_json().get("warning", "")

    def test_the_other_qubits_channel_is_not_refused_for_a_name(self, client):
        r = client.post("/field/delete", data={"dot_path": "qubits.q2.z"})
        assert r.status_code == 200, r.data[:300]

    def test_a_delete_that_takes_the_slot_too_loses_nothing(self, client):
        r = client.post("/field/delete", data={"dot_path": "qubit_pairs.q1-q2.macros.cz_flat"})
        assert (r.get_json() or {}).get("error_kind") != "by_name"


class TestTheDeleteTogetherDoor:
    def _post(self, client, paths, **extra):
        return client.post("/api/pulse/delete-together",
                           json={"path": paths[0], "paths": paths, **extra})

    def test_a_set_without_the_slot_is_refused_naming_it(self, client):
        r = self._post(client, [OP, "qubits.q1.z.operations.cz_other"])
        assert r.status_code == 409, r.data[:300]
        j = r.get_json()
        assert j["error_kind"] == "by_name" and j["by_name"] == [HOLDER]
        assert _store(client).get_value(f"{OP}.length") == 40

    def test_forced_it_goes_through(self, client):
        r = self._post(client, [OP, "qubits.q1.z.operations.cz_other"], force="1")
        assert r.status_code == 200, r.data[:300]
        with pytest.raises(Exception):
            _store(client).get_value(f"{OP}.length")

    def test_a_set_holding_the_slot_is_not_refused_for_a_name(self, client):
        r = self._post(client, [OP, "qubit_pairs.q1-q2.macros.cz_flat.flux_pulse_qubit"])
        assert (r.get_json() or {}).get("error_kind") != "by_name", r.data[:300]


class TestTheRowCountFollowsTheSlot:
    """The Pulses table's used_by: by-name users are counted like pointer
    users, and refreshed the way pointer users are (a write at a slot's id
    is never a value-only step, so the rows rebuild cold)."""

    def _row_users(self, client):
        ctx = next(iter(client._app.config["contexts"].values()))
        from quam_state_manager.core.pulse_index import PulseIndex
        pi = ctx.get("pulse_index") or PulseIndex(ctx["store"])
        ctx["pulse_index"] = pi
        return pi, pi.row(OP)["used_by"]

    def test_the_row_counts_the_slot_labelled(self, client):
        _pi, users = self._row_users(client)
        assert users == [f"{HOLDER} ({BY_NAME_LABEL})"]

    def test_an_id_edit_refreshes_the_count(self, client):
        pi, _users = self._row_users(client)
        ctx = next(iter(client._app.config["contexts"].values()))
        ctx["modifier"].set_value(HOLDER, "something_else")
        assert pi.row(OP)["used_by"] == []
        ctx["modifier"].set_value(HOLDER, "cz_flat_q2")
        assert pi.row(OP)["used_by"] == [f"{HOLDER} ({BY_NAME_LABEL})"]

    def test_a_phase_edit_stays_incremental(self, client):
        from quam_state_manager.core.pulse_index import is_by_name_holder
        assert not is_by_name_holder("qubit_pairs.q1-q2.macros.cz_flat.phase_shift_control")
        assert is_by_name_holder(HOLDER)


class TestTheEditBatchDeleteDoor:
    """/field/edit-batch delete rows are judged like /field/delete: a
    container delete that takes an op a slot plays by name is refused."""

    def _post(self, client, paths, **extra):
        return client.post("/field/edit-batch", json={
            "updates": [{"dot_path": p, "delete": True} for p in paths], **extra})

    def test_a_channel_delete_is_refused_and_nothing_written(self, client):
        r = self._post(client, ["qubits.q1.z"])
        assert r.status_code == 409, r.data[:300]
        j = r.get_json()
        assert j["error_kind"] == "by_name" and j["by_name"] == [HOLDER]
        # the batch door has no confirm step of its own: it names the token
        assert "force=1" in j["error"] and "confirm step" not in j["error"]
        assert _store(client).get_value(f"{OP}.length") == 40

    def test_the_qubit_itself_is_refused_too(self, client):
        r = self._post(client, ["qubits.q1"])
        assert r.status_code == 409 and r.get_json()["error_kind"] == "by_name"

    def test_independent_mode_is_refused_too(self, client):
        r = self._post(client, ["qubits.q1.z"], independent=True)
        assert r.status_code == 409
        assert _store(client).get_value(f"{OP}.length") == 40

    def test_forced_it_deletes_and_one_undo_restores(self, client):
        r = self._post(client, ["qubits.q1.z", "qubits.q2.xy"], force="1")
        assert r.status_code == 200, r.data[:300]
        with pytest.raises(Exception):
            _store(client).get_value(f"{OP}.length")
        assert client.post("/undo").status_code == 200
        st = _store(client)
        assert st.get_value(f"{OP}.length") == 40
        assert st.get_value("qubits.q2.xy") == {"operations": {}}

    def test_the_slot_owner_deleted_with_it_is_not_refused(self, client):
        r = self._post(client, ["qubits.q1.z", "qubit_pairs.q1-q2"])
        assert (r.get_json() or {}).get("error_kind") != "by_name", r.data[:300]

    def test_an_unrelated_container_is_unchanged(self, client):
        r = self._post(client, ["qubits.q2.z"])
        assert r.status_code == 200, r.data[:300]


class TestTheDiagnosticsFinding:
    def _found(self, st):
        from quam_state_manager.core import diagnostics
        from quam_state_manager.core.loader import QuamStore
        return [f for f in diagnostics.lint_state(QuamStore.from_dicts(st, _wiring()))
                if f.category == "cz.gate_plays_missing_name"]

    def test_a_slot_naming_a_missing_op_is_found(self):
        st = _state()
        del st["qubits"]["q1"]["z"]["operations"]["cz_flat_q2"]
        found = self._found(st)
        assert [f.jump_path for f in found] == [HOLDER]
        assert "cz_flat_q2" in found[0].message

    def test_the_name_on_another_qubit_does_not_count(self):
        st = _state()
        del st["qubits"]["q1"]["z"]["operations"]["cz_flat_q2"]
        assert "cz_flat_q2" in st["qubits"]["q2"]["z"]["operations"]
        assert len(self._found(st)) == 1

    def test_a_chip_whose_slots_resolve_has_none(self):
        assert self._found(_state()) == []


class TestTheRowCountFollowsTheChannel:
    """A pair field that moves the slot to another channel (moving_qubit and
    friends) is a value write too, and the by-name count must follow it."""

    def _pi(self, client):
        ctx = next(iter(client._app.config["contexts"].values()))
        from quam_state_manager.core.pulse_index import PulseIndex
        pi = ctx.get("pulse_index") or PulseIndex(ctx["store"])
        ctx["pulse_index"] = pi
        return ctx, pi

    def test_a_moving_qubit_edit_moves_the_count(self, client):
        ctx, pi = self._pi(client)
        q2 = "qubits.q2.z.operations.cz_flat_q2"
        assert pi.row(OP)["used_by"] == [f"{HOLDER} ({BY_NAME_LABEL})"]
        assert pi.row(q2)["used_by"] == []
        ctx["modifier"].set_value("qubit_pairs.q1-q2.moving_qubit", "target")
        assert pi.row(OP)["used_by"] == []
        assert pi.row(q2)["used_by"] == [f"{HOLDER} ({BY_NAME_LABEL})"]

    def test_the_channel_fields_are_holders(self):
        from quam_state_manager.core.pulse_index import is_by_name_holder
        for p in ("qubit_pairs.q1-q2.moving_qubit", "qubit_pairs.q1-q2.qubit_control",
                  "qubit_pairs.q1-q2.coupler",
                  "qubit_pairs.q1-q2.macros.cz_flat.spectator_qubits.q3"):
            assert is_by_name_holder(p), p
        assert not is_by_name_holder("qubit_pairs.q1-q2.extras.note")
