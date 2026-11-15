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
#: the checks that read the lab's answer; "spectator_scale" is SM's own and is
#: omitted for a gate without spectator rows
LAB = tuple(c for c in gate_check.CHECKS if c != "spectator_scale")
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
    assert _checks(res) == {c: "pass" for c in LAB}
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
    assert {_checks(res)[c] for c in LAB} == {"skipped"}
    assert "spectator_scale" not in _checks(res) and _g(res)["unchecked"] == list(LAB)
    assert "ImportError: lab" in _msg(res, "ops")
    res = gate_check.evaluate([GATE], _ran(before={"load_error": None,
                                                   "config_error": "KeyError: x"}), PREVIEW)
    assert not res["checked"] and {_checks(res)[c] for c in LAB} == {"skipped"}
    assert "spectator_scale" not in _checks(res)


def test_after_load_fails():
    res = gate_check.evaluate([GATE], _ran(after={"load_error": "TypeError: bad",
                                                  "config_error": None}), PREVIEW)
    c = _checks(res)
    assert c["load_config"] == "fail" and not res["ok"]
    assert {c[k] for k in LAB[1:]} == {"skipped"}


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


def test_a_checked_gate_that_cannot_run_always_fails():
    """Re-checking an existing broken gate is not ok; the message says it
    already failed before. A new broken gate fails too."""
    err = "KeyError: \"Operation 'cz_x' not found in channel 'q1.z'\""
    res = gate_check.evaluate([GATE], _ran(error=err, before_gates={PATH: {"error": err}}))
    assert _checks(res)["ops"] == "fail" and not res["ok"]
    assert "already failed this way before this change" in _msg(res, "ops")
    res = gate_check.evaluate([GATE], _ran(error=err))
    assert _checks(res)["ops"] == "fail" and "already" not in _msg(res, "ops")


def test_another_broken_gate_of_the_chip_does_not_fail_this_one():
    ran = _ran()
    ran["data"]["before_gates"]["qubit_pairs.q3-4.macros.cz_old"] = {"error": "KeyError: old"}
    res = gate_check.evaluate([GATE], ran, PREVIEW, after_merged=_spectator_chip())
    assert res["ok"] and {_checks(res)[c] for c in LAB} == {"pass"}
    assert _checks(res)["spectator_scale"] == "pass"


@pytest.mark.parametrize("length,status", [(16, "pass"), (20, "pass"), (18, "fail"),
                                           (12, "fail"), (16.0, "pass"), (96.0, "pass"),
                                           (16.5, "fail"), ("16", "fail")])
def test_length_rule(length, status):
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(length=length)))
    assert _checks(res)["length"] == status
    if status == "fail":
        assert "cz_pulse" in _msg(res, "length") and "q1.z" in _msg(res, "length")
    if length == 16.5:
        assert "must be a whole number of ns: cz_pulse on q1.z is 16.5 ns" in _msg(res, "length")
    if isinstance(length, float) and status == "pass":
        assert "stored as" in _msg(res, "length")
        assert _g(res)["timeline"]["rows"], "an integral float is drawn"



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
    assert c["range"] == "skipped" and "timeline could not be drawn" in _msg(res, "range")
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
    assert _checks(res)["range"] == "fail"
    assert "cz_pulse on q1.z, port con1/1/1: 0.55 V is above this port's limit of 0.5 V (channel offset 0.15 V, play scale 1)" in _msg(res, "range")
    assert _checks(gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.4))))["range"] == "pass"
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=-0.4, offset=-0.15)))
    assert _checks(res)["range"] == "fail" and "-0.55 V is below this port's limit of -0.5 V" in _msg(res, "range")


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
    ran["data"]["gates"][gate_check.macro_path(*other)]["unresolved_plays"] = "x"
    res = gate_check.evaluate([other, GATE], ran, PREVIEW)
    assert _checks(res, other)["ops"] == "skipped"
    assert _checks(res) == {c: "pass" for c in LAB}


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
    assert {_checks(res)[c] for c in LAB} == {"skipped"}
    assert "spectator_scale" not in _checks(res) and _g(res)["unchecked"] == list(LAB)
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


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -float("inf")])
def test_non_finite_sample_fails_range_and_preview(bad):
    samples = _wave(0.2)
    samples[3] = bad
    cfg = _cfg(samples=samples)
    ops = _ops(cfg)
    assert ops[0]["waveform"]["nonfinite"] == 1
    json.dumps(ops, allow_nan=False)
    res = gate_check.evaluate([GATE], _ran(cfg=cfg, ops=ops), PREVIEW)
    c = _checks(res)
    assert c["range"] == "fail" and "cz_pulse contains values that are not numbers" in _msg(res, "range")
    assert c["preview"] == "fail" and not res["ok"]


def test_non_finite_constant_fails_range():
    cfg = _cfg()
    cfg["waveforms"]["cz.wf"] = {"type": "constant", "sample": float("nan")}
    res = gate_check.evaluate([GATE], _ran(cfg=cfg))
    assert _checks(res)["range"] == "fail"


def test_unreadable_waveform_is_never_counted_as_checked():
    cfg = _cfg()
    cfg["pulses"]["cz_pulse.pulse"]["waveforms"]["single"] = "missing.wf"
    res = gate_check.evaluate([GATE], _ran(cfg=cfg), PREVIEW)
    c = _checks(res)
    assert c["range"] == "skipped" and "missing.wf" in _msg(res, "range")
    assert c["preview"] == "skipped" and "missing.wf" in _msg(res, "preview")
    cfg["waveforms"]["missing.wf"] = {"type": "constant"}
    res = gate_check.evaluate([GATE], _ran(cfg=cfg))
    assert _checks(res)["range"] == "skipped" and "no sample" in _msg(res, "range")


def test_numpy_numbers_from_the_config_are_plain():
    np = pytest.importorskip("numpy")
    cfg = _cfg(length=np.int64(N), samples=list(np.array(_wave(0.2))))
    ops = _ops(cfg)
    json.dumps(ops)
    assert type(ops[0]["length"]) is int
    assert _checks(gate_check.evaluate([GATE], _ran(cfg=cfg, ops=ops)))["length"] == "pass"
    ops = _ops(_cfg(length=np.float64(16.0)))
    assert type(ops[0]["length"]) is float
    assert _checks(gate_check.evaluate([GATE], _ran(ops=ops)))["length"] == "pass"


def test_array_valued_config_value_only_spoils_that_operation():
    np = pytest.importorskip("numpy")
    cfg = _cfg()
    cfg["waveforms"]["cz.wf"]["samples"] = np.array(_wave(0.2))
    ops = _ops(cfg)
    assert ops[0]["waveform"]["samples"] == _wave(0.2)
    json.dumps(ops)

    class Odd:
        __module__ = "numpy"
    cfg["waveforms"]["cz.wf"]["samples"] = Odd()
    ops = _ops(cfg)
    assert ops[0]["waveform"]["error"] and "could not be read" in ops[0]["waveform"]["error"]
    res = gate_check.evaluate([GATE], _ran(cfg=cfg, ops=ops), PREVIEW)
    assert res["status"] == "ok" and _checks(res)["range"] == "skipped"


@pytest.mark.parametrize("stmt,status", [
    ('play("cz_pulse", "q1.z", duration=2)', "fail"),
    ('play("cz_pulse", "q1.z", duration=0)', "fail"),
    ('wait(3, "q1.z")', "note"),
    ('play("cz_pulse", "q1.z", duration=4)', "pass"),
    ('wait(4, "q1.z")', "pass"),
])
def test_play_time_duration_minimum(stmt, status):
    script = SCRIPT.replace('play("cz_pulse", "q1.z")', stmt + '\n    play("cz_pulse", "q1.z")')
    res = gate_check.evaluate([GATE], _ran(script=script))
    assert _checks(res)["length"] == status
    if status in ("fail", "note"):
        assert stmt in _msg(res, "length")
    if status == "note":
        assert "minimum wait of 4 clock cycles" in _msg(res, "length")


def test_runner_error_text_drops_the_temp_folders():
    b, a = r"C:\tmp\sm_chipcopy_x\before", r"C:\tmp\sm_chipcopy_x\after"
    e1 = runner_mod._err_text(RuntimeError(f"no file {b}\\q1"), [b, a])
    e2 = runner_mod._err_text(RuntimeError(f"no file {a.replace(chr(92), '/')}/q1"), [b, a])
    assert "sm_chipcopy_x" not in e1 + e2 and e1.replace("\\", "/") == e2.replace("\\", "/")


def test_chip_level_errors_never_show_the_temp_folder(tmp_path):
    from quam_state_manager.generator.path_scrub import scrub_paths
    parent = tmp_path / "sm_chipcopy_q"
    b, a = str(parent / "before"), str(parent / "after")
    for text in (f"cannot read {parent}\\x.json", f"cannot read {str(parent).upper()}/x",
                 f"bad {b.replace(chr(92), '/')}/state.json"):
        assert "sm_chipcopy_q" not in scrub_paths(text, [b, a]).lower()

    def runner(py, before, after, macros, **kw):
        err = f"KeyError: the chip in {Path(before).parent} does not load"
        return {"status": "ok", "error": None, "data": {
            "before": {"load_error": err, "config_error": None},
            "after": {"load_error": err, "config_error": None}, "gates": {}}}
    res = gate_check.check_gates(_store(tmp_path), "python.exe", [GATE], runner=runner)
    assert "<chip>" in res["chip_error"] and "sm_chipcopy_" not in json.dumps(res["gates"])
    assert "sm_chipcopy_" not in res["chip_error"]


def test_zero_plays_fail_and_unresolved_plays_are_counted():
    empty = SCRIPT.replace('    play("cz_pulse", "q1.z")\n', "")
    res = gate_check.evaluate([GATE], _ran(script=empty, ops=[]))
    assert _checks(res)["ops"] == "fail" and "plays no pulse" in _msg(res, "ops")
    var = SCRIPT.replace('play("cz_pulse", "q1.z")', 'play("cz_pulse", "q1.z")\n    play(op_name, "q1.z")')
    ran = _ran(script=var)
    ran["data"]["gates"][PATH]["unresolved_plays"] = runner_mod.unresolved_plays(var)
    res = gate_check.evaluate([GATE], ran, PREVIEW)
    assert _checks(res)["ops"] == "note" and "1 plays use a name SM could not read" in _msg(res, "ops")
    assert "ops" in _g(res)["partly_checked"]


def test_range_partly_checked_names_both_sides():
    script = SCRIPT.replace('play("cz_pulse", "q1.z")',
                            'play("cz_pulse", "q1.z")\n    play("other", "q2.z")')
    cfg = _cfg()
    cfg["elements"]["q2.z"] = {"singleInput": {"port": ["con9", 1]},
                               "operations": {"other": "cz_pulse.pulse"}}
    res = gate_check.evaluate([GATE], _ran(script=script, cfg=cfg))
    assert _checks(res)["range"] == "note" and "range" in _g(res)["partly_checked"]
    m = _msg(res, "range")
    assert "Inside the output range: cz_pulse on q1.z" in m and "other on q2.z" in m.split("Not checked")[1]


def test_busy_lock_is_a_typed_result(tmp_path, monkeypatch):
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")

    class Taken:
        def acquire(self, blocking=True):
            return False

        def release(self):
            raise AssertionError("never taken")
    monkeypatch.setattr(lab_waveform, "_machine_lock", lambda p: Taken())
    res = lab_waveform.run_gate_check(str(py), "B", "A", [PATH], lock_wait=0.2)
    assert res["status"] == "busy" and "busy" in res["error"]
    assert gate_check.evaluate([GATE], res)["status"] == "busy"


def test_store_identity_travels_in_the_result(tmp_path):
    from quam_state_manager.core import chip_copy
    store = _store(tmp_path)
    res = gate_check.check_gates(store, "python.exe", [GATE],
                                 runner=lambda *a, **k: {"status": "crash", "error": "x", "data": None})
    assert res["store_id"] == chip_copy.store_id(store)
    assert chip_copy.store_at_seq(store, res)
    (tmp_path / "o").mkdir()
    other = _store(tmp_path / "o")
    assert not chip_copy.store_at_seq(other, res)
    with pytest.raises(TypeError):
        chip_copy.store_at_seq(store, res["seq"])


def test_preview_coverage_is_stated():
    pv = {GATE: {("q1.z", "cz_pulse"): _wave(0.2), ("q9.z", "ghost"): [0.1]}}
    res = gate_check.evaluate([GATE], _ran(), pv)
    assert _checks(res)["preview"] == "note"
    assert "1 of 1 played" in _msg(res, "preview") and "ghost on q9.z" in _msg(res, "preview")
    script = SCRIPT.replace('play("cz_pulse", "q1.z")',
                            'play("cz_pulse", "q1.z")\n    play("other", "q2.z")')
    cfg = _cfg()
    cfg["elements"]["q2.z"] = {"singleInput": {"port": ["con1", 1, 1]},
                               "operations": {"other": "cz_pulse.pulse"}}
    res = gate_check.evaluate([GATE], _ran(script=script, cfg=cfg), PREVIEW)
    assert "1 of 2 played" in _msg(res, "preview") and "No preview for: other on q2.z" in \
        _msg(res, "preview")


@pytest.mark.parametrize("bad", [None, [float("nan")] * N, ["x"] * N, {"a": 1}])
def test_bad_preview_entry_only_skips_the_preview(bad):
    res = gate_check.evaluate([GATE], _ran(), {GATE: {("q1.z", "cz_pulse"): bad}})
    c = _checks(res)
    assert c["preview"] == "skipped"
    assert all(c[k] == "pass" for k in LAB if k != "preview")


def test_leftover_folder_reported(tmp_path, monkeypatch):
    from quam_state_manager.core import chip_copy
    monkeypatch.setattr(chip_copy, "REMOVE_PAUSE_S", 0)
    calls = []

    def stuck(path, *a, **k):
        calls.append(path)
        raise PermissionError("in use")
    monkeypatch.setattr(chip_copy.shutil, "rmtree", stuck)
    res = gate_check.check_gates(_store(tmp_path), "python.exe", [GATE],
                                 runner=lambda *a, **k: {"status": "timeout", "error": "t",
                                                         "data": None})
    assert res["leftover"] and len(calls) == chip_copy.REMOVE_TRIES
    json.dumps(res)


def test_one_store_identity_for_web_and_gate_check(tmp_path):
    from quam_state_manager.core import chip_copy
    from quam_state_manager.web import routes
    a = _store(tmp_path)
    (tmp_path / "b").mkdir()
    b = _store(tmp_path / "b")
    web_b = routes._store_uid(b)                  # b numbered by the web layer
    first = chip_copy.store_id(a)                 # a read before the web layer asks
    assert first != web_b                         # one counter: never equal
    assert chip_copy.store_id(b) == web_b
    assert routes._store_uid(a) == first == chip_copy.store_id(a), "never changes"


def test_copy_failure_message_has_no_path(tmp_path, monkeypatch):
    from quam_state_manager.core import chip_copy
    made = []
    real = chip_copy.tempfile.mkdtemp
    monkeypatch.setattr(chip_copy.tempfile, "mkdtemp",
                        lambda **kw: made.append(Path(real(**kw))) or str(made[-1]))

    def full(folder, s, w):
        raise OSError(28, "No space left on device", str(folder / "state.json"))
    monkeypatch.setattr(chip_copy, "write_chip_folder", full)
    res = gate_check.check_gates(_store(tmp_path), "python.exe", [GATE],
                                 runner=lambda *a, **k: pytest.fail("not reached"))
    text = json.dumps(res)
    assert res["status"] == "crash" and "no space left on device" in res["error"]
    assert "sm_chipcopy_" not in text and str(Path(made[0]).parent) not in text.replace("\\\\", "\\")
    assert not made[0].exists() and made[0].name not in text


def test_timeout_text_names_the_timeout_used(monkeypatch, tmp_path):
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    from quam_state_manager.core import config_generator

    def fake(argv, work_dir, timeout, outcome, **kw):
        outcome.update(ok=False, stderr="Lab worker timed out", result=None, error="x")
    monkeypatch.setattr(config_generator, "_run_script_outcome", fake)
    out = lab_waveform._run_cold(str(py), [{"qclass": "@gatecheck", "params": {}}], timeout=180)
    assert "180 s" in out["error"] and "90 s" not in out["error"]


def test_unreadable_program_text_is_skipped_not_no_pulse():
    assert runner_mod.script_readable(SCRIPT)
    assert not runner_mod.script_readable("play('x', 'q1.z')")
    ran = _ran(ops=[])
    ran["data"]["gates"][PATH]["script_unreadable"] = True
    res = gate_check.evaluate([GATE], ran)
    assert _checks(res)["ops"] == "skipped" and "could not be read" in _msg(res, "ops")
    assert "ops" in _g(res)["unchecked"] and not res["ok"]


def test_before_error_different_is_said_so():
    res = gate_check.evaluate([GATE], _ran(error="KeyError: a",
                                           before_gates={PATH: {"error": "KeyError: b"}}))
    assert "also failed before this change, with another error" in _msg(res, "ops")
    assert "already failed this way" not in _msg(res, "ops")


def test_ok_needs_every_gate_run():
    ran = _ran()
    ran["data"]["gates"][PATH] = "unreadable"
    res = gate_check.evaluate([GATE], ran, PREVIEW, after_merged=_spectator_chip())
    assert {_checks(res)[c] for c in LAB} == {"skipped"} and not res["ok"]
    assert _checks(res)["spectator_scale"] == "pass"



def test_existing_macro_check_import_failure_text(monkeypatch):
    """Pins the text the existing door gives when qm cannot be imported (the
    same as before the shared helpers were added)."""
    import sys
    monkeypatch.setitem(sys.modules, "qm", None)
    monkeypatch.setitem(sys.modules, "qm.qua", None)
    res = runner_mod._check_macros("json.JSONDecoder", {"contents": {}, "macros": ["a"]})
    assert res["reason"] == "class-unavailable" and res["macros"] == {}
    assert res["error"].startswith("could not import json.JSONDecoder: ModuleNotFoundError: ")


def test_timeline_not_drawn_is_unchecked_and_unknown_level_is_null():
    res = gate_check.evaluate([GATE], _ran(script=LOOP), PREVIEW)
    assert "timeline" in _g(res)["unchecked"]
    from quam_state_manager.core.static_timeline import static_timeline
    tl = static_timeline(SCRIPT, [{"element": "q1.z", "operation": "cz_pulse", "length": 16,
                                   "waveform": {"type": None, "error": "gone"}}])
    assert tl["rows"][0].level is None and any("level is not known" in n for n in tl["notes"])


def _top(res):
    return res["failed"], res["all_checked"], res["why_not_checked"]


def test_product_fields_all_passed():
    res = gate_check.evaluate([GATE], _ran(), PREVIEW)
    assert _top(res) == (False, True, None) and _g(res)["ran"] and res["ok"]


def test_product_fields_no_preview_is_not_all_checked():
    res = gate_check.evaluate([GATE], _ran(), {})
    failed, all_checked, why = _top(res)
    assert not failed and not all_checked and "preview" in why and _g(res)["ran"]


@pytest.mark.parametrize("status,word", [("busy", "busy"), ("no-env", "environment"),
                                         ("timeout", "in time"), ("crash", "could not run")])
def test_product_fields_typed_failures(status, word):
    res = gate_check.evaluate([GATE], {"status": status, "error": "e", "data": None})
    failed, all_checked, why = _top(res)
    assert not failed and not all_checked and word in why and not _g(res)["ran"]


def test_product_fields_one_gate_fails_another_not_run():
    other = ("q3-4", "cz_b")
    ran = _ran(cfg=_cfg(length=18))
    ran["data"]["gates"][gate_check.macro_path(*other)] = "unreadable"
    res = gate_check.evaluate([GATE, other], ran, PREVIEW)
    assert res["failed"] and not res["all_checked"] and res["why_not_checked"] is None
    assert not _g(res, other)["ran"] and _g(res)["ran"]


def test_product_fields_chip_as_is_does_not_load_and_after_breaks():
    res = gate_check.evaluate([GATE], _ran(before={"load_error": "E", "config_error": None}))
    assert _top(res)[:2] == (False, False) and "as it is does not load" in res["why_not_checked"]
    res = gate_check.evaluate([GATE], _ran(after={"load_error": "E", "config_error": None}))
    assert res["failed"] and not _g(res)["ran"]


def test_product_fields_unreadable_program_and_apply_failure():
    ran = _ran(ops=[])
    ran["data"]["gates"][PATH]["script_unreadable"] = True
    res = gate_check.evaluate([GATE], ran)
    assert _top(res)[:2] == (False, False) and "program text" in res["why_not_checked"]
    assert not _g(res)["ran"]
    res = gate_check.evaluate([GATE], _ran(error="KeyError: x"))
    assert res["failed"] and not _g(res)["ran"]


def test_product_fields_invalid_create(tmp_path):
    res = gate_check.check_gates(_store(tmp_path), "python.exe", [GATE],
                                 [("qubit_pairs.q1-2", {})], runner=lambda *a, **k: None)
    assert res["failed"] and not res["all_checked"] and res["why_not_checked"] is None


def test_copy_failure_leftover_is_reported(tmp_path, monkeypatch):
    from quam_state_manager.core import chip_copy
    monkeypatch.setattr(chip_copy, "REMOVE_PAUSE_S", 0)

    def full(folder, s, w):
        raise OSError("disk full")
    monkeypatch.setattr(chip_copy, "write_chip_folder", full)
    real = chip_copy.shutil.rmtree
    monkeypatch.setattr(chip_copy.shutil, "rmtree",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")))
    res = gate_check.check_gates(_store(tmp_path), "python.exe", [GATE], runner=lambda *a, **k: None)
    monkeypatch.setattr(chip_copy.shutil, "rmtree", real)
    try:
        assert res["leftover"] and Path(res["leftover"]).exists()
        assert "disk full" in res["error"] and "free some disk space" in res["error"]
        assert "OSError" not in res["error"]
    finally:
        real(res["leftover"], ignore_errors=True)


@pytest.mark.parametrize("exc,words", [
    (OSError("something odd"), "something odd"),
    (OSError(28, "No space left on device"), "free some disk space and try again"),
    (PermissionError(13, "Permission denied"), "check that SM may write to the temporary folder"),
    (OSError(), "an unknown error"),
])
def test_copy_error_texts(exc, words):
    from quam_state_manager.core import chip_copy
    msg = str(chip_copy._copy_error(exc))
    assert words in msg and type(exc).__name__ not in msg


def test_crash_path_scrubs_the_copy_folder_name_only(tmp_path, monkeypatch):
    """The copies were never created: the pattern and the literal temp base
    do the work; a lab path under the same temp base is kept."""
    import tempfile
    lab_path = str(Path(tempfile.gettempdir()) / "lab_code" / "pulse.py")
    name = "sm_chipcopy_ab12"
    copy_dir = str(Path(tempfile.gettempdir()) / name)

    def broken(*a, **k):
        raise RuntimeError(f"failed in {copy_dir}\\before\\state.json near {lab_path}")
    monkeypatch.setattr(gate_check.chip_copy, "make_chip_copies", broken)
    res = gate_check.check_gates(_store(tmp_path), "python.exe", [GATE],
                                 runner=lambda *a, **k: pytest.fail("not reached"))
    assert res["status"] == "crash"
    assert name not in res["error"] and "sm_chipcopy_" not in res["error"]
    assert lab_path in res["error"], "a lab path under the same temp base is kept"


@pytest.mark.parametrize("form", ["plain", "quoted", "doubled"])
def test_scrub_copy_names_with_a_space_in_the_user_folder(monkeypatch, form):
    base = r"C:\Users\John Doe\AppData\Local\Temp"
    monkeypatch.setattr("tempfile.gettempdir", lambda: base)
    path = base + r"\sm_chipcopy_ab\after\state.json"
    if form == "quoted":
        text = f"cannot open '{path}'"
    elif form == "doubled":
        text = "cannot open " + path.replace("\\", "\\\\")
    else:
        text = "cannot open " + path
    out = gate_check.scrub_copy_names(text)
    assert "John" not in out and "sm_chipcopy_" not in out and "<chip>" in out


def test_why_not_checked_names_every_gate_grouped():
    a, b, c = ("q1-2", "cz_a"), ("q3-4", "cz_b"), ("q5-6", "cz_c")
    ran = _ran()
    gates = ran["data"]["gates"]
    entry = gates.pop(PATH)
    for k in (a, b, c):
        gates[gate_check.macro_path(*k)] = dict(entry)
    gates[gate_check.macro_path(*c)]["script_unreadable"] = True
    res = gate_check.evaluate([a, b, c], ran, {})
    why = res["why_not_checked"]
    assert not res["failed"]
    assert why.index("cz_c") < why.index("cz_a"), why
    assert "For the gate cz_c, the program text could not be read." in why
    assert "For the gates cz_a and cz_b, not fully checked: the preview comparison." in why


def test_no_gates_is_an_input_error(tmp_path, monkeypatch):
    from quam_state_manager.core import chip_copy
    monkeypatch.setattr(chip_copy, "make_chip_copies", lambda *a, **k: pytest.fail("no copy"))
    for res in (gate_check.check_gates(_store(tmp_path), "python.exe", [],
                                       runner=lambda *a, **k: pytest.fail("no run")),
                gate_check.evaluate([], _ran())):
        assert res["status"] == "no-gates" and not res["failed"] and not res["ok"]
        assert res["why_not_checked"] == "No gate was given to check."
        json.dumps(res)


def test_write_that_breaks_the_chip_is_failed_without_a_gate_result():
    ran = _ran(after={"load_error": None, "config_error": "ValueError: cfg"})
    out = gate_check._finalize({"status": "ok", "checked": True, "gates": []},
                               write_broke=True)
    assert out["failed"]
    assert gate_check.evaluate([GATE], ran)["failed"]


def test_copy_failure_sentence_has_one_full_stop(tmp_path, monkeypatch):
    from quam_state_manager.core import chip_copy

    def full(folder, s, w):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(chip_copy, "write_chip_folder", full)
    res = gate_check.check_gates(_store(tmp_path), "python.exe", [GATE], runner=lambda *a, **k: None)
    assert ".." not in res["why_not_checked"] and res["why_not_checked"].endswith(".")


def test_non_os_copy_error_keeps_the_leftover(tmp_path, monkeypatch):
    from quam_state_manager.core import chip_copy
    monkeypatch.setattr(chip_copy, "REMOVE_PAUSE_S", 0)

    def odd(folder, s, w):
        raise ValueError("odd data")
    monkeypatch.setattr(chip_copy, "write_chip_folder", odd)
    real = chip_copy.shutil.rmtree
    monkeypatch.setattr(chip_copy.shutil, "rmtree",
                        lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")))
    res = gate_check.check_gates(_store(tmp_path), "python.exe", [GATE], runner=lambda *a, **k: None)
    monkeypatch.setattr(chip_copy.shutil, "rmtree", real)
    try:
        assert res["status"] == "crash" and res["leftover"] and Path(res["leftover"]).exists()
        assert "sm_chipcopy_" not in res["error"]
    finally:
        real(res["leftover"], ignore_errors=True)


# --------------------------------------------------------------- real env

def _find_env_python():
    """The env the other real-env tests use, else the first QM-capable env
    the generator's own discovery finds (``test_config_generator``'s rule)."""
    python = Path(r"C:\ProgramData\miniconda3\envs\qsm_official_ext\python.exe")
    if python.is_file():
        return str(python)
    from quam_state_manager.core.config_generator import discover_envs, probe_env
    for env in discover_envs():
        if probe_env(env["python"])["usable"]:
            return env["python"]
    return None


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
    assert ok["timeline"] == "pass" and ok["length"] == "pass", gate_check.gate_result(res, pair, "cz_copy")
    copy_entry = gate_check.gate_result(res, pair, "cz_copy")
    assert copy_entry["timeline"]["rows"], copy_entry
    assert "all 0 played" not in copy_entry["checks"]["ops"]["message"]
    rng = copy_entry["checks"]["range"]
    assert rng["status"] in ("pass", "note", "skipped"), rng
    for row in copy_entry["timeline"]["rows"]:
        # every played operation is named, as compared or as not checked with a reason
        assert f"{row['operation']} on {row['element']}" in rng["message"], rng
    broken = gate_check.gate_result(res, pair, "cz_broken")["checks"]["ops"]
    assert broken["status"] == "fail" and "no_such_op" in broken["message"], broken
    assert res["quam_config"] is not None
    assert store.mutation_seq == seq0 and not store.change_log
    assert {p.name: p.read_bytes() for p in out.iterdir() if p.is_file()} == bytes0
    assert os.environ.get("QUAM_STATE_PATH") == str(out)


def test_the_lab_error_is_quoted_and_ends_with_its_own_full_stop():
    m = gate_check._apply_message("cz_x", "ValueError: the two pulses must share flat_length")
    assert m.endswith('The lab\'s error: "ValueError: the two pulses must share flat_length."')
    assert gate_check._apply_message("cz_x", "Boom!").endswith('"Boom!"')
    assert m.isascii(), "user text uses plain ASCII quotes"


def test_range_adds_the_flux_point_offset_of_the_gate_line():
    merged = {"qubits": {"q1": {"z": {"flux_point": "joint", "joint_offset": 0.15}}}}
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.4)), None, merged)
    assert _checks(res)["range"] == "fail"
    assert "0.55 V is above this port's limit of 0.5 V (joint flux-point offset 0.15 V" in _msg(res, "range")
    merged["qubits"]["q1"]["z"]["joint_offset"] = 0.05
    assert _checks(gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.4)), None, merged))["range"] == "pass"


def test_range_with_an_unknown_flux_point_is_not_checked_and_says_why():
    merged = {"qubits": {"q1": {"z": {"flux_point": "sideways"}}}}
    res = gate_check.evaluate([GATE], _ran(cfg=_cfg(sample=0.4)), None, merged)
    assert _checks(res)["range"] == "skipped"
    assert "the flux point 'sideways' at qubits.q1.z.flux_point" in _msg(res, "range")


def test_check_gates_reads_the_flux_point_from_the_chip(tmp_path):
    folder = tmp_path / "chip"
    folder.mkdir()
    state = {"__class__": "pkg.Root", "qubit_pairs": {"q1-2": {"macros": {}}},
             "qubits": {"q1": {"z": {"flux_point": "joint", "joint_offset": 0.15}}}}
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    (folder / "wiring.json").write_text(json.dumps({"wiring": {}}), encoding="utf-8")
    res = gate_check.check_gates(QuamStore(folder), "python.exe", [GATE],
                                 runner=lambda *a, **k: _ran(cfg=_cfg(sample=0.4)))
    assert _checks(res)["range"] == "fail" and "joint flux-point offset" in _msg(res, "range")


def _spectator_chip():
    return {"qubits": {"q3": {"z": {"operations": {"spectator_unit": {"amplitude": 0.2}}}}},
            "qubit_pairs": {GATE[0]: {"macros": {GATE[1]: {
                "spectator_qubits": {"q3": "#/qubits/q3"},
                "spectator_levels": {"q3": 0.1}, "spectator_lengths": {"q3": 20}}}}}}


@pytest.mark.parametrize("path", ["checked", "no-env", "busy", "crash", "timeout", "chip-error"])
def test_no_rows_client_checks_match_stored_count(tmp_path, path):
    from quam_state_manager.core import gate_check_log
    if path == "no-env":
        res = gate_check.check_gates(_store(tmp_path), None, [GATE])
    else:
        ran = (_ran() if path == "checked" else
               _ran(before={"load_error": "broken"}) if path == "chip-error" else
               {"status": path, "error": "unavailable", "data": None})
        res = gate_check.evaluate([GATE], ran, PREVIEW)
    entry = _g(res)
    checks = list(entry["checks"].values())
    assert set(entry["checks"]) == set(LAB)
    assert len(checks) == gate_check_log.outcome(res, entry)["total"] == 6
    if path == "checked":
        assert gate_check_log.outcome(res, entry)["passed"] == 6
    else:
        # The client's allSkippedReason accepts exactly this single-callout shape.
        assert all(c == {"status": "skipped", "message": checks[0]["message"]} for c in checks)
        assert checks[0]["message"]
