"""Route tests for the Explorer CRUD endpoints: /field/create, /field/delete,
/field/refs, /schema/missing-keys — plus their policy guards and the
modifier-is-the-only-type-gate rule."""

from __future__ import annotations

import json

import pytest

from tests.test_type_policy import MANIFEST, _state


@pytest.fixture
def client(tmp_path):
    from quam_state_manager.core import type_policy as tp
    from quam_state_manager.web.app import create_app
    state = _state()
    state["active_qubit_names"] = ["qA1"]
    (tmp_path / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (tmp_path / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
    c = app.test_client()
    c.post("/load", data={"folder": str(tmp_path)})
    with app.app_context():
        ctx = app.config["contexts"][app.config["active_context"]]
        ctx["store"].type_policy = tp.TypePolicy(MANIFEST, {})
    return c


class TestCreate:
    def test_scalar_create_with_type_hint(self, client):
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.extras.T2_star", "value": "1.5e-6",
            "expect_type": "number"})
        assert r.status_code == 200
        peek = client.get("/field/peek?dot_path=qubits.qA1.extras.T2_star").get_json()
        assert peek["values"]["qubits.qA1.extras.T2_star"] == 1.5e-6

    def test_subtree_create_json(self, client):
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.xy.operations.y90",
            "value": '{"__class__": "q.Pulse", "amplitude": 0.05, "length": 20}'})
        assert r.status_code == 200

    def test_env_schema_enforced_via_modifier_not_route(self, client):
        # a created pulse with a wrong-typed field is blocked by the MODIFIER
        # gate (check_subtree), not any route-level duplicate
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.xy.operations.bad",
            "value": '{"__class__": "q.Pulse", "amplitude": "oops", "length": 20}'})
        assert r.status_code == 400
        assert r.get_json()["error_kind"] == "type_mismatch"

    def test_existing_key_conflict(self, client):
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.f_01", "value": "1"})
        assert r.status_code == 400
        assert "already exists" in r.get_json()["error"]

    def test_missing_parent_400(self, client):
        r = client.post("/field/create", data={
            "dot_path": "qubits.qZZ.brand.new", "value": "1"})
        assert r.status_code == 400

    def test_policy_blocks(self, client):
        for path in ("active_qubit_names.1", "qubits.qA1.xy.__class__"):
            r = client.post("/field/create", data={"dot_path": path, "value": "x"})
            assert r.status_code == 400
            assert r.get_json().get("error_kind") == "policy"

    def test_assign_type_convenience(self, client):
        client.post("/field/create", data={
            "dot_path": "qubits.qA1.extras.count", "value": "3",
            "expect_type": "int", "assign_type": "1"})
        j = client.get("/field/type-assignments").get_json()
        assert "qubits.qA1.extras.count" in j["assignments"]


class TestDelete:
    def test_delete_reports_counts(self, client):
        r = client.post("/field/delete", data={
            "dot_path": "qubits.qA1.xy.operations.x180"})
        j = r.get_json()
        assert r.status_code == 200 and j["ok"] is True
        assert j["removed_leaves"] >= 2
        # the alias pointer "#./x180" now dangles and is reported
        assert j["dangling_refs"] >= 1

    def test_delete_then_undo_restores(self, client):
        client.post("/field/delete", data={"dot_path": "qubits.qA1.extras"})
        peek = client.get("/field/peek?dot_path=qubits.qA1.extras.free_form").get_json()
        assert peek["values"]["qubits.qA1.extras.free_form"] is None

    def test_policy_blocks_identity_and_membership(self, client):
        for path in ("qubits.qA1.id", "active_qubit_names",
                     "qubits.qA1.confusion_matrix.0"):
            r = client.post("/field/delete", data={"dot_path": path})
            assert r.status_code == 400, path

    def test_top_level_blocked(self, client):
        r = client.post("/field/delete", data={"dot_path": "qubits"})
        assert r.status_code == 400


class TestRefs:
    def test_counts_pointers_into_subtree(self, client):
        j = client.get("/field/refs?dot_path=qubits.qA1.xy.operations.x180").get_json()
        assert j["ok"] is True and j["total"] >= 1
        assert any(r["pointer"] == "#./x180" for r in j["refs"])

    def test_no_refs(self, client):
        j = client.get("/field/refs?dot_path=qubits.qA1.f_01").get_json()
        assert j["total"] == 0


class TestMissingKeys:
    def test_missing_schema_keys_listed(self, client):
        j = client.get("/schema/missing-keys?scope=qubits.qA1.xy").get_json()
        assert j["warm"] is True
        keys = {m["key"] for m in j["missing"]}
        assert "intermediate_frequency" not in keys      # present in state
        # q.Channel has no other missing fields in the fixture; add one via
        # delete then re-query
        client.post("/field/delete", data={
            "dot_path": "qubits.qA1.xy.intermediate_frequency"})
        j2 = client.get("/schema/missing-keys?scope=qubits.qA1.xy").get_json()
        m = {x["key"]: x for x in j2["missing"]}
        assert "intermediate_frequency" in m
        assert m["intermediate_frequency"]["expected_type"] == "number"

    def test_cold_manifest(self, tmp_path):
        from quam_state_manager.web.app import create_app
        (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        (tmp_path / "wiring.json").write_text('{"wiring": {}}', encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        j = c.get("/schema/missing-keys?scope=qubits.qA1").get_json()
        assert j["warm"] is False and j["missing"] == []
