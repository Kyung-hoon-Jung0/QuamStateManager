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

    def test_existing_key_error_is_not_wrapped_in_quotes(self, client):
        """JT-22: str(KeyError) is the message's repr, so the Explorer's
        add-key panel showed '"Cannot create ...: key already exists"' with
        literal double quotes around it."""
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.f_01", "value": "1"})
        err = r.get_json()["error"]
        assert err == "Cannot create 'qubits.qA1.f_01': key already exists", err

    def test_missing_parent_400(self, client):
        r = client.post("/field/create", data={
            "dot_path": "qubits.qZZ.brand.new", "value": "1"})
        assert r.status_code == 400

    def test_policy_blocks(self, client):
        for path in ("active_qubit_names.1", "qubits.qA1.xy.__class__"):
            r = client.post("/field/create", data={"dot_path": path, "value": "x"})
            assert r.status_code == 400
            assert r.get_json().get("error_kind") == "policy"

    def _peek(self, client, path):
        return client.get("/field/peek?dot_path=" + path).get_json()["values"].get(path, "ABSENT")

    @pytest.mark.parametrize("typ,value", [
        ("list", "5"), ("dict", "[1]"), ("matrix", "[1,2]"), ("matrix", "5")])
    def test_the_chosen_shape_is_enforced(self, client, typ, value):
        """jsontree-r2-22: the list/dict branch of the hint parse is plain
        json.loads and the modifier never sees the hint, so '5' under list
        was stored as 5 -- the user's choice enforced by nobody."""
        path = "qubits.qA1.extras.qa_" + typ
        r = client.post("/field/create", data={
            "dot_path": path, "value": value, "expect_type": typ})
        assert r.status_code == 400, r.get_json()
        assert r.get_json()["error_kind"] == "type_mismatch"
        assert self._peek(client, path) in ("ABSENT", None)
        assert "qa_" + typ not in json.dumps(
            client.get("/field/peek?dot_path=qubits.qA1.extras").get_json()["values"])

    def test_a_ragged_matrix_is_still_a_matrix(self, client):
        """By design: the grammar's matrix is list<list>, not rectangular."""
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.extras.rag", "value": "[[1,2],[3]]",
            "expect_type": "matrix"})
        assert r.status_code == 200, r.get_json()
        assert self._peek(client, "qubits.qA1.extras.rag") == [[1, 2], [3]]

    @pytest.mark.parametrize("typ", ["infer", "str", "int", "real", "bool"])
    def test_an_empty_value_creates_null(self, client, typ):
        """jsontree-r2-22: empty became "" (a schema suggestion's Optional[str]
        default None was created as a real empty thread name). ("list" left
        this list in review: an explicit container choice is the next pin.)"""
        path = "qubits.qA1.extras.e_" + typ
        r = client.post("/field/create", data={
            "dot_path": path, "value": "", "expect_type": typ})
        assert r.status_code == 200, r.get_json()
        assert self._peek(client, path) is None

    @pytest.mark.parametrize("typ,empty", [
        ("dict", {}), ("list", []), ("matrix", []), ("list<int>", [])])
    def test_an_empty_container_choice_creates_the_empty_container(
            self, client, typ, empty):
        """jsontree-r2-22 review: picking dict with nothing typed made a null
        leaf -- no ＋ on it, so the children the user picked a dict for could
        not be added without first typing {} through the JSON editor."""
        path = "qubits.qA1.extras.c_" + typ.replace("<", "_").replace(">", "")
        r = client.post("/field/create", data={
            "dot_path": path, "value": "", "expect_type": typ})
        assert r.status_code == 200, r.get_json()
        got = self._peek(client, path)
        assert got == empty and type(got) is type(empty), got

    @pytest.mark.parametrize("typ", ["dict", "list", "str"])
    def test_a_class_default_none_stays_null(self, client, typ):
        """...while the schema suggestion whose class default is None (the
        panel says "null (class default)") still creates null."""
        path = "qubits.qA1.extras.d_" + typ
        r = client.post("/field/create", data={
            "dot_path": path, "value": "", "expect_type": typ,
            "empty_is_default": "1"})
        assert r.status_code == 200, r.get_json()
        assert self._peek(client, path) is None

    def test_a_quoted_empty_string_is_still_an_empty_string(self, client):
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.extras.blank", "value": '""'})
        assert r.status_code == 200, r.get_json()
        assert self._peek(client, "qubits.qA1.extras.blank") == ""

    def test_a_dotted_key_is_refused_by_name(self, client):
        """jsontree-r2-24: 'v1.2' was read as nesting -- "Parent key 'v1' not
        found" in quotes."""
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.extras.v1.2", "key": "v1.2", "value": "5"})
        assert r.status_code == 400
        j = r.get_json()
        assert j["error_kind"] == "invalid_key" and "cannot contain" in j["error"]

    def test_a_dotted_key_never_lands_inside_an_existing_dict(self, client):
        """...worse: with a dict 'v1' already there, v1["2"] = 5 was staged
        silently while the tree showed a flat 'v1.2'."""
        assert client.post("/field/create", data={
            "dot_path": "qubits.qA1.extras.v1", "value": '{"a": 1}'}).status_code == 200
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.extras.v1.2", "key": "v1.2", "value": "5"})
        assert r.status_code == 400
        assert self._peek(client, "qubits.qA1.extras.v1") == {"a": 1}

    def test_a_plain_key_with_the_key_field_still_creates(self, client):
        r = client.post("/field/create", data={
            "dot_path": "qubits.qA1.extras.plain", "key": "plain", "value": "5"})
        assert r.status_code == 200, r.get_json()

    def test_missing_parent_message_is_not_a_repr(self, client):
        r = client.post("/field/create", data={
            "dot_path": "qubits.qZZ.brand.new", "value": "1"})
        err = r.get_json()["error"]
        assert not err.startswith('"') and not err.startswith("'"), err

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

    def test_missing_key_error_is_not_wrapped_in_quotes(self, client):
        """JT-22 (same str(KeyError) glitch on the delete door)."""
        r = client.post("/field/delete", data={"dot_path": "qubits.qA1.no_such_key"})
        assert r.status_code == 400
        err = r.get_json()["error"]
        assert err == "Cannot delete 'qubits.qA1.no_such_key': key does not exist", err


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
        assert m["intermediate_frequency"]["expected_type"] == "real"

    def test_cold_manifest(self, tmp_path):
        from quam_state_manager.web.app import create_app
        (tmp_path / "state.json").write_text(json.dumps(_state()), encoding="utf-8")
        (tmp_path / "wiring.json").write_text('{"wiring": {}}', encoding="utf-8")
        app = create_app(testing=True, instance_path=str(tmp_path / "_i"))
        c = app.test_client()
        c.post("/load", data={"folder": str(tmp_path)})
        j = c.get("/schema/missing-keys?scope=qubits.qA1").get_json()
        assert j["warm"] is False and j["missing"] == []
