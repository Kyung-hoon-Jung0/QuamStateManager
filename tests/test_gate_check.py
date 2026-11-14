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
N = 16


def _wave(sample):
    return [0.0] + [sample] * (N - 2) + [0.0]


def _cfg(*, sample=0.2, offset=0.0, mode="direct", fem=True, length=N, samples=None,
         rate=1e9):
    port = ["con1", 1, 1] if fem else ["con1", 1]
    out = {"delay": 4, "sampling_rate": rate, "offset": offset}
    if mode:
        out["output_mode"] = mode
    con = ({"fems": {1: {"type": "LF", "analog_outputs": {1: out}}}} if fem
           else {"analog_outputs": {1: out}})
    return {"controllers": {"con1": con},
            "elements": {"q1.z": {"singleInput": {"port": port},
                                  "operations": {"cz_pulse": "cz_pulse.pulse"}}},
            "pulses": {"cz_pulse.pulse": {"length": length, "waveforms": {"single": "cz.wf"}}},
            "waveforms": {"cz.wf": {"type": "arbitrary",
                                    "samples": samples if samples is not None else _wave(sample)}}}


def _ops(cfg, script=SCRIPT, cap=runner_mod._MAX_SAMPLES):
    return [runner_mod.op_data(cfg, el, op, cap) for el, op in runner_mod.played_ops(script)]


def _gate_entry(script=SCRIPT, error=None, cfg=None, ops=None):
    cfg = cfg if cfg is not None else _cfg()
    return {"script": None if error else script, "error": error,
            "ops": [] if error else (ops if ops is not None else _ops(cfg, script))}


def _ran(*, before=None, after=None, script=SCRIPT, error=None, cfg=None, ops=None,
         before_gates=None):
    return {"status": "ok", "error": None, "data": {
        "ok": True, "before": before or {"load_error": None, "config_error": None},
        "after": after or {"load_error": None, "config_error": None},
        "gates": {PATH: _gate_entry(script, error, cfg, ops)},
        "before_gates": before_gates or {},
        "quam_config": {"with_lab_dir": "a", "without_lab_dir": "b"}}}


def _g(res, key=GATE):
    return gate_check.gate_result(res, *key)


def _checks(res, key=GATE):
    return {k: v["status"] for k, v in _g(res, key)["checks"].items()}


def _msg(res, check, key=GATE):
    return _g(res, key)["checks"][check]["message"]


PREVIEW = {GATE: {("q1.z", "cz_pulse"): _wave(0.2)}}


def test_all_pass_and_json_safe():
    res = gate_check.evaluate([GATE], _ran(), PREVIEW)
    assert res["ok"] and res["checked"]
    assert _checks(res) == {c: "pass" for c in gate_check.CHECKS}
    assert _g(res)["unchecked"] == []
    rows = _g(res)["timeline"]["rows"]
    assert [(r["start_ns"], r["end_ns"]) for r in rows] == [(4, 4 + N)]
    assert res["quam_config"] == {"with_lab_dir": "a", "without_lab_dir": "b"}
    assert json.loads(json.dumps(res))["gates"][0]["pair"] == "q1-2"


@pytest.mark.parametrize("ran", [
    _ran(), _ran(script=LOOP), _ran(error="KeyError: x"),
    _ran(before={"load_error": "E", "config_error": None}),
    {"status": "crash", "error": "e", "data": None},
    {"status": "timeout", "error": "e", "data": None},
    {"status": "no-env", "error": "e", "data": None}])
def test_result_is_json_serialisable_for_every_status(ran):
    json.dumps(gate_check.evaluate([GATE], ran, PREVIEW), allow_nan=False)


def test_before_fails_everything_skipped_with_chip_error():
    res = gate_check.evaluate([GATE], _ran(before={"load_error": "ImportError: lab",
                                                   "config_error": None}), PREVIEW)
    assert not res["checked"] and res["chip_error"] == "ImportError: lab"
    assert set(_checks(res).values()) == {"skipped"}
    assert _g(res)["unchecked"] == list(gate_check.CHECKS)
    assert "ImportError: lab" in _msg(res, "ops")
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
    assert c["load_config"] == "fail" and "ValueError: cfg" in _msg(res, "load_config")
    assert c["preview"] == "skipped" and c["range"] == "skipped" and c["timeline"] == "note"


def test_missing_operation_names_operation_and_channel():
    err = "KeyError: \"Operation 'cz_x_pulse' not found in channel 'q1.z'\""
    res = gate_check.evaluate([GATE], _ran(error=err), PREVIEW)
    c = _checks(res)
    assert c["ops"] == "fail" and c["load_config"] == "pass"
    assert "'cz_x_pulse'" in _msg(res, "ops") and "'q1.z'" in _msg(res, "ops")


def test_op_absent_from_config_fails():
    cfg = _cfg()
    del cfg["elements"]["q1.z"]["operations"]["cz_pulse"]
    res = gate_check.evaluate([GATE], _ran(cfg=cfg), PREVIEW)
    assert _checks(res)["ops"] == "fail" and "cz_pulse" in _msg(res, "ops")


def test_existing_gate_old_error_is_not_new_but_new_error_fails():
    """The runner applies only the named macros, on "before" too when the
    path exists there: the same error (memory address aside) is a note; a
    different one, or a gate that exists only in "after", fails."""
    old = "AssertionError: lines differ <obj at 0x0000021A>"
    same = "AssertionError: lines differ <obj at 0x7FFF1234>"
    res = gate_check.evaluate([GATE], _ran(error=same, before_gates={PATH: {"error": old}}))
    assert _checks(res)["ops"] == "note" and res["ok"]
    res = gate_check.evaluate([GATE], _ran(error="KeyError: other",
                                           before_gates={PATH: {"error": old}}))
    assert _checks(res)["ops"] == "fail"
    res = gate_check.evaluate([GATE], _ran(error=same, before_gates={PATH: {"error": None}}))
    assert _checks(res)["ops"] == "fail"
    assert _checks(gate_check.evaluate([GATE], _ran(error=same)))["ops"] == "fail"


@pytest.mark.parametrize("length,status", [(16, "pass"), (20, "pass"), (18, "fail"),
                                           (12, "fail"), (16.0, "fail"), ("16", "fail")])
def test_length_rule(length, status):
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(length=length)))
    assert _checks(res)["length"] == status
    if status == "fail":
        assert "cz_pulse" in _msg(res, "length") and "q1.z" in _msg(res, "length")


def test_preview_one_sample_off_fails_and_tolerance_passes():
    off = _wave(0.2)
    off[2] += 1e-3
    res = gate_check.evaluate([GATE], _ran(), {GATE: {("q1.z", "cz_pulse"): off}})
    assert _checks(res)["preview"] == "fail" and "sample 2" in _msg(res, "preview")
    near = _wave(0.2)
    near[2] += gate_check.PREVIEW_TOL / 2
    assert _checks(gate_check.evaluate([GATE], _ran(), {GATE: {("q1.z", "cz_pulse"): near}}))[
        "preview"] == "pass"
    short = {GATE: {("q1.z", "cz_pulse"): _wave(0.2)[:-1]}}
    assert _checks(gate_check.evaluate([GATE], _ran(), short))["preview"] == "fail"
    assert _checks(gate_check.evaluate([GATE], _ran(), {}))["preview"] == "skipped"


def test_previews_keyed_like_gates():
    as_text = {"q1-2/cz_new": PREVIEW[GATE]}
    assert _checks(gate_check.evaluate([GATE], _ran(), as_text))["preview"] == "pass"
    with pytest.raises(TypeError):
        gate_check.evaluate([GATE], _ran(), {"q1-2": PREVIEW[GATE]})


def test_preview_at_another_sampling_rate_is_skipped():
    cfg = _cfg(rate=2e9, samples=[0.0] + [0.2] * (2 * N - 2) + [0.0])
    res = gate_check.evaluate([GATE], _ran(cfg=cfg), PREVIEW)
    assert _checks(res)["preview"] == "skipped"
    assert str(N) in _msg(res, "preview") and str(2 * N) in _msg(res, "preview")
    assert "2e+09" in _msg(res, "preview")


def test_truncated_waveform_never_fails_for_length_and_range_skipped():
    long_n = 40
    cfg = _cfg(length=long_n, samples=[0.1] * long_n)
    ops = _ops(cfg, cap=10)
    res = gate_check.evaluate([GATE], _ran(cfg=cfg, ops=ops),
                              {GATE: {("q1.z", "cz_pulse"): [0.1] * long_n}})
    c = _checks(res)
    assert c["preview"] == "note" and "not" in _msg(res, "preview")
    assert c["range"] == "skipped" and "too long" in _msg(res, "range")
    assert res["ok"]


def test_timeline_cannot_draw_is_a_note_and_range_scale_unknown():
    res = gate_check.evaluate([GATE], _ran(script=LOOP), PREVIEW)
    c = _checks(res)
    assert c["timeline"] == "note" and "declare" in _msg(res, "timeline")
    assert _g(res)["timeline"]["rows"] == []
    assert c["ops"] == "pass" and c["preview"] == "pass"
    assert c["range"] == "skipped" and "scale is not known" in _msg(res, "range")
    assert res["ok"] and "range" in _g(res)["unchecked"]


def test_range_with_scale_from_the_program():
    script = SCRIPT.replace('play("cz_pulse", "q1.z")', 'play("cz_pulse" * amp(-3.0), "q1.z")')
    res = gate_check.evaluate([GATE], _ran(script=script))
    assert _checks(res)["range"] == "fail" and "-0.6" in _msg(res, "range")
    script = SCRIPT.replace('play("cz_pulse", "q1.z")', 'play("cz_pulse" * amp(2.0), "q1.z")')
    assert _checks(gate_check.evaluate([GATE], _ran(script=script)))["range"] == "pass"


def test_range_outside_fails():
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.6)))
    assert _checks(res)["range"] == "fail"


def test_range_inside_but_offset_pushes_outside():
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.4, offset=0.15)))
    assert _checks(res)["range"] == "fail" and "maximum + offset" in _msg(res, "range")
    assert _checks(gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.4))))["range"] == "pass"
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=-0.4, offset=-0.15)))
    assert _checks(res)["range"] == "fail"


def test_range_amplified_port_allows_more():
    assert _checks(gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.6, mode="amplified"))))[
        "range"] == "pass"


def test_range_unknown_is_skipped_with_message():
    for cfg in (_cfg(mode=None), _cfg(fem=False)):
        res = gate_check.evaluate([GATE], _ran(cfg=cfg))
        assert _checks(res)["range"] == "skipped" and "not known" in _msg(res, "range")


def test_iq_element_says_q_is_not_checked():
    cfg = _cfg()
    cfg["elements"]["q1.z"]["mixInputs"] = cfg["elements"]["q1.z"].pop("singleInput")
    cfg["elements"]["q1.z"]["mixInputs"] = {"I": ["con1", 1, 1], "Q": ["con1", 1, 2]}
    res = gate_check.evaluate([GATE], _ran(cfg=cfg), PREVIEW)
    assert "Q waveform" in _msg(res, "range") and "Q waveform" in _msg(res, "preview")


def test_one_bad_gate_does_not_lose_the_others():
    other = ("q3-4", "cz_b")
    ran = _ran()
    ran["data"]["gates"][gate_check.macro_path(*other)] = {"script": SCRIPT, "error": None,
                                                           "ops": "not a list"}
    res = gate_check.evaluate([other, GATE], ran, PREVIEW)
    assert set(_checks(res, other).values()) == {"skipped"}
    assert _checks(res) == {c: "pass" for c in gate_check.CHECKS}


def test_two_gates_in_either_order_give_the_same_result():
    other = ("q3-4", "cz_b")
    ran = _ran()
    ran["data"]["gates"][gate_check.macro_path(*other)] = _gate_entry(error="KeyError: b")
    a = gate_check.evaluate([GATE, other], ran, PREVIEW)
    b = gate_check.evaluate([other, GATE], ran, PREVIEW)
    for key in (GATE, other):
        assert _g(a, key) == _g(b, key)


@pytest.mark.parametrize("status", ["crash", "timeout", "no-env"])
def test_typed_runner_failures(status):
    res = gate_check.evaluate([GATE], {"status": status, "error": "words", "data": None})
    assert res["status"] == status and not res["checked"] and not res["ok"]
    assert set(_checks(res).values()) == {"skipped"}
    assert "words" in _msg(res, "load_config")


def _store(tmp_path, root=True):
    folder = tmp_path / "chip"
    folder.mkdir()
    state = {"qubit_pairs": {"q1-2": {"macros": {}}}}
    if root:
        state["__class__"] = "pkg.Root"
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    return QuamStore(folder)


def test_check_gates_no_env_and_runner_raise(tmp_path):
    store = _store(tmp_path)
    assert gate_check.check_gates(store, None, [GATE])["status"] == "no-env"

    def boom(*a, **k):
        raise RuntimeError("runner died")
    res = gate_check.check_gates(store, "python.exe", [GATE], runner=boom)
    assert res["status"] == "crash" and "runner died" in res["error"]


def test_check_gates_passes_the_root_class(tmp_path):
    seen = {}

    def fake(py, b, a, macros, **kw):
        seen.update(kw)
        return {"status": "crash", "error": "x", "data": None}
    gate_check.check_gates(_store(tmp_path), "python.exe", [GATE], runner=fake)
    assert seen["root_class"] == "pkg.Root"


def test_runner_root_class_fallback(tmp_path):
    (tmp_path / "state.json").write_text("{}", encoding="utf-8")
    assert runner_mod._root_class_of(tmp_path, "pkg.Root") == "pkg.Root"
    with pytest.raises(ValueError):
        runner_mod._root_class_of(tmp_path)


def test_create_onto_existing_key_is_a_failed_check(tmp_path):
    store = _store(tmp_path)
    called = []
    res = gate_check.check_gates(store, "python.exe", [GATE],
                                 [("qubit_pairs.q1-2", {"macros": {}})],
                                 runner=lambda *a, **k: called.append(1))
    assert res["status"] == "invalid-create" and not called and not res["ok"]
    assert _checks(res)["load_config"] == "fail" and "qubit_pairs.q1-2" in _msg(res, "load_config")
    json.dumps(res)


def test_run_gate_check_typed(monkeypatch, tmp_path):
    assert lab_waveform.run_gate_check(None, "a", "b", [])["status"] == "no-env"
    assert lab_waveform.run_gate_check(str(tmp_path / "nope.exe"), "a", "b", [])["status"] == "no-env"
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    seen = {}

    def cold(python, items, *, timeout=None, env_extra=None):
        seen.update(env=env_extra, items=items)
        return {"ok": False, "error": "words", "timed_out": True, "items": [], "sources": {}}
    monkeypatch.setattr(lab_waveform, "_run_cold", cold)
    res = lab_waveform.run_gate_check(str(py), "B", "A", [PATH], root_class="pkg.Root")
    assert res["status"] == "timeout"
    assert seen["env"] == {"QUAM_STATE_PATH": "A"}
    params = seen["items"][0]["params"]
    assert params["after"] == "A" and params["macros"] == [PATH] and params["root_class"] == "pkg.Root"
    # the error text alone never makes a timeout: the typed flag does
    monkeypatch.setattr(lab_waveform, "_run_cold", lambda *a, **k: {
        "ok": False, "error": "the lab timed out talking to a server", "items": [], "sources": {}})
    assert lab_waveform.run_gate_check(str(py), "B", "A", [PATH])["status"] == "crash"


def test_run_cold_sets_the_typed_timeout_flag(monkeypatch, tmp_path):
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    from quam_state_manager.core import config_generator

    def fake(argv, work_dir, timeout, outcome, **kw):
        outcome.update(ok=False, stderr="Lab worker timed out", result=None, error="x")
    monkeypatch.setattr(config_generator, "_run_script_outcome", fake)
    out = lab_waveform._run_cold(str(py), [{"qclass": "@gatecheck", "params": {}}])
    assert out["timed_out"] is True


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
    ok = {k: v["status"] for k, v in gate_check.gate_result(res, pair, "cz_copy")["checks"].items()}
    assert ok["load_config"] == "pass" and ok["ops"] == "pass", gate_check.gate_result(res, pair, "cz_copy")
    assert ok["timeline"] in ("pass", "note")
    broken = gate_check.gate_result(res, pair, "cz_broken")["checks"]["ops"]
    assert broken["status"] == "fail" and "no_such_op" in broken["message"], broken
    assert res["quam_config"] is not None
    assert store.mutation_seq == seq0 and not store.change_log
    assert {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()} == bytes0
    assert os.environ.get("QUAM_STATE_PATH") == str(out)
