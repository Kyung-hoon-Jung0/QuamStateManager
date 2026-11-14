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
    assert items == [{"pair": "q1-2", "pairs": ["q1-2"], "port": "ports/analog_outputs/con1/3/5",
                      "port_label": "con1 / slot 3 / port 5", "z_delay": 161}]
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
            "qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "twpas": [],
            "lines": [{"element": "q1", "line": "flux", "channel": None}]}
    body = {"spec": spec, "output_path": str(out), "source_folder": str(src)}

    res = client.post("/regenerate/build", json=body).get_json()
    assert res["needs_confirm"] is True and res["confirm_kind"] == "coupler_delay"
    assert res["coupler_delay"][0]["z_delay"] == 161 and not out.exists()

    body["coupler_delay_ack"] = "maybe"
    r = client.post("/regenerate/build", json=body)
    assert r.status_code == 400 and "set" in r.get_json()["error"] and not out.exists()

    body["coupler_delay_ack"] = "keep"
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    assert "Coupler port delays kept as stored, as chosen." in res["result"]["warnings"]
    kept = json.loads((out / "state.json").read_text())
    assert kept["ports"]["analog_outputs"]["con1"]["3"]["5"]["delay"] == 0

    body["coupler_delay_ack"] = "set"
    body["force"] = True
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    got = json.loads((out / "state.json").read_text())
    assert got["ports"]["analog_outputs"]["con1"]["3"]["5"]["delay"] == 161
    assert any("161" in w for w in (res.get("result") or {}).get("warnings") or [])


def _three_pair_chip():
    """Three pairs in chip order: coupler at 0, coupler already at the z delay,
    coupler set by hand to 5; every moving qubit's z port holds 161."""
    state = {"qubits": {}, "qubit_pairs": {}, "ports": {"analog_outputs": {"con1": {"3": {}}}}}
    wiring = {"wiring": {"qubits": {}, "qubit_pairs": {}}}
    ports = state["ports"]["analog_outputs"]["con1"]["3"]
    for i, cd in enumerate((0, 161, 5)):
        qc, qt, pid = f"q{2 * i + 1}", f"q{2 * i + 2}", f"q{2 * i + 1}-{2 * i + 2}"
        for q, n in ((qc, 10 * i + 1), (qt, 10 * i + 2)):
            state["qubits"][q] = {"z": {"opx_output": f"#/wiring/qubits/{q}/z/opx_output"}}
            wiring["wiring"]["qubits"][q] = {"z": {"opx_output": f"#/ports/analog_outputs/con1/3/{n}"}}
            ports[str(n)] = _lf(n, 161)
        ports[str(10 * i + 5)] = _lf(10 * i + 5, cd)
        state["qubit_pairs"][pid] = {
            "qubit_control": f"#/qubits/{qc}", "qubit_target": f"#/qubits/{qt}",
            "coupler": {"opx_output": f"#/wiring/qubit_pairs/{pid}/c/opx_output"}}
        wiring["wiring"]["qubit_pairs"][pid] = {
            "c": {"opx_output": f"#/ports/analog_outputs/con1/3/{10 * i + 5}"}}
    return state, wiring


def _write(folder, state, wiring):
    (folder / "state.json").write_text(json.dumps(state))
    (folder / "wiring.json").write_text(json.dumps(wiring))


def test_a_mixed_chip_changes_only_the_zero_coupler(tmp_path):
    _write(tmp_path, *_three_pair_chip())
    done = regenerate.set_coupler_delays(tmp_path)
    assert [d["pair"] for d in done] == ["q1-2"]
    ports = json.loads((tmp_path / "state.json").read_text())["ports"]["analog_outputs"]["con1"]["3"]
    assert (ports["5"]["delay"], ports["15"]["delay"], ports["25"]["delay"]) == (161, 161, 5)


def _shared_chip():
    """Two pairs on one coupler port at 0, in chip order q2-1 (not sorted first)
    then q1-2: q2-1 moves q2 (z 141), q1-2 moves q1 (z 161)."""
    state, wiring = _chip(0, z1=161, z2=141)
    state["qubit_pairs"] = {
        "q2-1": {"qubit_control": "#/qubits/q2", "qubit_target": "#/qubits/q1",
                 "coupler": {"opx_output": "#/wiring/qubit_pairs/q1-2/c/opx_output"}},
        "q1-2": state["qubit_pairs"]["q1-2"]}
    return state, wiring


def test_a_shared_coupler_port_is_decided_by_the_first_pair_and_offered_once(tmp_path):
    state, wiring = _shared_chip()
    items = regenerate.coupler_delay_mismatches(state, wiring)
    assert len(items) == 1 and items[0]["pairs"] == ["q2-1", "q1-2"] and items[0]["z_delay"] == 141
    _write(tmp_path, state, wiring)
    regenerate.set_coupler_delays(tmp_path)
    after = json.loads((tmp_path / "state.json").read_text())
    assert after["ports"]["analog_outputs"]["con1"]["3"]["5"]["delay"] == 141


def test_a_pair_the_spec_removed_is_not_offered():
    assert regenerate.coupler_delay_mismatches(*_chip(0), {"qubit_pairs": []}) == []
    assert regenerate.coupler_delay_mismatches(*_chip(0), {"qubit_pairs": [["q2", "q1"]]})


def _route(tmp_path, monkeypatch, source_coupler):
    import sys as _sys
    from quam_state_manager.core import config_generator
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    from quam_state_manager.web.app import create_app

    manifest = {"ok": True, "cached": False, "error": None, "versions": {},
                "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS}}
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: manifest)

    def fake_run(py, src, spec, out_dir, timeout=300, **kw):
        out_dir.mkdir(parents=True, exist_ok=True)
        _write(out_dir, *_chip(0))
        return {"ok": True, "status": "ok", "error": None, "merge": None, "result": {}}

    monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
    client = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    client.post("/generate/select-env", json={"python": _sys.executable})
    src = tmp_path / "src"
    src.mkdir()
    _write(src, *_chip(source_coupler))
    spec = {"network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 3, "fem": "lf"}]}],
                            "opx_plus": [], "octaves": []},
            "qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "twpas": [],
            "lines": [{"element": "q1", "line": "flux", "channel": None}]}
    return client, {"spec": spec, "output_path": str(tmp_path / "out"), "source_folder": str(src)}


def test_no_answer_writes_nothing(tmp_path, monkeypatch):
    # no offer from the source (its coupler holds 77) while the built chip has
    # a 0 coupler: with no answer the built chip stays as built
    client, body = _route(tmp_path, monkeypatch, source_coupler=77)
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True, res
    got = json.loads((tmp_path / "out" / "state.json").read_text())
    assert got["ports"]["analog_outputs"]["con1"]["3"]["5"]["delay"] == 0


def test_a_failed_set_is_reported_in_the_result(tmp_path, monkeypatch):
    client, body = _route(tmp_path, monkeypatch, source_coupler=0)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(regenerate, "set_coupler_delays", boom)
    body["coupler_delay_ack"] = "set"
    r = client.post("/regenerate/build", json=body)
    res = r.get_json()
    assert r.status_code == 200 and res.get("ok") is True
    assert ("The chip was built, but the coupler delays could not be set: disk full"
            in res["result"]["warnings"])
    body["force"] = True    # the output claim was released
    assert client.post("/regenerate/build", json=body).status_code != 409
