"""Re-generate and the MW output time base.

A stored chip that mixes MW band 2 with bands 1 / 3 and holds 0 on every MW
output delay is offered ONE shift: band 1 / 3 outputs get 20 ns, and every
calibrated timing inside a qubit's flux / xy pair is kept (z += the xy shift,
a coupler += its moving qubit's shift, time of flight += the readout shift).
"keep" changes nothing; an unknown answer is a 400. Ports a rebuild adds
follow the source chip's time base.
"""
from __future__ import annotations

import copy
import json

from quam_state_manager.core import regenerate


def _mw(port, band, delay=0):
    return {"controller_id": "con1", "fem_id": 1, "port_id": port, "band": band, "delay": delay,
            "__class__": "quam.components.ports.analog_outputs.MWFEMAnalogOutputPort"}


def _lf(port, delay):
    return {"controller_id": "con1", "fem_id": 3, "port_id": port, "delay": delay,
            "__class__": "quam.components.ports.analog_outputs.LFFEMAnalogOutputPort"}


def _chip(xy_bands=(2, 1), z=(61, 63), ro_band=2, moving=None, tof=32, coupler=True):
    """qubit i (q1..) drives on MW port i in xy_bands[i], flux on LF port i,
    every resonator on one MW readout port 8; one coupler (LF port 9) on q1-q2."""
    state = {"qubits": {}, "qubit_pairs": {},
             "ports": {"mw_outputs": {"con1": {"1": {"8": _mw(8, ro_band)}}},
                       "analog_outputs": {"con1": {"3": {}}}}}
    wiring = {"wiring": {"qubits": {}, "qubit_pairs": {}}}
    for i, (b, zd) in enumerate(zip(xy_bands, z), start=1):
        q = f"q{i}"
        state["ports"]["mw_outputs"]["con1"]["1"][str(i)] = _mw(i, b)
        state["ports"]["analog_outputs"]["con1"]["3"][str(i)] = _lf(i, zd)
        state["qubits"][q] = {
            "xy": {"opx_output": f"#/wiring/qubits/{q}/xy/opx_output"},
            "z": {"opx_output": f"#/wiring/qubits/{q}/z/opx_output"},
            "resonator": {"opx_output": f"#/wiring/qubits/{q}/rr/opx_output",
                          "time_of_flight": tof}}
        wiring["wiring"]["qubits"][q] = {
            "xy": {"opx_output": f"#/ports/mw_outputs/con1/1/{i}"},
            "z": {"opx_output": f"#/ports/analog_outputs/con1/3/{i}"},
            "rr": {"opx_output": "#/ports/mw_outputs/con1/1/8"}}
    if coupler and len(xy_bands) > 1:
        pair = {"qubit_control": "#/qubits/q1", "qubit_target": "#/qubits/q2",
                "coupler": {"opx_output": "#/wiring/qubit_pairs/q1-2/c/opx_output"}}
        if moving:
            pair["moving_qubit"] = moving
        state["qubit_pairs"]["q1-2"] = pair
        state["ports"]["analog_outputs"]["con1"]["3"]["9"] = _lf(9, z[0])
        wiring["wiring"]["qubit_pairs"]["q1-2"] = {
            "c": {"opx_output": "#/ports/analog_outputs/con1/3/9"}}
    return state, wiring


def _write(folder, state, wiring):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "state.json").write_text(json.dumps(state))
    (folder / "wiring.json").write_text(json.dumps(wiring))


def _ports(folder):
    s = json.loads((folder / "state.json").read_text())
    return s, s["ports"]["mw_outputs"]["con1"]["1"], s["ports"]["analog_outputs"]["con1"]["3"]


def test_shift_moves_the_band_1_qubit_and_keeps_its_xy_z_timing(tmp_path):
    _write(tmp_path, *_chip())
    plan = regenerate.apply_mw_band_shift(tmp_path)
    assert plan and [p["port"] for p in plan["ports"]] == ["ports/mw_outputs/con1/1/2"]
    s, mw, lf = _ports(tmp_path)
    assert (mw["1"]["delay"], mw["2"]["delay"], mw["8"]["delay"]) == (0, 20, 0)
    assert (lf["1"]["delay"], lf["2"]["delay"]) == (61, 83)      # band-2 qubit unchanged
    assert lf["9"]["delay"] == 61                                # coupler follows q1 (control)
    assert {q["resonator"]["time_of_flight"] for q in s["qubits"].values()} == {32}
    assert [x["qubit"] for x in plan["spacing"]] == ["q2"]


def test_the_coupler_follows_its_moving_qubit(tmp_path):
    _write(tmp_path, *_chip(moving="target"))
    regenerate.apply_mw_band_shift(tmp_path)
    _, _, lf = _ports(tmp_path)
    assert lf["9"]["delay"] == 61 + 20


def test_a_shared_feedline_shifts_once_and_moves_five_times_of_flight(tmp_path):
    _write(tmp_path, *_chip(xy_bands=(2,) * 5, z=(61,) * 5, ro_band=3, coupler=False))
    plan = regenerate.apply_mw_band_shift(tmp_path)
    s, mw, lf = _ports(tmp_path)
    assert [p["port"] for p in plan["ports"]] == ["ports/mw_outputs/con1/1/8"]
    assert mw["8"]["delay"] == 20
    assert [q["resonator"]["time_of_flight"] for q in s["qubits"].values()] == [52] * 5
    assert {p["delay"] for p in lf.values()} == {61}             # no xy shifted
    text = regenerate.mw_band_shift_offer_text(plan)
    assert text.startswith("Band 1/3 MW outputs leave 20 ns before band 2 ones. Shift them by 20 ns?")
    assert "q1, q2, q3, q4, q5" in text


def test_no_offer_for_one_class_or_an_already_shifted_chip():
    assert regenerate.mw_band_shift_plan(*_chip(xy_bands=(1, 3), ro_band=3)) is None
    assert regenerate.mw_band_shift_plan(*_chip(xy_bands=(2, 2), ro_band=2)) is None
    state, wiring = _chip()
    state["ports"]["mw_outputs"]["con1"]["1"]["2"]["delay"] = 20
    assert regenerate.mw_band_shift_plan(state, wiring) is None
    assert regenerate.mw_band_shift_plan({}, {}) is None


def test_new_ports_follow_an_unshifted_source():
    old, _ = _chip(xy_bands=(2,), z=(61,), coupler=False)
    merged, wiring = _chip()                       # the rebuild added q2 (band 1)
    merged = copy.deepcopy(merged)
    merged["ports"]["mw_outputs"]["con1"]["1"]["2"]["delay"] = 20     # fresh build values
    merged["ports"]["analog_outputs"]["con1"]["3"]["2"]["delay"] = 161
    merged["ports"]["analog_outputs"]["con1"]["3"]["9"]["delay"] = 161
    regenerate.follow_source_time_base(merged, old, wiring)
    mw = merged["ports"]["mw_outputs"]["con1"]["1"]
    lf = merged["ports"]["analog_outputs"]["con1"]["3"]
    assert mw["2"]["delay"] == 0 and lf["2"]["delay"] == 141     # today's per-band values
    assert lf["9"]["delay"] == 61                                 # moving qubit's stored z
    assert lf["1"]["delay"] == 61                                 # stored port untouched
    assert regenerate.mw_band_shift_plan(merged, wiring)          # the offer covers q2 too


def test_new_ports_keep_the_base_of_a_shifted_source():
    old, _ = _chip(xy_bands=(2, 1))
    old["ports"]["mw_outputs"]["con1"]["1"]["2"]["delay"] = 20
    merged, wiring = _chip(xy_bands=(2, 1, 3), z=(61, 83, 161))
    merged["ports"]["mw_outputs"]["con1"]["1"]["2"]["delay"] = 20
    merged["ports"]["mw_outputs"]["con1"]["1"]["3"]["delay"] = 20
    assert regenerate.follow_source_time_base(merged, old, wiring) == []
    assert merged["ports"]["mw_outputs"]["con1"]["1"]["3"]["delay"] == 20


def _route(tmp_path, monkeypatch, src_chip):
    import sys as _sys
    from quam_state_manager.core import config_generator
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    from quam_state_manager.web.app import create_app

    manifest = {"ok": True, "cached": False, "error": None, "versions": {},
                "capabilities": {c: {"available": True, "detail": ""} for c in CATALOG_IDS}}
    monkeypatch.setattr(config_generator, "probe_capabilities", lambda *a, **k: manifest)

    def fake_run(py, src, spec, out_dir, timeout=300, **kw):
        _write(out_dir, *src_chip)
        return {"ok": True, "status": "ok", "error": None, "merge": None, "result": {}}

    monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
    client = create_app(testing=True, instance_path=str(tmp_path / "_inst")).test_client()
    client.post("/generate/select-env", json={"python": _sys.executable})
    _write(tmp_path / "src", *src_chip)
    spec = {"network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {"controllers": [{"con": 1, "fems": [
                {"slot": 1, "fem": "mw"}, {"slot": 3, "fem": "lf"}]}],
                "opx_plus": [], "octaves": []},
            "qubits": ["q1", "q2"], "qubit_pairs": [["q1", "q2"]], "twpas": [],
            "lines": [{"element": "q1", "line": "flux", "channel": None}]}
    return client, {"spec": spec, "output_path": str(tmp_path / "out"),
                    "source_folder": str(tmp_path / "src")}


def test_the_route_asks_once_and_honours_shift_and_keep(tmp_path, monkeypatch):
    client, body = _route(tmp_path, monkeypatch, _chip())
    out = tmp_path / "out"
    res = client.post("/regenerate/build", json=body).get_json()
    assert res["needs_confirm"] is True and res["confirm_kind"] == "mw_shift"
    assert res["mw_shift"]["ports"][0]["new"] == 20 and not out.exists()
    assert "Shift them by 20 ns?" in res["error"]

    body["mw_shift_ack"] = "maybe"
    r = client.post("/regenerate/build", json=body)
    assert r.status_code == 400 and not out.exists()

    body["mw_shift_ack"] = "keep"
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    assert any("left unshifted as chosen" in w for w in res["result"]["warnings"])
    _, mw, lf = _ports(out)
    assert mw["2"]["delay"] == 0 and lf["2"]["delay"] == 63

    body["mw_shift_ack"] = "shift"
    body["force"] = True
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    _, mw, lf = _ports(out)
    assert mw["2"]["delay"] == 20 and lf["2"]["delay"] == 83


def test_both_offers_are_one_question(tmp_path, monkeypatch):
    state, wiring = _chip()
    state["ports"]["analog_outputs"]["con1"]["3"]["9"]["delay"] = 0   # the old coupler 0
    client, body = _route(tmp_path, monkeypatch, (state, wiring))
    res = client.post("/regenerate/build", json=body).get_json()
    assert res["confirm_kind"] == "coupler_delay"
    assert res["coupler_delay"] and res["mw_shift"]
    body["coupler_delay_ack"] = "set"
    body["mw_shift_ack"] = "shift"
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    _, mw, lf = _ports(tmp_path / "out")
    assert lf["9"]["delay"] == 61 and mw["2"]["delay"] == 20 and lf["2"]["delay"] == 83
