"""Re-generate and the coupler flux-port delay.

A stored coupler delay is carried by the merge (a hand-set value stays). An old
chip whose coupler ports hold the old wizard's 0 while the pair's moving-qubit
z port holds another delay gets ONE offer before the build ("set the couplers
to N?"); "set" writes the z delay to those ports in the built chip, "keep"
leaves 0; nothing changes unless the user says set.
"""
from __future__ import annotations

import json

from quam_state_manager.core import regen_merge, regenerate


def _lf(port, delay):
    return {"controller_id": "con1", "fem_id": 3, "port_id": port, "delay": delay,
            "__class__": "quam.components.ports.analog_outputs.LFFEMAnalogOutputPort"}


def _chip(coupler_delay, z1=161, z2=141, moving=None):
    pair = {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
            "coupler": {"opx_output": "#/wiring/qubit_pairs/q1-2/c/opx_output"}}
    if moving:
        pair["moving_qubit"] = moving
    state = {
        "qubits": {q: {"z": {"opx_output": f"#/wiring/qubits/{q}/z/opx_output"}}
                   for q in ("q1", "q2")},
        "qubit_pairs": {"q1-2": pair},
        "ports": {"analog_outputs": {"con1": {"3": {
            "1": _lf(1, z1), "2": _lf(2, z2), "5": _lf(5, coupler_delay)}}}},
    }
    wiring = {"wiring": {
        "qubits": {"q1": {"z": {"opx_output": "#/ports/analog_outputs/con1/3/1"}},
                   "q2": {"z": {"opx_output": "#/ports/analog_outputs/con1/3/2"}}},
        "qubit_pairs": {"q1-2": {"c": {"opx_output": "#/ports/analog_outputs/con1/3/5"}}}}}
    return state, wiring


def test_a_stored_coupler_delay_is_carried_by_the_merge():
    old, ow = _chip(coupler_delay=77)
    new, nw = _chip(coupler_delay=161)
    r = regen_merge.merge_states(old, new, old_wiring=ow, new_wiring=nw)
    assert r.merged["ports"]["analog_outputs"]["con1"]["3"]["5"]["delay"] == 77


def test_zero_coupler_with_a_z_delay_is_offered_with_the_moving_qubit_value():
    items = regenerate.coupler_delay_mismatches(*_chip(0))
    assert items == [{"pair": "q1-2", "port": "ports/analog_outputs/con1/3/5", "z_delay": 161}]
    assert regenerate.coupler_delay_mismatches(*_chip(0, moving="target"))[0]["z_delay"] == 141
    text = regenerate.coupler_delay_offer_text(items)
    assert "delay 0" in text and "161" in text and text.endswith("?")


def test_no_offer_when_the_coupler_is_set_or_the_z_delay_is_zero():
    assert regenerate.coupler_delay_mismatches(*_chip(77)) == []
    assert regenerate.coupler_delay_mismatches(*_chip(0, z1=0)) == []
    assert regenerate.coupler_delay_mismatches({}, {}) == []


def test_set_writes_the_z_delay_into_the_built_chip(tmp_path):
    state, wiring = _chip(0)
    (tmp_path / "state.json").write_text(json.dumps(state))
    (tmp_path / "wiring.json").write_text(json.dumps(wiring))
    done = regenerate.set_coupler_delays(tmp_path)
    assert [d["z_delay"] for d in done] == [161]
    after = json.loads((tmp_path / "state.json").read_text())
    assert after["ports"]["analog_outputs"]["con1"]["3"]["5"]["delay"] == 161
    assert after["ports"]["analog_outputs"]["con1"]["3"]["2"]["delay"] == 141   # z ports untouched


def test_the_build_route_offers_once_and_honours_the_answer(tmp_path, monkeypatch):
    import sys as _sys
    from quam_state_manager.core import config_generator
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    from quam_state_manager.web.app import create_app

    manifest = {"ok": True, "cached": False, "error": None, "versions": {},
                "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS}}
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: manifest)
    out = tmp_path / "out"

    def fake_run(py, src, spec, out_dir, timeout=300, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        s, w = _chip(0)
        (out_dir / "state.json").write_text(json.dumps(s))
        (out_dir / "wiring.json").write_text(json.dumps(w))
        return {"ok": True, "status": "ok", "error": None, "merge": None, "result": {}}

    monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
    client = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    client.post("/generate/select-env", json={"python": _sys.executable})
    src = tmp_path / "src"
    src.mkdir()
    s, w = _chip(0)
    (src / "state.json").write_text(json.dumps(s))
    (src / "wiring.json").write_text(json.dumps(w))
    spec = {"network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 3, "fem": "lf"}]}],
                            "opx_plus": [], "octaves": []},
            "qubits": ["q1", "q2"], "qubit_pairs": [], "twpas": [],
            "lines": [{"element": "q1", "line": "flux", "channel": None}]}
    body = {"spec": spec, "output_path": str(out), "source_folder": str(src)}

    res = client.post("/regenerate/build", json=body).get_json()
    assert res["needs_confirm"] is True and res["confirm_kind"] == "coupler_delay"
    assert res["coupler_delay"][0]["z_delay"] == 161 and not out.exists()

    body["coupler_delay_ack"] = "keep"
    assert client.post("/regenerate/build", json=body).get_json().get("ok") is True
    kept = json.loads((out / "state.json").read_text())
    assert kept["ports"]["analog_outputs"]["con1"]["3"]["5"]["delay"] == 0

    body["coupler_delay_ack"] = "set"
    body["force"] = True
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    got = json.loads((out / "state.json").read_text())
    assert got["ports"]["analog_outputs"]["con1"]["3"]["5"]["delay"] == 161
    assert any("161" in w for w in (res.get("result") or {}).get("warnings") or [])
