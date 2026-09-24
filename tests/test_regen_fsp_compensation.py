"""QA regenerate-r2-04 / r2-06: a port FSP changed in the Re-generate wizard
must not silently move every carried pulse on that port.

``P = FSP + 20·log10|amp|`` — the rebuild protects the NEW FSP, and tier-1
used to carry every other amplitude on the port at its OLD value, so each
pulse shifted by the FSP delta (-4 dB on the EF pi pulse in absolute mode;
-6 dB on every q2 pulse in manual mode) with only "N populate edits applied"
in the report. Pins: the offer is Live Edit's own plan computed on the source
chip; absolute mode rescales; manual mode rescales only on an explicit
``comp`` answer (with the popup's edited amplitudes), keeps amplitudes on
``solo``, and otherwise reports the port in populate_conflicts; the build
route asks before building.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path

from quam_state_manager.core import regen_populate as rp
from quam_state_manager.core import regenerate

PORT = "ports.mw_outputs.con1.1.2"


def _chip(fsp=10):
    state = {
        "qubits": {"q1": {
            "f_01": 5.1e9,
            "xy": {
                "RF_frequency": 5.1e9,
                "opx_output": "#/wiring/qubits/q1/xy/opx_output",
                "operations": {
                    "x180_DragCosine": {"length": 32, "amplitude": 0.29170},
                    "x90_DragCosine": {"length": 32, "amplitude": 0.14504},
                    "x180": "#./x180_DragCosine",           # alias op
                    "EF_x180": {"length": 32, "amplitude": 0.074521},
                    "EF_x90": {"length": 32, "amplitude": 0.037260},
                    "saturation": {"length": 10000, "amplitude": 0.02},
                },
            },
        }},
        "ports": {"mw_outputs": {"con1": {"1": {
            "2": {"upconverter_frequency": 5.0e9, "band": 1,
                  "full_scale_power_dbm": fsp},
        }}}},
    }
    wiring = {"wiring": {"qubits": {"q1": {
        "xy": {"opx_output": "#/ports/mw_outputs/con1/1/2"}}}}}
    return state, wiring


def _paths(plan):
    return {a["path"].split(".")[-2] for a in plan["amps"]}


def test_offer_is_the_live_edit_plan_on_the_source_chip():
    old_state, old_wiring = _chip(fsp=0)
    pop = {"qubit": {"q1": {"full_scale_power_dbm": -6}}}
    changed = [("qubit", "q1", "full_scale_power_dbm")]
    offers = rp.fsp_offers(changed, pop, old_state, old_wiring)
    assert len(offers) == 1
    plan = offers[0]
    assert plan["fsp_path"] == PORT + ".full_scale_power_dbm"
    assert (plan["fsp_old"], plan["fsp_new"]) == (0, -6.0)
    assert _paths(plan) == {"x180_DragCosine", "x90_DragCosine", "EF_x180",
                            "EF_x90", "saturation"}      # alias op not twice
    ef = next(a for a in plan["amps"] if "EF_x180" in a["path"])
    assert math.isclose(ef["new"], 0.074521 * 10 ** (6 / 20), rel_tol=1e-12)
    assert plan["rows"] == [["qubit", "q1"]] and "q1 xy" in plan["port"]


def test_offer_drops_the_amplitudes_the_wizard_rewrites():
    # absolute mode re-solved x180 + saturation; the DragCosine family is
    # re-seeded from x180 (docs/72), so only EF_* are genuinely carried.
    old_state, old_wiring = _chip(fsp=10)
    pop = {"qubit": {"q1": {"full_scale_power_dbm": 6}},
           "pulses": {"q1": {"x180_amplitude": 0.46, "saturation_amplitude": 0.03}}}
    changed = [("qubit", "q1", "full_scale_power_dbm"),
               ("pulses", "q1", "x180_amplitude"),
               ("pulses", "q1", "saturation_amplitude")]
    plan = rp.fsp_offers(changed, pop, old_state, old_wiring)[0]
    assert _paths(plan) == {"EF_x180", "EF_x90"}


def _merged_after_build(old_state, new_fsp, protect_extra=()):
    """What merge_states leaves: old amps tier-1 carried, the new FSP protected."""
    merged = copy.deepcopy(old_state)
    merged["ports"]["mw_outputs"]["con1"]["1"]["2"]["full_scale_power_dbm"] = new_fsp
    protect = {PORT + ".full_scale_power_dbm", *protect_extra}
    return merged, protect


def test_absolute_mode_rescales_the_carried_amplitudes():   # r2-04
    old_state, old_wiring = _chip(fsp=10)
    pop = {"qubit": {"q1": {"full_scale_power_dbm": 6}},
           "pulses": {"q1": {"x180_amplitude": 0.46232}}}
    changed = [("qubit", "q1", "full_scale_power_dbm"),
               ("pulses", "q1", "x180_amplitude")]
    offers = rp.fsp_offers(changed, pop, old_state, old_wiring)
    x180 = "qubits.q1.xy.operations.x180_DragCosine.amplitude"
    merged, protect = _merged_after_build(old_state, 6, (x180,))
    merged["qubits"]["q1"]["xy"]["operations"]["x180_DragCosine"]["amplitude"] = 0.46232
    comp, conf = rp.apply_fsp_compensation(merged, offers, protect, merged,
                                           old_wiring, auto=True)
    ops = merged["qubits"]["q1"]["xy"]["operations"]
    k = 10 ** (4 / 20)
    assert math.isclose(ops["EF_x180"]["amplitude"], 0.074521 * k, rel_tol=1e-12)
    assert math.isclose(ops["EF_x90"]["amplitude"], 0.037260 * k, rel_tol=1e-12)
    # the EF pi pulse keeps its absolute power: -12.56 dBm before and after
    before = 10 + 20 * math.log10(0.074521)
    after = 6 + 20 * math.log10(ops["EF_x180"]["amplitude"])
    assert math.isclose(before, after, abs_tol=1e-9)
    assert ops["x180_DragCosine"]["amplitude"] == 0.46232      # wizard's value, not twice
    assert {c["path"].split(".")[-2] for c in comp} >= {"EF_x180", "EF_x90"}
    assert conf == []


def test_manual_mode_without_an_answer_says_so():             # r2-06
    old_state, old_wiring = _chip(fsp=0)
    pop = {"qubit": {"q1": {"full_scale_power_dbm": -6}}}
    offers = rp.fsp_offers([("qubit", "q1", "full_scale_power_dbm")], pop,
                           old_state, old_wiring)
    merged, protect = _merged_after_build(old_state, -6)
    before = copy.deepcopy(merged)
    comp, conf = rp.apply_fsp_compensation(merged, offers, protect, merged,
                                           old_wiring, auto=False, fsp_ack=None)
    assert comp == [] and merged == before
    assert len(conf) == 1 and "6.0 dB weaker" in conf[0] and "5 calibrated" in conf[0]


def test_manual_mode_comp_answer_rescales_with_the_popup_edits():
    old_state, old_wiring = _chip(fsp=0)
    pop = {"qubit": {"q1": {"full_scale_power_dbm": -6}}}
    offers = rp.fsp_offers([("qubit", "q1", "full_scale_power_dbm")], pop,
                           old_state, old_wiring)
    ef = "qubits.q1.xy.operations.EF_x180.amplitude"
    ack = {PORT + ".full_scale_power_dbm": {
        "mode": "comp", "fsp_new": -6, "amps": [{"dot_path": ef, "value": "0.15"}]}}
    merged, protect = _merged_after_build(old_state, -6)
    comp, conf = rp.apply_fsp_compensation(merged, offers, protect, merged,
                                           old_wiring, auto=False, fsp_ack=ack)
    ops = merged["qubits"]["q1"]["xy"]["operations"]
    k = 10 ** (6 / 20)
    assert math.isclose(ops["x180_DragCosine"]["amplitude"], 0.29170 * k, rel_tol=1e-12)
    assert ops["EF_x180"]["amplitude"] == 0.15                 # the user's own number
    assert len(comp) == 5 and conf == []


def test_a_stale_or_solo_answer():
    old_state, old_wiring = _chip(fsp=0)
    pop = {"qubit": {"q1": {"full_scale_power_dbm": -6}}}
    offers = rp.fsp_offers([("qubit", "q1", "full_scale_power_dbm")], pop,
                           old_state, old_wiring)
    key = PORT + ".full_scale_power_dbm"
    # answered for -3 dBm, then the FSP moved to -6: not an answer to this offer
    assert rp.fsp_ack_mode(offers[0], {key: {"mode": "comp", "fsp_new": -3}}) is None
    merged, protect = _merged_after_build(old_state, -6)
    before = copy.deepcopy(merged)
    comp, conf = rp.apply_fsp_compensation(
        merged, offers, protect, merged, old_wiring, auto=False,
        fsp_ack={key: {"mode": "solo", "fsp_new": -6}})
    assert comp == [] and conf == [] and merged == before       # FSP only, as chosen


def test_a_moved_port_is_never_compensated_and_a_clip_is_named():
    old_state, old_wiring = _chip(fsp=10)
    pop = {"qubit": {"q1": {"full_scale_power_dbm": -11}}}
    offers = rp.fsp_offers([("qubit", "q1", "full_scale_power_dbm")], pop,
                           old_state, old_wiring)
    merged, protect = _merged_after_build(old_state, -11)
    moved_wiring = {"wiring": {"qubits": {"q1": {
        "xy": {"opx_output": "#/ports/mw_outputs/con1/1/3"}}}}}
    merged_moved = copy.deepcopy(merged)
    merged_moved["ports"]["mw_outputs"]["con1"]["1"]["3"] = {"full_scale_power_dbm": -11}
    comp, conf = rp.apply_fsp_compensation(copy.deepcopy(merged), offers, protect,
                                           merged_moved, moved_wiring, auto=True)
    assert comp == [] and "moved" in conf[0]
    # 21 dB down: x180 0.29 * 11.2 > 1 clips at the DAC — rescaled AND named
    comp, conf = rp.apply_fsp_compensation(merged, offers, protect, merged,
                                           old_wiring, auto=True)
    assert any("clips at the DAC" in c and "x180_DragCosine" in c for c in conf)


def test_the_build_route_helper_reads_the_source_and_honours_answers(tmp_path):
    old_state, old_wiring = _chip(fsp=0)
    (tmp_path / "state.json").write_text(json.dumps(old_state))
    (tmp_path / "wiring.json").write_text(json.dumps(old_wiring))
    spec = {"qubits": ["q1"], "populate": {"qubit": {"q1": {"full_scale_power_dbm": -6}}}}
    base = {"qubit": {"q1": {"full_scale_power_dbm": 0}}}
    pend = regenerate.pending_fsp_offers(tmp_path, spec, base, [], None)
    assert len(pend) == 1 and pend[0]["fsp_new"] == -6.0
    key = pend[0]["fsp_path"]
    assert regenerate.pending_fsp_offers(
        tmp_path, spec, base, [], {key: {"mode": "solo", "fsp_new": -6}}) == []
    assert regenerate.pending_fsp_offers(tmp_path, spec, None, [], None) == []
    assert regenerate.pending_fsp_offers(tmp_path / "nope", spec, base, [], None) == []


def test_run_regenerate_end_to_end(tmp_path, monkeypatch):
    old_state, old_wiring = _chip(fsp=0)
    (tmp_path / "old").mkdir()
    (tmp_path / "old" / "state.json").write_text(json.dumps(old_state))
    (tmp_path / "old" / "wiring.json").write_text(json.dumps(old_wiring))
    fresh, _ = _chip(fsp=-6)               # the build applied the new FSP
    for op in fresh["qubits"]["q1"]["xy"]["operations"].values():
        if isinstance(op, dict):
            op["amplitude"] = 0.5          # builder defaults, tier-1 overrides them

    def fake_build(python_path, mode, spec, out_dir, timeout=300):
        out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "state.json").write_text(json.dumps(fresh))
        (out_dir / "wiring.json").write_text(json.dumps(old_wiring))
        return {"ok": True, "status": "ok", "error": None, "result": {}}

    monkeypatch.setattr(regenerate.config_generator, "run_generator", fake_build)
    spec = {"qubits": ["q1"], "populate": {"qubit": {"q1": {"full_scale_power_dbm": -6}}}}
    base = {"qubit": {"q1": {"full_scale_power_dbm": 0}}}
    key = PORT + ".full_scale_power_dbm"

    out = regenerate.run_regenerate("py", tmp_path / "old", spec, tmp_path / "a",
                                    populate_baseline=base)
    merged = json.loads((tmp_path / "a" / "state.json").read_text())
    ops = merged["qubits"]["q1"]["xy"]["operations"]
    assert merged["ports"]["mw_outputs"]["con1"]["1"]["2"]["full_scale_power_dbm"] == -6
    assert ops["EF_x180"]["amplitude"] == 0.074521               # unanswered: carried…
    assert any("6.0 dB weaker" in c for c in out["merge"]["populate_conflicts"])  # …and said
    assert out["merge"]["fsp_compensated_total"] == 0

    out = regenerate.run_regenerate("py", tmp_path / "old", spec, tmp_path / "b",
                                    populate_baseline=base,
                                    fsp_ack={key: {"mode": "comp", "fsp_new": -6}})
    ops = json.loads((tmp_path / "b" / "state.json").read_text())["qubits"]["q1"]["xy"]["operations"]
    assert math.isclose(ops["EF_x180"]["amplitude"], 0.074521 * 10 ** (6 / 20), rel_tol=1e-12)
    assert out["merge"]["fsp_compensated_total"] == 5
    assert not any("dB weaker" in c for c in out["merge"]["populate_conflicts"])

    out = regenerate.run_regenerate("py", tmp_path / "old", spec, tmp_path / "c",
                                    populate_baseline=base, power_mode="absolute")
    assert out["merge"]["fsp_compensated_total"] == 5


def test_the_build_route_asks_before_building(tmp_path, monkeypatch):
    """/regenerate/build answers an unanswered manual-mode FSP change with the
    offer (confirm_kind "fsp") and never builds; an answer or absolute mode
    reaches run_regenerate with power_mode / fsp_ack."""
    import sys as _sys
    from quam_state_manager.core import config_generator
    from quam_state_manager.generator.probe_capabilities import CATALOG_IDS
    from quam_state_manager.web.app import create_app

    manifest = {"ok": True, "cached": False, "error": None, "versions": {},
                "capabilities": {c: {"available": True, "detail": ""}
                                 for c in CATALOG_IDS}}
    monkeypatch.setattr(config_generator, "probe_capabilities",
                        lambda *a, **k: manifest)
    got = []

    def fake_run(py, src, spec, out, timeout=300, **kw):
        got.append(kw)
        return {"ok": True, "status": "ok", "error": None, "merge": None}

    monkeypatch.setattr(regenerate, "run_regenerate", fake_run)
    client = create_app(testing=True,
                        instance_path=str(tmp_path / "_inst")).test_client()
    client.post("/generate/select-env", json={"python": _sys.executable})
    src = tmp_path / "src"
    src.mkdir()
    old_state, old_wiring = _chip(fsp=0)
    (src / "state.json").write_text(json.dumps(old_state))
    (src / "wiring.json").write_text(json.dumps(old_wiring))
    spec = {"network": {"host": "1.2.3.4", "cluster_name": "C", "port": None},
            "instruments": {"controllers": [{"con": 1, "fems": [{"slot": 1, "fem": "mw"}]}],
                            "opx_plus": [], "octaves": []},
            "qubits": ["q1"], "qubit_pairs": [], "twpas": [],
            "lines": [{"element": "q1", "line": "drive", "channel": None}],
            "populate": {"qubit": {"q1": {"full_scale_power_dbm": -6}}}}
    body = {"spec": spec, "output_path": str(tmp_path / "out"),
            "source_folder": str(src),
            "populate_baseline": {"qubit": {"q1": {"full_scale_power_dbm": 0}}},
            "populate_touched": [], "power_mode": "manual"}

    res = client.post("/regenerate/build", json=body).get_json()
    assert res["needs_confirm"] is True and res["confirm_kind"] == "fsp"
    plan = res["fsp_compensation"]
    assert plan["fsp_new"] == -6.0 and len(plan["amps"]) == 5
    assert got == []                                   # nothing was built

    body["fsp_ack"] = {plan["fsp_path"]: {"mode": "comp", "fsp_new": -6, "amps": []}}
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True
    assert got[-1]["fsp_ack"] == body["fsp_ack"] and got[-1]["power_mode"] == "manual"

    body.pop("fsp_ack")
    body["power_mode"] = "absolute"                    # compensates by itself
    res = client.post("/regenerate/build", json=body).get_json()
    assert res.get("ok") is True and got[-1]["power_mode"] == "absolute"


def test_a_protected_amplitude_is_never_rewritten():
    # Defence in depth beside fsp_offers' own filter: even an unfiltered plan
    # never rewrites a path the wizard protected (its value wins), including
    # when the user re-committed the very value the chip already had.
    from quam_state_manager.core import mw_fem
    old_state, old_wiring = _chip(fsp=0)
    plan = mw_fem.fsp_compensation_plan(rp._root_of(old_state, old_wiring),
                                        PORT + ".full_scale_power_dbm", -6)
    plan["rows"] = [["qubit", "q1"]]
    x180 = "qubits.q1.xy.operations.x180_DragCosine.amplitude"
    merged, protect = _merged_after_build(old_state, -6, (x180,))
    comp, _ = rp.apply_fsp_compensation(merged, [plan], protect, merged,
                                        old_wiring, auto=True)
    assert merged["qubits"]["q1"]["xy"]["operations"]["x180_DragCosine"]["amplitude"] == 0.29170
    assert x180 not in {c["path"] for c in comp} and len(comp) == 4
