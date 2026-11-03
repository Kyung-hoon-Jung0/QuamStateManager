"""Synthetic identity fixtures only; no hardware stack or original state files."""
import copy
import json

import pytest

from quam_state_manager.core.port_identity import port_identity_mismatches
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.core.diagnostics import lint_state, _port_identity_findings
from quam_state_manager.core.regen_spec import reconstruct_spec
from quam_state_manager.web.app import create_app
from quam_state_manager.web.port_identity import edit_offer

KINDS = ("analog_outputs", "analog_inputs", "mw_outputs", "mw_inputs", "digital_outputs")
BASE = "ports.analog_outputs.con1.2.1"
PTR = "#/ports/analog_outputs/con1/2/1"


def state(kind="analog_outputs", fem="2", port="1"):
    return {"qubits": {"q1": {"id": "q1", "z": {
        "opx_output": "#/wiring/qubits/q1/z/opx_output"}}},
        "ports": {kind: {"con1": {fem: {port: {
            "controller_id": "con1", "fem_id": 2, "port_id": 1}}}}}}


def wiring():
    return {"wiring": {"qubits": {"q1": {"z": {"opx_output": PTR}}}}}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field,value", [("controller_id", "con2"), ("fem_id", 3), ("port_id", 4)])
def test_all_kinds_and_fields(kind, field, value):
    s = state(kind)
    s["ports"][kind]["con1"]["2"]["1"][field] = value
    original = copy.deepcopy(s)
    rows = port_identity_mismatches(s)
    assert len(rows) == 1
    assert rows[0]["path"] == f"ports.{kind}.con1.2.1"
    assert rows[0]["key"] == ("con1", "2", "1")
    assert rows[0]["differ"] == [field]
    assert rows[0]["fields"][({"controller_id": 0, "fem_id": 1, "port_id": 2}[field])] == value
    assert s == original


@pytest.mark.parametrize("fem,port", [(2, 1), ("2", "1"), (2, "1")])
def test_string_comparison_and_missing_claims(fem, port):
    s = state(fem=fem, port=port)
    assert port_identity_mismatches(s) == []
    entity = s["ports"]["analog_outputs"]["con1"][fem][port]
    entity.update(fem_id="2", port_id="1")
    assert port_identity_mismatches(s) == []
    for field in list(entity):
        del entity[field]
        assert port_identity_mismatches(s) == []


@pytest.mark.parametrize("field", ("controller_id", "fem_id", "port_id"))
@pytest.mark.parametrize("value", [5, "5", 5.0, "05", None, "#/ports/..", "#./x"])
def test_normalized_identity_claims(field, value):
    entity = {"controller_id": 5, "fem_id": 5, "port_id": 5}
    entity[field] = value
    s = {"ports": {"analog_outputs": {"5": {"5": {"5": entity}}}}}
    assert port_identity_mismatches(s) == []


@pytest.mark.parametrize("value", [6, "6", 6.0, "06", 5.5, "invalid", True])
def test_normalized_identity_mismatch(value):
    s = state(port="5")
    s["ports"]["analog_outputs"]["con1"]["2"]["5"]["port_id"] = value
    assert port_identity_mismatches(s)[0]["differ"] == ["port_id"]


@pytest.mark.parametrize("field", ("controller_id", "fem_id", "port_id"))
def test_bool_is_not_an_integer_claim(field):
    entity = {"controller_id": 1, "fem_id": 1, "port_id": 1}
    entity[field] = True
    s = {"ports": {"analog_outputs": {"1": {"1": {"1": entity}}}}}
    assert port_identity_mismatches(s)[0]["differ"] == [field]


@pytest.mark.parametrize("value", [None, "#/ports/..", "#./x"])
def test_unresolved_claims_do_not_claim_duplicates(value):
    s = state()
    ports = s["ports"]["analog_outputs"]["con1"]["2"]
    ports["1"]["port_id"] = value
    ports["9"] = dict(ports["1"])
    assert port_identity_mismatches(s) == []


def test_duplicate_claims_use_numeric_normalization():
    s = state(port="5")
    ports = s["ports"]["analog_outputs"]["con1"]["2"]
    ports["5"]["port_id"] = 5.0
    ports["9"] = dict(ports["5"], port_id="05")
    rows = port_identity_mismatches(s)
    assert len(rows) == 2
    assert all(row["duplicate_paths"] for row in rows)


@pytest.mark.parametrize("kind", KINDS)
def test_duplicates_report_both_entities(kind):
    s = state(kind)
    ports = s["ports"][kind]["con1"]["2"]
    ports["9"] = dict(ports["1"], fem_id="2", port_id="1")
    rows = port_identity_mismatches(s)
    assert len(rows) == 2
    assert rows[0]["differ"] == []
    assert rows[0]["duplicate_paths"] == [f"ports.{kind}.con1.2.9"]
    assert rows[1]["duplicate_paths"] == [f"ports.{kind}.con1.2.1"]


def test_different_kinds_and_partial_fields_do_not_claim_duplicates():
    s = state()
    s["ports"].update(state("analog_inputs")["ports"])
    s["ports"]["analog_outputs"]["con1"]["2"]["9"] = {"fem_id": 2}
    assert port_identity_mismatches(s) == []
    assert port_identity_mismatches({"ports": {"analog_inputs": None}}) == []


def test_diagnostic_error_names_values_users_and_pin():
    s = state()
    s["ports"]["analog_outputs"]["con1"]["2"]["1"]["port_id"] = 3
    rows = [f for f in lint_state(QuamStore.from_dicts(s, wiring())) if f.category == "port_identity"]
    assert len(rows) == 1
    f = rows[0]
    assert f.severity == "error"
    assert "('con1', '2', '1')" in f.message and "('con1', 2, 3)" in f.message
    assert "wiring.qubits.q1.z.opx_output" in f.detail
    assert "qubits.q1.z.opx_output" in f.detail
    assert f.jump_path == BASE + ".port_id"
    assert f.port_key == {"ctrl": "con1", "fem": "2", "port": "1", "port_type": "analog_outputs", "io": "out"}


@pytest.mark.parametrize("kind,io", [("analog_inputs", "in"), ("digital_outputs", "out")])
def test_diagnostic_identity_pin_preserves_port_kind(kind, io):
    s = state(kind)
    s["ports"][kind]["con1"]["2"]["1"]["port_id"] = 3
    findings = _port_identity_findings(s)
    assert len(findings) == 1
    assert findings[0].port_key["port_type"] == kind
    assert findings[0].port_key["io"] == io


def test_reconstruct_notes_explain_both_addresses_and_which_is_used():
    s = state()
    s["ports"]["analog_outputs"]["con1"]["2"]["1"]["port_id"] = 3
    rec = reconstruct_spec(s, wiring())
    note = next(n for n in rec.notes if BASE in n)
    assert "('con1', '2', '1')" in note and "('con1', 2, 3)" in note
    assert "Rebuild pins use the wiring pointer's dict-key address" in note
    assert "QUAM config generation uses the resolved port's identity fields" in note
    assert next(ln for ln in rec.spec["lines"] if ln["line"] == "flux")["channel"]["out_port"] == 1


@pytest.fixture
def env(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "state.json").write_text(json.dumps(state()), encoding="utf-8")
    (live / "wiring.json").write_text(json.dumps(wiring()), encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(live)}).status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    return client, ctx


@pytest.mark.parametrize("field,value,new", [("controller_id", "con2", "ports.analog_outputs.con2.2.1"),
    ("fem_id", "3", "ports.analog_outputs.con1.3.1"), ("port_id", "4", "ports.analog_outputs.con1.2.4")])
def test_edit_guard_then_explicit_move_and_undo(env, field, value, new):
    client, ctx = env
    store = ctx["store"]
    before = copy.deepcopy((store.state, store.wiring))
    data = {"dot_path": BASE + "." + field, "value": value}
    r = client.post("/field/edit", data=data)
    assert r.status_code == 409, r.get_json()
    assert r.get_json()["port_identity"]["choices"] == ["fix", "keep"]
    assert not store.change_log
    assert (store.state, store.wiring) == before
    r = client.post("/field/edit", data={**data, "port_identity_ack": "fix"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert r.get_json()["port_identity_moved"] == {"old_path": BASE, "new_path": new}
    assert str(store.get_value(new + "." + field)) == value
    assert store.get_value("wiring.qubits.q1.z.opx_output") == "#/" + new.replace(".", "/")
    assert port_identity_mismatches(store.state) == []
    assert len({e.group_id for e in store.change_log}) == 1
    assert client.post("/undo").status_code == 200
    assert (store.state, store.wiring) == before
    assert not store.change_log


def test_keep_as_typed_and_apply_confirmation(env):
    client, ctx = env
    r = client.post("/field/edit", data={"dot_path": BASE + ".port_id", "value": "4", "port_identity_ack": "keep"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert ctx["store"].get_value(BASE + ".port_id") == 4
    assert ctx["store"].get_value("wiring.qubits.q1.z.opx_output") == PTR
    preflight = client.get("/state/overwrite-live/preflight")
    assert preflight.status_code == 200
    assert BASE in preflight.get_json()["port_identity"][0]
    assert client.post("/state/apply-to-live").status_code == 200


@pytest.mark.parametrize("value", ["1", "1.0", "01"])
def test_matching_edit_does_not_ask(env, value):
    client, _ = env
    r = client.post("/field/edit", data={"dot_path": BASE + ".port_id", "value": value})
    assert r.status_code == 200 and r.get_json()["ok"]


@pytest.mark.parametrize("value", [1, "1", 1.0, "01", None, "#/ports/..", "#./x"])
def test_edit_offer_uses_normalized_claims(value):
    assert edit_offer(BASE + ".port_id", value) is None


@pytest.mark.parametrize("field", ["fem_id", "port_id"])
@pytest.mark.parametrize("value", [True, False, 1.5, "1.5", "invalid", float("inf"), float("nan")])
def test_edit_offer_does_not_build_noninteger_path(field, value):
    assert edit_offer(BASE + "." + field, value) is None


@pytest.mark.parametrize("value", [4, "4", 4.0, "04"])
def test_edit_offer_builds_integer_path(value):
    assert edit_offer(BASE + ".port_id", value)["new_path"] == "ports.analog_outputs.con1.2.4"


def test_integral_float_move_uses_integer_path(env):
    client, ctx = env
    r = client.post("/field/edit", data={"dot_path": BASE + ".port_id", "value": "4.0",
                                        "port_identity_ack": "fix"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert r.get_json()["port_identity_moved"]["new_path"] == "ports.analog_outputs.con1.2.4"
    assert ctx["store"].get_value("wiring.qubits.q1.z.opx_output") == "#/ports/analog_outputs/con1/2/4"
    assert port_identity_mismatches(ctx["store"].state) == []


def test_move_collision_leaves_state_untouched(env):
    client, ctx = env
    ctx["modifier"].create_subtree("ports.analog_outputs.con1.2.4", {"port_id": 4})
    before = copy.deepcopy((ctx["store"].state, ctx["store"].wiring))
    count = len(ctx["store"].change_log)
    r = client.post("/field/edit", data={"dot_path": BASE + ".port_id", "value": "4", "port_identity_ack": "fix"})
    assert r.status_code == 400 and "already exists" in r.get_json()["error"]
    assert (ctx["store"].state, ctx["store"].wiring) == before
    assert len(ctx["store"].change_log) == count


def test_move_rolls_back_new_parents_when_pointer_write_fails(env, monkeypatch):
    client, ctx = env
    modifier, store = ctx["modifier"], ctx["store"]
    before = copy.deepcopy((store.state, store.wiring))
    original = modifier.set_value

    def fail_pointer(path, value, **kwargs):
        if path.startswith("wiring."):
            raise ValueError("injected pointer failure")
        return original(path, value, **kwargs)

    monkeypatch.setattr(modifier, "set_value", fail_pointer)
    r = client.post("/field/edit", data={"dot_path": BASE + ".controller_id", "value": "con2",
                                        "port_identity_ack": "fix"})
    assert r.status_code == 400 and "injected pointer failure" in r.get_json()["error"]
    assert (store.state, store.wiring) == before
    assert not store.change_log


def test_move_retargets_port_subfield_pointers(env):
    client, ctx = env
    modifier, store = ctx["modifier"], ctx["store"]
    modifier.create_subtree("qubits.q1.link", PTR + "/fem_id")
    r = client.post("/field/edit", data={"dot_path": BASE + ".port_id", "value": "4",
                                        "port_identity_ack": "fix"})
    assert r.status_code == 200 and r.get_json()["ok"], r.get_json()
    assert store.get_value("qubits.q1.link") == "#/ports/analog_outputs/con1/2/4/fem_id"


def test_mcp_forwards_identity_answer_and_returns_offer(monkeypatch):
    from unittest.mock import Mock
    from quam_state_manager import mcp
    link = Mock()
    monkeypatch.setattr(mcp, "_sm", lambda: link)
    monkeypatch.setattr(mcp, "_chip_facts", lambda: {"loaded": True})
    monkeypatch.setattr(mcp, "_tray_seen", lambda: {"count": 0, "entries": []})
    link.post_form.return_value = (409, {"port_identity": {"choices": ["fix", "keep"]}})
    result = mcp.t_state_edit({"path": BASE + ".port_id", "value": 4})
    assert result["needs_answer"] and not result["staged"]
    assert "port_identity_ack" in result["how"]
    link.post_form.return_value = (200, {"ok": True})
    assert mcp.t_state_edit({"path": BASE + ".port_id", "value": 4, "port_identity_ack": "keep"})["staged"]
    assert link.post_form.call_args.args[1]["port_identity_ack"] == "keep"
