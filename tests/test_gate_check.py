"""Gate check caller: every status of every check, typed runner failures, one real env."""
import json
import os
from pathlib import Path

import pytest

from quam_state_manager.core import gate_check, lab_waveform
from quam_state_manager.core.loader import QuamStore
from quam_state_manager.generator import run_pulse_waveform as runner_mod

GATE = ("q1-2", "cz_new")
PATH = gate_check.macro_path(*GATE)
SCRIPT = ('from qm.qua import *\n\nwith program() as prog:\n'
          '    align("q1.z", "q2.z")\n    play("cz_pulse", "q1.z")\n'
          '    align("q1.z", "q2.z")\n\nconfig = {}\n')
LOOP = ('from qm.qua import *\n\nwith program() as prog:\n'
        '    v1 = declare(int, )\n    play("cz_pulse", "q1.z")\n')


def _cfg(*, sample=0.2, offset=0.0, mode="direct", fem=True):
    port = ["con1", 1, 1] if fem else ["con1", 1]
    out = {"delay": 4, "sampling_rate": 1e9, "offset": offset}
    if mode:
        out["output_mode"] = mode
    con = ({"fems": {1: {"type": "LF", "analog_outputs": {1: out}}}} if fem
           else {"analog_outputs": {1: out}})
    return {"controllers": {"con1": con},
            "elements": {"q1.z": {"singleInput": {"port": port},
                                  "operations": {"cz_pulse": "cz_pulse.pulse"}}},
            "pulses": {"cz_pulse.pulse": {"length": 4, "waveforms": {"single": "cz.wf"}}},
            "waveforms": {"cz.wf": {"type": "arbitrary", "samples": [0.0, sample, sample, 0.0]}}}


def _ops(cfg):
    return [runner_mod.op_data(cfg, el, op) for el, op in runner_mod.played_ops(SCRIPT)]


def _ran(*, before=None, after=None, script=SCRIPT, error=None, cfg=None, ops=None):
    cfg = cfg if cfg is not None else _cfg()
    return {"status": "ok", "error": None, "data": {
        "ok": True, "before": before or {"load_error": None, "config_error": None},
        "after": after or {"load_error": None, "config_error": None},
        "gates": {PATH: {"script": None if error else script, "error": error,
                         "ops": [] if error else (ops if ops is not None else _ops(cfg))}},
        "quam_config": {"with_lab_dir": "a", "without_lab_dir": "b"}}}


def _checks(res):
    return {k: v["status"] for k, v in res["gates"][GATE]["checks"].items()}


PREVIEW = {GATE: {("q1.z", "cz_pulse"): [0.0, 0.2, 0.2, 0.0]}}


def test_all_pass():
    res = gate_check.evaluate([GATE], _ran(), PREVIEW)
    assert res["ok"] and res["checked"]
    assert _checks(res) == {c: "pass" for c in gate_check.CHECKS}
    rows = res["gates"][GATE]["timeline"]["rows"]
    assert [(r.start_ns, r.end_ns) for r in rows] == [(4, 8)]
    assert res["quam_config"] == {"with_lab_dir": "a", "without_lab_dir": "b"}


def test_before_fails_everything_skipped_with_chip_error():
    res = gate_check.evaluate([GATE], _ran(before={"load_error": "ImportError: lab",
                                                   "config_error": None}), PREVIEW)
    assert not res["checked"] and res["chip_error"] == "ImportError: lab"
    assert set(_checks(res).values()) == {"skipped"}
    assert "ImportError: lab" in res["gates"][GATE]["checks"]["ops"]["message"]
    res = gate_check.evaluate([GATE], _ran(before={"load_error": None,
                                                   "config_error": "KeyError: x"}), PREVIEW)
    assert not res["checked"] and set(_checks(res).values()) == {"skipped"}


def test_after_load_fails():
    res = gate_check.evaluate([GATE], _ran(after={"load_error": "TypeError: bad",
                                                  "config_error": None}), PREVIEW)
    c = _checks(res)
    assert c["load_config"] == "fail" and not res["ok"]
    assert {c[k] for k in gate_check.CHECKS[1:]} == {"skipped"}


def test_after_config_fails():
    res = gate_check.evaluate([GATE], _ran(after={"load_error": None,
                                                  "config_error": "ValueError: cfg"}), PREVIEW)
    c = _checks(res)
    assert c["load_config"] == "fail" and "ValueError: cfg" in \
        res["gates"][GATE]["checks"]["load_config"]["message"]
    assert c["preview"] == "skipped" and c["range"] == "skipped" and c["timeline"] == "note"


def test_missing_operation_names_operation_and_channel():
    err = "KeyError: \"Operation 'cz_x_pulse' not found in channel 'q1.z'\""
    res = gate_check.evaluate([GATE], _ran(error=err), PREVIEW)
    c = _checks(res)
    assert c["ops"] == "fail" and c["load_config"] == "pass"
    msg = res["gates"][GATE]["checks"]["ops"]["message"]
    assert "'cz_x_pulse'" in msg and "'q1.z'" in msg


def test_op_absent_from_config_fails():
    cfg = _cfg()
    del cfg["elements"]["q1.z"]["operations"]["cz_pulse"]
    res = gate_check.evaluate([GATE], _ran(cfg=cfg), PREVIEW)
    assert _checks(res)["ops"] == "fail"
    assert "cz_pulse" in res["gates"][GATE]["checks"]["ops"]["message"]


def test_unrelated_old_fault_does_not_fail_the_new_gate():
    """Another gate of the chip is broken (its apply() fails), but it is not
    the gate asked about and the chip still loads: the new gate passes."""
    ran = _ran()
    ran["data"]["gates"]["qubit_pairs.q3-4.macros.cz_old"] = {
        "script": None, "error": "KeyError: old", "ops": []}
    res = gate_check.evaluate([GATE], ran, PREVIEW)
    assert res["ok"] and set(_checks(res).values()) == {"pass"}


def test_preview_one_sample_off_fails_and_tolerance_passes():
    off = {GATE: {("q1.z", "cz_pulse"): [0.0, 0.2, 0.2 + 1e-3, 0.0]}}
    res = gate_check.evaluate([GATE], _ran(), off)
    assert _checks(res)["preview"] == "fail"
    assert "sample 2" in res["gates"][GATE]["checks"]["preview"]["message"]
    near = {GATE: {("q1.z", "cz_pulse"): [0.0, 0.2, 0.2 + gate_check.PREVIEW_TOL / 2, 0.0]}}
    assert _checks(gate_check.evaluate([GATE], _ran(), near))["preview"] == "pass"
    short = {GATE: {("q1.z", "cz_pulse"): [0.0, 0.2, 0.2]}}
    assert _checks(gate_check.evaluate([GATE], _ran(), short))["preview"] == "fail"
    assert _checks(gate_check.evaluate([GATE], _ran(), {}))["preview"] == "skipped"


def test_timeline_cannot_draw_is_a_note_and_others_reported():
    res = gate_check.evaluate([GATE], _ran(script=LOOP), PREVIEW)
    c = _checks(res)
    assert c["timeline"] == "note" and "declare" in res["gates"][GATE]["checks"]["timeline"]["message"]
    assert res["gates"][GATE]["timeline"]["rows"] == []
    assert c["ops"] == "pass" and c["preview"] == "pass" and c["range"] == "pass"
    assert res["ok"]


def test_range_outside_fails():
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.6)),
                              {GATE: {("q1.z", "cz_pulse"): [0.0, 0.6, 0.6, 0.0]}})
    assert _checks(res)["range"] == "fail"


def test_range_inside_but_offset_pushes_outside():
    cfg = _cfg(sample=0.4, offset=0.15)
    res = gate_check.evaluate([GATE], _ran(cfg=cfg))
    assert _checks(res)["range"] == "fail"
    assert "maximum + offset" in res["gates"][GATE]["checks"]["range"]["message"]
    assert _checks(gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.4))))["range"] == "pass"


def test_range_amplified_port_allows_more():
    assert _checks(gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.6, mode="amplified"))))[
        "range"] == "pass"


def test_range_unknown_is_skipped_with_message():
    for cfg in (_cfg(mode=None), _cfg(fem=False)):
        res = gate_check.evaluate([GATE], _ran(cfg=cfg))
        assert _checks(res)["range"] == "skipped"
        assert "not known" in res["gates"][GATE]["checks"]["range"]["message"]


@pytest.mark.parametrize("status", ["crash", "timeout", "no-env"])
def test_typed_runner_failures(status):
    res = gate_check.evaluate([GATE], {"status": status, "error": "words", "data": None})
    assert res["status"] == status and not res["checked"] and not res["ok"]
    assert set(_checks(res).values()) == {"skipped"}
    assert "words" in res["gates"][GATE]["checks"]["load_config"]["message"]


def _store(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    (folder / "state.json").write_text(json.dumps({"__class__": "pkg.Root", "qubit_pairs": {}}),
                                       encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    return QuamStore(folder)


def test_check_gates_no_env_and_runner_raise(tmp_path):
    store = _store(tmp_path)
    assert gate_check.check_gates(store, None, [GATE])["status"] == "no-env"

    def boom(*a):
        raise RuntimeError("runner died")
    res = gate_check.check_gates(store, "python.exe", [GATE], runner=boom)
    assert res["status"] == "crash" and "runner died" in res["error"]


def test_run_gate_check_typed(monkeypatch, tmp_path):
    assert lab_waveform.run_gate_check(None, "a", "b", [])["status"] == "no-env"
    assert lab_waveform.run_gate_check(str(tmp_path / "nope.exe"), "a", "b", [])["status"] == "no-env"
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    seen = {}

    def cold(python, items, *, timeout=None, env_extra=None):
        seen.update(env=env_extra, items=items)
        return {"ok": False, "error": lab_waveform._timeout_text(), "items": [], "sources": {}}
    monkeypatch.setattr(lab_waveform, "_run_cold", cold)
    res = lab_waveform.run_gate_check(str(py), "B", "A", [PATH])
    assert res["status"] == "timeout"
    assert seen["env"] == {"QUAM_STATE_PATH": "A"}
    assert seen["items"][0]["params"]["after"] == "A" and seen["items"][0]["params"]["macros"] == [PATH]
    monkeypatch.setattr(lab_waveform, "_run_cold", lambda *a, **k: {
        "ok": False, "error": "Traceback boom", "items": [], "sources": {}})
    assert lab_waveform.run_gate_check(str(py), "B", "A", [PATH])["status"] == "crash"


# --------------------------------------------------------------- real env

def _find_env_python():
    python = Path(r"C:\ProgramData\miniconda3\envs\qsm_official_ext\python.exe")
    return str(python) if python.is_file() else None


def test_real_env_copy_gate_and_broken_gate(tmp_path, monkeypatch):
    """Builds a temp chip from the sample spec in an env with quam, then checks a
    copy of an existing pair gate (passes) and a gate whose pulse names an
    operation its channel lacks (the ops check fails, naming it)."""
    python = _find_env_python()
    if python is None:
        pytest.skip("no lab environment with quam is discoverable")
    from quam_state_manager.core import config_generator
    spec = json.loads((Path(__file__).resolve().parents[1] /
                       "docs/examples/sample_spec_3q.json").read_text(encoding="utf-8"))
    spec["twpas"] = []
    spec["lines"] = [ln for ln in spec["lines"] if ln["line"] != "twpa_pump"]
    out = tmp_path / "chip"
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.setenv("QUAM_STATE_PATH", str(out))
    built = config_generator.run_generator(python, mode="build", spec=spec, out_dir=out, timeout=420)
    if not built.get("ok"):
        pytest.skip(f"the environment could not build the fixture chip: {built.get('error')}")
    store = QuamStore(out)
    pair, gate, macro = None, None, None
    for pn, p in (store.state.get("qubit_pairs") or {}).items():
        for gn, g in ((p or {}).get("macros") or {}).items():
            if gn.startswith("cz") and isinstance(g, dict) and g.get("flux_pulse_qubit"):
                pair, gate, macro = pn, gn, g
                break
        if pair:
            break
    if pair is None:
        pytest.skip("the fixture chip has no pair gate to copy")
    good = dict(macro)
    bad = dict(macro, flux_pulse_qubit="no_such_op")
    creates = [(f"qubit_pairs.{pair}.macros.cz_copy", good),
               (f"qubit_pairs.{pair}.macros.cz_broken", bad)]
    bytes0 = {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()}
    seq0 = store.mutation_seq
    res = gate_check.check_gates(store, python, [(pair, "cz_copy"), (pair, "cz_broken")], creates)
    assert res["status"] == "ok", res
    assert res["checked"], res
    ok = {k: v["status"] for k, v in res["gates"][(pair, "cz_copy")]["checks"].items()}
    assert ok["load_config"] == "pass" and ok["ops"] == "pass", res["gates"][(pair, "cz_copy")]
    assert ok["timeline"] in ("pass", "note")
    broken = res["gates"][(pair, "cz_broken")]["checks"]["ops"]
    assert broken["status"] == "fail" and "no_such_op" in broken["message"], broken
    assert res["quam_config"] is not None
    assert store.mutation_seq == seq0 and not store.change_log
    assert {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()} == bytes0
    assert os.environ.get("QUAM_STATE_PATH") == str(out)
