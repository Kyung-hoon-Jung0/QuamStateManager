"""Gate ID discovery, repair, rename and regeneration contracts."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess

import pytest
from flask import render_template

from quam_state_manager.core import gate_pulse_ids as ids
from quam_state_manager.core.diagnostics import _gate_pulse_id_findings, domain_of, lint_state, summarize
from quam_state_manager.core.regen_merge import merge_states
from quam_state_manager.web.app import create_app

ROOT = Path(__file__).resolve().parents[1]
OP = "qubits.q1.z.operations"
SLOT = "qubit_pairs.q1-q2.macros.cz."


def state():
    return {
        "qubits": {"q1": {"id": "q1", "z": {"operations": {
            "first": {"__class__": "quam.components.pulses.SquarePulse", "id": None, "length": 40, "amplitude": 0.1},
            "second": {"__class__": "quam.components.pulses.SquarePulse", "length": 40, "amplitude": 0.2},
            "custom": {"id": "explicit"},
            "chain": "#../operations/first",
        }}}},
        "qubit_pairs": {"q1-q2": {"macros": {"cz": {
            "__class__": "quam_builder.architecture.superconducting.CZGate",
            "flux_pulse_qubit": "#/qubits/q1/z/operations/chain",
            "flux_pulse_target": "#/qubits/q1/z/operations/second",
        }}}},
    }


def test_pointer_slots_only_and_non_null_preserved():
    s = state()
    macro = s["qubit_pairs"]["q1-q2"]["macros"]["cz"]
    macro["flux_pulse_qubit"] = {"id": None}
    macro["flux_pulse_target"] = "first"
    macro["coupler_flux_pulse"] = "#/qubits/q1/z/operations/custom"
    assert ids.missing_gate_ids(s) == []
    assert ids.fill_edits(s) == []
    assert s["qubits"]["q1"]["z"]["operations"]["custom"]["id"] == "explicit"


def test_dedup_chain_and_all_slots():
    s = state()
    macro = s["qubit_pairs"]["q1-q2"]["macros"]["cz"]
    for slot in ids.GATE_ID_SLOTS:
        macro[slot] = "#/qubits/q1/z/operations/chain"
    assert ids.missing_gate_ids(s) == [(OP + ".first", "first", SLOT + "flux_pulse_qubit")]
    for slot in ids.GATE_ID_SLOTS:
        macro.clear()
        macro[slot] = "#/qubits/q1/z/operations/chain"
        assert ids.missing_gate_ids(s) == [(OP + ".first", "first", SLOT + slot)]
    s["qubit_pairs"]["q1-q2"]["coupler"] = {"operations": {"coupled": {"id": None}}}
    macro.clear()
    macro["coupler_flux_pulse"] = "#/qubit_pairs/q1-q2/coupler/operations/coupled"
    assert ids.fill_edits(s) == [{"path": "qubit_pairs.q1-q2.coupler.operations.coupled.id", "value": "coupled"}]


def test_dangling_cycle_and_non_operation_targets():
    s = state()
    macro = s["qubit_pairs"]["q1-q2"]["macros"]["cz"]
    for pointer in ("#/missing", "#/qubits/q1", "#./flux_pulse_qubit",
                    "#/qubit_pairs/q1-q2/macros/cz/flux_pulse_qubit"):
        macro.clear()
        macro["flux_pulse_qubit"] = pointer
        assert ids.missing_gate_ids(s) == []


@pytest.fixture
def loaded(tmp_path):
    folder = tmp_path / "state"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps(state()), encoding="utf-8")
    (folder / "wiring.json").write_text("{}", encoding="utf-8")
    app = create_app(testing=True, instance_path=str(tmp_path / "instance"))
    client = app.test_client()
    assert client.post("/load", data={"folder": str(folder)}).status_code in (200, 302)
    ctx = next(iter(app.config["contexts"].values()))
    from quam_state_manager.web.routes import _active_chip_token
    with app.test_request_context():
        token = _active_chip_token()
    return client, ctx["store"], token, app


def browser_requests(edits):
    if not shutil.which("node"):
        pytest.skip("Node is required for the shipped handler contract")
    source = (ROOT / "quam_state_manager/web/static/app.js").read_text(encoding="utf-8")
    source = source[source.index("    window.applyDiagFix = function(btn)"):]
    source = source[:source.index("    /* QA diagnostics-r2-20")]
    script = '''
const requests = [];
global.window = {__chipToken: 'token', confirm: () => true};
global._diagFocusAnchorFor = () => null;
global._keepMineSeen = () => ({n: 7, sig: 'signature'});
global.alert = () => {};
global.fetch = (url, options) => { requests.push({url, body: JSON.parse(options.body)}); return Promise.resolve({json: () => ({ok:false})}); };
''' + source + '''
const attrs = {'data-action': 'fill_gate_ids', 'data-edits': process.argv[1]};
window.applyDiagFix({getAttribute: k => attrs[k], textContent: 'Fill ids'});
console.log(JSON.stringify(requests));
'''
    result = subprocess.run(["node", "-e", script, json.dumps(edits)], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_repair_real_batch_one_group_and_chip_gate(loaded):
    client, store, token, _app = loaded
    requests = browser_requests(ids.fill_edits(store.merged))
    assert len(requests) == 1
    request = requests[0]
    assert request["url"] == "/field/edit-batch"
    payload = request["body"]
    assert payload["expect_chip"] == "token"
    assert payload["seen_changes"] == 7
    assert payload["group"] == "new"
    assert all(u["create"] for u in payload["updates"])
    before = deepcopy(store.state)
    payload["expect_chip"] = "wrong"
    refused = client.post(request["url"], json=payload)
    assert refused.status_code == 409
    assert store.state == before and not store.change_log
    payload["expect_chip"] = token
    result = client.post(request["url"], json=payload)
    assert result.status_code == 200, result.data
    assert result.get_json()["ok"]
    assert ids.missing_gate_ids(store.merged) == []
    groups = {e.group_id for e in store.change_log}
    assert len(groups) == 1 and None not in groups
    client.post("/undo")
    assert store.state == before


@pytest.mark.parametrize("path,value", [(OP + ".first.id", "wrong"),
                                        (OP + ".custom.id", "custom"),
                                        ("qubits.q1.id", "other")])
def test_batch_identity_exception_is_narrow(loaded, path, value):
    client, store, token, _app = loaded
    before = deepcopy(store.state)
    result = client.post("/field/edit-batch", json={
        "updates": [{"dot_path": path, "value": value, "create": True}],
        "expect_chip": token, "group": "new"})
    assert not result.get_json()["ok"]
    assert store.state == before and not store.change_log


@pytest.mark.parametrize("old_id", ["first", "explicit", None])
def test_gate_rename_id_and_undo(loaded, old_id):
    client, store, token, _app = loaded
    # Point directly at the renamed operation, as the build does.
    store.state["qubit_pairs"]["q1-q2"]["macros"]["cz"]["flux_pulse_qubit"] = "#/qubits/q1/z/operations/first"
    store.state["qubits"]["q1"]["z"]["operations"]["first"]["id"] = old_id
    store._merge()
    before = deepcopy(store.state)
    result = client.post("/api/pulse/rename", data={"path": OP + ".first", "new_name": "renamed", "retarget": "1", "expect_chip": token})
    assert result.status_code == 200, result.data
    assert store.get_value(OP + ".renamed.id") == ("renamed" if old_id == "first" else old_id)
    assert store.get_value(SLOT + "flux_pulse_qubit") == "#/qubits/q1/z/operations/renamed"
    assert len({e.group_id for e in store.change_log}) == 1
    client.post("/undo")
    assert store.state == before


def test_regen_narrow_id_rule():
    old = {"operations": {"first": {"id": None}, "second": {"id": "explicit"}, "third": {"id": None}}, "other": {"id": None}}
    new = {"operations": {"first": {"id": "first"}, "second": {"id": "second"}, "third": {"id": "different"}}, "other": {"id": "other"}}
    result = merge_states(old, new)
    assert result.merged["operations"]["first"]["id"] == "first"
    assert result.stats.id_filled == 1
    assert result.merged["operations"]["second"]["id"] == "explicit"
    assert result.merged["operations"]["third"]["id"] is None
    assert result.merged["other"]["id"] is None


def test_build_slot_vocabulary_and_assignments():
    source = (ROOT / "quam_state_manager/generator/run_build.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    slots = next(ast.literal_eval(n.value) for n in module.body if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "BUILD_GATE_ID_SLOTS" for t in n.targets))
    assert set(slots) == set(ids.GATE_ID_SLOTS)
    assert "qubit_pulse.id = zop" in source
    assert "coupler_pulse.id = cop" in source
    assert "moving_q.z.operations[pulse_id].id = pulse_id" in source


def test_finding_and_fill_button(loaded):
    _client, store, _token, app = loaded
    findings = _gate_pulse_id_findings(store.merged)
    assert any(f.category == "cz.gate_pulse_id_missing" for f in lint_state(store))
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "warning" and f.category == "cz.gate_pulse_id_missing"
    assert f.message == "2 CZ gate pulses have no id"
    assert "nodes 30 and 31 (repair_routing=True)" in f.detail
    assert OP + ".first" in f.detail and OP + ".second" in f.detail
    assert domain_of(f.category) == "gates"
    for allow in (True, False):
        with app.app_context():
            html = render_template("_diagnostics_list.html", findings=findings, diag_summary=summarize(findings), allow_jump=True, allow_fix=allow)
        assert "2Q gates" in html
        assert ('data-action="fill_gate_ids"' in html) is allow
        assert ("Fill 2 ids" in html) is allow


def test_regenerate_serializes_id_filled(tmp_path, monkeypatch):
    from quam_state_manager.core import regenerate
    old = state()
    fresh = deepcopy(old)
    for edit in ids.fill_edits(fresh):
        node = fresh
        parts = edit["path"].split(".")
        for key in parts[:-1]:
            node = node[key]
        node[parts[-1]] = edit["value"]
    source = tmp_path / "source"
    source.mkdir()
    (source / "state.json").write_text(json.dumps(old), encoding="utf-8")
    (source / "wiring.json").write_text("{}", encoding="utf-8")

    def build(_python, _mode, _spec, out_dir, timeout=300):
        target = Path(out_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "state.json").write_text(json.dumps(fresh), encoding="utf-8")
        (target / "wiring.json").write_text("{}", encoding="utf-8")
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", build)
    result = regenerate.run_regenerate("python", source, {"qubits": []}, tmp_path / "rebuilt")
    assert result["ok"], result
    assert result["merge"]["id_filled"] == 1  # absent IDs are new leaves, outside tier 1
    source_js = (ROOT / "quam_state_manager/web/static/generate.js").read_text(encoding="utf-8")
    assert "m.id_filled" in source_js and " gate pulse ids filled</span>" in source_js
